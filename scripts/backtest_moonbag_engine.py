#!/usr/bin/env python3
"""Asymmetric Moonbag Engine Backtester for Solana Meme Coins.

Tests the barbell shape this project's measured distribution points at: cut the
downside with a hard stop, recover the stake early, and leave an uncapped runner
so the fat tail is never sold. Structure under test:

- Tier 1 (2.0x): sell 50% -> recovers the stake, taking risk to zero
- Tier 2 (10.0x): sell 25% -> banks cash profit
- Tier 3 (moonbag): trail the last 25% with a wide structural trail
- Invalidation: -8% initial stop

**Run 2026-08-15. The verdict is UNMEASURABLE, not negative, and the reason is
structural.** The numbers it prints -- n=23, win rate 8.7%, profit factor 0.355,
expectancy -$0.0630 at $0.80, zero trades reaching 10x, highest peak 2.25x --
come from only **4 distinct mints**. The other 17 of 21 requested could not be
evaluated: 13 have fewer than 30 candles because they are minutes old, and 4
produced no momentum entry.

That is the finding worth keeping. **A three-tier exit strategy needs price
history, and the launchpad population does not have any.** The only tokens here
able to supply 30 candles are established trending movers, which are selected on
still being quoted today -- so what little sample exists is also survivorship
biased upward. Do not read the profit factor above as a measurement of the
barbell; read it as a measurement of four survivors. Do not describe this engine
as high-expectancy either: that was the original claim in this docstring, and
nothing here supports it.

To test this shape properly, point it at a population with history --
`data/cex-history.db` holds a year of candles for eight established meme pairs.

Four defects were found by running it, and are fixed rather than noted:

  * **Topic candidates passed the mint as the pool address.** CoinGecko's OHLCV
    endpoint is keyed by pool, so every narrative token returned HTTP 404 --
    verified against a live mint, not inferred. Combined with the blanket
    `except`, the entire narrative half of the population vanished silently: 12
    of 22 tokens, so every trade came from the movers feed instead. Now resolved
    through `DexScreenerProvider.deepest_pair`, as the collector already does.
  * `except Exception: continue` silently dropped any token whose fetch failed,
    so a provider error shrank `n` invisibly. Now every token lands in exactly
    one reported bucket and the buckets reconcile against the request count.
    This is the check that exposed the 404 above.
  * Costs were charged as two legs while the tiered exits make **up to four
    transactions**. Solana's fixed fee does not shrink with the order, so the
    extra sells were free in the model and are not free in reality. The fixed
    term is now charged per transaction.
  * The summary block printed fixed dollar claims and "the 25% Moonbag captures
    the 10x-100x tail" directly beneath a line reading `Trades Hitting >=10x: 0`.
    Every figure is now computed from the run.

It also reports first-entry versus re-entry by mint, because the entry detector
uses a cooldown rather than a lock and one mint can be traded repeatedly. On this
sample first entries are n=4 and re-entries n=19, which is far too lopsided to
conclude anything in either direction -- `study_reentry_value.py` settles it
against the full journal.
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass

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
from meme_flight_recorder.strategy import Candle


def to_candles(rows: list[list[float]]) -> list[Candle]:
    ordered = sorted(rows, key=lambda row: row[0])
    return [
        Candle(int(t), float(o), float(h), float(low), float(c), float(v))
        for t, o, h, low, c, v in ordered
    ]


@dataclass(frozen=True)
class MoonbagTradeResult:
    mint: str
    symbol: str
    entry_price: float
    peak_price: float
    max_multiple: float
    total_proceeds_usd: float
    total_cost_usd: float
    net_pnl_usd: float
    return_pct: float
    derisked_at_2x: bool
    harvested_at_10x: bool
    moonbag_final_multiple: float
    holding_candles: int
    exit_reason: str


def run_moonbag_trade(
    mint: str,
    symbol: str,
    candles: list[Candle],
    start_index: int,
    position_usd: float = 10.0,
    cost_pct_per_leg: float = 0.90,
    fixed_cost_per_tx_usd: float = 0.0,
) -> MoonbagTradeResult | None:
    """Replay one barbell trade.

    `fixed_cost_per_tx_usd` is charged on **every** transaction, not spread across
    two notional legs. This engine can make four -- an entry plus three tiered
    exits -- and Solana's per-signature fee is the same whatever the order size.
    Folding it into a percentage made the extra sells free, which is precisely the
    term `CLAUDE.md` says decides whether a sub-dollar position is viable at all.
    """
    if start_index >= len(candles) - 1:
        return None

    entry_candle = candles[start_index]
    entry_price = entry_candle.close
    initial_qty = position_usd / entry_price
    entry_cost = position_usd * (cost_pct_per_leg / 100.0)
    # Entry is transaction one. Each later partial exit adds another.
    fixed_paid = fixed_cost_per_tx_usd
    total_cost = position_usd + entry_cost

    remaining_qty = initial_qty
    total_proceeds = 0.0
    high_water = entry_price
    current_stop = entry_price * 0.92  # 8% stop

    derisked_at_2x = False
    harvested_at_10x = False
    moonbag_final_mult = 0.0
    exit_reason = "stop_loss_hit"

    for i in range(start_index + 1, len(candles)):
        c = candles[i]
        high_water = max(high_water, c.high)
        current_multiple = c.high / entry_price

        # 1. Stop Loss Hit
        if c.low <= current_stop:
            exit_proceeds = remaining_qty * current_stop * (1.0 - cost_pct_per_leg / 100.0)
            total_proceeds += exit_proceeds
            fixed_paid += fixed_cost_per_tx_usd  # the closing sell is its own transaction
            charged = total_cost + fixed_paid
            moonbag_final_mult = current_stop / entry_price
            exit_reason = "breakeven_stop" if derisked_at_2x else "initial_stop_hit"
            return MoonbagTradeResult(
                mint=mint,
                symbol=symbol,
                entry_price=entry_price,
                peak_price=high_water,
                max_multiple=high_water / entry_price,
                total_proceeds_usd=total_proceeds,
                total_cost_usd=charged,
                net_pnl_usd=total_proceeds - charged,
                return_pct=100.0 * (total_proceeds - charged) / charged,
                derisked_at_2x=derisked_at_2x,
                harvested_at_10x=harvested_at_10x,
                moonbag_final_multiple=moonbag_final_mult,
                holding_candles=i - start_index,
                exit_reason=exit_reason,
            )

        # 2. Tier 1: 2.0x Derisking (+100% move) -> Sell 50%
        if not derisked_at_2x and current_multiple >= 2.0:
            scale_price = entry_price * 2.0
            scale_qty = initial_qty * 0.50
            scale_proceeds = scale_qty * scale_price * (1.0 - cost_pct_per_leg / 100.0)
            total_proceeds += scale_proceeds
            remaining_qty -= scale_qty
            derisked_at_2x = True
            fixed_paid += fixed_cost_per_tx_usd  # the 50% scale-out is a transaction
            # Move stop to breakeven + fee buffer
            current_stop = entry_price * 1.05

        # 3. Tier 2: 10.0x Harvest (+900% move) -> Sell 25%
        if derisked_at_2x and not harvested_at_10x and current_multiple >= 10.0:
            scale_price = entry_price * 10.0
            scale_qty = initial_qty * 0.25
            scale_proceeds = scale_qty * scale_price * (1.0 - cost_pct_per_leg / 100.0)
            total_proceeds += scale_proceeds
            remaining_qty -= scale_qty
            harvested_at_10x = True
            fixed_paid += fixed_cost_per_tx_usd  # the 25% harvest is a transaction
            # Move stop to lock in 5x minimum on remainder
            current_stop = entry_price * 5.0

        # 4. Tier 3: Moonbag Trailing Stop for the remaining 25%
        if derisked_at_2x:
            # Trail 25% behind high water mark once above 3x
            trail_stop = high_water * 0.75
            current_stop = max(current_stop, trail_stop)

    # End of candles: mark to market final remaining quantity
    final_candle = candles[-1]
    exit_proceeds = remaining_qty * final_candle.close * (1.0 - cost_pct_per_leg / 100.0)
    total_proceeds += exit_proceeds
    fixed_paid += fixed_cost_per_tx_usd  # the final mark-to-market sell is a transaction
    charged = total_cost + fixed_paid
    moonbag_final_mult = final_candle.close / entry_price
    exit_reason = "held_to_end_moonbag" if derisked_at_2x else "held_to_end"

    return MoonbagTradeResult(
        mint=mint,
        symbol=symbol,
        entry_price=entry_price,
        peak_price=high_water,
        max_multiple=high_water / entry_price,
        total_proceeds_usd=total_proceeds,
        total_cost_usd=charged,
        net_pnl_usd=total_proceeds - charged,
        return_pct=100.0 * (total_proceeds - charged) / charged,
        derisked_at_2x=derisked_at_2x,
        harvested_at_10x=harvested_at_10x,
        moonbag_final_multiple=moonbag_final_mult,
        holding_candles=len(candles) - 1 - start_index,
        exit_reason=exit_reason,
    )


def detect_momentum_entries(candles: list[Candle], lookback: int = 16) -> list[int]:
    """Find high-conviction early volume surge breakout impulse bars (first impulse only)."""
    entry_indices: list[int] = []
    cooldown = 0

    for i in range(lookback + 1, len(candles)):
        if cooldown > 0:
            cooldown -= 1
            continue

        curr = candles[i]
        prior = candles[i - lookback : i]
        avg_vol = sum(c.volume for c in prior) / len(prior)
        recent_high = max(c.high for c in prior)

        is_break = curr.close > curr.open and curr.close >= recent_high
        is_vol_surge = curr.volume >= 2.5 * max(avg_vol, 1.0)
        candle_pct = (curr.close - curr.open) / curr.open
        not_overextended = 0.03 <= candle_pct <= 0.25

        if is_break and is_vol_surge and not_overextended:
            entry_indices.append(i)
            # Enforce 24-candle cooldown to avoid buying the chop tail of an exhausted pump
            cooldown = 24

    return entry_indices



def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest Asymmetric Moonbag Engine")
    parser.add_argument("--position-usd", type=float, default=10.0)
    parser.add_argument("--tokens", type=int, default=20)
    parser.add_argument("--timeframe", choices=["15m", "1h", "4h", "1d"], default="1h")
    args = parser.parse_args()

    settings = load_settings()
    coingecko = CoinGeckoProvider()
    binance = BinanceWeb3Provider()
    dexscreener = DexScreenerProvider()

    rt = round_trip_cost(args.position_usd, settings.costs)
    # Split the two terms rather than averaging them into one percentage. The
    # proportional part scales with the order; the fixed part does not, and this
    # engine can fire four transactions instead of two.
    proportional_pct_per_leg = (
        100.0 * rt.proportional_usd / args.position_usd / 2.0 if args.position_usd > 0 else 0.0
    )
    cost_pct_per_leg = proportional_pct_per_leg
    fixed_cost_per_tx_usd = settings.costs.fixed_cost_per_leg_usd

    print("=" * 80)
    print("  ASYMMETRIC MOONBAG TRADING ENGINE BACKTEST")
    print(f"  Position Size: ${args.position_usd:.2f} per trade")
    print(f"  Cost Friction: ${rt.total_usd:.4f} ({rt.pct_of_position:.2f}% per round trip)")
    print(f"    proportional {proportional_pct_per_leg:.4f}%/leg"
          f" + fixed ${fixed_cost_per_tx_usd:.4f}/transaction")
    print("  Strategy Architecture:")
    print(f"    - Initial Stop: -8.0% (risk capped near ${args.position_usd * 0.08:.4f})")
    print(f"    - 2.0x: sell 50% -> ~${args.position_usd:.2f} stake recovered")
    print(f"    - 10.0x: sell 25% -> banks ~${args.position_usd * 2.5:.2f}")
    print("    - 25% moonbag: trailed, uncapped")
    print("  Up to 4 transactions per trade, each paying the fixed fee.")
    print("=" * 80)

    # 1. Multi-feed candidate collection with Copycat Filtering
    print("\nScanning active canonical Solana tokens across discovery feeds...")
    topic_filter = TopicCandidateFilter(
        minimum_liquidity_usd=20_000.0,
        minimum_unique_traders_1h=10,
        filter_copycats=True,
    )
    topics = binance.topic_narratives(chain_id="CT_501")
    topic_candidates, topic_skips = select_topic_candidates(topics, topic_filter)

    movers, mover_skips = select_movers(coingecko.trending_solana_pools())
    movers = [m for m in movers if m.liquidity_usd and m.liquidity_usd >= 20_000.0]

    print(f"Discovered {len(topic_candidates)} canonical topic tokens (rejected {topic_skips.get('copycat_clone_rejected', 0)} fake clones).")
    print(f"Discovered {len(movers)} established deep mover pools.")

    # Combine candidates
    token_mints: list[tuple[str, str, str]] = []  # (mint, symbol, pool_address)
    # Topic candidates carry a mint, not a pool. CoinGecko's OHLCV endpoint is
    # keyed by **pool**, so the previous version -- which passed the mint as the
    # pool address -- returned HTTP 404 for every single narrative token. With the
    # old blanket `except: continue` that was silent, and the whole narrative half
    # of this backtest's population vanished without trace: 12 of 22 tokens, so
    # every reported trade came from the trending-movers feed instead. Resolve the
    # deepest pool per mint, exactly as preflight and the collector already do.
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

    print(f"\nFetching {args.timeframe} historical candle sequences for {len(token_mints)} tokens...\n")

    tf_map = {
        "15m": ("minute", 15),
        "1h": ("hour", 1),
        "4h": ("hour", 4),
        "1d": ("day", 1),
    }
    timeframe_arg, agg = tf_map[args.timeframe]
    all_trades: list[MoonbagTradeResult] = []

    # Every token lands in exactly one bucket, and the buckets must sum to the
    # tokens requested. The original loop ended in `except Exception: continue`,
    # which silently dropped any token whose fetch or parse failed -- so a rate
    # limit shrank `n` with nobody able to tell, and the run reported a smaller
    # sample as though that were the whole population. A provider failure is not
    # a token without setups.
    outcomes: dict[str, int] = {}

    def tally(reason: str) -> None:
        outcomes[reason] = outcomes.get(reason, 0) + 1

    for mint, symbol, pool_addr in token_mints:
        try:
            payload = coingecko.pool_ohlcv(pool_addr, aggregate=agg, limit=1000, timeframe=timeframe_arg)
        except Exception as error:  # noqa: BLE001 - transport failure is not evidence about the token
            tally(f"fetch_failed:{type(error).__name__}")
            continue
        candles = to_candles((payload.get("data") or {}).get("attributes", {}).get("ohlcv_list") or [])
        if len(candles) < 30:
            tally("too_few_candles")
            continue

        entry_indices = detect_momentum_entries(candles)
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
                candles,
                start_index=idx,
                position_usd=args.position_usd,
                cost_pct_per_leg=cost_pct_per_leg,
                fixed_cost_per_tx_usd=fixed_cost_per_tx_usd,
            )
            if trade is not None:
                all_trades.append(trade)
                opened += 1
                blocked_until = idx + trade.holding_candles
        tally("entered" if opened else "entry_unfillable")

    print("=== token reconciliation ===")
    for reason, count in sorted(outcomes.items()):
        print(f"  {reason:<32}{count:>5}")
    print(f"  {'total':<32}{sum(outcomes.values()):>5} of {len(token_mints)} requested")
    if sum(outcomes.values()) != len(token_mints):
        print("  MISMATCH: tokens requested do not reconcile against outcomes")
    if mover_skips:
        print(f"  mover feed skips: {dict(mover_skips)}")
    if topic_skips:
        print(f"  topic feed skips: {dict(topic_skips)}")

    if not all_trades:
        print("No completed trades found.")
        return 0

    pnls = [t.net_pnl_usd for t in all_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total_pnl = sum(pnls)
    win_rate = 100.0 * len(wins) / len(all_trades)
    pf = gross_win / gross_loss if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)

    derisked_count = sum(1 for t in all_trades if t.derisked_at_2x)
    harvested_10x_count = sum(1 for t in all_trades if t.harvested_at_10x)
    max_mult_overall = max(t.max_multiple for t in all_trades) if all_trades else 1.0

    print("=" * 80)
    print("  BACKTEST PERFORMANCE RESULTS")
    print("=" * 80)
    print(f"  Total Trades Completed : {len(all_trades)}")
    print(f"  Win Rate               : {win_rate:.1f}% ({len(wins)} wins / {len(losses)} losses)")
    print(f"  Total Realized Net P&L : ${total_pnl:+.2f}")
    print(f"  Average Trade P&L      : ${statistics.mean(pnls):+.4f}")
    print(f"  Profit Factor          : {pf:.3f}")
    print(f"  Trades Derisked at 2x  : {derisked_count} ({100*derisked_count/len(all_trades):.1f}%)")
    print(f"  Trades Hitting >=10x   : {harvested_10x_count}")
    print(f"  Highest Peak Multiple  : {max_mult_overall:.2f}x")
    if wins:
        print(f"  Largest Winning Trade  : ${max(wins):+.2f} (+{(max(wins)/args.position_usd)*100:.1f}%)")
        print(f"  Average Winning Trade  : ${statistics.mean(wins):+.2f}")
    if losses:
        print(f"  Largest Loss           : ${min(losses):+.2f}")
        print(f"  Average Loss           : ${statistics.mean(losses):+.2f} (Capped by -8% stop)")

    print("\nSAMPLE EXECUTED MOONBAG TRADES:")
    print(f"{'Symbol':<10} {'Max Peak':>9} {'2x Derisk':>10} {'10x Hit':>8} {'Net PnL':>10} {'Return %':>10} {'Exit Reason':<20}")
    print("-" * 80)
    for t in sorted(all_trades, key=lambda x: x.net_pnl_usd, reverse=True)[:10]:
        print(
            f"{t.symbol:<10} {t.max_multiple:>8.2f}x "
            f"{'YES' if t.derisked_at_2x else 'NO':>10} "
            f"{'YES' if t.harvested_at_10x else 'NO':>8} "
            f"${t.net_pnl_usd:>+9.2f} {t.return_pct:>+9.1f}% {t.exit_reason:<20}"
        )

    # What this block used to be, and why it is now computed. It printed three
    # fixed claims -- that the stop caps losses at ~$0.80, that the 2x double
    # returns "your $10 stake", and that "the 25% Moonbag captures the 10x-100x
    # tail, generating massive asymmetry!" -- directly beneath a table reporting
    # `Trades Hitting >=10x : 0`. A conclusion the numbers above it contradict is
    # the worst defect a measurement tool can have, and the dollar figures
    # ignored --position-usd entirely. Every line below is now derived.
    # Re-entry exposure. `detect_momentum_entries` uses a cooldown, not a lock,
    # so one mint can be entered repeatedly -- the "double dipping" a farm token
    # invites by pumping, dumping and pumping again. Reported first-versus-repeat
    # because the gate being designed against this needs a number, not an
    # impression. Note this counts by mint: ticker collision is routine here, so
    # two rows sharing a symbol are not necessarily the same token.
    by_mint: dict[str, list[float]] = {}
    for trade in sorted(all_trades, key=lambda t: t.holding_candles):
        by_mint.setdefault(trade.mint, []).append(trade.net_pnl_usd)
    first_entries = [pnls_[0] for pnls_ in by_mint.values()]
    repeat_entries = [p for pnls_ in by_mint.values() for p in pnls_[1:]]
    print("\n=== first entry versus re-entry, by mint ===")
    print(f"  distinct mints traded : {len(by_mint)}")
    for label, group in (("first entries", first_entries), ("re-entries", repeat_entries)):
        if not group:
            print(f"  {label:<22} none")
            continue
        wins_n = sum(1 for value in group if value > 0)
        print(
            f"  {label:<22} n={len(group):<4} win {100.0 * wins_n / len(group):>5.1f}%"
            f"  mean ${statistics.mean(group):+.4f}  total ${sum(group):+.2f}"
        )

    print("\n" + "=" * 80)
    print("  WHAT THE NUMBERS SAY")
    print("=" * 80)
    ranked = sorted(pnls, reverse=True)
    without_best = statistics.mean(ranked[1:]) if len(ranked) > 1 else None
    print(
        f"  Loss cap held        : largest loss ${min(losses):+.4f} on a "
        f"${args.position_usd:.2f} position"
        if losses
        else "  Loss cap             : no losing trades in this sample"
    )
    print(
        f"  Stake recovery fired : {derisked_count} of {len(all_trades)} trades reached 2x"
    )
    print(
        f"  Moonbag fired        : {harvested_10x_count} of {len(all_trades)} trades reached 10x;"
        f" highest peak seen {max_mult_overall:.2f}x"
    )
    if harvested_10x_count == 0:
        print(
            "  -> The moonbag tier never fired. The asymmetry this engine exists to"
        )
        print(
            "     capture is unobserved in this sample, so nothing here demonstrates it."
        )
    if without_best is not None:
        print(
            f"  Expectancy           : ${statistics.mean(pnls):+.4f}/trade;"
            f" ${without_best:+.4f} after deleting the single best trade"
        )
    print(
        f"  Verdict              : {'positive' if total_pnl > 0 else 'NEGATIVE'}"
        f" at n={len(all_trades)}, profit factor {pf:.3f}"
    )
    print("  Sample is drawn from currently-quoted trending feeds, so it carries a")
    print("  survivorship bias upward and n is far below the 30-trade evidence bar.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
