#!/usr/bin/env python3
"""Can an exit rule rescue a population whose median outcome is 0.001?

Selection has been eliminated as the edge. Recall is zero, and the one filter
that looked like an edge turned out to be a single token out of 166. What that
leaves is the observation underneath: tokens passing every filter have a median
outcome of 0.001. They do not drift down, they die.

That points at exit speed, and exit speed has never been measured on this
strategy. `compare_exit_policies.py` tested exits against *breakout* entries --
a different population, entered at a different moment, on a rule that has since
been eliminated. Filter-and-hold has only ever been measured on endpoints, which
cannot see what happened on the way.

**Entry comes from the journal, not from a trending list.** The first version of
this study sampled currently-trending pools and entered at the start of their
returned candle history -- about a thousand bars ago. That measures "buy a token
that is trending today, ten days ago", and it reported a median of 3.24 with a 0%
death rate against a population measured elsewhere at 0.001 and 49%. Survivorship
bias, built in by the sampling, and obvious only because the numbers disagreed
violently with everything else known about this population.

Entry is therefore each mint's **first journalled observation** -- a moment
chosen before the outcome existed. The candle at or after that timestamp is the
entry, and only candles after it are visible to the exit rule.

**Judged on geometric growth, not arithmetic expectancy.** Expectancy chose the
current 10% position size, which decays the account to a quarter of itself over
50 trades. Growth is the quantity that decides whether an account compounds
through a fat tail, so every policy is scored at the growth-optimal fraction.

**Decision rule, fixed before any number is seen.** A policy is interesting only
if its growth is positive *and* stays positive after its single best outcome is
removed. The withdrawn deep-pool finding failed exactly that test, and an exit
rule does not get an easier standard.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_strategy import TIMEFRAMES, to_candles

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.coingecko import CoinGeckoProvider
from meme_flight_recorder.strategy import Candle

# A policy sees the entry price and the candles since entry, and returns the
# multiple it realised. Every policy pays the same costs, applied by the caller.
Policy = Callable[[float, list[Candle]], float]


def hold(entry: float, candles: list[Candle]) -> float:
    """Never exit. The baseline the endpoint studies already measured."""
    return candles[-1].close / entry


def fixed_stop(drop_pct: float) -> Policy:
    """Leave when price closes below a fixed fraction of entry.

    Evaluated on the close rather than the intra-candle low, because this system
    polls and can only act on a price it actually observed. That understates
    losses on a gap, which is a known and separately measured limitation --
    a replayed collapse fell 1,700x below its stop inside one candle.
    """

    def policy(entry: float, candles: list[Candle]) -> float:
        floor = entry * (1 - drop_pct / 100.0)
        for candle in candles:
            if candle.close <= floor:
                return candle.close / entry
        return candles[-1].close / entry

    return policy


def trailing_stop(drop_pct: float) -> Policy:
    """Leave when price closes a fixed fraction below its high-water mark."""

    def policy(entry: float, candles: list[Candle]) -> float:
        peak = entry
        for candle in candles:
            peak = max(peak, candle.close)
            if candle.close <= peak * (1 - drop_pct / 100.0):
                return candle.close / entry
        return candles[-1].close / entry

    return policy


def time_stop(bars: int) -> Policy:
    """Leave after a fixed number of bars, whatever the price."""

    def policy(entry: float, candles: list[Candle]) -> float:
        index = min(bars, len(candles)) - 1
        return candles[index].close / entry

    return policy


def take_profit(multiple: float, stop_pct: float = 50.0) -> Policy:
    """Leave at a target, with a stop underneath so it is not a naked hold."""

    def policy(entry: float, candles: list[Candle]) -> float:
        floor = entry * (1 - stop_pct / 100.0)
        for candle in candles:
            if candle.close >= entry * multiple:
                return multiple
            if candle.close <= floor:
                return candle.close / entry
        return candles[-1].close / entry

    return policy


POLICIES: dict[str, Policy] = {
    "hold": hold,
    "stop -30%": fixed_stop(30.0),
    "stop -50%": fixed_stop(50.0),
    "trail -30%": trailing_stop(30.0),
    "trail -50%": trailing_stop(50.0),
    "time 24 bars": time_stop(24),
    "time 96 bars": time_stop(96),
    "take 2x, stop -50%": take_profit(2.0),
    "take 5x, stop -50%": take_profit(5.0),
}


def growth_per_trade(multiples: list[float], fraction: float, cost_pct: float) -> float:
    """Expected log growth of equity per trade at a given position fraction."""
    leg = cost_pct / 100.0 / 2.0
    total = 0.0
    for multiple in multiples:
        net = multiple * (1 - leg) - leg
        total += math.log(max(1 + fraction * (net - 1), 1e-9))
    return total / len(multiples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", type=int, default=40)
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAMES), default="15m")
    parser.add_argument("--minimum-liquidity", type=float, default=50_000.0)
    parser.add_argument("--fraction", type=float, default=0.02, help="Position as equity share.")
    arguments = parser.parse_args()
    timeframe, aggregate = TIMEFRAMES[arguments.timeframe]

    settings = load_settings()
    provider = CoinGeckoProvider()
    position = settings.starting_equity_usd * arguments.fraction
    cost_pct = round_trip_cost(position, settings.costs).pct_of_position

    print(
        f"position {arguments.fraction:.0%} of ${settings.starting_equity_usd:.0f} "
        f"= ${position:.2f}, round trip {cost_pct:.2f}%, candles {arguments.timeframe}\n"
    )

    recorder = FlightRecorder(settings.database_path)
    first: dict[str, dict[str, Any]] = {}
    for event in recorder.events_by_type("candidate_observed"):
        first.setdefault(event["entity_id"], event["payload"])

    candidates = [
        payload
        for payload in first.values()
        if float(payload.get("liquidity_usd") or 0.0) >= arguments.minimum_liquidity
        and payload.get("pair_address")
        and payload.get("observed_at")
    ][: arguments.tokens]
    print(f"{len(candidates)} journalled deep-pool candidates with a pool and a timestamp")

    paths: list[tuple[str, float, list[Candle]]] = []
    no_candles = too_short = no_entry_bar = 0
    for payload in candidates:
        try:
            response = provider.pool_ohlcv(
                str(payload["pair_address"]),
                aggregate=aggregate,
                limit=1000,
                timeframe=timeframe,
            )
        except Exception:  # noqa: BLE001
            no_candles += 1
            continue
        candles = to_candles(
            (response.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        )
        if len(candles) < 10:
            too_short += 1
            continue
        observed = datetime.fromisoformat(str(payload["observed_at"])).timestamp()
        # The first bar at or after the moment the candidate was actually seen.
        entry_index = next(
            (i for i, candle in enumerate(candles) if candle.timestamp >= observed), None
        )
        if entry_index is None or entry_index >= len(candles) - 5:
            no_entry_bar += 1
            continue
        paths.append(
            (
                str(payload.get("symbol") or "")[:14],
                candles[entry_index].close,
                candles[entry_index + 1 :],
            )
        )

    print(f"\ndenominator: {len(candidates)} candidates = {no_candles} no candles")
    print(f"           + {too_short} too short + {no_entry_bar} no bar at observation")
    print(f"           + {len(paths)} usable\n")
    if len(paths) < 10:
        print("Too few usable price paths to read. This is missing data, not a result.")
        return 1

    header = (
        f"{'exit policy':<20} {'n':>4} {'median':>8} {'dead':>6} "
        f"{'growth':>9} {'-top1':>9} {'x@50':>7}"
    )
    print(header)
    print("-" * len(header))

    survivors: list[str] = []
    for name, policy in POLICIES.items():
        multiples = [policy(entry, candles) for _symbol, entry, candles in paths]
        ordered = sorted(multiples, reverse=True)
        full = growth_per_trade(multiples, arguments.fraction, cost_pct)
        trimmed = growth_per_trade(ordered[1:], arguments.fraction, cost_pct)
        dead = 100.0 * sum(1 for value in multiples if value < 0.10) / len(multiples)
        print(
            f"{name:<20} {len(multiples):>4} {statistics.median(multiples):>8.3f} "
            f"{dead:>5.0f}% {full:>+9.4f} {trimmed:>+9.4f} {math.exp(full * 50):>7.2f}"
        )
        if full > 0 and trimmed > 0:
            survivors.append(name)

    print("\n'growth' is expected log growth of equity per trade; 'x@50' compounds it")
    print("over 50 trades. '-top1' repeats it with the single best outcome removed.\n")

    if survivors:
        print("Policies with positive growth that survives removing their best outcome:")
        for name in survivors:
            print(f"  {name}")
        print("\nWorth pursuing. Re-run on a larger sample before sizing on it.")
    else:
        print("VERDICT: no exit policy produces growth that survives removing its single")
        print("best outcome. Exit speed does not rescue this population: the losses are")
        print("not slow enough to step out of, and capping the upside removes the only")
        print("thing paying for them.")
        print()
        print("That closes the last untested lever on this strategy. What remains is")
        print("a different population or more capital, not a different rule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
