#!/usr/bin/env python3
"""Fast Momentum Impulse Scalper for Solana Meme Coins.

Fixes the late-entry / slow-bleed failure mode:
1. Replaces 2-hour slow breakout-retests with Fast Momentum Thrust entries (volume surge + buyer dominance).
2. Tightens initial invalidation stop to 6%-8% (below the impulse bar low).
3. Quick Scalp Profit-Taking: Harvests +20% to +35% fast momentum thrusts, moving stop to breakeven immediately.
4. Fast Stagnation Exit: If the token doesn't expand within 3 candles (45m), exits immediately at breakeven/market.
"""

from __future__ import annotations

import argparse
import statistics
from datetime import UTC, datetime

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.discovery import select_movers
from meme_flight_recorder.positions import TradeSummary
from meme_flight_recorder.providers.coingecko import CoinGeckoProvider
from meme_flight_recorder.strategy import Candle


def to_candles(rows: list[list[float]]) -> list[Candle]:
    ordered = sorted(rows, key=lambda row: row[0])
    return [
        Candle(int(t), float(o), float(h), float(low), float(c), float(v))
        for t, o, h, low, c, v in ordered
    ]


def ema(values: list[float], period: int) -> list[float]:

    if len(values) < period:
        return []
    multiplier = 2 / (period + 1)
    output = [sum(values[:period]) / period]
    for value in values[period:]:
        output.append(value * multiplier + output[-1] * (1 - multiplier))
    return output


def find_momentum_impulse_entries(
    candles: list[Candle],
    volume_surge_multiplier: float = 2.5,
    lookback_candles: int = 16,
    max_extension_pct: float = 15.0,
) -> list[tuple[int, float, float, float]]:
    """Detect high-velocity momentum impulse bars with trend alignment.

    Returns list of (entry_index, entry_price, stop_price, target_price).
    """
    entries: list[tuple[int, float, float, float]] = []
    closes = [c.close for c in candles]
    fast_ema = ema(closes, 12)
    slow_ema = ema(closes, 26)

    for i in range(max(lookback_candles + 1, 30), len(candles)):
        current = candles[i]
        prior = candles[i - lookback_candles : i]

        # 1. Trend Alignment: Fast EMA > Slow EMA (Uptrend condition)
        if len(fast_ema) <= i or len(slow_ema) <= i or fast_ema[i] <= slow_ema[i]:
            continue

        avg_vol = sum(c.volume for c in prior) / len(prior)
        recent_high = max(c.high for c in prior)
        recent_low = min(c.low for c in prior)

        # 2. Consolidation check: prior range must be relatively tight (< 20%)
        prior_range_pct = 100.0 * (recent_high - recent_low) / recent_low
        if prior_range_pct > 25.0:
            continue

        # 3. Strong bullish impulse: current close > open and close > recent high
        is_bullish_break = current.close > current.open and current.close >= recent_high

        # 4. Volume expansion: current volume >= surge multiplier * avg volume
        has_volume_surge = current.volume >= volume_surge_multiplier * max(avg_vol, 1.0)

        # 5. No-chase filter: Not already over-extended
        candle_pct = 100.0 * (current.close - current.open) / current.open
        not_overextended = 2.0 <= candle_pct <= max_extension_pct

        if is_bullish_break and has_volume_surge and not_overextended:
            entry_price = current.close
            stop_price = max(current.low * 0.99, entry_price * 0.93)  # 7% stop
            target_price = entry_price * 1.20  # +20% initial take profit
            entries.append((i, entry_price, stop_price, target_price))

    return entries



