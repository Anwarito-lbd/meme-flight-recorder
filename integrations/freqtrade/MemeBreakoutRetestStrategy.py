"""Freqtrade dry-run adapter for the established-meme breakout/retest strategy.

This file intentionally lives outside the core package. Freqtrade owns order simulation;
the core flight recorder remains exchange-independent and unable to submit live orders.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from freqtrade.persistence import Order, Trade
from freqtrade.strategy import IStrategy, informative, stoploss_from_absolute
from pandas import DataFrame


class MemeBreakoutRetestStrategy(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = False
    minimal_roi: ClassVar[dict[str, float]] = {"0": 10.0}
    stoploss = -0.30
    trailing_stop = False
    process_only_new_candles = True
    startup_candle_count = 220
    use_exit_signal = True
    use_custom_stoploss = True

    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = dataframe["close"].ewm(span=20, adjust=False).mean()
        dataframe["ema50"] = dataframe["close"].ewm(span=50, adjust=False).mean()
        previous = dataframe["close"].shift(1)
        true_range = DataFrame(
            {
                "hl": dataframe["high"] - dataframe["low"],
                "hc": (dataframe["high"] - previous).abs(),
                "lc": (dataframe["low"] - previous).abs(),
            }
        ).max(axis=1)
        dataframe["atr14"] = true_range.rolling(14).mean()
        dataframe["trend_ok"] = (
            (dataframe["ema20"] > dataframe["ema50"])
            & (dataframe["ema20"] > dataframe["ema20"].shift(1))
            & (dataframe["ema50"] >= dataframe["ema50"].shift(1))
            & (dataframe["close"] >= dataframe["ema20"])
            & ((dataframe["close"] - dataframe["ema20"]) <= 2.5 * dataframe["atr14"])
        )
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        previous = dataframe["close"].shift(1)
        true_range = DataFrame(
            {
                "hl": dataframe["high"] - dataframe["low"],
                "hc": (dataframe["high"] - previous).abs(),
                "lc": (dataframe["low"] - previous).abs(),
            }
        ).max(axis=1)
        dataframe["atr14"] = true_range.rolling(14).mean()
        # shift(1) is mandatory: the breakout candle cannot define its own range.
        dataframe["range_high"] = dataframe["high"].shift(1).rolling(16).max()
        dataframe["range_low"] = dataframe["low"].shift(1).rolling(16).min()
        dataframe["volume_mean20"] = dataframe["volume"].shift(1).rolling(20).mean()
        dataframe["breakout"] = (
            (dataframe["close"] > dataframe["range_high"])
            & (dataframe["volume"] >= 1.5 * dataframe["volume_mean20"])
            & ((dataframe["range_high"] - dataframe["range_low"]) <= 2.5 * dataframe["atr14"])
            & ((dataframe["close"] - dataframe["range_high"]) <= 2 * dataframe["atr14"])
        )
        # A breakout remains eligible for eight completed candles. Retest uses the
        # most recent prior breakout level, never a forward/back-filled level.
        dataframe["breakout_level"] = (
            dataframe["range_high"].where(dataframe["breakout"]).ffill(limit=8)
        )
        dataframe["bars_since_breakout"] = (
            dataframe["breakout"]
            .rolling(9)
            .apply(
                lambda values: (
                    0
                    if values[-1]
                    else next((offset for offset, value in enumerate(values[::-1]) if value), 99)
                ),
                raw=True,
            )
        )
        tolerance = 0.25 * dataframe["atr14"]
        dataframe["valid_retest"] = (
            (dataframe["bars_since_breakout"] >= 1)
            & (dataframe["bars_since_breakout"] <= 8)
            & (dataframe["low"] <= dataframe["breakout_level"] + tolerance)
            & (dataframe["close"] >= dataframe["breakout_level"])
            & (dataframe["close"] > dataframe["open"])
        )
        dataframe["signal_stop"] = dataframe["low"].rolling(8).min() - 0.2 * dataframe["atr14"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe["valid_retest"] & dataframe["trend_ok_1h"] & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "breakout_retest")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        return dataframe

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        stop = float(dataframe.iloc[-1]["signal_stop"])
        risk_pct = (current_rate - stop) / current_rate
        if risk_pct <= 0 or risk_pct > 0.30:
            return 0
        risk_amount = self.wallets.get_total_stake_amount() * 0.005
        return min(max_stake, risk_amount / risk_pct)

    def order_filled(
        self, pair: str, trade: Trade, order: Order, current_time: datetime, **kwargs
    ) -> None:
        if trade.nr_of_successful_entries == 1 and order.ft_order_side == trade.entry_side:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            trade.set_custom_data(
                key="initial_stop", value=float(dataframe.iloc[-1]["signal_stop"])
            )

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        stop = trade.get_custom_data(key="initial_stop")
        if stop is None or float(stop) >= current_rate:
            return None
        return stoploss_from_absolute(float(stop), current_rate, is_short=False, leverage=1.0)

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | None:
        stop = trade.get_custom_data(key="initial_stop")
        if stop is None:
            return None
        # If the market gaps through the intended stop, do not fall back to the
        # class-level emergency -30% stop. Request an exit at the first opportunity.
        if current_rate <= float(stop):
            return "initial_stop_breached"
        target = trade.open_rate + 2 * (trade.open_rate - float(stop))
        return "two_r_target" if current_rate >= target else None
