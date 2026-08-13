#!/usr/bin/env python3
"""Test each first-buyer feature individually against forward outcomes.

Individually is the instruction and the design. No composite score: this
project's record is that combined scores hide which term did the work, and the
one time a score was imported wholesale it carried an unjustified 80% threshold.

Each feature is split at its own median into a low and a high arm, and both arms
are measured with the same after-cost machinery every other study here uses. A
feature is only interesting if the two arms **differ**, the difference survives
the chronological held-out split, and it survives deleting the best trade --
the test that killed the deep-pool filter and then enter_on_sight.

Features come from the first-10, first-25 and first-50 buyers separately,
because those are different populations and pooling them would let a mint with
three trades and a mint with fifty vote with equal weight.

Read-only.
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.first_buyers import FEATURE_NAMES, Buy, compute

CUTOFF = "2026-08-05"
HOLD_MINUTES = 240.0
COHORTS = (10, 25, 50)
RUG_BELOW = 0.10


@dataclass(frozen=True)
class Outcome:
    mint: str
    observed_at: str
    gross: float
    net_pnl: float
    liquidatable: bool


def load_outcomes(database: str, cache: str, position_usd: float, cost_multiple: float) -> dict[str, Outcome]:
    """Forward outcome per mint, entering at the first print and holding."""
    journal = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    first: dict[str, str] = {}
    for mint, observed_at in journal.execute(
        "select entity_id, min(observed_at) from events "
        "where event_type = 'candidate_observed' group by entity_id"
    ):
        first[mint] = observed_at
    journal.close()

    history = sqlite3.connect(f"file:{cache}?mode=ro", uri=True)
    keys = {
        mint: key
        for mint, key, status in history.execute(
            "select mint, coalesce(series_key, pair), status from fetches"
        )
        if status == "ok"
    }
    series: dict[str, list[tuple]] = defaultdict(list)
    for key, ts, open_, close in history.execute(
        "select pair, ts, open, close from candles order by pair, ts"
    ):
        series[key].append((ts, open_, close))
    history.close()

    outcomes: dict[str, Outcome] = {}
    for mint, key in keys.items():
        candles = series.get(key) or []
        observed_at = first.get(mint)
        if not candles or not observed_at:
            continue
        start = int(datetime.fromisoformat(observed_at).timestamp())
        entry_candle = next((c for c in candles if c[0] >= start), None)
        if entry_candle is None:
            continue
        entry = entry_candle[1] or entry_candle[2]
        if not entry or entry <= 0:
            continue
        exit_at = entry_candle[0] + int(HOLD_MINUTES * 60)
        marked = next(
            (c[2] or c[1] for c in candles if c[0] >= exit_at and (c[2] or c[1])), None
        )
        gross = (marked / entry) if marked else 0.0
        outcomes[mint] = Outcome(
            mint=mint,
            observed_at=observed_at,
            gross=gross,
            net_pnl=position_usd * (gross - cost_multiple) - position_usd,
            liquidatable=marked is not None,
        )
    return outcomes


def load_buys(path: str) -> dict[str, list[Buy]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    buys: dict[str, list[Buy]] = defaultdict(list)
    first_time: dict[str, int] = {}
    rows = list(
        connection.execute(
            "select mint, ordinal, wallet, slot, block_time from buys order by mint, ordinal"
        )
    )
    connection.close()
    for mint, _ordinal, _wallet, _slot, block_time in rows:
        if block_time and mint not in first_time:
            first_time[mint] = int(block_time)
    for mint, ordinal, wallet, slot, block_time in rows:
        base = first_time.get(mint)
        buys[mint].append(
            Buy(
                wallet=wallet,
                slot=int(slot),
                order=int(ordinal),
                seconds_since_first=(
                    float(int(block_time) - base) if block_time and base is not None else None
                ),
            )
        )
    return dict(buys)


def summarise(outcomes: list[Outcome], drop: int = 0) -> dict[str, Any]:
    if not outcomes:
        return {"n": 0}
    ranked = sorted(outcomes, key=lambda item: item.net_pnl, reverse=True)[drop:]
    if not ranked:
        return {"n": 0}
    pnls = [item.net_pnl for item in ranked]
    wins = [value for value in pnls if value > 0]
    losses = [-value for value in pnls if value < 0]
    return {
        "n": len(ranked),
        "expectancy": statistics.fmean(pnls),
        "profit_factor": (sum(wins) / sum(losses)) if losses else float("inf"),
        "dead_pct": 100.0 * sum(1 for o in ranked if not o.liquidatable) / len(ranked),
        "rug_pct": 100.0 * sum(1 for o in ranked if o.gross < RUG_BELOW) / len(ranked),
        "median": statistics.median(o.gross for o in ranked),
    }


def render(title: str, rows: list[tuple[str, dict[str, Any]]]) -> None:
    print(f"\n=== {title} ===")
    header = f"{'arm':<34}{'n':>5}{'expectancy':>12}{'PF':>8}{'dead%':>7}{'rug%':>7}{'median':>8}"
    print(header)
    print("-" * len(header))
    for label, stats in rows:
        if not stats.get("n"):
            print(f"{label:<34}{0:>5}   (no trades)")
            continue
        factor = stats["profit_factor"]
        text = "inf" if factor == float("inf") else f"{factor:.3f}"
        print(
            f"{label:<34}{stats['n']:>5}{stats['expectancy']:>12.4f}{text:>8}"
            f"{stats['dead_pct']:>7.1f}{stats['rug_pct']:>7.1f}{stats['median']:>8.3f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buys", default="data/first_buyers.db")
    parser.add_argument("--cache", default="data/pool_history.db")
    parser.add_argument("--min-arm", type=int, default=8, help="Smallest arm worth reporting.")
    arguments = parser.parse_args()

    settings = load_settings()
    position_usd = settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0
    cost = round_trip_cost(position_usd, settings.costs)
    print(
        f"position ${position_usd:.2f}   round trip {cost.pct_of_position:.3f}%   "
        f"hold {HOLD_MINUTES:.0f}m   split at {CUTOFF}"
    )

    outcomes = load_outcomes(
        str(settings.database_path), arguments.cache, position_usd, cost.pct_of_position / 100.0
    )
    buys = load_buys(arguments.buys)
    print(f"mints with a forward outcome: {len(outcomes)}")
    print(f"mints with first-buyer data:  {len(buys)}")

    joined = sorted(set(outcomes) & set(buys))
    print(f"mints with BOTH (the study population): {len(joined)}")
    if not joined:
        print("\nnothing to measure yet -- the extractor is still running")
        return 1

    for cohort in COHORTS:
        features: dict[str, dict[str, float | None]] = {}
        for mint in joined:
            record = compute(mint, buys[mint][:cohort])
            features[mint] = {
                name: getattr(record, name) for name in FEATURE_NAMES
            }

        print(f"\n{'#' * 78}\n# FIRST {cohort} BUYERS   population {len(joined)}\n{'#' * 78}")
        for name in FEATURE_NAMES:
            values = {
                mint: value
                for mint, value in ((m, features[m][name]) for m in joined)
                if value is not None
            }
            if len(values) < arguments.min_arm * 2:
                print(f"\n=== {name} === n={len(values)}  NOT MEASURABLE (insufficient coverage)")
                continue
            ordered = sorted(values.values())
            median = ordered[len(ordered) // 2]
            low = [outcomes[m] for m, v in values.items() if v <= median]
            high = [outcomes[m] for m, v in values.items() if v > median]
            if len(low) < arguments.min_arm or len(high) < arguments.min_arm:
                print(
                    f"\n=== {name} === n={len(values)} median={median} "
                    f"NOT SPLITTABLE (arms {len(low)}/{len(high)})"
                )
                continue

            def held(items: list[Outcome], out: bool) -> list[Outcome]:
                return [o for o in items if (o.observed_at[:10] >= CUTOFF) == out]

            render(
                f"{name}  (median split at {median})",
                [
                    (f"low <= {median}", summarise(low)),
                    (f"high > {median}", summarise(high)),
                    ("low, top-1 removed", summarise(low, 1)),
                    ("high, top-1 removed", summarise(high, 1)),
                    ("low, HELD OUT", summarise(held(low, True))),
                    ("high, HELD OUT", summarise(held(high, True))),
                ],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
