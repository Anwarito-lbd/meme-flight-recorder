#!/usr/bin/env python3
"""Does waiting until a token has survived to some age make it worth trading?

Every prior expectancy figure in this project compared the price at the first
observation with the price *today*. Two things are wrong with that. It measures
"what happened eventually" rather than "what could have been traded", and it
silently drops the 84% of mints that no longer quote anywhere -- a survivorship
denominator that flatters every number computed on it.

This study replaces both. It reads the cached minute candles, which exist for
dead pools too, and it simulates a decision that could actually have been taken:

    observe at t0, do nothing, and enter at t0 + B only if the pool is still
    trading then -- using no information from after t0 + B.

Decision rule, fixed before any number was seen:

  * A band is a candidate only if it is positive after costs on the development
    split, positive on the held-out split, **and** still positive after deleting
    its single best trade. That last test is what killed the deep-pool filter at
    n=166, and it is the one that matters in a lottery-shaped distribution.
  * Costs are charged on both legs from `costs.round_trip_cost`, never a flat
    percentage.
  * Every mint lands in exactly one bucket per band, and the buckets reconcile
    to the population. A band that cannot be measured reports why.
  * A pool that stopped trading before the exit is a **total loss**, not a
    mark-to-last-print. You cannot sell into a pool with no bids, and scoring it
    at its final close is how a backtest invents money it could never have had.

Read-only. Requires `scripts/backfill_pool_history.py` to have run.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost

# Entry bands, in minutes after the first observation. 0 is "enter on sight",
# which is what the paper book would have done, and is the control arm.
BANDS: tuple[tuple[str, float], ...] = (
    ("enter on sight", 0.0),
    ("survive 5m", 5.0),
    ("survive 15m", 15.0),
    ("survive 30m", 30.0),
    ("survive 1h", 60.0),
    ("survive 2h", 120.0),
    ("survive 6h", 360.0),
)

# How long a position is held after entry.
HOLD_MINUTES = 240.0

# A price is only usable if a trade printed within this long before the decision
# moment. Beyond it the last close is a stale quote rather than a price, and
# entering on it would be inventing a fill.
MAX_PRICE_STALENESS_MINUTES = 10.0

# Below this multiple of the entry the trade is a rug in the sense that matters.
RUG_BELOW = 0.10


@dataclass(frozen=True)
class Trade:
    mint: str
    band: str
    decided_at: int
    token_age_minutes: float | None
    entry_price: float
    exit_price: float
    gross_multiple: float
    net_multiple: float
    net_pnl_usd: float
    mae: float
    mfe: float
    liquidatable: bool


def first_observations(database_path: str) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    first: dict[str, dict[str, Any]] = {}
    query = (
        "select entity_id, observed_at, payload_json from events "
        "where event_type = 'candidate_observed' order by id"
    )
    for mint, observed_at, payload in connection.execute(query):
        if mint in first:
            continue
        record = json.loads(payload)
        record["observed_at"] = observed_at
        first[mint] = record
    connection.close()
    return first


def load_cache(cache_path: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[tuple]]]:
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    fetches: dict[str, dict[str, Any]] = {}
    for mint, key, source, status, count, last_ts in connection.execute(
        "select mint, coalesce(series_key, pair), coalesce(source, 'geckoterminal'), "
        "status, candle_count, last_candle_ts from fetches"
    ):
        # The series key is the pool address for the pool-keyed provider and the
        # mint for the mint-keyed one. Both index the same candles table, and
        # the source is carried so a disagreement between them is attributable.
        fetches[mint] = {
            "pair": key,
            "source": source,
            "status": status,
            "count": count,
            "last_ts": last_ts,
        }
    series: dict[str, list[tuple]] = defaultdict(list)
    for pair, ts, open_, high, low, close in connection.execute(
        "select pair, ts, open, high, low, close from candles order by pair, ts"
    ):
        series[pair].append((ts, open_, high, low, close))
    connection.close()
    return fetches, dict(series)


def fill_at(candles: list[tuple], moment: int, patience_seconds: float) -> float | None:
    """The price a buyer decided at `moment` would actually have got.

    An order placed at `moment` fills at the *next* print, not the last one, so
    this looks forward to the first candle at or after the decision and takes
    its open. Using the preceding close instead would be a mark rather than a
    fill, and at band 0 -- where the pool has often not traded yet -- it reports
    no entry at all, which is how "enter on sight" measured as unmeasurable.

    `patience_seconds` bounds the wait. A pool whose next trade is an hour away
    is not a pool an order fills in; returning None there keeps the study from
    inventing a fill that never existed.
    """
    for ts, open_, _high, _low, close in candles:
        if ts < moment:
            continue
        price = open_ if open_ and open_ > 0 else close
        if not price or price <= 0:
            continue
        if ts - moment > patience_seconds:
            return None
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


def extremes(candles: list[tuple], start: int, end: int) -> tuple[float | None, float | None]:
    lows = [c[3] for c in candles if start <= c[0] <= end and c[3] and c[3] > 0]
    highs = [c[2] for c in candles if start <= c[0] <= end and c[2] and c[2] > 0]
    return (min(lows) if lows else None, max(highs) if highs else None)


def summarise(trades: list[Trade], position_usd: float) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    nets = [trade.net_multiple for trade in trades]
    pnls = [trade.net_pnl_usd for trade in trades]
    wins = [value for value in pnls if value > 0]
    losses = [-value for value in pnls if value < 0]
    profit_factor = (sum(wins) / sum(losses)) if losses else float("inf")
    ranked = sorted(pnls, reverse=True)
    without_best = ranked[1:]
    return {
        "n": len(trades),
        "dead_pct": 100.0 * sum(1 for t in trades if not t.liquidatable) / len(trades),
        "rug_pct": 100.0 * sum(1 for t in trades if t.net_multiple < RUG_BELOW) / len(trades),
        "median": statistics.median(nets),
        "mean": statistics.fmean(nets),
        "win_pct": 100.0 * len(wins) / len(trades),
        "two_x_pct": 100.0 * sum(1 for value in nets if value >= 2.0) / len(trades),
        "five_x_pct": 100.0 * sum(1 for value in nets if value >= 5.0) / len(trades),
        "expectancy_usd": statistics.fmean(pnls),
        "profit_factor": profit_factor,
        "expectancy_drop_best": statistics.fmean(without_best) if without_best else None,
        "mae": statistics.median([t.mae for t in trades]),
        "mfe": statistics.median([t.mfe for t in trades]),
        "position_usd": position_usd,
    }


def print_table(title: str, rows: list[tuple[str, dict[str, Any]]]) -> None:
    print(f"\n=== {title} ===")
    header = (
        f"{'population':<18}{'n':>5}{'net exp $':>11}{'PF':>7}"
        f"{'dead%':>7}{'rug%':>7}{'win%':>7}{'2x%':>6}{'5x%':>6}"
        f"{'median':>8}{'mean':>8}{'-top1 $':>10}"
    )
    print(header)
    print("-" * len(header))
    for label, stats in rows:
        if not stats.get("n"):
            print(f"{label:<18}{0:>5}   (no measurable trades)")
            continue
        factor = stats["profit_factor"]
        factor_text = "inf" if factor == float("inf") else f"{factor:.3f}"
        raw_drop = stats["expectancy_drop_best"]
        drop_best = "n/a" if raw_drop is None else f"{raw_drop:.4f}"
        print(
            f"{label:<18}{stats['n']:>5}{stats['expectancy_usd']:>11.4f}{factor_text:>7}"
            f"{stats['dead_pct']:>7.1f}{stats['rug_pct']:>7.1f}{stats['win_pct']:>7.1f}"
            f"{stats['two_x_pct']:>6.1f}{stats['five_x_pct']:>6.1f}"
            f"{stats['median']:>8.3f}{stats['mean']:>8.3f}{drop_best:>10}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/pool_history.db")
    parser.add_argument("--hold-minutes", type=float, default=HOLD_MINUTES)
    parser.add_argument(
        "--safety",
        choices=("pass", "all", "both"),
        default="both",
        help="Which safety cohort to report.",
    )
    arguments = parser.parse_args()

    settings = load_settings()
    position_usd = settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0
    cost = round_trip_cost(position_usd, settings.costs)
    cost_multiple = cost.pct_of_position / 100.0
    print(
        f"position ${position_usd:.2f}   round trip {cost.pct_of_position:.3f}%   "
        f"breakeven {cost.breakeven_multiple:.4f}x   hold {arguments.hold_minutes:.0f}m"
    )

    first = first_observations(str(settings.database_path))
    fetches, series = load_cache(arguments.cache)
    print(f"journalled mints: {len(first)}   cached fetches: {len(fetches)}")

    status_counts: dict[str, int] = defaultdict(int)
    for mint in first:
        record = fetches.get(mint)
        status_counts[record["status"] if record else "not_fetched"] += 1
    print("cache coverage over the journalled population:")
    for name, count in sorted(status_counts.items(), key=lambda item: -item[1]):
        print(f"  {name:<22}{count:>7}  {100.0 * count / len(first):>5.1f}%")

    trades: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    buckets: dict[str, dict[str, int]] = {label: defaultdict(int) for label, _ in BANDS}

    for mint, record in first.items():
        fetch = fetches.get(mint)
        cohorts = ["all"] + ([] if record.get("failures") else ["pass"])
        observed_at = int(datetime.fromisoformat(record["observed_at"]).timestamp())
        age_at_observation = record.get("age_minutes")
        candles = series.get(fetch["pair"], []) if fetch else []

        for label, offset in BANDS:
            bucket = buckets[label]
            if fetch is None:
                bucket["not_fetched"] += 1
                continue
            if fetch["status"] != "ok" or not candles:
                bucket[fetch["status"] if fetch["status"] != "ok" else "no_candles"] += 1
                continue

            decided_at = observed_at + int(offset * 60)
            # Survival is the whole question: the pool must still have been
            # trading at the decision moment, judged only on candles up to it.
            entry = fill_at(candles, decided_at, MAX_PRICE_STALENESS_MINUTES * 60)
            if entry is None:
                bucket["did_not_survive_to_band"] += 1
                continue

            exit_at = decided_at + int(arguments.hold_minutes * 60)
            marked = exit_fill_at(candles, exit_at)
            if marked is None:
                # The pool never printed again. There was no buyer at any
                # price, so the position is a total loss rather than a missing
                # measurement -- and counting it as missing is exactly how the
                # 84% of vanished mints disappeared from every earlier study.
                liquidatable = False
                exit_price = 0.0
                gross = 0.0
            else:
                liquidatable = True
                exit_price = marked
                gross = marked / entry

            low, high = extremes(candles, decided_at, exit_at)
            net_multiple = gross - cost_multiple
            trade = Trade(
                mint=mint,
                band=label,
                decided_at=decided_at,
                token_age_minutes=(
                    float(age_at_observation) + offset
                    if isinstance(age_at_observation, int | float)
                    else None
                ),
                entry_price=entry,
                exit_price=exit_price,
                gross_multiple=gross,
                net_multiple=net_multiple,
                net_pnl_usd=position_usd * net_multiple - position_usd,
                mae=(low / entry) if low else 0.0,
                mfe=(high / entry) if high else gross,
                liquidatable=liquidatable,
            )
            bucket["entered"] += 1
            for cohort in cohorts:
                trades[(label, cohort)].append(trade)

    print("\n=== reconciliation, per band (every mint in exactly one bucket) ===")
    for label, _offset in BANDS:
        bucket = buckets[label]
        total = sum(bucket.values())
        if total != len(first):
            raise AssertionError(f"{label}: buckets sum to {total}, population is {len(first)}")
        parts = "  ".join(f"{name}={count}" for name, count in sorted(bucket.items()))
        print(f"  {label:<16} total={total}  {parts}")

    wanted = ("pass", "all") if arguments.safety == "both" else (arguments.safety,)
    for cohort in wanted:
        rows = [(label, summarise(trades[(label, cohort)], position_usd)) for label, _ in BANDS]
        print_table(f"safety {cohort.upper()} cohort, hold {arguments.hold_minutes:.0f}m", rows)

    # Chronological split. Optimising on everything and calling it validated is
    # the failure this project has already recorded twice.
    print("\n=== chronological split ===")
    cut = "2026-08-05"
    for cohort in wanted:
        development = [
            (
                label,
                summarise(
                    [
                        trade
                        for trade in trades[(label, cohort)]
                        if first[trade.mint]["observed_at"][:10] < cut
                    ],
                    position_usd,
                ),
            )
            for label, _ in BANDS
        ]
        held_out = [
            (
                label,
                summarise(
                    [
                        trade
                        for trade in trades[(label, cohort)]
                        if first[trade.mint]["observed_at"][:10] >= cut
                    ],
                    position_usd,
                ),
            )
            for label, _ in BANDS
        ]
        print_table(f"{cohort.upper()} development (before {cut})", development)
        print_table(f"{cohort.upper()} held out (from {cut})", held_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
