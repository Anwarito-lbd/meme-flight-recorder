#!/usr/bin/env python3
"""Is the entry rule the problem? Structure versus chart signal.

Four exit policies were tested on identical entries and every one lost money.
That eliminated exits as the cause and left the entry, which has never been
varied. This varies it.

The current entry is breakout-and-retest on 15-minute candles: a
trend-continuation rule. The measured distribution is not a trend. Median 0.255,
mean 2.48, with the expectancy carried by a ~6% tail. Applying a continuation
rule to a lottery is a category error, and no exit policy could have rescued it.

Two filters already measured on journalled data separate outcomes more sharply
than the chart rule does:

    pool >= $50,000          63% end above 1x, versus 34% at the $5,000 floor
    developer supply spent    7% dead, versus 28% where the developer still holds

Both are *structural* -- properties of the token at the moment it was observed,
requiring no chart, no signal and no timing. This tests the hypothesis they
suggest: **filter on structure, then hold, and let the tail arrive.**

**No look-ahead.** Every arm enters at a token's *first* journalled observation,
using only fields recorded at that moment, and is scored against the price now.
The filters cannot see the outcome they are being judged on.

**Decision rule, fixed before any number is seen.** An arm is only interesting if
it beats the unfiltered base rate *and* still beats it after its top 3 winners
are removed. The second test is the one that matters: this market's expectancy
lives in a handful of tokens, so any filter will look excellent if it happens to
catch one. Stage 6 flow was withdrawn on exactly this basis, and an entry filter
does not get an easier standard than the gates already rejected.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections.abc import Callable
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

Filter = Callable[[dict[str, Any]], bool]


def _liquidity(payload: dict[str, Any]) -> float:
    return float(payload.get("liquidity_usd") or 0.0)


def _dev_sold_pct(payload: dict[str, Any]) -> float | None:
    value = (payload.get("cluster_metrics") or {}).get("dev_sell_pct")
    return None if value is None else float(value)


def everything(_payload: dict[str, Any]) -> bool:
    """Every journalled mint, including ones no order could have filled.

    Shown for reference and **excluded from the comparison**. It is dominated by
    tokens with no liquidity, where a price moved but no trade was possible: one
    candidate sat in a $0.00 pool and shows a nominal 2,176,870x. Using that as a
    baseline would make every real filter look terrible by comparison with a
    number nobody could have collected.
    """
    return True


def tradeable(payload: dict[str, Any]) -> bool:
    """The honest baseline: what the risk engine's liquidity floor already allows.

    A filter has to beat *this* to be worth adding, because this is what the
    system would do today without any new rule.
    """
    return _liquidity(payload) >= 5_000.0


def deep_liquidity(payload: dict[str, Any]) -> bool:
    return _liquidity(payload) >= 50_000.0


def deep_and_dev_exhausted(payload: dict[str, Any]) -> bool:
    """Deep pool, and the developer has no supply left to dump.

    The mechanism, which is why this is worth testing rather than merely
    correlated: a developer who has already exited cannot exit again. The
    overhang is spent. A token whose developer still holds has that supply
    pointed at it, and no stop survives the moment it arrives.
    """
    sold = _dev_sold_pct(payload)
    return deep_liquidity(payload) and sold is not None and sold >= 50.0


ARMS: dict[str, Filter] = {
    "everything (ref only)": everything,
    "tradeable (>=$5k)": tradeable,
    "deep (>=$50k)": deep_liquidity,
    "deep + dev exhausted": deep_and_dev_exhausted,
}

# The arm every other arm is judged against. Not "everything": see that
# filter's docstring for why an unexecutable baseline would flatter nothing.
BASELINE = "tradeable (>=$5k)"


def report_arm(name: str, multiples: list[float], cost_pct: float) -> tuple[float, float] | None:
    """Print one arm and return (headline EV, EV without top 3)."""
    count = len(multiples)
    if count < 5:
        print(f"{name:<22} {count:>5}  too few outcomes to read")
        return None

    ordered = sorted(multiples)
    leg = cost_pct / 100.0
    pnl = sorted((value * (1 - leg) - (1 + leg) for value in multiples), reverse=True)

    headline = statistics.mean(pnl)
    trimmed = statistics.mean(pnl[3:]) if count > 8 else float("nan")
    dead = 100.0 * sum(1 for value in ordered if value < 0.10) / count
    two_x = 100.0 * sum(1 for value in ordered if value >= 2.0) / count
    five_x = 100.0 * sum(1 for value in ordered if value >= 5.0) / count

    print(
        f"{name:<22} {count:>5} {statistics.median(ordered):>8.3f} "
        f"{dead:>5.0f}% {two_x:>5.1f}% {five_x:>5.1f}% "
        f"{headline:>+9.3f} {trimmed:>+9.3f}"
    )
    return headline, trimmed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    arguments = parser.parse_args()
    del arguments

    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled yet.")
        return 1

    # First observation per mint: the only moment a trader could have acted.
    first: dict[str, dict[str, Any]] = {}
    for event in events:
        first.setdefault(event["entity_id"], event["payload"])

    priced = {
        mint: payload
        for mint, payload in first.items()
        if payload.get("price_usd") and float(payload["price_usd"]) > 0
    }
    prices = DexScreenerProvider().prices_for_tokens(list(priced))
    unresolved = sum(1 for mint in priced if mint not in prices)
    measurable = len(priced) - unresolved

    print(f"{len(first)} distinct mints journalled")
    print(f"  {len(first) - len(priced)} with no usable observation price")
    print(f"  {unresolved} whose current price could not be resolved")
    print(f"  {measurable} with a measurable outcome")

    reconciled = (len(first) - len(priced)) + unresolved + measurable
    if reconciled != len(first):
        print("  MISMATCH -- mints unaccounted for; do not read the table below.")
        return 1
    if not measurable:
        print("\nNo outcomes could be measured. This is 'no data', not 'no effect'.")
        return 0

    # Cost is charged at the position this account actually takes.
    nominal = settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0
    cost_pct = round_trip_cost(nominal, settings.costs).pct_of_position
    print(f"\ncosts {cost_pct:.3f}% round trip at a ${nominal:.2f} position\n")

    header = (
        f"{'arm':<22} {'n':>5} {'median':>8} {'dead':>6} "
        f"{'>=2x':>6} {'>=5x':>6} {'EV/$1':>9} {'-top3':>9}"
    )
    print(header)
    print("-" * len(header))

    results: dict[str, tuple[float, float]] = {}
    for name, keep in ARMS.items():
        multiples = [
            prices[mint] / float(payload["price_usd"])
            for mint, payload in priced.items()
            if mint in prices and keep(payload)
        ]
        outcome = report_arm(name, multiples, cost_pct)
        if outcome is not None:
            results[name] = outcome

    print("\n'EV/$1' is expectancy per dollar staked after costs.")
    print("'-top3' is the same with the three largest winners removed -- the test")
    print("that decides whether an arm has an edge or merely caught a lottery ticket.\n")

    print("'everything' is reference only: it includes tokens with no liquidity,")
    print("where a price moved but no order could have filled. Comparisons are")
    print(f"made against '{BASELINE}', which is what the system does today.\n")

    baseline = results.get(BASELINE)
    if baseline is None:
        print(f"No '{BASELINE}' baseline to compare against.")
        return 0

    survivors = [
        name
        for name, (headline, trimmed) in results.items()
        if name not in (BASELINE, "everything (ref only)")
        and headline > baseline[0]
        and not math.isnan(trimmed)
        and trimmed > baseline[1]
    ]

    if survivors:
        print("Arms beating the base rate both before and after removing their top 3:")
        for name in survivors:
            print(f"  {name}")
        print("\nWorth pursuing. Re-run on a frozen multi-day cohort before sizing on it.")
    else:
        print("VERDICT: UNPROVEN -- no arm beats the unfiltered base rate once its")
        print("top 3 winners are removed. Per the rule fixed before this ran, no")
        print("structural filter is wired into entry. Record the negative result.")
        print()
        print("If the filters separate outcomes but not tail-adjusted expectancy, the")
        print("honest reading is that they measure survival rather than return -- the")
        print("same shape as the cluster and deployer gates before them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
