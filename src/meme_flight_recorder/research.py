from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any

from .strategy import (
    BreakoutSetup,
    Candle,
    confirm_retest,
    detect_breakout,
    established_meme_trend_ok,
)


@dataclass(frozen=True)
class PaperTestAssumptions:
    starting_equity_usd: float = 10_000
    risk_per_trade_pct: float = 0.5
    maximum_position_pct: float = 100
    entry_slippage_pct: float = 0.10
    exit_slippage_pct: float = 0.10
    round_trip_fee_pct: float = 0.40
    maximum_holding_bars: int = 48
    trend_aggregation_bars: int = 4
    consolidation_max_range_atr: float = 2.5
    minimum_volume_ratio: float = 1.5


@dataclass(frozen=True)
class HistoricalTrade:
    entry_timestamp: int
    exit_timestamp: int
    entry: float
    exit: float
    stop: float
    target: float
    reason: str
    pnl_usd: float
    r_multiple: float


@dataclass(frozen=True)
class PaperTestResult:
    asset_id: str
    start_timestamp: int
    end_timestamp: int
    starting_equity_usd: float
    ending_equity_usd: float
    trades: tuple[HistoricalTrade, ...]
    maximum_drawdown_pct: float
    assumptions: PaperTestAssumptions
    data_quality: str = "coingecko_hourly_point_proxy_not_exchange_ohlcv"

    def metrics(self) -> dict[str, Any]:
        wins = [trade for trade in self.trades if trade.pnl_usd > 0]
        losses = [trade for trade in self.trades if trade.pnl_usd <= 0]
        gross_profit = sum(trade.pnl_usd for trade in wins)
        gross_loss = abs(sum(trade.pnl_usd for trade in losses))
        return {
            "asset_id": self.asset_id,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "trades": len(self.trades),
            "win_rate_pct": round(100 * len(wins) / len(self.trades), 2) if self.trades else 0,
            "expectancy_r": (
                round(
                    sum(trade.r_multiple for trade in self.trades) / len(self.trades),
                    3,
                )
                if self.trades
                else 0
            ),
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
            "net_pnl_usd": round(self.ending_equity_usd - self.starting_equity_usd, 2),
            "return_pct": round(
                100
                * (self.ending_equity_usd / self.starting_equity_usd - 1),
                3,
            ),
            "maximum_drawdown_pct": round(self.maximum_drawdown_pct, 3),
            "data_quality": self.data_quality,
            "assumptions": asdict(self.assumptions),
        }


def coingecko_points_to_proxy_candles(data: dict[str, Any]) -> list[Candle]:
    """Convert hourly point samples to conservative bars; never label these exchange OHLCV."""
    prices = {int(timestamp): float(price) for timestamp, price in data.get("prices", [])}
    volumes = {
        int(timestamp): float(volume) for timestamp, volume in data.get("total_volumes", [])
    }
    timestamps = sorted(prices)
    candles: list[Candle] = []
    for previous_timestamp, timestamp in pairwise(timestamps):
        previous = prices[previous_timestamp]
        current = prices[timestamp]
        candles.append(
            Candle(
                timestamp=timestamp,
                open=previous,
                high=max(previous, current),
                low=min(previous, current),
                close=current,
                volume=volumes.get(timestamp, 0),
            )
        )
    return candles


def aggregate_completed(candles: list[Candle], bars: int) -> list[Candle]:
    output: list[Candle] = []
    for start in range(0, len(candles) - bars + 1, bars):
        group = candles[start : start + bars]
        output.append(
            Candle(
                group[-1].timestamp,
                group[0].open,
                max(candle.high for candle in group),
                min(candle.low for candle in group),
                group[-1].close,
                sum(candle.volume for candle in group),
            )
        )
    return output


