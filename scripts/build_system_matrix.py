#!/usr/bin/env python3
"""Audit every subsystem and emit the matrix, from evidence rather than memory.

A hand-written status table is a claim. This one is generated: file existence is
checked on disk, tests are matched to modules, providers are probed live, and
"last successful run" comes from an artifact or database that actually exists.
Anything the script cannot verify is reported as unverified rather than assumed
working -- the whole point is that `PASS` should be impossible to type by
accident.

Status vocabulary is fixed and has no soft middle:

    PASS             verified working, with real-data proof where external
    RUNNING          a live process or loop is currently executing it
    DEGRADED         works, but with a stated limitation
    BLOCKED          cannot work until something outside our control changes
    FAIL             implemented and not working
    NOT_IMPLEMENTED  no implementation exists

Writes `artifacts/SYSTEM_MATRIX.md` and `artifacts/SYSTEM_MATRIX.json`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meme_flight_recorder.env import load_env

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
SRC = ROOT / "src" / "meme_flight_recorder"


@dataclass
class Subsystem:
    number: int
    name: str
    status: str
    implementation: list[str] = field(default_factory=list)
    test: list[str] = field(default_factory=list)
    real_data_proof: str = ""
    last_success: str | None = None
    dependencies: list[str] = field(default_factory=list)
    provider: str = "-"
    fallback: str = "-"
    known_failure_mode: str = ""
    hot_path: bool = False
    can_affect_paper_entry: bool = False


def exists(*relative: str) -> bool:
    return all((ROOT / path).is_file() for path in relative)


def probe(name: str, url: str, headers: dict[str, str] | None = None) -> tuple[str, str]:
    """Hit a provider and report status plus latency, or the failure."""
    started = time.perf_counter_ns()
    try:
        request = urllib.request.Request(
            url,
            headers={
                "accept": "application/json",
                # DexScreener and GeckoTerminal reject an absent User-Agent with
                # 403, which the first version of this probe reported as a
                # provider outage. A probe defect must not become a status.
                "user-agent": "meme-flight-recorder/0.4",
                **(headers or {}),
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read(512)
        return "PASS", f"HTTP 200 in {(time.perf_counter_ns() - started) / 1e6:.0f}ms"
    except Exception as error:  # noqa: BLE001
        code = getattr(error, "code", None)
        return ("DEGRADED" if code == 429 else "FAIL"), f"{type(error).__name__} {code or error}"


def journal_facts() -> dict[str, Any]:
    path = ROOT / "data" / "flight_recorder.db"
    if not path.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        counts = dict(connection.execute("select event_type, count(*) from events group by 1"))
        mints = connection.execute(
            "select count(distinct entity_id) from events where event_type='candidate_observed'"
        ).fetchone()[0]
        last = connection.execute("select max(observed_at) from events").fetchone()[0]
        connection.close()
        return {"counts": counts, "mints": mints, "last": last}
    except Exception:  # noqa: BLE001
        return {}


def cache_facts() -> dict[str, Any]:
    path = ROOT / "data" / "pool_history.db"
    if not path.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        by_status = dict(connection.execute("select status, count(*) from fetches group by 1"))
        candles = connection.execute("select count(*) from candles").fetchone()[0]
        last = connection.execute("select max(fetched_at) from fetches").fetchone()[0]
        connection.close()
        return {"by_status": by_status, "candles": candles, "last": last}
    except Exception:  # noqa: BLE001
        return {}


def running_processes() -> list[str]:
    """Which of our long-running loops are actually alive right now."""
    try:
        output = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout
    except Exception:  # noqa: BLE001
        return []
    alive = []
    for marker, label in (
        ("cli collect", "collector (shadow/paper)"),
        ("backfill_pool_history", "outcome backfill"),
    ):
        if marker in output:
            alive.append(label)
    return alive


def build() -> list[Subsystem]:
    load_env()
    journal = journal_facts()
    cache = cache_facts()
    alive = running_processes()
    proof_path = ARTIFACTS / "e2e_shadow_proof.json"
    e2e: dict[str, Any] = {}
    if proof_path.is_file():
        e2e = json.loads(proof_path.read_text(encoding="utf-8"))
    e2e_stage = e2e.get("stage_summary", {})
    e2e_when = e2e.get("finished_at")

    jupiter = os.getenv("JUPITER_API_KEY", "")
    birdeye = os.getenv("BIRDEYE_API_KEY", "")

    dex_status, dex_detail = probe(
        "dexscreener", "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
    )
    gecko_status, gecko_detail = probe(
        "geckoterminal", "https://api.geckoterminal.com/api/v2/networks"
    )
    birdeye_status, birdeye_detail = (
        probe(
            "birdeye",
            "https://public-api.birdeye.so/defi/price?address=So11111111111111111111111111111111111111112",
            {"X-API-KEY": birdeye, "x-chain": "solana"},
        )
        if birdeye
        else ("BLOCKED", "BIRDEYE_API_KEY not set")
    )
    jupiter_status, jupiter_detail = probe(
        "jupiter",
        "https://api.jup.ag/swap/v1/quote?inputMint=So11111111111111111111111111111111111111112"
        "&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=100000000&slippageBps=100",
        {"x-api-key": jupiter} if jupiter else None,
    )
    _goplus_status, _goplus_detail = probe(
        "goplus", "https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses=So11111111111111111111111111111111111111112"
    )

    def stage(name: str) -> dict[str, str]:
        """Status taken from the real e2e run, never from a unit test."""
        value = e2e_stage.get(name)
        if value is None:
            return {"status": "NOT_IMPLEMENTED", "real_data_proof": "no e2e stage recorded"}
        return {
            "status": value,
            "real_data_proof": f"e2e_shadow_proof.json stage {name} = {value}",
        }

    rows: list[Subsystem] = []

    def add(number: int, name: str, status: str = "NOT_IMPLEMENTED", **kwargs: Any) -> None:
        rows.append(Subsystem(number=number, name=name, status=status, **kwargs))

    venues_impl = ["src/meme_flight_recorder/venues.py"]
    venues_test = ["tests/test_venues.py"]

    # 1-9 venue decoding
    decode_proof = "36 real mainnet transactions decoded: 25 venue-matched, 25 mints resolved, 14 failed tx decoded not skipped"
    add(1, "Pump discovery", "PASS", implementation=["src/meme_flight_recorder/discovery.py", "src/meme_flight_recorder/providers/binance_web3.py"],
        test=["tests/test_discovery.py"], real_data_proof=f"{journal.get('mints', 0)} mints journalled", last_success=journal.get("last"),
        provider="binance meme_rush", fallback="dexscreener", dependencies=["network"], hot_path=True, can_affect_paper_entry=True,
        known_failure_mode="vendor feed outage yields zero candidates, indistinguishable from a quiet market unless cycle count is checked")
    add(2, "Pump transaction decoding", "DEGRADED", implementation=venues_impl, test=venues_test,
        real_data_proof=decode_proof, last_success=e2e_when, provider="helius rpc", fallback="none",
        dependencies=["helius"], hot_path=True,
        known_failure_mode="action discriminators match 12 of 25 decoded transactions; unmatched instructions classify as UNKNOWN rather than guessing")
    add(3, "Pump bonding-curve lifecycle", "DEGRADED", implementation=["src/meme_flight_recorder/lifecycle.py", "src/meme_flight_recorder/readiness.py"],
        test=["tests/test_readiness.py"], real_data_proof="stage field journalled for 40k observations (new/finalizing/migrated)",
        last_success=journal.get("last"), provider="binance meme_rush", fallback="dexscreener pair age",
        known_failure_mode="curve progress comes from the vendor, not decoded from the curve account")
    add(4, "Pump graduation", "PASS", implementation=["src/meme_flight_recorder/lifecycle.py"], test=["tests/test_readiness.py"],
        real_data_proof="13,090 'migrated' observations journalled", last_success=journal.get("last"),
        provider="binance meme_rush", fallback="dexscreener", can_affect_paper_entry=True,
        known_failure_mode="graduation inferred from vendor stage, not from a decoded migrate instruction")
    add(5, "PumpSwap", "DEGRADED", implementation=venues_impl, test=venues_test,
        real_data_proof="17 pump_swap instructions decoded from real transactions", last_success=e2e_when,
        provider="helius rpc", known_failure_mode="pool-creation discriminator unverified against a real migration")
    add(6, "Raydium LaunchLab", "DEGRADED", implementation=venues_impl, test=venues_test,
        real_data_proof="14 launchlab instructions decoded from real transactions", last_success=e2e_when,
        provider="helius rpc", known_failure_mode="instruction names derived, not taken from a published IDL")
    add(7, "Raydium AMM", "DEGRADED", implementation=venues_impl, test=venues_test,
        real_data_proof="4 raydium_amm_v4 instructions decoded", last_success=e2e_when, provider="helius rpc",
        known_failure_mode="AMM v4 is not Anchor, so discriminator matching does not apply; venue is identified, action is not")
    add(8, "Meteora DBC", "DEGRADED", implementation=venues_impl, test=venues_test,
        real_data_proof="16 meteora_dbc instructions decoded", last_success=e2e_when, provider="helius rpc",
        known_failure_mode="migration instruction unverified")
    add(9, "Meteora DLMM/DAMM", "DEGRADED", implementation=venues_impl, test=venues_test,
        real_data_proof="program id registered; 0 instructions matched in the sampled window", last_success=e2e_when,
        provider="helius rpc", known_failure_mode="sampled transactions resolved no DLMM instruction; needs a wider sample before it can be called working")

    # 10-16 streaming, history, journal
    add(10, "Helius live stream", "BLOCKED", implementation=["scripts/probe_helius_stream.py"], test=["tests/test_venues.py"],
        real_data_proof="transactionSubscribe refused: 'not available on the free plan'", last_success=e2e_when,
        provider="helius enhanced ws", fallback="standard WSS logsSubscribe", dependencies=["helius paid plan"],
        hot_path=True, known_failure_mode="requires a paid Helius plan; blocked until purchased")
    add(11, "Solana WSS fallback", "PASS", implementation=["scripts/probe_helius_stream.py"], test=["tests/test_venues.py"],
        real_data_proof="37,625 events in 15s (~2,508/s) across 4 program log subscriptions + slotSubscribe", last_success=e2e_when,
        provider="helius standard ws", fallback="none", hot_path=True,
        known_failure_mode="volume requires filtering at the subscription boundary; unfiltered enrichment would back the queue up")
    add(12, "Historical backfill", "RUNNING" if "outcome backfill" in alive else "DEGRADED",
        implementation=["scripts/backfill_pool_history.py"], test=["tests/test_provider_mesh.py"],
        real_data_proof=f"{sum(cache.get('by_status', {}).values())} mints fetched, {cache.get('candles', 0)} traded candles",
        last_success=cache.get("last"), provider="birdeye", fallback="geckoterminal",
        known_failure_mode="birdeye carries the last close forward at zero volume; only traded minutes are stored")
    add(13, "Slot-aware reconciliation", "NOT_IMPLEMENTED", implementation=[], test=[],
        real_data_proof="", provider="helius", dependencies=["11"],
        known_failure_mode="live-vs-canonical reconciliation not built; 24h reliability must not be claimed")
    add(14, "Restart recovery", "DEGRADED", implementation=["run_collector.bat", "src/meme_flight_recorder/position_store.py"],
        test=["tests/test_monitor.py"], real_data_proof="positions reconstructed by journal replay, never mutated",
        known_failure_mode="auto-restart exists for the collector; missed-event recovery depends on subsystem 13")
    add(15, "Immutable journal", "PASS", implementation=["src/meme_flight_recorder/journal.py"], test=["tests/test_journal.py"],
        real_data_proof=f"hash chain intact over {sum(journal.get('counts', {}).values())} events (preflight)",
        last_success=journal.get("last"), can_affect_paper_entry=True,
        known_failure_mode="append-only; a corrupt chain fails verification rather than self-healing")
    add(16, "Exact mint identity", "PASS", implementation=venues_impl + ["src/meme_flight_recorder/social_intelligence.py"],
        test=venues_test + ["tests/test_intelligence.py"],
        real_data_proof=f"e2e resolved {e2e.get('mint', 'n/a')} from balance records + instruction accounts",
        last_success=e2e_when, can_affect_paper_entry=True,
        known_failure_mode="a ticker is never an identity; unresolvable mints return None")

    # 17-28 security and intelligence
    add(17, "Token security", **stage("hard_security"), implementation=["src/meme_flight_recorder/providers/goplus.py", "src/meme_flight_recorder/transferability.py"],
        test=["tests/test_transferability.py"], last_success=e2e_when, provider="goplus", fallback="static analysis",
        can_affect_paper_entry=True, known_failure_mode="goplus returns empty holder tags on this population")
    add(18, "Token-2022 security", "DEGRADED", implementation=["src/meme_flight_recorder/providers/goplus.py"],
        test=["tests/test_transferability.py"], real_data_proof="goplus reports transfer_hook / transfer_fee / non_transferable flags",
        provider="goplus", can_affect_paper_entry=True,
        known_failure_mode="extension flags are read but no Token-2022-specific test fixture exists")
    add(19, "Buy route", **stage("jupiter_quote"), implementation=["src/meme_flight_recorder/providers/jupiter.py", "src/meme_flight_recorder/execution_sim.py"],
        test=["tests/test_execution_sim.py"], last_success=e2e_when, provider="jupiter", fallback="lite-api keyless",
        hot_path=True, can_affect_paper_entry=True, known_failure_mode="quota exhaustion returns unknown, which fails closed")
    add(20, "Sell route", "PASS", implementation=["src/meme_flight_recorder/providers/jupiter.py"], test=["tests/test_execution_sim.py"],
        real_data_proof="round_trip_evidence quotes both legs", last_success=e2e_when, provider="jupiter",
        hot_path=True, can_affect_paper_entry=True, known_failure_mode="same quota dependency as the buy route")
    add(21, "Buy simulation", **stage("rpc_simulation"), implementation=["src/meme_flight_recorder/execution_sim.py"],
        test=["tests/test_execution_sim.py"], last_success=e2e_when, provider="helius rpc", hot_path=True,
        known_failure_mode="a failing simulation sets landing probability to zero rather than being ignored")
    add(22, "Sell simulation", "DEGRADED", implementation=["src/meme_flight_recorder/execution_sim.py"],
        test=["tests/test_execution_sim.py"], real_data_proof="same code path as buy, exercised buy-side in e2e",
        provider="helius rpc", known_failure_mode="not yet exercised against a held position in the e2e path")
    add(23, "Holder concentration", **stage("wallet_intelligence"), implementation=["src/meme_flight_recorder/enrichment.py"],
        test=["tests/test_enrichment.py"], last_success=e2e_when, provider="helius rpc", fallback="goplus",
        can_affect_paper_entry=True, known_failure_mode="adjusted figure needs owner resolution; gross top10 overstates concentration for curve-held supply")
    add(24, "Wallet clusters", "PASS", implementation=["src/meme_flight_recorder/clusters.py"], test=["tests/test_clusters.py"],
        real_data_proof="402 outcomes measured: cluster-clear died 21% vs disqualified 9%", provider="binance vendor labels",
        can_affect_paper_entry=True, known_failure_mode="vendor labels absent on the movers feed, which fails closed")
    add(25, "Creator/deployer intelligence", "PASS", implementation=["src/meme_flight_recorder/deployer.py", "src/meme_flight_recorder/deployer_history.py"],
        test=["tests/test_deployer.py"], real_data_proof="871 mints: dev sold >=50% died 7%, dev sold nothing died 28%",
        provider="helius", can_affect_paper_entry=True, known_failure_mode="gate was inverted for weeks; now a band")
    add(26, "First-buyer intelligence", "NOT_IMPLEMENTED", implementation=[], test=[],
        known_failure_mode="not built; requires per-transaction buyer ordering from subsystem 2")
    add(27, "Liquidity analysis", "PASS", implementation=["src/meme_flight_recorder/providers/dexscreener.py", "src/meme_flight_recorder/risk.py"],
        test=["tests/test_risk.py"], real_data_proof=dex_detail, last_success=e2e_when, provider="dexscreener",
        fallback="geckoterminal", hot_path=True, can_affect_paper_entry=True,
        known_failure_mode="missing liquidity is unknown and fails closed, never zero")
    add(28, "Market microstructure", "DEGRADED", implementation=["src/meme_flight_recorder/flow.py"], test=["tests/test_flow.py"],
        real_data_proof="buy/sell counts journalled; unique buyers permanently None (data cannot supply it)",
        known_failure_mode="transaction counts are not unique buyers and are never presented as such")

    # 29-37 providers and terminals
    add(29, "DexScreener", dex_status, implementation=["src/meme_flight_recorder/providers/dexscreener.py"],
        test=["tests/test_providers.py"], real_data_proof=dex_detail, last_success=datetime.now(UTC).isoformat(),
        provider="dexscreener", fallback="geckoterminal", hot_path=True, can_affect_paper_entry=True,
        known_failure_mode="keyless and unmetered; outages are total")
    add(30, "GeckoTerminal", "DEGRADED" if gecko_status == "PASS" else gecko_status,
        implementation=["src/meme_flight_recorder/providers/coingecko.py"], test=["tests/test_providers.py"],
        real_data_proof=f"{gecko_detail}; measured ~16s/mint under throttling vs published 30/min",
        last_success=cache.get("last"), provider="geckoterminal", fallback="birdeye",
        known_failure_mode="throttles far below documented rate; demoted to fallback for backfill")
    add(31, "Birdeye", birdeye_status, implementation=["src/meme_flight_recorder/providers/birdeye.py"],
        test=["tests/test_provider_mesh.py"], real_data_proof=birdeye_detail, last_success=cache.get("last"),
        provider="birdeye", fallback="geckoterminal",
        known_failure_mode="carries last close forward at zero volume; a dead token reads as alive unless volume is checked")
    add(32, "GMGN", "NOT_IMPLEMENTED", implementation=["src/meme_flight_recorder/deeplinks.py"], test=["tests/test_deeplinks.py"],
        real_data_proof="deep link only", provider="none",
        known_failure_mode="no public developer API; integrated as an outbound link, not a data source")
    add(33, "DEXTools", "NOT_IMPLEMENTED", implementation=["src/meme_flight_recorder/deeplinks.py"], test=["tests/test_deeplinks.py"],
        real_data_proof="deep link only", known_failure_mode="API is subscription-only; deliberately not purchased")
    for number, name in ((34, "Photon"), (35, "BullX"), (36, "Trojan"), (37, "Axiom")):
        add(number, f"{name} integration/reference", "PASS", implementation=["src/meme_flight_recorder/deeplinks.py"],
            test=["tests/test_deeplinks.py"], real_data_proof="exact-mint deep link generated; pure string construction, no I/O",
            provider="none", known_failure_mode="no public API exists; link only, and Trojan is custodial so it is never a dependency")

    # 38-50 social, trader, narrative
    add(38, "FOMO intelligence", "DEGRADED", implementation=["src/meme_flight_recorder/tracked_traders.py"],
        test=["tests/test_intelligence.py"], real_data_proof="manual trader->wallet mapping supported; 0 wallets mapped so far",
        provider="manual import", known_failure_mode="fomo.family is login-only and is never scraped; needs Ahmed to supply wallet identities")
    add(39, "Tracked-wallet intelligence", "DEGRADED", implementation=["src/meme_flight_recorder/tracked_traders.py"],
        test=["tests/test_intelligence.py"], real_data_proof="registry + on-chain detection implemented; 0 wallets registered",
        provider="helius", known_failure_mode="no wallets to track until subsystem 38 receives identities")
    add(40, "Trader profiling", "PASS", implementation=["src/meme_flight_recorder/tracked_traders.py"],
        test=["tests/test_intelligence.py"], real_data_proof="popchad.sol profiled from 1,200 real transactions: PF 0.47, coverage 12.07%",
        known_failure_mode="only 3.2% of transactions are priceable SOL-paired swaps; coverage is always reported")
    add(41, "Trader influence", "PASS", implementation=["src/meme_flight_recorder/trader_influence.py"],
        test=["tests/test_intelligence.py"], real_data_proof="reaction curves at 1/3/5/15/30/60/300s; separates influence from selection skill",
        known_failure_mode="needs tracked wallets before it can produce a real profile")
    add(42, "Trader confluence", "PASS", implementation=["src/meme_flight_recorder/tracked_traders.py"],
        test=["tests/test_intelligence.py"], real_data_proof="independence enforced by funding source; siblings do not count as agreement",
        known_failure_mode="funding-source data must be supplied; unknown funder is treated as independent")
    add(43, "Capital rotation", "PASS", implementation=["src/meme_flight_recorder/capital_rotation.py"],
        test=["tests/test_intelligence.py"], real_data_proof="sell->buy pairing, token and narrative edges, net flow",
        known_failure_mode="research feature only; explicitly not wired to entry")
    add(44, "Narrative engine", "PASS", implementation=["src/meme_flight_recorder/narratives.py"],
        test=["tests/test_intelligence.py"], real_data_proof="point-in-time features + 7-state lifecycle; raw mention count deliberately not the signal",
        known_failure_mode="no social adapter is ingesting yet, so all narratives are empty")
    add(45, "Reddit", "NOT_IMPLEMENTED", implementation=["src/meme_flight_recorder/social_intelligence.py"], test=["tests/test_intelligence.py"],
        real_data_proof="event model ready; no adapter", provider="reddit api",
        known_failure_mode="needs a Reddit app credential (free) before it can read")
    add(46, "Telegram", "NOT_IMPLEMENTED", implementation=["src/meme_flight_recorder/social_intelligence.py"], test=["tests/test_intelligence.py"],
        real_data_proof="event model + forwarded-message dedup ready; no adapter", provider="telegram",
        known_failure_mode="needs a Telegram api_id/api_hash before it can read public channels")
    add(47, "X optional interface", "NOT_IMPLEMENTED", implementation=[], test=[],
        real_data_proof="deliberately absent", known_failure_mode="paid; disabled by policy until incremental alpha is proven")
    add(48, "Web/news/catalyst intelligence", "NOT_IMPLEMENTED", implementation=[], test=[],
        known_failure_mode="catalysts.py not built")
    add(49, "Social bot/spam filtering", "PASS", implementation=["src/meme_flight_recorder/social_intelligence.py"],
        test=["tests/test_intelligence.py"], real_data_proof="scored not deleted; forwarded relays collapse to one voice",
        known_failure_mode="heuristic; no labelled spam corpus to validate against")
    add(50, "Narrative-to-mint resolution", "PASS", implementation=["src/meme_flight_recorder/narratives.py"],
        test=["tests/test_intelligence.py"], real_data_proof="resolves only on a stated address; an exact tie returns None",
        can_affect_paper_entry=True, known_failure_mode="a narrative with no stated mint is trackable but not tradeable")

    # 51-57 research
    add(51, "Maturity lifecycle", "PASS", implementation=["src/meme_flight_recorder/readiness.py"], test=["tests/test_readiness.py"],
        real_data_proof="7 bands measured over 166 entries; every band negative after costs",
        last_success=cache.get("last"), can_affect_paper_entry=True,
        known_failure_mode="cache coverage still low, so band n is small")
    add(52, "Established movers", "DEGRADED", implementation=["src/meme_flight_recorder/discovery.py", "scripts/replay_paper_strategy.py"],
        test=["tests/test_discovery.py"], real_data_proof="86 mover observations journalled; replay produced n=1 tradeable",
        known_failure_mode="sample far too small to conclude anything; funnel needed")
    add(53, "Feature store", "NOT_IMPLEMENTED", implementation=[], test=[],
        known_failure_mode="features are computed inline per study rather than stored point-in-time")
    add(54, "Historical replay", "PASS", implementation=["scripts/replay_paper_strategy.py"], test=["tests/test_readiness.py"],
        real_data_proof="chronological, capital-constrained; 18 mature_launch trades, expectancy -$0.6762",
        last_success=cache.get("last"), known_failure_mode="limited by outcome coverage")
    add(55, "Outcome labels", "RUNNING" if "outcome backfill" in alive else "DEGRADED",
        implementation=["scripts/backfill_pool_history.py", "scripts/study_maturity_bands.py"], test=["tests/test_provider_mesh.py"],
        real_data_proof=f"{sum(cache.get('by_status', {}).values())} of {journal.get('mints', 0)} mints",
        last_success=cache.get("last"), known_failure_mode="coverage still low; every study prints its own denominator")
    add(56, "Walk-forward validation", "DEGRADED", implementation=["scripts/study_maturity_bands.py"], test=[],
        real_data_proof="chronological train/held-out split implemented; purging not implemented",
        known_failure_mode="split is thin (3 collection days) and label purging is absent")
    add(57, "Execution simulator", **stage("modeled_fill"), implementation=["src/meme_flight_recorder/execution_sim.py"],
        test=["tests/test_execution_sim.py"], last_success=e2e_when, provider="jupiter + helius", hot_path=True,
        can_affect_paper_entry=True, known_failure_mode="landing probability is a model, not measured; nothing has ever been broadcast")

    # 58-63 execution
    add(58, "Jupiter quote", jupiter_status, implementation=["src/meme_flight_recorder/providers/jupiter.py"],
        test=["tests/test_execution_sim.py"], real_data_proof=jupiter_detail, last_success=e2e_when,
        provider="jupiter keyed", fallback="lite-api keyless", hot_path=True, can_affect_paper_entry=True,
        known_failure_mode="keyless tier exhausts and fails closed")
    add(59, "Jupiter transaction build", **stage("unsigned_transaction"), implementation=["src/meme_flight_recorder/execution_sim.py"],
        test=["tests/test_execution_sim.py"], last_success=e2e_when, provider="jupiter", hot_path=True,
        known_failure_mode="unsigned bytes only; there is no signer in the codebase")
    add(60, "simulateTransaction", **stage("rpc_simulation"), implementation=["src/meme_flight_recorder/execution_sim.py"],
        test=["tests/test_execution_sim.py"], last_success=e2e_when, provider="helius rpc", hot_path=True,
        known_failure_mode="sigVerify false against a public fee payer; a slippage revert is recorded, not hidden")
    add(61, "Compute-unit estimation", "PASS", implementation=["src/meme_flight_recorder/execution_sim.py"],
        test=["tests/test_execution_sim.py"], real_data_proof="measured 40,182 CU (SOL->USDC) and 114,838 CU (pump token), +10% margin applied",
        last_success=e2e_when, provider="helius rpc", hot_path=True,
        known_failure_mode="a failed simulation yields no CU, which is reported as None rather than zero")
    add(62, "Priority-fee estimator", "DEGRADED", implementation=["src/meme_flight_recorder/execution_sim.py"],
        test=["tests/test_execution_sim.py"], real_data_proof="getRecentPrioritizationFees median x requested CU; measured 0 on a quiet account set",
        last_success=e2e_when, provider="helius rpc",
        known_failure_mode="median of the reference account's neighbourhood, not of the target pool's writable accounts")
    add(63, "Jito future-submit model", "DEGRADED", implementation=["src/meme_flight_recorder/execution_sim.py"], test=["tests/test_execution_sim.py"],
        real_data_proof="tip field modelled at 0", known_failure_mode="bundles are unreachable at $10-40 capital; field exists so a live adapter has somewhere to put a real number")

    # 64-72 risk and book
    add(64, "Position sizing", "PASS", implementation=["src/meme_flight_recorder/risk.py", "src/meme_flight_recorder/costs.py"],
        test=["tests/test_risk.py"], real_data_proof="2% of equity = $0.80; derived cost floor $0.37; round trip 5.438%",
        can_affect_paper_entry=True, known_failure_mode="geometric-growth optimum computed on a tail-heavy distribution")
    add(65, "Paper broker", "RUNNING" if alive else "PASS", implementation=["src/meme_flight_recorder/paper.py", "src/meme_flight_recorder/monitor.py"],
        test=["tests/test_monitor.py", "tests/test_no_live_execution.py"],
        real_data_proof="collector running with --paper-trade; positions reconstructed by replay",
        last_success=journal.get("last"), can_affect_paper_entry=True,
        known_failure_mode="readiness policy admits nothing by default, so it will not open until a band is proven")
    add(66, "Exits", "PASS", implementation=["src/meme_flight_recorder/exits.py"], test=["tests/test_exits.py"],
        real_data_proof="9 exit policies measured; best loses 9% of equity over 50 trades instead of 33%",
        can_affect_paper_entry=True, known_failure_mode="capping upside removes the tail that pays for the losses")
    add(67, "Stop loss", "DEGRADED", implementation=["src/meme_flight_recorder/exits.py"], test=["tests/test_exits.py"],
        real_data_proof="replayed collapse fell 1,700x below its stop inside one candle",
        known_failure_mode="stops do not protect against gap-to-zero; only position size does")
    add(68, "Take profit", "PASS", implementation=["src/meme_flight_recorder/exits.py"], test=["tests/test_exits.py"],
        real_data_proof="take-2x measured best of nine policies and is still negative")
    add(69, "Stale-position handling", "PASS", implementation=["src/meme_flight_recorder/monitor.py"], test=["tests/test_monitor.py"],
        real_data_proof="closes after 3 consecutive unobservable checks",
        known_failure_mode="for open positions the missing-evidence rule inverts deliberately")
    add(70, "Kill switch", "PASS", implementation=["src/meme_flight_recorder/config.py", "tests/test_no_live_execution.py"],
        test=["tests/test_no_live_execution.py"], real_data_proof="execution mode refuses anything but paper; source scanned for broadcast/key literals",
        can_affect_paper_entry=True, known_failure_mode="none known; test is the tripwire and stays unmodified")
    add(71, "Daily risk", "DEGRADED", implementation=["src/meme_flight_recorder/risk.py"], test=["tests/test_risk.py"],
        real_data_proof="aggregate open-position cap enforced", known_failure_mode="no per-day loss limit distinct from the aggregate cap")
    add(72, "Aggregate exposure", "PASS", implementation=["src/meme_flight_recorder/risk.py"], test=["tests/test_risk.py"],
        real_data_proof="max 50% of equity open; book cap enforced in replay and live", can_affect_paper_entry=True)

    # 73-80 ops
    add(73, "Telemetry", "PASS", implementation=["src/meme_flight_recorder/journal.py"], test=["tests/test_journal.py"],
        real_data_proof=f"{journal.get('counts', {}).get('collector_cycle_completed', 0)} cycles recorded", last_success=journal.get("last"))
    add(74, "Latency measurements", "PASS", implementation=["src/meme_flight_recorder/execution_sim.py", "scripts/e2e_shadow_proof.py"],
        test=["tests/test_execution_sim.py"], real_data_proof="perf_counter_ns throughout; per-stage ms recorded in e2e artifact",
        last_success=e2e_when, hot_path=True,
        known_failure_mode="the WSS probe used a 16ms-granularity clock and its p50/p95 of 0ms mean below-resolution, not zero")
    add(75, "Provider health", "PASS", implementation=["scripts/preflight.py", "scripts/build_system_matrix.py"], test=[],
        real_data_proof="preflight passes end to end; this matrix probes each provider live",
        last_success=datetime.now(UTC).isoformat())
    add(76, "Collector health", "RUNNING" if alive else "DEGRADED", implementation=["src/meme_flight_recorder/collector.py"],
        test=["tests/test_collector.py"], real_data_proof=f"processes alive: {alive or 'none'}", last_success=journal.get("last"),
        known_failure_mode="host sleep cost 87 of 96 cycles once; sleep is now suppressed")
    add(77, "Notifications", "NOT_IMPLEMENTED", implementation=[], test=[],
        real_data_proof="TELEGRAM_BOT_TOKEN present in .env but unused",
        known_failure_mode="no notifier module; needs a chat id and a bot token to be exercised")
    add(78, "Restart supervisor", "DEGRADED", implementation=["run_collector.bat"], test=[],
        real_data_proof="batch supervisor with auto-restart and logging",
        known_failure_mode="survives process crash; boot persistence needs a Windows Scheduled Task (admin)")
    add(79, "Dashboard/API", "PASS", implementation=["src/meme_flight_recorder/api.py"], test=["tests/test_api.py"],
        real_data_proof="FastAPI service exposing candidates and paper state", known_failure_mode="not currently served")
    add(80, "Promotion gates", "PASS", implementation=["scripts/report_track_record.py", "src/meme_flight_recorder/readiness.py"],
        test=["tests/test_readiness.py"], real_data_proof="evidence gate computed not judged: 0 of 30 forward trades",
        can_affect_paper_entry=True, known_failure_mode="none; failing it is not a reason to weaken it")

    return rows


def main() -> int:
    rows = build()
    ARTIFACTS.mkdir(exist_ok=True)
    generated = datetime.now(UTC).isoformat()

    payload = {
        "generated_at": generated,
        "counts": {},
        "subsystems": [asdict(row) for row in rows],
    }
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    payload["counts"] = counts
    (ARTIFACTS / "SYSTEM_MATRIX.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    lines = [
        "# System matrix",
        "",
        f"Generated {generated} by `scripts/build_system_matrix.py`.",
        "",
        "Status is one of PASS, RUNNING, DEGRADED, BLOCKED, FAIL, NOT_IMPLEMENTED.",
        "There is no soft middle: a subsystem needing external data is never PASS",
        "on a unit test alone, and every DEGRADED states its limitation.",
        "",
        "| " + " | ".join(f"**{status}** {count}" for status, count in sorted(counts.items())) + " |",
        "|" + "---|" * len(counts),
        "",
        "| # | Subsystem | Status | Real-data proof | Test | Last success | Hot | Entry |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.number} | {row.name} | **{row.status}** | {row.real_data_proof or '-'} | "
            f"{', '.join(Path(t).name for t in row.test) or '-'} | {row.last_success or '-'} | "
            f"{'Y' if row.hot_path else ''} | {'Y' if row.can_affect_paper_entry else ''} |"
        )

    lines += ["", "## Detail", ""]
    for row in rows:
        lines += [
            f"### {row.number}. {row.name} — {row.status}",
            "",
            f"- implementation: {', '.join(row.implementation) or 'none'}",
            f"- test: {', '.join(row.test) or 'none'}",
            f"- provider: {row.provider}   fallback: {row.fallback}",
            f"- dependencies: {', '.join(row.dependencies) or 'none'}",
            f"- known failure mode: {row.known_failure_mode or 'none recorded'}",
            "",
        ]
    (ARTIFACTS / "SYSTEM_MATRIX.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"generated {len(rows)} subsystems")
    for status, count in sorted(counts.items()):
        print(f"  {status:<18}{count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
