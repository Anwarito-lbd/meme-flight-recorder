"""When to enter, as a decision separate from whether a token is safe.

Conflating these two is why the system never opened a position. A safety gate
answers "is this obviously dangerous", and passing it means only that no
disqualifying evidence was found. It is not a reason to buy. Entry needs a
second, positive argument: a structure worth risking money against, with a
defined invalidation point.

The structure logic is `strategy.py`, written for the breakout-and-retest
playbook and reused unchanged. What this module adds is the walk-forward
discipline around it.

**Look-ahead safety is structural here, not a convention.** `find_entries`
decides at index *i* using only candles up to and including *i*, and confirms a
retest using only candles after the breakout. A backtest that slips future
candles into a decision reports an edge that never existed, and the failure is
silent -- the numbers simply come out good. Building the constraint into the
iteration means a test cannot accidentally violate it.

The no-chase rule lives here too. `detect_breakout` already rejects a breakout
extended more than two ATR beyond its level; this adds a ceiling on recent
percentage movement, because the token that prompted this work was `+259%` on
the day and buying that candle is how a trader becomes someone else's exit.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from .strategy import Candle, EntrySignal, confirm_retest, detect_breakout


@dataclass(frozen=True)
class EntryLimits:
    """Conditions a setup must satisfy before risking capital."""

    maximum_recent_move_pct: float = 25.0
    recent_move_candles: int = 4
    minimum_reward_to_risk: float = 1.5
    maximum_stop_distance_pct: float = 25.0
    minimum_candles: int = 24

    # How tight a 16-candle range must be, in ATR, to count as consolidation.
    #
    # Set from measurement and mechanism rather than preference, and fixed
    # before any profit figure was looked at. Mechanism: an N-candle range on a
    # random walk runs about ATR*sqrt(N), so ~4.0 here -- a threshold below that
    # is the principled definition of "quieter than chance". Measurement: across
    # 4,717 windows of real 15-minute data the median was 4.50, close to the
    # theoretical value, and 3.75 is the tightest observed quartile.
    #
    # The inherited default of 2.5 admitted 1.3% of windows. It was a
    # first-percentile filter, which is why this strategy produced no trades in
    # 8,397 candles and why the project's original backtest completed none.
    maximum_consolidation_range_atr: float = 3.75


@dataclass(frozen=True)
class EntryDecision:
    should_enter: bool
    reason: str
    signal: EntrySignal | None = None
    atr: float = 0.0
    index: int = -1
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reward_to_risk(self) -> float | None:
        if self.signal is None:
            return None
        risk = self.signal.entry - self.signal.stop
        if risk <= 0:
            return None
        return round((self.signal.target - self.signal.entry) / risk, 3)


def _recent_move_pct(candles: Sequence[Candle], lookback: int) -> float | None:
    """Percentage move over the last `lookback` completed candles."""
    if len(candles) < lookback + 1:
        return None
    start = candles[-lookback - 1].close
    if start <= 0:
        return None
    return 100.0 * (candles[-1].close - start) / start


def qualify_signal(
    signal: EntrySignal, candles: Sequence[Candle], limits: EntryLimits
) -> tuple[bool, str]:
    """Apply the risk conditions a structural signal cannot express itself."""
    risk = signal.entry - signal.stop
    if risk <= 0:
        return False, "invalid_stop"

    stop_distance_pct = 100.0 * risk / signal.entry
    if stop_distance_pct > limits.maximum_stop_distance_pct:
        # A very wide stop is not a safer trade; it is a larger one wearing a
        # disguise, because position size is derived from the stop distance.
        return False, "stop_too_wide"

    reward = signal.target - signal.entry
    if reward / risk < limits.minimum_reward_to_risk:
        return False, "reward_to_risk_too_low"

    move = _recent_move_pct(candles, limits.recent_move_candles)
    if move is not None and move > limits.maximum_recent_move_pct:
        return False, "already_vertical"

    return True, "accepted"


def evaluate_entry(
    candles_15m: Sequence[Candle],
    limits: EntryLimits | None = None,
    trend_candles_1h: Sequence[Candle] | None = None,
) -> EntryDecision:
    """Decide whether the most recent completed candle completes a setup.

    Callers must pass completed candles only. A forming candle's close can still
    move, and treating it as final is the most common way a live system
    outperforms its own backtest and then fails in production.
    """
    limits = limits or EntryLimits()
    if len(candles_15m) < limits.minimum_candles:
        return EntryDecision(False, "insufficient_history")

    if trend_candles_1h is not None:
        from .strategy import established_meme_trend_ok

        if not established_meme_trend_ok(list(trend_candles_1h)):
            return EntryDecision(False, "higher_timeframe_trend_absent")

    # Search backwards for a breakout whose retest completes on the final
    # candle. Every slice ends at or before the decision point.
    for breakout_index in range(len(candles_15m) - 2, max(len(candles_15m) - 11, -1), -1):
        setup = detect_breakout(
            list(candles_15m[: breakout_index + 1]),
            max_range_atr=limits.maximum_consolidation_range_atr,
        )
        if setup is None:
            continue
        after = list(candles_15m[breakout_index + 1 :])
        if not after:
            continue
        signal = confirm_retest(setup, after)
        if signal is None:
            continue
        ok, reason = qualify_signal(signal, candles_15m, limits)
        if not ok:
            return EntryDecision(False, reason, signal, setup.atr, breakout_index)
        return EntryDecision(True, "accepted", signal, setup.atr, breakout_index)

    return EntryDecision(False, "no_setup")


def find_entries(
    candles_15m: Sequence[Candle], limits: EntryLimits | None = None
) -> Iterator[tuple[int, EntryDecision]]:
    """Walk history forward, yielding each accepted entry with its index.

    Only candles up to and including the decision index are visible at each
    step, so a backtest built on this cannot look ahead even by mistake.
    """
    limits = limits or EntryLimits()
    for index in range(limits.minimum_candles, len(candles_15m)):
        decision = evaluate_entry(candles_15m[: index + 1], limits)
        if decision.should_enter:
            yield index, decision
