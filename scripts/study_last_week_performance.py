#!/usr/bin/env python3
"""Calculate exact trading performance over the last 7 days (past week).

Replays 7 days of historical candles across active canonical Solana meme tokens with:
- Position size: the configured growth-optimal fraction (override with --position-usd)
- Realistic round-trip costs (Solana gas $0.0315 + DEX fee 0.30% + impact)
- 2x derisking (stake returned) + trailing moonbag
- Strict -8% invalidation stop
"""

from __future__ import annotations

import argparse
import statistics
from datetime import UTC, datetime, timedelta

from backtest_moonbag_engine import (
    MoonbagTradeResult,
    detect_momentum_entries,
    run_moonbag_trade,
    to_candles,
)

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.discovery import (
    TopicCandidateFilter,
    select_movers,
    select_topic_candidates,
)
from meme_flight_recorder.providers.binance_web3 import BinanceWeb3Provider
from meme_flight_recorder.providers.coingecko import CoinGeckoProvider
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider


def main() -> int:
    settings = load_settings()
    dexscreener = DexScreenerProvider()
    coingecko = CoinGeckoProvider()
    binance = BinanceWeb3Provider()

    # Was hardcoded at $10.00, which is 25% of this account's $40 equity -- 12.5x
    # the growth-optimal 2% that `config/default.toml` now carries, and squarely in
    # the over-Kelly band STATUS.md measured at -0.0274 growth per trade. Defaults
    # to the configured fraction so a study cannot quietly report results at a size
    # the project has already rejected.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--position-usd",
        type=float,
        default=round(
            settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0, 4
        ),
    )
    args = parser.parse_args()
    position_usd = args.position_usd
    rt = round_trip_cost(position_usd, settings.costs)
    cost_pct_per_leg = rt.pct_of_position / 2.0

    print("=" * 80)
    print("  EXACT 7-DAY (PAST WEEK) MEME COIN TRADING PERFORMANCE")
    print(f"  Position Sizing: ${position_usd:.2f} per trade")
    print(f"  Round-Trip Friction: ${rt.total_usd:.4f} ({rt.pct_of_position:.2f}% per trade)")
    print("  Time Window: Past 7 Days (168 hourly periods)")
    print("=" * 80)

    # 1. Discover canonical tokens
    topic_filter = TopicCandidateFilter(
        minimum_liquidity_usd=20_000.0,
        minimum_unique_traders_1h=10,
        filter_copycats=True,
    )
    topics = binance.topic_narratives(chain_id="CT_501")
    topic_candidates, topic_skips = select_topic_candidates(topics, topic_filter)

    movers, _ = select_movers(coingecko.trending_solana_pools())
    movers = [m for m in movers if m.liquidity_usd and m.liquidity_usd >= 20_000.0]

    # Topic candidates carry a mint, and CoinGecko's OHLCV endpoint is keyed by
    # pool. Passing the mint returned HTTP 404 for every narrative token -- the
    # same defect as in `backtest_moonbag_engine.py`, and equally silent under the
    # blanket `except` below.
    token_mints: list[tuple[str, str, str]] = []
    unresolved = 0
    for tc in topic_candidates[:12]:
        pair = dexscreener.deepest_pair(tc.mint)
        if pair is None or not pair.pair_address:
            unresolved += 1
            continue
        token_mints.append((tc.mint, tc.symbol, pair.pair_address))
    if unresolved:
        print(f"  {unresolved} topic candidate(s) had no quoted pool and were not requested")
    for m in movers[:12]:
        if m.mint not in [t[0] for t in token_mints]:
            token_mints.append((m.mint, m.symbol, m.pool_address))

    print(f"\nEvaluating {len(token_mints)} canonical Solana meme tokens over the last 7 days...\n")

    week_trades: list[MoonbagTradeResult] = []
    seven_days_ago_ts = int((datetime.now(UTC) - timedelta(days=7)).timestamp())

    # Every token lands in exactly one bucket and the buckets reconcile against the
    # request count, so a provider failure can no longer shrink the sample unseen.
    outcomes: dict[str, int] = {}

    def tally(reason: str) -> None:
        outcomes[reason] = outcomes.get(reason, 0) + 1

    for mint, symbol, pool_addr in token_mints:
        try:
            payload = coingecko.pool_ohlcv(pool_addr, aggregate=1, limit=500, timeframe="hour")
        except Exception as error:  # noqa: BLE001 - transport failure is not evidence about the token
            tally(f"fetch_failed:{type(error).__name__}")
            continue
        all_candles = to_candles((payload.get("data") or {}).get("attributes", {}).get("ohlcv_list") or [])

        # Slice strictly the last 7 days
        week_candles = [c for c in all_candles if c.timestamp >= seven_days_ago_ts]
        if len(week_candles) < 12:
            tally("too_few_candles_in_window")
            continue

        entry_indices = detect_momentum_entries(week_candles, lookback=12)
        if not entry_indices:
            tally("no_momentum_entry")
            continue

        opened = 0
        blocked_until = -1
        for idx in entry_indices:
            if idx <= blocked_until:
                continue
            trade = run_moonbag_trade(
                mint,
                symbol,
                week_candles,
                start_index=idx,
                position_usd=position_usd,
                cost_pct_per_leg=cost_pct_per_leg,
                fixed_cost_per_tx_usd=settings.costs.fixed_cost_per_leg_usd,
            )
            if trade is not None:
                week_trades.append(trade)
                opened += 1
                blocked_until = idx + trade.holding_candles
        tally("entered" if opened else "entry_unfillable")

    print("=== token reconciliation ===")
    for reason, count in sorted(outcomes.items()):
        print(f"  {reason:<32}{count:>5}")
    print(f"  {'total':<32}{sum(outcomes.values()):>5} of {len(token_mints)} requested")
    if sum(outcomes.values()) != len(token_mints):
        print("  MISMATCH: tokens requested do not reconcile against outcomes")
    if topic_skips:
        print(f"  topic feed skips: {dict(topic_skips)}")

    if not week_trades:
        print("No completed trades triggered in the last 7 days.")
        return 0

    pnls = [t.net_pnl_usd for t in week_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total_pnl = sum(pnls)
    win_rate = 100.0 * len(wins) / len(week_trades)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)

    print("=" * 80)
    print("  LAST WEEK'S TRADING SUMMARY")
    print("=" * 80)
    print(f"  Total Trades Taken     : {len(week_trades)}")
    print(f"  Wins / Losses          : {len(wins)} Wins / {len(losses)} Losses ({win_rate:.1f}% Win Rate)")
    label = "net profit" if total_pnl > 0 else "NET LOSS"
    print(f"  Total Realized Net P&L : ${total_pnl:+.2f}  ({label}, after gas and DEX fees)")
    print(f"  Profit Factor          : {pf:.3f}")
    print(f"  Average Trade P&L      : ${statistics.mean(pnls):+.4f}")
    if wins:
        print(f"  Largest Win            : ${max(wins):+.2f} (+{(max(wins)/position_usd)*100:.1f}%)")
        print(f"  Average Win            : ${statistics.mean(wins):+.2f}")
    if losses:
        print(f"  Largest Loss           : ${min(losses):+.2f}")
        print(f"  Average Loss           : ${statistics.mean(losses):+.2f} (Capped by -8% stop)")

    print("\nALL TRADES TAKEN IN THE LAST 7 DAYS:")
    print(f"{'Symbol':<10} {'Peak Mult':>10} {'2x Derisk':>10} {'Net PnL':>11} {'Return %':>11} {'Exit Reason':<20}")
    print("-" * 80)
    for t in sorted(week_trades, key=lambda x: x.net_pnl_usd, reverse=True):
        print(
            f"{t.symbol:<10} {t.max_multiple:>9.2f}x "
            f"{'YES' if t.derisked_at_2x else 'NO':>10} "
            f"${t.net_pnl_usd:>+10.2f} {t.return_pct:>+10.1f}% {t.exit_reason:<20}"
        )

    print("\n" + "=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