def run_historical_paper_test(
    asset_id: str,
    candles: list[Candle],
    assumptions: PaperTestAssumptions | None = None,
) -> PaperTestResult:
    cfg = assumptions or PaperTestAssumptions()
    if len(candles) < 240:
        raise ValueError("At least 240 completed base candles are required")
    equity = cfg.starting_equity_usd
    peak = equity
    maximum_drawdown = 0.0
    setup: BreakoutSetup | None = None
    entry: dict[str, float | int] | None = None
    trades: list[HistoricalTrade] = []

    for index in range(220, len(candles)):
        candle = candles[index]
        if entry is not None:
            stop = float(entry["stop"])
            target = float(entry["target"])
            age = index - int(entry["index"])
            reason: str | None = None
            raw_exit = candle.close
            if candle.low <= stop:
                reason, raw_exit = "stop", stop
            elif candle.high >= target:
                reason, raw_exit = "target", target
            elif age >= cfg.maximum_holding_bars:
                reason = "time_exit"
            if reason:
                exit_price = raw_exit * (1 - cfg.exit_slippage_pct / 100)
                quantity = float(entry["position_value"]) / float(entry["entry"])
                fees = float(entry["position_value"]) * cfg.round_trip_fee_pct / 100
                pnl = (exit_price - float(entry["entry"])) * quantity - fees
                risk = float(entry["risk"])
                equity += pnl
                peak = max(peak, equity)
                maximum_drawdown = max(maximum_drawdown, 100 * (peak - equity) / peak)
                trades.append(
                    HistoricalTrade(
                        int(entry["timestamp"]),
                        candle.timestamp,
                        float(entry["entry"]),
                        exit_price,
                        stop,
                        target,
                        reason,
                        round(pnl, 4),
                        round(pnl / risk, 4),
                    )
                )
                entry = None
            continue

        completed = candles[: index + 1]
        trend = aggregate_completed(completed, cfg.trend_aggregation_bars)
        if not established_meme_trend_ok(trend):
            setup = None
            continue
        if setup is None:
            setup = detect_breakout(
                completed,
                max_range_atr=cfg.consolidation_max_range_atr,
                minimum_volume_ratio=cfg.minimum_volume_ratio,
            )
            continue
        after = [item for item in completed if item.timestamp > setup.breakout_timestamp]
        if len(after) > setup.expires_after_candles:
            setup = None
            continue
        if after and after[-1].close < setup.level - 0.25 * setup.atr:
            setup = None
            continue
        signal = confirm_retest(setup, after)
        if signal is None:
            continue
        fill = signal.entry * (1 + cfg.entry_slippage_pct / 100)
        if signal.stop >= fill:
            setup = None
            continue
        risk = equity * cfg.risk_per_trade_pct / 100
        stop_pct = (fill - signal.stop) / fill
        position_value = min(
            risk / stop_pct,
            equity * cfg.maximum_position_pct / 100,
        )
        entry = {
            "timestamp": signal_entry_timestamp(after),
            "index": index,
            "entry": fill,
            "stop": signal.stop,
            "target": fill + 2 * (fill - signal.stop),
            "risk": risk,
            "position_value": position_value,
        }
        setup = None

    if entry is not None:
        candle = candles[-1]
        exit_price = candle.close * (1 - cfg.exit_slippage_pct / 100)
        quantity = float(entry["position_value"]) / float(entry["entry"])
        fees = float(entry["position_value"]) * cfg.round_trip_fee_pct / 100
        pnl = (exit_price - float(entry["entry"])) * quantity - fees
        risk = float(entry["risk"])
        equity += pnl
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, 100 * (peak - equity) / peak)
        trades.append(
            HistoricalTrade(
                int(entry["timestamp"]),
                candle.timestamp,
                float(entry["entry"]),
                exit_price,
                float(entry["stop"]),
                float(entry["target"]),
                "end_of_test",
                round(pnl, 4),
                round(pnl / risk, 4),
            )
        )

    return PaperTestResult(
        asset_id,
        candles[0].timestamp,
        candles[-1].timestamp,
        cfg.starting_equity_usd,
        equity,
        tuple(trades),
        maximum_drawdown,
        cfg,
    )


def signal_entry_timestamp(after: list[Candle]) -> int:
    if not after:
        raise ValueError("A retest candle is required")
    return after[-1].timestamp
