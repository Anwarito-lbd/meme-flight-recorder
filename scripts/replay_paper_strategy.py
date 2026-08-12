#!/usr/bin/env python3
"""Replay the paper strategy chronologically, with no information from ahead.

`study_maturity_bands.py` measures cohorts: every candidate that qualifies is
counted, as if capital were unlimited. A book is not like that. It holds a fixed
number of positions, it can be full when the good candidate arrives, and the
order events arrive in decides which trades exist at all. Those constraints can
turn a positive cohort into a negative book, so they are simulated separately
rather than assumed away.

The rules that make this a replay rather than a backtest:

  * Candidates are walked in journal order, which is the order they were
    actually observed.
  * A decision at time T uses the candidate row as journalled at T and candles
    up to T. Nothing later is readable -- the survivorship bug already caught in
    this project came from entering "a token trending today, ten days ago".
  * Entry fills at the next print after the decision, exits mark to the last
    print before the exit, and a pool that has gone quiet cannot be sold at all.
  * Costs come from `costs.round_trip_cost`, charged on both legs.

Every simulated trade is written out with the fields needed to audit it: mint,
decision time, age, lifecycle, safety verdict, entry reason, entry price,
executable price, slippage, fees, exit, MAE, MFE and PnL.

Read-only with respect to the journal. Writes a CSV of simulated trades.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.readiness import (
    LifecycleState,
    ReadinessPolicy,
    StrategyReadiness,
    assess_candidate,
)

# Strategies differ only in which maturity states they will enter and how long
# they wait. Safety is identical across all three and is never relaxed.
STRATEGIES: dict[str, dict[str, Any]] = {
    "launch_watch": {
        # Item 9: observe fresh graduations, never enter them. Included so the
        # replay can show the control arm rather than assert it.
        "entry_states": frozenset(),
        "wait_minutes": 0.0,
        "minimum_liquidity_usd": 0.0,
    },
    "mature_launch": {
        "entry_states": frozenset({LifecycleState.SURVIVING, LifecycleState.MATURE}),
        "wait_minutes": 60.0,
        "minimum_liquidity_usd": 5_000.0,
    },
    "movers": {
        "entry_states": frozenset({LifecycleState.MATURE, LifecycleState.ESTABLISHED}),
        "wait_minutes": 360.0,
        "minimum_liquidity_usd": 50_000.0,
    },
}

MAX_FILL_PATIENCE_SECONDS = 600.0


@dataclass
class SimulatedTrade:
    mint: str
    symbol: str
    strategy: str
    decided_at: str
    token_age_minutes: float | None
    lifecycle: str
    safety_verdict: str
    entry_reason: str
    quoted_price: float
    executable_price: float
    slippage_pct: float
    fees_usd: float
    position_usd: float
    exit_at: str
    exit_price: float
    exit_reason: str
    mae: float
    mfe: float
    gross_multiple: float
    net_pnl_usd: float


def load_candidates(database_path: str) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    query = (
        "select entity_id, observed_at, payload_json from events "
        "where event_type = 'candidate_observed' order by id"
    )
    for mint, observed_at, payload in connection.execute(query):
        if mint in seen:
            continue
        seen.add(mint)
        record = json.loads(payload)
        record["mint"] = mint
        record["observed_at"] = observed_at
        rows.append(record)
    connection.close()
    return rows


def load_cache(cache_path: str) -> tuple[dict[str, str], dict[str, list[tuple]]]:
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    pairs = {
        mint: pair
        for mint, pair, status in connection.execute(
            "select mint, coalesce(series_key, pair), status from fetches"
        )
        if status == "ok"
    }
    series: dict[str, list[tuple]] = {}
    for pair, ts, open_, high, low, close in connection.execute(
        "select pair, ts, open, high, low, close from candles order by pair, ts"
    ):
        series.setdefault(pair, []).append((ts, open_, high, low, close))
    connection.close()
    return pairs, series


def fill_at(candles: list[tuple], moment: int) -> float | None:
    for ts, open_, _high, _low, close in candles:
        if ts < moment:
            continue
        if ts - moment > MAX_FILL_PATIENCE_SECONDS:
            return None
        price = open_ if open_ and open_ > 0 else close
        if price and price > 0:
            return price
    return None


def exit_fill_at(candles: list[tuple], moment: int) -> float | None:
    """The price a seller at `moment` would actually have got, or None if none.

    The first version of this required a print within 30 minutes *before* the
    exit and scored everything else a total loss. That is the "absent is not
    zero" error this project has already paid for three times, committed here in
    a new place: a pool with no trades for half an hour is quiet, not empty, and
    one of the seven trades it condemned went on trading for another 34 hours.

    The rule that survives that check is symmetric with the entry fill. A sale
    at `moment` executes against the next print at or after it, whenever that
    arrives. Only a pool that never prints again is genuinely unsellable, and
    that is a real total loss rather than a missing measurement.
    """
    for ts, open_, _high, _low, close in candles:
        if ts < moment:
            continue
        price = open_ if open_ and open_ > 0 else close
        if price and price > 0:
            return price
    return None


def replay(
    strategy: str,
    candidates: list[dict[str, Any]],
    pairs: dict[str, str],
    series: dict[str, list[tuple]],
    settings: Any,
    hold_minutes: float,
    maximum_open: int,
) -> tuple[list[SimulatedTrade], dict[str, int]]:
    rules = STRATEGIES[strategy]
    policy = ReadinessPolicy(
        entry_states=rules["entry_states"],
        minimum_entry_age_minutes=0.0,
    )
    position_usd = settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0
    cost = round_trip_cost(position_usd, settings.costs)

    trades: list[SimulatedTrade] = []
    skipped: dict[str, int] = {}
    # (closes_at, mint) for positions currently held, so the book cap binds the
    # same way it would live.
    open_until: list[tuple[int, str]] = []

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for record in candidates:
        observed_at = int(datetime.fromisoformat(record["observed_at"]).timestamp())
        decided_at = observed_at + int(rules["wait_minutes"] * 60)

        open_until = [entry for entry in open_until if entry[0] > decided_at]
        if len(open_until) >= maximum_open:
            skip("book_full")
            continue

        pair = pairs.get(record["mint"])
        if pair is None:
            skip("no_cached_history")
            continue
        candles = series.get(pair) or []
        if not candles:
            skip("no_candles")
            continue

        # The maturity state is judged at the decision moment, and survival is
        # part of it: a pool with no print near the decision is not alive.
        alive = exit_fill_at(candles, decided_at) is not None
        age = record.get("age_minutes")
        aged = (
            float(age) + rules["wait_minutes"] if isinstance(age, int | float) else None
        )
        verdict = assess_candidate(
            {**record, "age_minutes": aged}, policy, still_trading=alive
        )
        if verdict.readiness is not StrategyReadiness.ENTRY:
            skip(f"{verdict.readiness.value}:{verdict.reason}")
            continue

        liquidity = record.get("liquidity_usd")
        if liquidity is None:
            # Fail closed. Unknown depth is not shallow and is not deep; it is
            # unmeasured, and a position cannot be sized against it.
            skip("liquidity_unknown")
            continue
        if float(liquidity) < rules["minimum_liquidity_usd"]:
            skip("pool_too_shallow")
            continue

        quoted = record.get("price_usd")
        executable = fill_at(candles, decided_at)
        if not quoted or executable is None:
            skip("no_fill_available")
            continue

        exit_at = decided_at + int(hold_minutes * 60)
        marked = exit_fill_at(candles, exit_at)
        if marked is None:
            exit_price, gross, exit_reason = 0.0, 0.0, "pool_went_quiet_unsellable"
        else:
            exit_price, gross, exit_reason = marked, marked / executable, "time_limit"

        window = [c for c in candles if decided_at <= c[0] <= exit_at]
        lows = [c[3] for c in window if c[3] and c[3] > 0]
        highs = [c[2] for c in window if c[2] and c[2] > 0]

        trades.append(
            SimulatedTrade(
                mint=record["mint"],
                symbol=str(record.get("symbol") or ""),
                strategy=strategy,
                decided_at=datetime.fromtimestamp(decided_at, tz=UTC).isoformat(),
                token_age_minutes=aged,
                lifecycle=verdict.lifecycle.value,
                safety_verdict=verdict.verdict.value,
                entry_reason=verdict.reason,
                quoted_price=float(quoted),
                executable_price=executable,
                slippage_pct=100.0 * (executable - float(quoted)) / float(quoted),
                fees_usd=cost.total_usd,
                position_usd=position_usd,
                exit_at=datetime.fromtimestamp(exit_at, tz=UTC).isoformat(),
                exit_price=exit_price,
                exit_reason=exit_reason,
                mae=(min(lows) / executable) if lows else 0.0,
                mfe=(max(highs) / executable) if highs else gross,
                gross_multiple=gross,
                net_pnl_usd=position_usd * gross - position_usd - cost.total_usd,
            )
        )
        open_until.append((exit_at, record["mint"]))

    return trades, skipped


def report(strategy: str, trades: list[SimulatedTrade], skipped: dict[str, int]) -> None:
    print(f"\n=== {strategy} ===")
    considered = len(trades) + sum(skipped.values())
    print(f"  considered {considered}   traded {len(trades)}")
    for reason, count in sorted(skipped.items(), key=lambda item: -item[1])[:8]:
        print(f"    skipped {reason:<44}{count}")
    if not trades:
        print("  no trades -- nothing to score")
        return
    pnls = [trade.net_pnl_usd for trade in trades]
    wins = [value for value in pnls if value > 0]
    losses = [-value for value in pnls if value < 0]
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for value in pnls:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    factor = (sum(wins) / sum(losses)) if losses else float("inf")
    print(f"  net expectancy   ${statistics.fmean(pnls):.4f} per trade")
    print(f"  profit factor    {factor:.3f}")
    print(f"  win rate         {100.0 * len(wins) / len(trades):.1f}%")
    print(f"  total PnL        ${sum(pnls):.2f}")
    print(f"  max drawdown     ${drawdown:.2f}")
    print(f"  median multiple  {statistics.median(t.gross_multiple for t in trades):.4f}")
    unsellable = sum(1 for t in trades if t.exit_reason == "never_printed_again_unsellable")
    print(f"  unsellable exits {unsellable} ({100.0 * unsellable / len(trades):.1f}%)")
    if len(trades) > 1:
        without_best = sorted(pnls, reverse=True)[1:]
        print(f"  expectancy -top1 ${statistics.fmean(without_best):.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/pool_history.db")
    parser.add_argument("--hold-minutes", type=float, default=240.0)
    parser.add_argument("--maximum-open", type=int, default=25)
    parser.add_argument("--out", default="data/replay_trades.csv")
    parser.add_argument("--strategy", choices=[*STRATEGIES, "all"], default="all")
    arguments = parser.parse_args()

    settings = load_settings()
    candidates = load_candidates(str(settings.database_path))
    pairs, series = load_cache(arguments.cache)
    print(
        f"candidates {len(candidates)}   cached pools {len(pairs)}   "
        f"hold {arguments.hold_minutes:.0f}m   book cap {arguments.maximum_open}"
    )

    wanted = list(STRATEGIES) if arguments.strategy == "all" else [arguments.strategy]
    everything: list[SimulatedTrade] = []
    for strategy in wanted:
        trades, skipped = replay(
            strategy,
            candidates,
            pairs,
            series,
            settings,
            arguments.hold_minutes,
            arguments.maximum_open,
        )
        report(strategy, trades, skipped)
        everything.extend(trades)

    if everything:
        with open(arguments.out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(everything[0])))
            writer.writeheader()
            for trade in everything:
                writer.writerow(asdict(trade))
        print(f"\nwrote {len(everything)} simulated trades to {arguments.out}")
    else:
        print("\nno simulated trades to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
