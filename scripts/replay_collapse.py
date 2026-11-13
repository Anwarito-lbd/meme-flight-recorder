#!/usr/bin/env python3
"""Replay a real token collapse through the exit engine.

A backtest that only sees winners teaches nothing. This harness takes a token
that actually went to zero, walks its real candles through the exit rules one at
a time, and reports where the position would have been closed and what it would
have cost.

The point is not to show the system escaping unharmed. It is to find out how
large the loss is on the worst case that has actually occurred, because that
number -- not the win rate -- is what determines whether an account survives long
enough for an edge to matter.

Read-only. Fetches public price history and simulates. Nothing is traded.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from meme_flight_recorder.config import ExitLimits, load_settings
from meme_flight_recorder.exits import (
    PositionObservation,
    PositionView,
    evaluate_exit,
    realistic_fill_price,
)
from meme_flight_recorder.providers.coingecko import CoinGeckoProvider

# The CATE that reached MONITOR and then went to zero.
DEAD_CATE = "9SNEJJGhpVVSmj8vJp2pxdn5prUtoJ7iZetbisNZpump"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mint", default=DEAD_CATE)
    parser.add_argument("--entry", type=float, default=0.05268, help="Observed entry price.")
    parser.add_argument("--stop-pct", type=float, default=10.0)
    parser.add_argument("--level-pct", type=float, default=5.0)
    parser.add_argument("--position-usd", type=float, default=10.0)
    parser.add_argument("--aggregate", type=int, default=5, help="Candle minutes.")
    arguments = parser.parse_args()

    load_settings()
    provider = CoinGeckoProvider()

    pools = (provider.token_pools(arguments.mint).get("data") or [])
    if not pools:
        print("No pool found for that mint.")
        return 1
    pool = pools[0]["attributes"]
    entry_liquidity = float(pool.get("reserve_in_usd") or 0.0)

    payload = provider.pool_ohlcv(
        pool["address"], aggregate=arguments.aggregate, limit=1000
    )
    candles = (payload.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
    if not candles:
        print("No candle history available.")
        return 1
    # Provider returns newest first; replay must run forward in time.
    candles = sorted(candles, key=lambda row: row[0])

    entry = arguments.entry
    stop = entry * (1 - arguments.stop_pct / 100)
    level = entry * (1 - arguments.level_pct / 100)
    opened_at = datetime.fromtimestamp(candles[0][0], tz=UTC)

    position = PositionView(
        mint=arguments.mint,
        opened_at=opened_at,
        entry_price=entry,
        stop_price=stop,
        breakout_level=level,
        entry_liquidity_usd=entry_liquidity,
        peak_liquidity_usd=entry_liquidity,
    )
    limits = ExitLimits()

    print(f"replaying {arguments.mint[:16]}..  {len(candles)} candles "
          f"@ {arguments.aggregate}m")
    print(f"entry ${entry:.8f}  stop ${stop:.8f}  level ${level:.8f}  "
          f"position ${arguments.position_usd:.2f}\n")

    quantity = arguments.position_usd / entry
    for timestamp, _open, _high, low, close, _volume in candles:
        moment = datetime.fromtimestamp(timestamp, tz=UTC)
        # Use the candle low: within a candle, the worst price is reached before
        # the close, and an exit rule that only ever sees closes will report
        # fills it could not have achieved.
        decision = evaluate_exit(
            position,
            PositionObservation(observed_at=moment, price_usd=low),
            limits,
        )
        if decision.should_exit:
            fill = realistic_fill_price(low, exit_impact_pct=3.0, urgent=decision.urgent)
            value = quantity * fill
            pnl = value - arguments.position_usd
            print(f"EXIT at {moment:%H:%M:%S}  {decision.reason.value}")
            print(f"  candle low   ${low:.10f}")
            print(f"  modelled fill ${fill:.10f}  (impact and urgency applied)")
            print(f"  position value ${value:.2f} from ${arguments.position_usd:.2f}")
            print(f"  realised P&L  ${pnl:+.2f}  ({100 * pnl / arguments.position_usd:+.1f}%)")
            print(f"  urgent: {decision.urgent}   notes: {', '.join(decision.notes) or '-'}")
            break
        position = PositionView(
            **{
                **position.__dict__,
                "high_water_price": max(position.high_water_price, close),
            }
        )
    else:
        final = candles[-1][4]
        value = quantity * final
        print("NO EXIT FIRED -- held to the end of available history")
        print(f"  final price ${final:.10f}")
        print(f"  position value ${value:.4f} from ${arguments.position_usd:.2f}")

    worst = min(row[3] for row in candles)
    print(f"\nfor comparison, holding to the bottom (${worst:.10f}) would have left "
          f"${quantity * worst:.4f} of ${arguments.position_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
