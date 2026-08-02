from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class BreakoutSetup:
    breakout_timestamp: int
    level: float
    breakout_close: float
    atr: float
    expires_after_candles: int = 8


@dataclass(frozen=True)
class EntrySignal:
    entry: float
    stop: float
    target: float
    breakout_level: float


def ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    output = [sum(values[:period]) / period]
    for value in values[period:]:
        output.append(value * multiplier + output[-1] * (1 - multiplier))
    return output


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    ranges = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return sum(ranges) / period


def established_meme_trend_ok(candles_1h: list[Candle], max_extension_atr: float = 2.5) -> bool:
    """Uses completed candles only; caller must not include the currently forming candle."""
    closes = [candle.close for candle in candles_1h]
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    current_atr = atr(candles_1h)
    if len(fast) < 2 or len(slow) < 2 or current_atr in (None, 0):
        return False
    return (
        fast[-1] > slow[-1]
        and fast[-1] > fast[-2]
        and slow[-1] >= slow[-2]
        and closes[-1] >= fast[-1]
        and closes[-1] - fast[-1] <= max_extension_atr * current_atr
    )


def detect_breakout(
    candles_15m: list[Candle],
    consolidation_candles: int = 16,
    max_range_atr: float = 2.5,
    minimum_volume_ratio: float = 1.5,
) -> BreakoutSetup | None:
    """The final candle is the candidate breakout; the range uses prior candles only."""
    if len(candles_15m) < max(consolidation_candles + 1, 22):
        return None
    breakout = candles_15m[-1]
    history = candles_15m[:-1]
    consolidation = history[-consolidation_candles:]
    current_atr = atr(history)
    if current_atr in (None, 0):
        return None
    level = max(candle.high for candle in consolidation)
    floor = min(candle.low for candle in consolidation)
    average_volume = sum(candle.volume for candle in history[-20:]) / 20
    if level - floor > max_range_atr * current_atr:
        return None
    if breakout.close <= level or breakout.volume < minimum_volume_ratio * average_volume:
        return None
    if breakout.close - level > 2 * current_atr:
        return None
    return BreakoutSetup(breakout.timestamp, level, breakout.close, current_atr)


def confirm_retest(
    setup: BreakoutSetup, candles_after_breakout: list[Candle]
) -> EntrySignal | None:
    """Waits for a bullish level-hold; returns None for no entry, expiry, or a failed retest."""
    if not candles_after_breakout or len(candles_after_breakout) > setup.expires_after_candles:
        return None
    tolerance = 0.25 * setup.atr
    for candle in candles_after_breakout:
        if candle.close < setup.level - tolerance:
            return None
    candle = candles_after_breakout[-1]
    touched = candle.low <= setup.level + tolerance
    bullish_hold = candle.close >= setup.level and candle.close > candle.open
    if not (touched and bullish_hold):
        return None
    swing_low = min(item.low for item in candles_after_breakout)
    stop = swing_low - 0.2 * setup.atr
    if stop <= 0 or stop >= candle.close:
        return None
    target = candle.close + 2 * (candle.close - stop)
    return EntrySignal(candle.close, stop, target, setup.level)
