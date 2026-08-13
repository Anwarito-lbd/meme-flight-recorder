#!/usr/bin/env python3
"""Prove the paper book actually opens, marks and closes a position on live data.

The book has never opened anything, so "it works" has never been demonstrated --
only asserted by unit tests. This drives `PositionMonitor` against **real live
candidates** and shows the whole cycle: open, mark against a live price, evaluate
the exit rules, close, and write the PnL to a journal that can be read back.

Two deliberate constraints, and both matter:

**It writes to its own database.** `data/paper_smoke.db`, never the production
journal. The forward record is the thing a real strategy will eventually be
judged on, and salting it with demonstration trades opened under a deliberately
permissive policy would destroy that. The evidence gate counts *complete forward
trades*; these are not those.

**The permissive policy is the point, and it is confined here.** The production
`MonitorConfig` admits no lifecycle state at all, because no band has produced
positive held-out expectancy after costs. This script overrides that to show the
machinery functions. It does not change what the live book will do, and the
safety verdict is *not* relaxed -- FAIL and UNKNOWN still cannot open, because
`readiness.assess` blocks them structurally and no configuration can admit them.

Paper only. No key, nothing signed, nothing broadcast.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.discovery import MoverFilter, select_movers
from meme_flight_recorder.env import load_env
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.models import CandidateStatus
from meme_flight_recorder.monitor import MonitorConfig, PositionMonitor
from meme_flight_recorder.position_store import closed_positions, open_positions
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider
from meme_flight_recorder.readiness import LifecycleState, ReadinessPolicy

SMOKE_DB = Path("data/paper_smoke.db")
STRATEGY_ID = "smoke_v1"


def live_candidates(minimum_liquidity: float, limit: int) -> list[dict[str, Any]]:
    """Real pools, right now, deep enough that a $0.80 position is plausible."""
    from meme_flight_recorder.providers.coingecko import CoinGeckoProvider

    pools, skipped = select_movers(
        CoinGeckoProvider().trending_solana_pools(),
        MoverFilter(
            minimum_liquidity_usd=minimum_liquidity,
            minimum_pool_age_minutes=60.0,
        ),
    )
    print(f"  select_movers kept {len(pools)}, skipped {skipped}")
    rows: list[dict[str, Any]] = []
    for pool in pools[: limit * 3]:
        mint = pool.mint
        price = pool.price_usd
        liquidity = pool.liquidity_usd
        if not mint or not price or not liquidity:
            continue
        rows.append(
            {
                "mint": str(mint),
                "symbol": str(pool.symbol or ""),
                # The book still checks failures and safety verdict itself; this
                # only says "no hard failure was recorded", which for a trending
                # pool with real depth is the honest starting point.
                "status": CandidateStatus.MONITOR.value,
                "failures": [],
                "price_usd": float(price),
                "liquidity_usd": float(liquidity),
                "age_minutes": pool.pool_age_minutes,
                "stage": "mover",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-liquidity", type=float, default=50_000.0)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--reset", action="store_true", help="Start from an empty smoke book.")
    parser.add_argument(
        "--force-exit",
        action="store_true",
        help="Set the hold limit to zero so the exit path runs and PnL is realised.",
    )
    parser.add_argument(
        "--position-usd",
        type=float,
        default=None,
        help="Fixed dollar size, overriding the measured 2%-of-equity rule.",
    )
    parser.add_argument("--take-profit", type=float, default=0.0, help="e.g. 2.0 sells at 2x.")
    parser.add_argument("--stop-loss", type=float, default=0.0, help="e.g. 50 sells at -50%%.")
    parser.add_argument("--hold-minutes", type=float, default=10_080.0)
    arguments = parser.parse_args()

    load_env()
    settings = load_settings()
    SMOKE_DB.parent.mkdir(exist_ok=True)
    if arguments.reset and SMOKE_DB.exists():
        SMOKE_DB.unlink()

    book = FlightRecorder(SMOKE_DB)
    provider = DexScreenerProvider()

    config = MonitorConfig(
        minimum_pool_liquidity_usd=arguments.minimum_liquidity,
        maximum_open_positions=5,
        maximum_hold_minutes=0 if arguments.force_exit else int(arguments.hold_minutes),
        position_usd_override=arguments.position_usd,
        take_profit_multiple=arguments.take_profit,
        stop_loss_pct=arguments.stop_loss,
        # Permissive *maturity* only. Safety is untouched and unrelaxable.
        readiness_policy=ReadinessPolicy(entry_states=frozenset(LifecycleState)),
    )
    monitor = PositionMonitor(settings, book, provider, config=config)

    size_text = (
        f"${arguments.position_usd:.2f} fixed"
        if arguments.position_usd is not None
        else f"{settings.micro.position_pct_of_equity}% of equity"
    )
    probe = round_trip_cost(
        arguments.position_usd
        if arguments.position_usd is not None
        else settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0,
        settings.costs,
    )
    print(f"paper smoke test -- writes to {SMOKE_DB}, never the production journal")
    print(f"strategy id: {STRATEGY_ID}   paper only, no key loaded")
    print(
        f"size: {size_text}   round trip {probe.pct_of_position:.3f}%   "
        f"breakeven {probe.breakeven_multiple:.4f}x"
    )
    if arguments.take_profit or arguments.stop_loss:
        print(
            f"exits: take profit {arguments.take_profit or 'off'}x   "
            f"stop loss {arguments.stop_loss or 'off'}%   hold {arguments.hold_minutes:.0f}m"
        )
    if arguments.position_usd and arguments.position_usd > settings.starting_equity_usd * 0.05:
        share = 100.0 * arguments.position_usd / settings.starting_equity_usd
        print(
            f"WARNING: ${arguments.position_usd:.2f} is {share:.0f}% of ${settings.starting_equity_usd:.0f} "
            f"equity. The measured growth-optimal fraction on this distribution is 2%; "
            f"10% compounds to 25% of starting equity over 50 trades."
        )
    print()

    print("fetching real trending pools ...")
    candidates = live_candidates(arguments.minimum_liquidity, arguments.limit)
    print(f"  {len(candidates)} live candidates with price and depth")
    for row in candidates:
        print(
            f"    {row['symbol']:<10}{row['mint'][:16]:<18}"
            f"${row['liquidity_usd']:>12,.0f}  ${row['price_usd']:.8g}"
        )
    if not candidates:
        print("\nno live candidates deep enough -- nothing to demonstrate")
        return 1

    print("\n--- cycle 1: manage existing, then consider entries ---")
    summary = monitor.run_cycle(candidates)
    print(f"  considered={summary.considered} opened={summary.opened} closed={summary.closed}")
    print(f"  still_open={summary.still_open}")
    for reason, count in sorted(summary.skipped.items(), key=lambda item: -item[1]):
        print(f"    skipped {reason:<44}{count}")
    for error in summary.errors[:5]:
        print(f"    error: {error}")

    live = open_positions(book)
    print(f"\n--- open positions: {len(live)} ---")
    for mint, position in live.items():
        print(
            f"  {mint[:16]:<18}entry=${position.entry_price:.8g} "
            f"qty={position.quantity:.6g} "
            f"notional=${position.entry_price * position.quantity:.2f} "
            f"pool=${position.entry_liquidity_usd or 0:,.0f} fills={len(position.fills)}"
        )

    print("\n--- cycle 2: re-mark the same positions against live prices ---")
    summary2 = monitor.run_cycle(candidates)
    print(f"  considered={summary2.considered} opened={summary2.opened} closed={summary2.closed}")
    print(f"  still_open={summary2.still_open}")

    closed = closed_positions(book)
    print(f"\n--- closed positions: {len(closed)} ---")
    if closed:
        print(
            f"  {'symbol':<10}{'entry':<14}{'exit':<14}{'gross':>8}{'fees $':>9}"
            f"{'net $':>9}{'reason':>22}"
        )
        total_net = 0.0
        total_fees = 0.0
        for record in closed:
            entry_fill = record.fills[0]
            exit_fill = record.fills[-1]
            notional = entry_fill.price * entry_fill.quantity
            gross = exit_fill.price / entry_fill.price if entry_fill.price else 0.0
            fees = entry_fill.cost_usd + exit_fill.cost_usd
            net = notional * gross - notional - fees
            total_net += net
            total_fees += fees
            print(
                f"  {record.symbol:<10}{entry_fill.price:<14.8g}{exit_fill.price:<14.8g}"
                f"{gross:>8.4f}{fees:>9.4f}{net:>9.4f}"
                f"{record.close_reason.value if record.close_reason else '':>22}"
            )
        wins = [1 for r in closed if r.fills[-1].price > r.fills[0].price]
        print(
            f"  {'TOTAL':<10}{'':<28}{'':>8}{total_fees:>9.4f}{total_net:>9.4f}"
            f"{len(closed):>10} trades"
        )
        print(
            f"  win rate {100.0 * len(wins) / len(closed):.1f}%   "
            f"fees are {100.0 * total_fees / max(1e-9, abs(total_net)):.0f}% of the loss"
        )

    print("\n--- journal ---")
    # PositionMonitor writes the cohort ledger, which is a deliberately
    # separate event family from PaperBroker's: STATUS.md records that reusing
    # one name for two payload shapes would break replay.
    events = book.events_by_type("cohort_position_opened") + book.events_by_type(
        "cohort_position_closed"
    )
    print(f"  {len(events)} paper position events written")
    print(f"  hash chain intact: {book.verify_chain()}")

    artifact = Path("artifacts/paper_smoke_test.json")
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "strategy_id": STRATEGY_ID,
                "database": str(SMOKE_DB),
                "candidates": candidates,
                "opened": summary.opened,
                "still_open": summary.still_open,
                "skipped": summary.skipped,
                "open_positions": {
                    mint: {
                        "entry_price": position.entry_price,
                        "quantity": position.quantity,
                        "notional_usd": position.entry_price * position.quantity,
                        "entry_liquidity_usd": position.entry_liquidity_usd,
                    }
                    for mint, position in live.items()
                },
                "chain_intact": book.verify_chain(),
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"  wrote {artifact}")

    if summary.opened == 0 and not live:
        print("\nRESULT: no position opened -- see skip reasons above")
        return 1
    print("\nRESULT: the paper book opens, sizes, marks and journals real positions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