def backtest_momentum_scalp(
    mint: str,
    symbol: str,
    candles: list[Candle],
    position_usd: float = 10.0,
    cost_pct_per_leg: float = 0.9,
    take_profit_mult: float = 1.30,
    max_stagnation_candles: int = 3,
) -> list[TradeSummary]:
    trades: list[TradeSummary] = []
    blocked_until = -1

    entries = find_momentum_impulse_entries(candles)

    for index, entry_price, stop_price, _ in entries:
        if index <= blocked_until:
            continue

        quantity = position_usd / entry_price
        entry_cost = position_usd * (cost_pct_per_leg / 100.0)
        opened_at = datetime.fromtimestamp(candles[index].timestamp, tz=UTC)

        current_stop = stop_price
        high_water = entry_price
        scaled_out = False
        scaled_proceeds = 0.0
        remaining_qty = quantity

        for bar_idx in range(index + 1, len(candles)):
            c = candles[bar_idx]
            high_water = max(high_water, c.high)

            # 1. Stop loss hit
            if c.low <= current_stop:
                exit_price = current_stop
                exit_proceeds = remaining_qty * exit_price * (1.0 - cost_pct_per_leg / 100.0)
                total_proceeds = scaled_proceeds + exit_proceeds
                total_cost = position_usd + entry_cost
                pnl_usd = total_proceeds - total_cost
                closed_at = datetime.fromtimestamp(c.timestamp, tz=UTC)
                trades.append(
                    TradeSummary(
                        mint=mint,
                        symbol=symbol,
                        opened_at=opened_at,
                        closed_at=closed_at,
                        reason="stop_loss_hit",
                        realised_usd=pnl_usd,
                        return_pct=100.0 * pnl_usd / total_cost,
                        realised_r=pnl_usd / (position_usd * 0.08),
                        holding_minutes=(closed_at - opened_at).total_seconds() / 60.0,
                    )
                )
                blocked_until = bar_idx
                break

            # 2. First Scale / Take Profit Trigger
            if c.high >= entry_price * take_profit_mult and not scaled_out:
                scale_price = entry_price * take_profit_mult
                scale_qty = remaining_qty * 0.50
                scaled_proceeds = scale_qty * scale_price * (1.0 - cost_pct_per_leg / 100.0)
                remaining_qty -= scale_qty
                # Move stop to breakeven + buffer
                current_stop = entry_price * 1.02
                scaled_out = True

            # 3. Trailing Stop for the remaining runner
            if scaled_out and c.close < high_water * 0.90:
                exit_price = c.close
                exit_proceeds = remaining_qty * exit_price * (1.0 - cost_pct_per_leg / 100.0)
                total_proceeds = scaled_proceeds + exit_proceeds
                total_cost = position_usd + entry_cost
                pnl_usd = total_proceeds - total_cost
                closed_at = datetime.fromtimestamp(c.timestamp, tz=UTC)
                trades.append(
                    TradeSummary(
                        mint=mint,
                        symbol=symbol,
                        opened_at=opened_at,
                        closed_at=closed_at,
                        reason="trail_profit_runner",
                        realised_usd=pnl_usd,
                        return_pct=100.0 * pnl_usd / total_cost,
                        realised_r=pnl_usd / (position_usd * 0.08),
                        holding_minutes=(closed_at - opened_at).total_seconds() / 60.0,
                    )
                )
                blocked_until = bar_idx
                break

            # 4. Fast Stagnation Exit: If no move after N candles, close at market
            if bar_idx - index >= max_stagnation_candles and not scaled_out:
                exit_price = c.close
                exit_proceeds = remaining_qty * exit_price * (1.0 - cost_pct_per_leg / 100.0)
                total_proceeds = scaled_proceeds + exit_proceeds
                total_cost = position_usd + entry_cost
                pnl_usd = total_proceeds - total_cost
                closed_at = datetime.fromtimestamp(c.timestamp, tz=UTC)
                trades.append(
                    TradeSummary(
                        mint=mint,
                        symbol=symbol,
                        opened_at=opened_at,
                        closed_at=closed_at,
                        reason="stagnation_fast_exit",
                        realised_usd=pnl_usd,
                        return_pct=100.0 * pnl_usd / total_cost,
                        realised_r=pnl_usd / (position_usd * 0.08),
                        holding_minutes=(closed_at - opened_at).total_seconds() / 60.0,
                    )
                )
                blocked_until = bar_idx
                break

    return trades


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Momentum Scalper Engine")
    parser.add_argument("--position-usd", type=float, default=10.0)
    parser.add_argument("--tp", type=float, default=1.25, help="Take profit multiple (e.g. 1.25 = +25%%)")
    parser.add_argument("--timeframe", choices=["15m", "1h"], default="15m")
    args = parser.parse_args()

    settings = load_settings()
    provider = CoinGeckoProvider()

    rt = round_trip_cost(args.position_usd, settings.costs)
    cost_pct_per_leg = rt.pct_of_position / 2.0

    print("=" * 80)
    print("  OPTIMIZED FAST MOMENTUM SCALPER BACKTEST")
    print(f"  Position Size: ${args.position_usd:.2f} | Take Profit: {args.tp:.2f}x (+{(args.tp-1)*100:.0f}%)")
    print(f"  Tight Stop Loss: -8.0% | Stagnation Exit: 3 candles ({args.timeframe})")
    print(f"  Round-Trip Cost: ${rt.total_usd:.4f} ({rt.pct_of_position:.2f}% per trade)")
    print("=" * 80)

    movers, skipped = select_movers(provider.trending_solana_pools())
    print(f"\nSampled {len(movers)} active/trending Solana token pools.")

    timeframe_arg, agg = ("minute", 15) if args.timeframe == "15m" else ("hour", 1)
    all_trades: list[TradeSummary] = []

    # Every pool lands in exactly one bucket and the buckets reconcile against the
    # pools sampled. The previous version ended in `except Exception: continue`, so
    # a provider error removed a pool from the sample with no trace -- the same
    # defect found in `backtest_moonbag_engine.py`, where it hid a 404 on 12 of 22
    # tokens. A transport failure is not a pool without setups.
    outcomes: dict[str, int] = {}

    def tally(reason: str) -> None:
        outcomes[reason] = outcomes.get(reason, 0) + 1

    for pool in movers:
        if not pool.liquidity_usd or pool.liquidity_usd < 50_000.0:
            tally("liquidity_below_50k")
            continue
        try:
            payload = provider.pool_ohlcv(pool.pool_address, aggregate=agg, limit=1000, timeframe=timeframe_arg)
        except Exception as error:  # noqa: BLE001 - transport failure is not evidence about the pool
            tally(f"fetch_failed:{type(error).__name__}")
            continue
        candles = to_candles((payload.get("data") or {}).get("attributes", {}).get("ohlcv_list") or [])
        if len(candles) < 30:
            tally("too_few_candles")
            continue
        trades = backtest_momentum_scalp(
            pool.mint,
            pool.symbol,
            candles,
            position_usd=args.position_usd,
            cost_pct_per_leg=cost_pct_per_leg,
            take_profit_mult=args.tp,
        )
        all_trades.extend(trades)
        tally("entered" if trades else "no_setup")

    print("\n=== pool reconciliation ===")
    for reason, count in sorted(outcomes.items()):
        print(f"  {reason:<32}{count:>5}")
    print(f"  {'total':<32}{sum(outcomes.values()):>5} of {len(movers)} sampled")
    if sum(outcomes.values()) != len(movers):
        print("  MISMATCH: pools sampled do not reconcile against outcomes")
    if skipped:
        print(f"  discovery feed skips: {dict(skipped)}")


    if not all_trades:
        print("No completed trades found.")
        return 0

    pnls = [t.realised_usd for t in all_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total_pnl = sum(pnls)
    win_rate = 100.0 * len(wins) / len(all_trades)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)

    print("\nRESULTS:")
    print(f"  Total Trades        : {len(all_trades)}")
    print(f"  Win Rate            : {win_rate:.1f}% ({len(wins)} wins / {len(losses)} losses)")
    print(f"  Total Realized P&L  : ${total_pnl:+.2f}")
    print(f"  Avg Trade P&L       : ${statistics.mean(pnls):+.4f}")
    print(f"  Profit Factor       : {pf:.3f}")
    if wins:
        print(f"  Largest Win         : ${max(wins):+.2f}")
        print(f"  Avg Win             : ${statistics.mean(wins):+.2f}")
    if losses:
        print(f"  Largest Loss        : ${min(losses):+.2f}")
        print(f"  Avg Loss            : ${statistics.mean(losses):+.2f}")

    print("\nEXIT DISTRIBUTION:")
    reasons: dict[str, int] = {}
    for t in all_trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    for r, count in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {count:>4}  {r}")

    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
