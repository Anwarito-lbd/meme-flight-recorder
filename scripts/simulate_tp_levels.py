#!/usr/bin/env python3
"""Simulate different Take-Profit targets & bankroll sizes over the last month.

Answers:
1. What happens if we set Take Profit higher (3x, 5x, 10x) vs lower (1.3x, 1.5x, 2x)?
2. What happens if we trade with a $10 capital bankroll over the last month?
3. How do Solana transaction costs, DEX fees, and slippage impact small capital?
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Callable
from datetime import UTC, datetime

from meme_flight_recorder.config import ExitLimits, load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.discovery import select_movers
from meme_flight_recorder.entry import EntryLimits, find_entries
from meme_flight_recorder.exits import (
    ExitDecision,
    ExitReason,
    PositionObservation,
    PositionView,
    evaluate_exit,
)
from meme_flight_recorder.positions import TradeSummary
from meme_flight_recorder.providers.coingecko import CoinGeckoProvider
from meme_flight_recorder.strategy import Candle

TIMEFRAMES = {
    "15m": ("minute", 15),
    "1h": ("hour", 1),
    "4h": ("hour", 4),
    "1d": ("day", 1),
}

PROTECTIVE = frozenset(
    {
        ExitReason.SECURITY_AUTHORITY,
        ExitReason.SECURITY_DEVELOPER,
        ExitReason.SECURITY_LIQUIDITY_PULL,
        ExitReason.STALE_UNOBSERVABLE,
        ExitReason.STOP_HIT,
        ExitReason.LIQUIDITY_THIN,
        ExitReason.LIQUIDITY_IMPACT,
    }
)


def to_candles(rows: list[list[float]]) -> list[Candle]:
    ordered = sorted(rows, key=lambda row: row[0])
    return [
        Candle(int(t), float(o), float(h), float(low), float(c), float(v))
        for t, o, h, low, c, v in ordered
    ]


def make_tp_policy(tp_multiple: float) -> Callable[[PositionView, PositionObservation, ExitLimits], ExitDecision]:
    """Create a fixed take-profit multiple exit policy with safety protections."""

    def policy(view: PositionView, observation: PositionObservation, limits: ExitLimits) -> ExitDecision:
        decision = evaluate_exit(view, observation, limits)
        if decision.should_exit and decision.reason in PROTECTIVE:
            return decision

        if observation.price_usd is not None and view.entry_price > 0:
            current_mult = observation.price_usd / view.entry_price
            if current_mult >= tp_multiple:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.PROFIT_SECOND_SCALE,
                    fraction=1.0,
                )
        return ExitDecision(should_exit=False)

    return policy


def make_scale_runner_policy(first_scale_mult: float = 2.0) -> Callable[[PositionView, PositionObservation, ExitLimits], ExitDecision]:
    """Half out at target, let remainder ride with trailing stop."""

    def policy(view: PositionView, observation: PositionObservation, limits: ExitLimits) -> ExitDecision:
        decision = evaluate_exit(view, observation, limits)
        if decision.should_exit and decision.reason in PROTECTIVE:
            return decision

        if observation.price_usd is not None and view.entry_price > 0:
            current_mult = observation.price_usd / view.entry_price
            if current_mult >= first_scale_mult and view.scaled_out_fraction <= 0:
                return ExitDecision(
                    should_exit=True,
                    reason=ExitReason.PROFIT_FIRST_SCALE,
                    fraction=0.5,
                )
        return ExitDecision(should_exit=False)

    return policy



def simulate_trades(
    mint: str,
    symbol: str,
    candles: list[Candle],
    position_usd: float,
    cost_pct_per_leg: float,
    policy: Callable[[PositionView, PositionObservation, ExitLimits], ExitDecision],
) -> list[TradeSummary]:
    trades: list[TradeSummary] = []
    blocked_until = -1
    entry_limits = EntryLimits()
    exit_limits = ExitLimits()

    for index, decision in find_entries(candles, entry_limits):
        if index <= blocked_until or decision.signal is None:
            continue

        entry_candle = candles[index]
        entry_price = entry_candle.close
        quantity = position_usd / entry_price
        entry_cost = position_usd * (cost_pct_per_leg / 100.0)
        opened_at = datetime.fromtimestamp(entry_candle.timestamp, tz=UTC)

        view = PositionView(
            mint=mint,
            opened_at=opened_at,
            entry_price=entry_price,
            stop_price=entry_price * 0.85,
            breakout_level=decision.signal.entry if decision.signal else entry_price,
            entry_liquidity_usd=100_000.0,
            atr=0.05 * entry_price,
            high_water_price=entry_price,
            peak_liquidity_usd=100_000.0,
        )

        scaled_out = False
        scaled_proceeds = 0.0
        remaining_qty = quantity

        for bar_idx in range(index + 1, len(candles)):
            candle = candles[bar_idx]
            obs = PositionObservation(
                price_usd=candle.close,
                liquidity_usd=100_000.0,
                observed_at=datetime.fromtimestamp(candle.timestamp, tz=UTC),
            )

            exit_dec = policy(view, obs, exit_limits)
            if exit_dec.should_exit:
                if exit_dec.fraction < 1.0 and not scaled_out:
                    # Partial exit
                    scale_qty = remaining_qty * exit_dec.fraction
                    scaled_proceeds = scale_qty * candle.close * (1.0 - cost_pct_per_leg / 100.0)
                    remaining_qty -= scale_qty
                    view = PositionView(
                        mint=view.mint,
                        opened_at=view.opened_at,
                        entry_price=view.entry_price,
                        stop_price=view.entry_price,  # Move stop to breakeven
                        breakout_level=view.breakout_level,
                        entry_liquidity_usd=view.entry_liquidity_usd,
                        atr=view.atr,
                        high_water_price=max(view.high_water_price, candle.high),
                        scaled_out_fraction=exit_dec.fraction,
                        peak_liquidity_usd=view.peak_liquidity_usd,
                    )
                    scaled_out = True
                    continue

                # Final exit
                exit_proceeds = remaining_qty * candle.close * (1.0 - cost_pct_per_leg / 100.0)
                total_proceeds = scaled_proceeds + exit_proceeds
                total_cost = position_usd + entry_cost
                pnl_usd = total_proceeds - total_cost



                closed_at = datetime.fromtimestamp(candle.timestamp, tz=UTC)
                trades.append(
                    TradeSummary(
                        mint=mint,
                        symbol=symbol,
                        opened_at=opened_at,
                        closed_at=closed_at,
                        reason=exit_dec.reason.value if exit_dec.reason else "exit",
                        realised_usd=pnl_usd,
                        return_pct=100.0 * pnl_usd / total_cost,
                        realised_r=pnl_usd / (position_usd * 0.15),
                        holding_minutes=(closed_at - opened_at).total_seconds() / 60.0,
                    )
                )
                blocked_until = bar_idx
                break


    return trades


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate TP targets on $10 bankroll")
    parser.add_argument("--bankroll", type=float, default=10.0, help="Starting bankroll in USD")
    parser.add_argument("--pos-pct", type=float, default=10.0, help="Position size %% of equity")
    parser.add_argument("--tokens", type=int, default=15)
    args = parser.parse_args()

    settings = load_settings()
    provider = CoinGeckoProvider()

    position_usd = args.bankroll * (args.pos_pct / 100.0)
    rt = round_trip_cost(position_usd, settings.costs)
    cost_pct_per_leg = rt.pct_of_position / 2.0

    print("=" * 78)
    print("  TAKE-PROFIT SIMULATION OVER 1-MONTH REAL CANDLES")
    print(f"  Bankroll: ${args.bankroll:.2f} | Position Size: ${position_usd:.2f} ({args.pos_pct:.0f}%)")
    print(f"  Round-Trip Cost: ${rt.total_usd:.4f} ({rt.pct_of_position:.2f}% of position)")
    print(f"  (Fixed Solana Gas: ${rt.fixed_usd:.4f} + Taker/DEX Fee: ${rt.proportional_usd:.4f} + Slippage)")
    print("=" * 78)


    movers, skipped = select_movers(provider.trending_solana_pools())
    movers = movers[: args.tokens]
    print(f"\nSampled {len(movers)} active/trending Solana token pools from CoinGecko.\n")

    # Fetch 1-month candles (1h timeframe, ~720-1000 bars)
    # Every pool lands in exactly one bucket and the buckets reconcile against the
    # pools sampled. `except Exception: continue` used to drop pools silently, which
    # is how a provider error turns into a smaller sample that reads as the whole
    # population -- the same defect corrected in the other two backtest scripts.
    token_candles: list[tuple[str, str, list[Candle]]] = []
    outcomes: dict[str, int] = {}

    def tally(reason: str) -> None:
        outcomes[reason] = outcomes.get(reason, 0) + 1

    for pool in movers:
        try:
            payload = provider.pool_ohlcv(pool.pool_address, aggregate=1, limit=1000, timeframe="hour")
        except Exception as error:  # noqa: BLE001 - transport failure is not evidence about the pool
            tally(f"fetch_failed:{type(error).__name__}")
            continue
        candles = to_candles((payload.get("data") or {}).get("attributes", {}).get("ohlcv_list") or [])
        if len(candles) < 50:
            tally("too_few_candles")
            continue
        token_candles.append((pool.mint, pool.symbol, candles))
        tally("loaded")

    print("=== pool reconciliation ===")
    for reason, count in sorted(outcomes.items()):
        print(f"  {reason:<32}{count:>5}")
    print(f"  {'total':<32}{sum(outcomes.values()):>5} of {len(movers)} sampled")
    if sum(outcomes.values()) != len(movers):
        print("  MISMATCH: pools sampled do not reconcile against outcomes")
    if skipped:
        print(f"  discovery feed skips: {dict(skipped)}")
    print(f"\nLoaded 1-month hourly candles for {len(token_candles)} tokens.\n")

    tp_targets = [
        ("Take 1.3x (+30%)", make_tp_policy(1.3)),
        ("Take 1.5x (+50%)", make_tp_policy(1.5)),
        ("Take 2.0x (+100%)", make_tp_policy(2.0)),
        ("Take 3.0x (+200%)", make_tp_policy(3.0)),
        ("Take 5.0x (+400%)", make_tp_policy(5.0)),
        ("Take 10.0x (+900%)", make_tp_policy(10.0)),
        ("Scale 2x + Runner", make_scale_runner_policy(2.0)),
        ("Trailing Hierarchy", evaluate_exit),
    ]

    print(f"{'Strategy / TP Rule':<20} {'Trades':>6} {'Win %':>7} {'Total PnL':>11} {'Avg Trade':>11} {'Profit Factor':>13} {'Top Win':>9}")
    print("-" * 80)

    summary: list[dict[str, float | str]] = []
    for name, policy in tp_targets:
        all_trades: list[TradeSummary] = []
        for mint, symbol, candles in token_candles:
            trades = simulate_trades(mint, symbol, candles, position_usd, cost_pct_per_leg, policy)
            all_trades.extend(trades)

        if not all_trades:
            print(f"{name:<20} {0:>6}     --           --          --            --        --")
            continue

        pnls = [t.realised_usd for t in all_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        win_rate = 100.0 * len(wins) / len(all_trades)
        total_pnl = sum(pnls)
        avg_trade = statistics.mean(pnls)
        pf = gross_win / gross_loss if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)
        max_win = max(wins) if wins else 0.0

        print(
            f"{name:<20} {len(all_trades):>6} {win_rate:>6.1f}% "
            f"${total_pnl:>+10.2f} ${avg_trade:>+10.4f} "
            f"{pf:>13.3f} ${max_win:>+8.2f}"
        )
        summary.append(
            {"name": name, "pf": pf, "total": total_pnl, "max_win": max_win, "n": len(all_trades)}
        )

    # This block used to print three fixed "key takeaways" that the table above
    # refuted: it claimed low TPs win 40-50% (measured 33.3% and 18.2%) and that
    # "Scale 2x + Runner or 2.0x-3.0x TP provides the best balance", when those
    # rows measured 0.0% win rate and a profit factor of 0.000. A conclusion the
    # numbers contradict is worse than no conclusion, so every line is derived.
    print("\n" + "=" * 78)
    print(f"  WHAT THE SWEEP SHOWS (position ${position_usd:.2f}, {len(token_candles)} tokens)")
    print("=" * 78)
    if not summary:
        print("  No policy produced a trade; nothing can be concluded.")
        print("=" * 78)
        return 0
    best = max(summary, key=lambda row: row["pf"])
    positive = [row for row in summary if row["total"] > 0]
    print(f"  Least-bad policy   : {best['name']} at profit factor {best['pf']:.3f}")
    print(
        f"  Policies in profit : {len(positive)} of {len(summary)}"
        if positive
        else f"  Policies in profit : 0 of {len(summary)} -- every policy lost money"
    )
    doubling = [row for row in summary if row["max_win"] >= position_usd]
    if not doubling:
        print("  No policy produced a single trade that doubled the stake, so the")
        print("  take-profit level above 2x is untestable here: nothing reached it.")
    print(f"  Round-trip cost    : {rt.pct_of_position:.2f}% of position, charged both legs")
    print("  Sample is drawn from a currently-trending feed, so it is survivorship")
    print("  biased upward, and every policy's n is below the 30-trade evidence bar.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
