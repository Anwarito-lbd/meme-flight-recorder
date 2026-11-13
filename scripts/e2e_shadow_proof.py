#!/usr/bin/env python3
"""Drive one real mainnet token through every stage, and prove it did.

The acceptance question is not "do the units pass" -- they did that while the
book was empty for weeks. It is whether a **real** Solana event can travel the
whole path and come out the other side as a paper position with a PnL and an
audit trail. So this starts from a live transaction on a launch venue and stops
only at the evidence ledger.

    real transaction -> program -> exact mint -> raw evidence -> decoder
    -> lifecycle -> enrichment -> hard security -> wallet intelligence
    -> market data -> strategy features -> decision -> quote
    -> unsigned transaction -> simulation -> modeled fill -> paper position
    -> monitoring -> paper exit -> PnL -> evidence ledger

Rules it holds itself to:

  * **Real data or the stage FAILS.** No stage may be marked PASS from a fixture.
    A stage that could not reach its provider is recorded DEGRADED or FAIL with
    the reason, never quietly skipped.
  * **The decision stage is allowed to say no.** A candidate that the gates
    reject still traverses every later stage in *modelled* form, because the
    point is to prove the pipeline works, not to manufacture an entry. Whether
    the strategy would really have entered is recorded separately as
    `would_enter`, and the current default policy says WATCH for everything.
  * **Paper only.** Nothing signs, nothing broadcasts.

Writes `artifacts/e2e_shadow_proof.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.deeplinks import links_for
from meme_flight_recorder.env import load_env
from meme_flight_recorder.execution_sim import ExecutionSimulator
from meme_flight_recorder.providers.birdeye import BirdeyeProvider
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider
from meme_flight_recorder.providers.goplus import GoPlusProvider
from meme_flight_recorder.readiness import (
    ReadinessPolicy,
    SafetyVerdict,
    StrategyReadiness,
    assess,
    lifecycle_state,
)
from meme_flight_recorder.venues import VENUE_PROGRAM_IDS, Venue, decode_transaction

WSOL = "So11111111111111111111111111111111111111112"
ARTIFACTS = Path("artifacts")


class Stages:
    """Ordered record of what happened, with timings on a monotonic clock."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add(
        self,
        name: str,
        status: str,
        detail: Any = None,
        elapsed_ms: float | None = None,
    ) -> None:
        self.records.append(
            {
                "stage": name,
                "status": status,
                "detail": detail,
                "elapsed_ms": round(elapsed_ms, 3) if elapsed_ms is not None else None,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        marker = {"PASS": "[PASS]", "DEGRADED": "[WARN]", "FAIL": "[FAIL]"}.get(status, "[----]")
        timing = f"{elapsed_ms:8.1f}ms" if elapsed_ms is not None else " " * 10
        print(f"  {marker} {name:<26}{timing}  {detail if detail is not None else ''}")

    @property
    def passed(self) -> bool:
        return all(record["status"] != "FAIL" for record in self.records)

    def status_of(self, name: str) -> str | None:
        for record in self.records:
            if record["stage"] == name:
                return record["status"]
        return None


def rpc(url: str, method: str, params: list[Any], timeout: int = 30) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def find_live_candidate(rpc_url: str, stages: Stages, attempts: int = 40) -> Any:
    """Walk real recent transactions until one yields a non-SOL mint.

    Most transactions on these programs are trades in tokens we cannot price, or
    fail, or carry only wrapped SOL. Rather than pretend the first one works,
    this searches and reports how many it had to look at -- which is itself a
    measurement of how noisy the raw feed is.
    """
    started = time.perf_counter_ns()
    inspected = 0
    failed_seen = 0
    for venue in (Venue.PUMP_FUN, Venue.PUMP_SWAP):
        program = VENUE_PROGRAM_IDS[venue]
        signatures = rpc(rpc_url, "getSignaturesForAddress", [program, {"limit": attempts}])
        for entry in signatures.get("result") or []:
            inspected += 1
            payload = rpc(
                rpc_url,
                "getTransaction",
                [
                    entry["signature"],
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
            ).get("result")
            if not payload:
                continue
            decoded = decode_transaction(payload)
            if not decoded.succeeded:
                failed_seen += 1
            mint = decoded.primary_mint
            if mint and mint != WSOL:
                stages.add(
                    "real_solana_event",
                    "PASS",
                    f"{venue.value} sig={decoded.signature[:16]} slot={decoded.slot} "
                    f"(inspected {inspected}, {failed_seen} failed tx decoded not skipped)",
                    (time.perf_counter_ns() - started) / 1e6,
                )
                return decoded
    stages.add(
        "real_solana_event",
        "FAIL",
        f"no decodable mint in {inspected} real transactions",
        (time.perf_counter_ns() - started) / 1e6,
    )
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mint", help="Force a specific mint instead of searching live.")
    parser.add_argument("--hold-minutes", type=float, default=60.0)
    arguments = parser.parse_args()

    load_env()
    settings = load_settings()
    stages = Stages()
    proof: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "paper_only": True,
        "signed_anything": False,
        "broadcast_anything": False,
    }

    helius_key = os.getenv("HELIUS_API_KEY", "")
    if not helius_key:
        stages.add("real_solana_event", "FAIL", "HELIUS_API_KEY missing")
        return 1
    rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"

    print("e2e shadow proof -- real mainnet, paper only\n")

    # ---------------------------------------------------------------- source
    decoded = None
    if arguments.mint:
        mint = arguments.mint
        stages.add("real_solana_event", "DEGRADED", f"mint forced by operator: {mint}")
    else:
        decoded = find_live_candidate(rpc_url, stages)
        if decoded is None:
            write(proof, stages)
            return 1
        mint = decoded.primary_mint

    proof["mint"] = mint

    if decoded is not None:
        # The mint can be recovered from the runtime balance records even when
        # no venue instruction decoded -- that is why they are preferred as the
        # identity source. But it is not the same evidence, and reporting PASS
        # for a program we did not actually decode would overstate the pipeline.
        venue_names = ", ".join(venue.value for venue in decoded.venues)
        stages.add(
            "exact_program",
            "PASS" if venue_names else "DEGRADED",
            venue_names or "no venue instruction decoded; mint from balance records",
        )
        stages.add("exact_mint", "PASS", mint)
        stages.add(
            "raw_evidence",
            "PASS",
            f"identity=(slot={decoded.slot}, tx_index={decoded.transaction_index}, "
            f"sig={decoded.signature[:12]}) succeeded={decoded.succeeded}",
        )
        actions = sorted({item.action.value for item in decoded.instructions})
        stages.add(
            "decoder",
            "PASS" if actions and actions != ["unknown"] else "DEGRADED",
            f"instructions={len(decoded.instructions)} actions={actions}",
        )
        proof["source_transaction"] = {
            "signature": decoded.signature,
            "slot": decoded.slot,
            "transaction_index": decoded.transaction_index,
            "succeeded": decoded.succeeded,
            "venues": [venue.value for venue in decoded.venues],
            "actions": actions,
        }

    # ------------------------------------------------------------ market data
    started = time.perf_counter_ns()
    pair = None
    try:
        pair = DexScreenerProvider().deepest_pair(mint)
        elapsed = (time.perf_counter_ns() - started) / 1e6
        if pair is None:
            stages.add("market_data", "DEGRADED", "no pair quoted yet", elapsed)
        else:
            stages.add(
                "market_data",
                "PASS",
                f"liquidity=${pair.liquidity_usd or 0:,.0f} price={pair.price_usd} "
                f"age={pair.pair_age_minutes:.1f}m"
                if pair.pair_age_minutes is not None
                else f"liquidity=${pair.liquidity_usd or 0:,.0f}",
                elapsed,
            )
    except Exception as error:  # noqa: BLE001
        stages.add("market_data", "FAIL", f"{type(error).__name__}: {error}")

    liquidity = getattr(pair, "liquidity_usd", None)
    age_minutes = getattr(pair, "pair_age_minutes", None)
    # Absent until the chain tells us. Never defaulted to 9, which would make
    # every price conversion quietly wrong for any token that is not 9-decimal.
    token_decimals: int | None = None

    # ------------------------------------------------------------- lifecycle
    started = time.perf_counter_ns()
    state = lifecycle_state(
        age_minutes,
        graduated=True if pair is not None else None,
        liquidity_usd=liquidity,
    )
    stages.add(
        "lifecycle",
        "PASS",
        f"{state.value} (age={age_minutes}m)",
        (time.perf_counter_ns() - started) / 1e6,
    )

    # -------------------------------------------------------- hard security
    started = time.perf_counter_ns()
    security: dict[str, Any] = {}
    try:
        report = GoPlusProvider().token_security(mint)
        elapsed = (time.perf_counter_ns() - started) / 1e6
        if report is None:
            stages.add("hard_security", "DEGRADED", "no security record returned", elapsed)
        else:
            security = {
                "sellable": report.sellable,
                "top10_holder_pct": report.top10_holder_pct,
            }
            stages.add(
                "hard_security",
                "PASS",
                f"sellable={report.sellable} top10={report.top10_holder_pct}",
                elapsed,
            )
    except Exception as error:  # noqa: BLE001
        stages.add("hard_security", "DEGRADED", f"{type(error).__name__}")

    # -------------------------------------------------- wallet intelligence
    started = time.perf_counter_ns()
    try:
        largest = rpc(rpc_url, "getTokenLargestAccounts", [mint]).get("result", {})
        holders = largest.get("value") or []
        supply = rpc(rpc_url, "getTokenSupply", [mint]).get("result", {}).get("value") or {}
        total = float(supply.get("uiAmount") or 0.0)
        if supply.get("decimals") is not None:
            token_decimals = int(supply["decimals"])
        top10 = sum(float(h.get("uiAmount") or 0.0) for h in holders[:10])
        concentration = (100.0 * top10 / total) if total > 0 else None
        stages.add(
            "wallet_intelligence",
            "PASS" if concentration is not None else "DEGRADED",
            f"holders_sampled={len(holders)} top10={concentration:.2f}%"
            if concentration is not None
            else "supply unknown",
            (time.perf_counter_ns() - started) / 1e6,
        )
        security["chain_top10_pct"] = concentration
    except Exception as error:  # noqa: BLE001
        stages.add("wallet_intelligence", "DEGRADED", f"{type(error).__name__}")

    # ------------------------------------------------------ strategy features
    started = time.perf_counter_ns()
    features: dict[str, Any] = {
        "liquidity_usd": liquidity,
        "age_minutes": age_minutes,
        "lifecycle": state.value,
        "top10_pct": security.get("chain_top10_pct"),
        # Narrative and trader features are computed by their own modules and
        # are None here because no social event has been ingested for this mint.
        # None, not zero: an unobserved narrative is not a quiet one.
        "narrative_state": None,
        "qualified_trader_confluence": None,
    }
    stages.add(
        "strategy_features",
        "PASS",
        f"{sum(1 for v in features.values() if v is not None)}/{len(features)} present",
        (time.perf_counter_ns() - started) / 1e6,
    )

    # ---------------------------------------------------------------- decision
    started = time.perf_counter_ns()
    verdict = SafetyVerdict.PASS if security.get("sellable") is not False else SafetyVerdict.FAIL
    if liquidity is None:
        verdict = SafetyVerdict.UNKNOWN
    decision = assess(verdict, state, age_minutes, ReadinessPolicy())
    stages.add(
        "decision",
        "PASS",
        f"safety={decision.verdict.value} lifecycle={decision.lifecycle.value} "
        f"readiness={decision.readiness.value} ({decision.reason})",
        (time.perf_counter_ns() - started) / 1e6,
    )
    proof["decision"] = {
        "safety_verdict": decision.verdict.value,
        "lifecycle": decision.lifecycle.value,
        "readiness": decision.readiness.value,
        "reason": decision.reason,
        "would_enter": decision.readiness is StrategyReadiness.ENTRY,
    }

    # ------------------------------------- quote, build, simulate, modeled fill
    position_usd = settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0
    lamports = max(1, int(position_usd / 150.0 * 1_000_000_000))
    simulator = ExecutionSimulator()
    attempt = simulator.attempt(mint, "buy", WSOL, mint, lamports, slippage_bps=300)

    stages.add(
        "jupiter_quote",
        "PASS" if attempt.quote_price else "FAIL",
        f"price={attempt.quote_price} impact={attempt.price_impact_pct}% "
        f"route={list(attempt.route)}",
        attempt.quote_latency_ms,
    )
    stages.add(
        "unsigned_transaction",
        "PASS" if attempt.stages.get("tx_bytes") else "FAIL",
        f"{int(attempt.stages.get('tx_bytes', 0))} bytes, never signed",
        attempt.build_latency_ms,
    )
    stages.add(
        "rpc_simulation",
        "PASS" if attempt.simulation_ok else "DEGRADED",
        f"ok={attempt.simulation_ok} CU={attempt.compute_units_simulated} "
        f"err={attempt.simulation_error}",
        attempt.simulate_latency_ms,
    )
    stages.add(
        "modeled_fill",
        "PASS" if attempt.modeled_fill_price else "FAIL",
        f"fill={attempt.modeled_fill_price} cost=${attempt.total_cost_usd} "
        f"land_p={attempt.landing_probability}",
    )
    proof["execution"] = attempt.as_dict()

    # ------------------------------------------------- paper position and exit
    started = time.perf_counter_ns()
    cost = round_trip_cost(position_usd, settings.costs)

    # The quote is denominated in output base units per input lamport. The exit
    # mark is USD per whole token. Comparing them directly is a unit error that
    # reported a real position as a total loss, so the entry is converted into
    # the same unit the exit is measured in:
    #
    #     tokens_received = outAmount / 10**decimals
    #     entry_usd_per_token = position_usd / tokens_received
    #
    # Without decimals the conversion is impossible, and an unconvertible price
    # is None rather than a number in the wrong unit.
    entry_price = None
    if attempt.modeled_fill_price and token_decimals is not None:
        tokens_received = attempt.modeled_fill_price * lamports / (10**token_decimals)
        if tokens_received > 0:
            entry_price = position_usd / tokens_received
    if not entry_price:
        stages.add(
            "paper_position",
            "FAIL",
            f"no executable fill price in USD (decimals={token_decimals})",
        )
        write(proof, stages)
        return 1
    stages.add(
        "paper_position",
        "PASS",
        f"opened MODELLED ${position_usd:.2f} at ${entry_price:.6g}/token "
        f"(readiness={decision.readiness.value}, so the live book would only WATCH)",
        (time.perf_counter_ns() - started) / 1e6,
    )

    started = time.perf_counter_ns()
    exit_price = None
    try:
        birdeye = BirdeyeProvider()
        now = int(time.time())
        series = birdeye.candles(mint, now - int(arguments.hold_minutes * 60), now)
        traded = series.trades_only if series else ()
        if traded:
            exit_price = traded[-1].close
            stages.add(
                "monitoring",
                "PASS",
                f"{len(traded)} traded minutes of {len(series.candles)} "
                f"({series.silent_minutes} silent)",
                (time.perf_counter_ns() - started) / 1e6,
            )
        else:
            stages.add(
                "monitoring",
                "DEGRADED",
                "no traded minute in window -- position would be unsellable",
                (time.perf_counter_ns() - started) / 1e6,
            )
    except Exception as error:  # noqa: BLE001
        stages.add("monitoring", "DEGRADED", f"{type(error).__name__}")

    # A pool with no print cannot be sold into. That is a total loss, not a
    # missing measurement -- the distinction this project has already paid for.
    gross = (exit_price / entry_price) if exit_price and entry_price else 0.0
    net_pnl = position_usd * gross - position_usd - cost.total_usd
    stages.add(
        "paper_exit",
        "PASS",
        f"exit={exit_price} gross={gross:.4f}x reason="
        f"{'time_limit' if exit_price else 'unsellable_no_print'}",
    )
    stages.add(
        "pnl",
        "PASS",
        f"net ${net_pnl:+.4f} on ${position_usd:.2f} "
        f"(round trip {cost.pct_of_position:.3f}%, breakeven {cost.breakeven_multiple:.4f}x)",
    )
    proof["paper_trade"] = {
        "position_usd": position_usd,
        "token_decimals": token_decimals,
        "entry_price_usd_per_token": entry_price,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_multiple": gross,
        "round_trip_cost_usd": cost.total_usd,
        "net_pnl_usd": net_pnl,
        "hold_minutes": arguments.hold_minutes,
        "modelled_only": True,
    }

    # ------------------------------------------------------- evidence ledger
    started = time.perf_counter_ns()
    proof["evidence_ledger"] = {
        "mint": mint,
        "features": features,
        "security": security,
        "deep_links": links_for(mint).as_dict(),
        "stages": stages.records,
    }
    stages.add(
        "evidence_ledger",
        "PASS",
        f"{len(stages.records) + 1} stages recorded",
        (time.perf_counter_ns() - started) / 1e6,
    )

    write(proof, stages)
    return 0 if stages.passed else 1


def write(proof: dict[str, Any], stages: Stages) -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    proof["stages"] = stages.records
    proof["finished_at"] = datetime.now(UTC).isoformat()
    proof["result"] = "PASS" if stages.passed else "FAIL"
    proof["stage_summary"] = {
        record["stage"]: record["status"] for record in stages.records
    }
    path = ARTIFACTS / "e2e_shadow_proof.json"
    path.write_text(json.dumps(proof, indent=2, default=str), encoding="utf-8")
    print(f"\nresult: {proof['result']}   written to {path}")


if __name__ == "__main__":
    raise SystemExit(main())
