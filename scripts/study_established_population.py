#!/usr/bin/env python3
"""Does the established meme population pay, under the strategy already written?

STATUS.md records that on newborn Solana launchpad tokens every lever has been
measured and every one came back negative: recall against winners is zero,
selection is dead, exits buy survival by removing the tail that paid, waiting
does the same from the other direction, and corrected sizing only slows the
bleed. Its conclusion is that what remains is "a different population or more
capital -- not a different rule".

This study tests the first half of that claim, and it deliberately does **not**
introduce a new rule to do it. `src/meme_flight_recorder/cex_engine.py` already
contains `CexPaperEngine`, a completed-candle breakout state machine for
established meme coins, built on `established_meme_trend_ok`, `detect_breakout`
and `confirm_retest`. It is tested, it is wired into `api.py`, and `cli.py`
contains zero references to it -- so it has never been run against data. This
script runs exactly those functions, unmodified, over a year of real candles.

THE DECISION RULE, FIXED BEFORE THE NUMBERS WERE SEEN
-----------------------------------------------------
The bar is not invented here. It is the project's own evidence gate, read from
`CohortLimits` so that it cannot drift from the gate that governs live
execution, and it is evaluated on the **held-out** half of the period only:

  1. at least `minimum_observable_trades` (30) completed trades;
  2. positive net expectancy after costs charged on both legs;
  3. profit factor above `minimum_profit_factor` (1.2);
  4. no single trade contributing more than `maximum_single_trade_profit_share`
     (1/3) of gross profit;
  5. expectancy still positive after deleting the single best trade.

Condition 5 is not in the live-execution gate and is added deliberately. It is
the test that killed the `deep >=$50k` filter: at n=166 removing one 209.7x
token flipped geometric growth negative at every position size. A population
whose edge does not survive the loss of its best trade is a lottery wearing the
costume of a strategy, and this project has already been fooled by that once.

Anything short of all five is UNPROVEN, and UNPROVEN means nothing gets wired
into entry. Two of three Stage 3/6/8 studies returned unproven and stayed out of
the entry path; that is the system working.

WHAT THIS STUDY CANNOT SEE, STATED RATHER THAN BURIED
-----------------------------------------------------
  * **Survivorship.** The universe is pairs listed *today*. A meme coin that was
    delisted during the year is absent, and delistings are not random. This
    biases the result *upward* and cannot be corrected from this data.
  * **Costs are modelled, not quoted.** Fees and spread come from parameters and
    are charged on both legs through `costs.round_trip_cost`, never as a flat
    percentage. Unlike the Solana case the fixed term is genuinely zero -- a CEX
    charges no per-trade network fee -- so `fixed_cost_per_leg_usd` is zero
    because that is true here, not as a convenience.
  * **Slippage beyond the modelled spread is not simulated.** At a sub-dollar
    position on books this deep that is a reasonable omission, and it is the
    one assumption that a larger account would have to revisit.

Read-only. Touches the candle cache and nothing else; the journal is not opened.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from meme_flight_recorder.config import CohortLimits, CostModel
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.strategy import (
    Candle,
    confirm_retest,
    detect_breakout,
    established_meme_trend_ok,
)

# `detect_breakout` needs 22 entry candles and `established_meme_trend_ok` needs
# 51 trend closes for its slow EMA plus one for the ATR lookback. Starting the
# walk before both are satisfiable would report "no signal" for a window where
# no signal was computable -- absent read as zero, which this project has
# committed three times.
MINIMUM_ENTRY_CANDLES = 22
MINIMUM_TREND_CANDLES = 52

# Feeding `detect_breakout` the whole series from the start is O(n^2) and takes
# hours over a year of 15m bars. Truncating to the trailing 22 is **exact, not
# an approximation**: the function reads `candles[-1]`, `history[-16:]` for the
# consolidation range, `atr(history)` which reads `history[-15:]`, and
# `history[-20:]` for average volume -- 21 candles of history plus the breakout
# bar. Its own guard then requires at least 22. `test_study_established.py`
# asserts equality against the untruncated call rather than trusting this note.
DETECT_LOOKBACK = 22

# The trend filter cannot be truncated exactly: `ema` seeds from the first
# `period` values and is path-dependent from the start of the series, so any
# window is an approximation. The multiplier for the slow EMA is 2/51, so an
# initial discrepancy decays by ~0.9608 per bar and is below 1e-6 after ~310
# bars. 400 is used, and `--trend-window` exists so the sensitivity is measured
# rather than assumed. Measured 2026-08-15 on the full year, 8 symbols: windows
# of 200, 400 and 800 produce byte-identical output -- same 54 entries, same
# expectancy, same profit factor. The approximation is not affecting the result.
DEFAULT_TREND_WINDOW = 400


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_ts: int
    exit_ts: int
    entry: float
    exit: float
    reason: str
    net_multiple: float
    net_pnl_usd: float


def load_series(
    cache_path: str, exchange: str, quote: str, timeframe: str
) -> dict[str, list[Candle]]:
    """Candles per symbol, zero-volume rows dropped.

    A candle with no volume is not a price. Birdeye carried a rugged token's
    last close forward for sixteen hours in 963 such rows; the same shape on a
    CEX is a halted or illiquid minute. Dropping them here is why the backfill
    stores the volume and counts them per series rather than discarding silently.
    """
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    series: dict[str, list[Candle]] = defaultdict(list)
    rows = connection.execute(
        "select symbol, ts, open, high, low, close, volume from candles"
        " where exchange = ? and timeframe = ? and symbol like ?"
        " order by symbol, ts",
        (exchange, timeframe, f"%/{quote}"),
    )
    for symbol, ts, open_, high, low, close, volume in rows:
        if not volume:
            continue
        if not all(value and value > 0 for value in (open_, high, low, close)):
            continue
        series[symbol].append(Candle(int(ts), open_, high, low, close, volume))
    connection.close()
    return dict(series)


def advance_trend_cursor(
    trend: list[Candle], cursor: int, entry_close_ms: int, timeframe_ms: int
) -> int:
    """First index of `trend` that is *not* yet complete at `entry_close_ms`.

    A 1h candle stamped at `ts` is not complete until `ts + 1h`. Including the
    candle currently forming would let the trend filter read a close that had
    not happened yet, which is the ordinary way a backtest invents an edge.

    The cursor only ever moves forward, which is what makes the walk linear
    instead of quadratic; the caller must not rewind it.
    """
    while cursor < len(trend) and trend[cursor].timestamp + timeframe_ms <= entry_close_ms:
        cursor += 1
    return cursor


def resolve_exit(
    candles: list[Candle], start_index: int, signal_stop: float, signal_target: float, max_bars: int
) -> tuple[int, float, str]:
    """Walk forward to the first stop or target touch, or time out.

    Two conservatisms, both deliberate. When a single bar's range contains both
    the stop and the target there is no way to know from OHLC which came first,
    so the **stop** is assumed to have hit -- the pessimistic reading. And a gap
    through either level fills at the bar's open rather than at the level, which
    hurts on stops and helps on targets, because that is what actually happens.
    """
    for offset in range(start_index, min(start_index + max_bars, len(candles))):
        candle = candles[offset]
        if candle.open <= signal_stop:
            return offset, candle.open, "stop_gap"
        if candle.low <= signal_stop:
            return offset, signal_stop, "stop"
        if candle.open >= signal_target:
            return offset, candle.open, "target_gap"
        if candle.high >= signal_target:
            return offset, signal_target, "target"
    last = min(start_index + max_bars, len(candles)) - 1
    if last < start_index:
        return -1, 0.0, "no_bars"
    return last, candles[last].close, "timeout"


def run_strategy(
    entry_candles: dict[str, list[Candle]],
    trend_candles: dict[str, list[Candle]],
    entry_tf_ms: int,
    trend_tf_ms: int,
    max_hold_bars: int,
    position_usd: float,
    cost_model: CostModel,
    trend_window: int,
) -> tuple[list[Trade], dict[str, int]]:
    """Replay `CexPaperEngine`'s own functions bar by bar, with no look-ahead.

    Every bar lands in exactly one bucket and the buckets sum to the bars
    walked, so a run that produces no trades can be told apart from a run that
    could not evaluate anything.
    """
    trades: list[Trade] = []
    buckets: dict[str, int] = defaultdict(int)
    cost = round_trip_cost(position_usd, cost_model)

    for symbol, candles in sorted(entry_candles.items()):
        trend = trend_candles.get(symbol, [])
        cursor = 0
        index = MINIMUM_ENTRY_CANDLES
        while index < len(candles) - 1:
            buckets["bars_walked"] += 1
            bar = candles[index]
            bar_close_ms = bar.timestamp + entry_tf_ms
            cursor = advance_trend_cursor(trend, cursor, bar_close_ms, trend_tf_ms)
            if cursor < MINIMUM_TREND_CANDLES:
                buckets["trend_history_insufficient"] += 1
                index += 1
                continue
            usable_trend = trend[max(0, cursor - trend_window) : cursor]
            if not established_meme_trend_ok(usable_trend):
                buckets["trend_failed"] += 1
                index += 1
                continue
            setup = detect_breakout(candles[index + 1 - DETECT_LOOKBACK : index + 1])
            if setup is None:
                buckets["no_breakout"] += 1
                index += 1
                continue

            # A setup exists. Offer `confirm_retest` progressively longer
            # windows, exactly as the live engine does one candle at a time.
            signal = None
            confirmed_at = None
            for ahead in range(1, setup.expires_after_candles + 1):
                if index + ahead >= len(candles):
                    break
                window = candles[index + 1 : index + 1 + ahead]
                signal = confirm_retest(setup, window)
                if signal is not None:
                    confirmed_at = index + ahead
                    break
            if signal is None or confirmed_at is None:
                buckets["retest_failed_or_expired"] += 1
                index += 1
                continue

            # The signal is produced by the close of `confirmed_at`, so the
            # order fills at the next bar's open -- never at the close that
            # generated it.
            fill_index = confirmed_at + 1
            if fill_index >= len(candles):
                buckets["signal_without_fill_bar"] += 1
                index += 1
                continue
            entry_price = candles[fill_index].open
            scale = entry_price / signal.entry if signal.entry > 0 else 1.0
            stop = signal.stop * scale
            target = signal.target * scale
            exit_index, exit_price, reason = resolve_exit(
                candles, fill_index, stop, target, max_hold_bars
            )
            if exit_index < 0 or entry_price <= 0:
                buckets["unfillable"] += 1
                index += 1
                continue

            gross = position_usd * (exit_price / entry_price)
            net_pnl = gross - position_usd - cost.total_usd
            trades.append(
                Trade(
                    symbol=symbol,
                    entry_ts=candles[fill_index].timestamp,
                    exit_ts=candles[exit_index].timestamp,
                    entry=entry_price,
                    exit=exit_price,
                    reason=reason,
                    net_multiple=(gross - cost.total_usd) / position_usd,
                    net_pnl_usd=net_pnl,
                )
            )
            buckets["entered"] += 1
            # A position is held to its exit; the next evaluation starts after
            # it, so the book never holds two positions in one symbol at once.
            index = exit_index + 1

    return trades, dict(buckets)


def run_random_control(
    entry_candles: dict[str, list[Candle]],
    entry_count: int,
    median_hold_bars: int,
    position_usd: float,
    cost_model: CostModel,
    seed: int,
) -> list[Trade]:
    """The control arm: same symbols, same costs, same holding period, no signal.

    Without this, a positive result cannot be attributed to the strategy rather
    than to the population simply having risen over the year. A signal that does
    not beat a coin flip on the same bars is not a signal.
    """
    generator = random.Random(seed)
    cost = round_trip_cost(position_usd, cost_model)
    pool = [
        (symbol, index)
        for symbol, candles in entry_candles.items()
        for index in range(MINIMUM_ENTRY_CANDLES, len(candles) - median_hold_bars - 1)
    ]
    if not pool or entry_count <= 0:
        return []
    trades: list[Trade] = []
    for symbol, index in generator.sample(pool, min(entry_count, len(pool))):
        candles = entry_candles[symbol]
        entry_price = candles[index].open
        exit_index = min(index + median_hold_bars, len(candles) - 1)
        exit_price = candles[exit_index].close
        if entry_price <= 0:
            continue
        gross = position_usd * (exit_price / entry_price)
        trades.append(
            Trade(
                symbol=symbol,
                entry_ts=candles[index].timestamp,
                exit_ts=candles[exit_index].timestamp,
                entry=entry_price,
                exit=exit_price,
                reason="control_timeout",
                net_multiple=(gross - cost.total_usd) / position_usd,
                net_pnl_usd=gross - position_usd - cost.total_usd,
            )
        )
    return trades


def run_buy_and_hold(
    entry_candles: dict[str, list[Candle]], position_usd: float, cost_model: CostModel
) -> list[Trade]:
    cost = round_trip_cost(position_usd, cost_model)
    trades = []
    for symbol, candles in sorted(entry_candles.items()):
        if len(candles) < 2:
            continue
        entry_price, exit_price = candles[0].open, candles[-1].close
        if entry_price <= 0:
            continue
        gross = position_usd * (exit_price / entry_price)
        trades.append(
            Trade(
                symbol=symbol,
                entry_ts=candles[0].timestamp,
                exit_ts=candles[-1].timestamp,
                entry=entry_price,
                exit=exit_price,
                reason="hold",
                net_multiple=(gross - cost.total_usd) / position_usd,
                net_pnl_usd=gross - position_usd - cost.total_usd,
            )
        )
    return trades


def summarise(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    pnls = [trade.net_pnl_usd for trade in trades]
    wins = [value for value in pnls if value > 0]
    losses = [-value for value in pnls if value < 0]
    gross_profit = sum(wins)
    ranked = sorted(pnls, reverse=True)
    return {
        "n": len(trades),
        "win_pct": 100.0 * len(wins) / len(trades),
        "expectancy_usd": statistics.fmean(pnls),
        "median_multiple": statistics.median(t.net_multiple for t in trades),
        "profit_factor": (gross_profit / sum(losses)) if losses else float("inf"),
        "top_trade_share": (max(wins) / gross_profit) if wins and gross_profit > 0 else None,
        "expectancy_drop_best": statistics.fmean(ranked[1:]) if len(ranked) > 1 else None,
        "total_pnl_usd": sum(pnls),
    }


def verdict(stats: dict[str, Any], limits: CohortLimits) -> tuple[bool, list[str]]:
    """The five conditions, computed. No judgement is applied anywhere here."""
    failures = []
    if stats.get("n", 0) < limits.minimum_observable_trades:
        failures.append(f"n={stats.get('n', 0)} < {limits.minimum_observable_trades}")
    if stats.get("n"):
        if stats["expectancy_usd"] <= 0:
            failures.append(f"expectancy {stats['expectancy_usd']:.4f} <= 0")
        if stats["profit_factor"] <= limits.minimum_profit_factor:
            failures.append(
                f"profit factor {stats['profit_factor']:.3f} <= {limits.minimum_profit_factor}"
            )
        share = stats["top_trade_share"]
        if share is None or share > limits.maximum_single_trade_profit_share:
            shown = "n/a" if share is None else f"{share:.3f}"
            failures.append(f"top trade share {shown} > {limits.maximum_single_trade_profit_share}")
        drop = stats["expectancy_drop_best"]
        if drop is None or drop <= 0:
            shown = "n/a" if drop is None else f"{drop:.4f}"
            failures.append(f"expectancy without best trade {shown} <= 0")
    return (not failures), failures


def print_arm(label: str, stats: dict[str, Any]) -> None:
    if not stats.get("n"):
        print(f"{label:<22}{0:>6}   (no trades)")
        return
    factor = stats["profit_factor"]
    factor_text = "inf" if factor == float("inf") else f"{factor:.3f}"
    share = stats["top_trade_share"]
    share_text = "n/a" if share is None else f"{share:.3f}"
    drop = stats["expectancy_drop_best"]
    drop_text = "n/a" if drop is None else f"{drop:.4f}"
    print(
        f"{label:<22}{stats['n']:>6}{stats['win_pct']:>8.1f}"
        f"{stats['expectancy_usd']:>12.4f}{factor_text:>9}"
        f"{stats['median_multiple']:>9.3f}{share_text:>9}{drop_text:>11}"
        f"{stats['total_pnl_usd']:>11.2f}"
    )


def header() -> None:
    line = (
        f"{'arm':<22}{'n':>6}{'win%':>8}{'net exp $':>12}{'PF':>9}"
        f"{'median':>9}{'top1sh':>9}{'-top1 $':>11}{'total $':>11}"
    )
    print(line)
    print("-" * len(line))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/cex-history.db")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--entry-timeframe", default="15m")
    parser.add_argument("--trend-timeframe", default="1h")
    parser.add_argument("--position-usd", type=float, default=0.80)
    # Kraken's published taker fee at the lowest volume tier is 0.40%; Binance's
    # is 0.10%. The higher figure is used so the result is not an artefact of
    # the cheapest possible venue, and it is charged on both legs.
    parser.add_argument("--taker-fee-pct", type=float, default=0.40)
    # Half-spread per leg. Established meme pairs quote a few basis points; 0.05%
    # is deliberately pessimistic for this liquidity.
    parser.add_argument("--half-spread-pct", type=float, default=0.05)
    parser.add_argument("--max-hold-bars", type=int, default=96)
    parser.add_argument("--trend-window", type=int, default=DEFAULT_TREND_WINDOW)
    parser.add_argument("--seed", type=int, default=20260814)
    arguments = parser.parse_args()

    timeframe_ms = {"15m": 900_000, "1h": 3_600_000, "5m": 300_000, "4h": 14_400_000}
    entry_tf_ms = timeframe_ms[arguments.entry_timeframe]
    trend_tf_ms = timeframe_ms[arguments.trend_timeframe]

    entry_candles = load_series(
        arguments.cache, arguments.exchange, arguments.quote, arguments.entry_timeframe
    )
    trend_candles = load_series(
        arguments.cache, arguments.exchange, arguments.quote, arguments.trend_timeframe
    )
    if not entry_candles:
        print("No candles in cache. Run scripts/backfill_cex_history.py first.")
        return 1

    # A CEX charges no per-trade network fee, so the fixed term is genuinely
    # zero here rather than suppressed. The proportional term carries both legs
    # of the taker fee and the half-spread, through the audited cost path.
    cost_model = CostModel(
        base_fee_sol=0.0,
        priority_fee_sol=0.0,
        dex_fee_pct=arguments.taker_fee_pct,
        assumed_impact_pct=arguments.half_spread_pct,
        sol_price_usd=0.0,
    )
    cost = round_trip_cost(arguments.position_usd, cost_model)

    span_start = min(candles[0].timestamp for candles in entry_candles.values())
    span_end = max(candles[-1].timestamp for candles in entry_candles.values())
    print("=== universe ===")
    print(f"exchange={arguments.exchange} quote={arguments.quote} symbols={len(entry_candles)}")
    print(f"{'symbol':<16}{'entry bars':>12}{'trend bars':>12}")
    for symbol in sorted(entry_candles):
        print(f"{symbol:<16}{len(entry_candles[symbol]):>12}{len(trend_candles.get(symbol, [])):>12}")
    print(
        f"period {datetime.fromtimestamp(span_start / 1000, UTC).date()}"
        f" .. {datetime.fromtimestamp(span_end / 1000, UTC).date()}"
    )
    print(
        f"position ${arguments.position_usd:.2f}; round trip ${cost.total_usd:.4f}"
        f" = {cost.pct_of_position:.3f}% of position;"
        f" breakeven {cost.breakeven_multiple:.5f}x"
    )

    trades, buckets = run_strategy(
        entry_candles,
        trend_candles,
        entry_tf_ms,
        trend_tf_ms,
        arguments.max_hold_bars,
        arguments.position_usd,
        cost_model,
        arguments.trend_window,
    )

    print("\n=== bar reconciliation ===")
    walked = buckets.pop("bars_walked", 0)
    accounted = sum(buckets.values())
    for name, count in sorted(buckets.items()):
        print(f"{name:<32}{count:>10}")
    print(f"{'total accounted':<32}{accounted:>10} of {walked} bars walked")
    if accounted != walked:
        print("MISMATCH: bars walked do not reconcile against outcome buckets")
        return 1

    midpoint = span_start + (span_end - span_start) // 2
    development = [trade for trade in trades if trade.entry_ts < midpoint]
    heldout = [trade for trade in trades if trade.entry_ts >= midpoint]
    hold_bars = (
        int(statistics.median((t.exit_ts - t.entry_ts) / entry_tf_ms for t in trades))
        if trades
        else 0
    )
    control = run_random_control(
        entry_candles,
        len(trades),
        max(hold_bars, 1),
        arguments.position_usd,
        cost_model,
        arguments.seed,
    )

    print("\n=== arms ===")
    header()
    print_arm("strategy (all)", summarise(trades))
    print_arm("  development half", summarise(development))
    print_arm("  held-out half", summarise(heldout))
    print_arm(f"random control ({hold_bars}b)", summarise(control))
    print_arm("buy and hold", summarise(run_buy_and_hold(entry_candles, arguments.position_usd, cost_model)))

    if trades:
        reasons: dict[str, int] = defaultdict(int)
        for trade in trades:
            reasons[trade.reason] += 1
        print("\n=== exit reasons (all trades) ===")
        for reason, count in sorted(reasons.items()):
            print(f"{reason:<20}{count:>8}{100.0 * count / len(trades):>8.1f}%")

    limits = CohortLimits()
    passed, failures = verdict(summarise(heldout), limits)
    print("\n=== verdict, on the held-out half, against the rule fixed in advance ===")
    if passed:
        print("PROVEN: all five conditions hold on data the strategy was not shaped against.")
        print("This still does not authorise live execution -- that needs forward trades.")
    else:
        print("UNPROVEN. Failing conditions:")
        for failure in failures:
            print(f"  - {failure}")
        print("\nNothing may be wired into entry on this result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
