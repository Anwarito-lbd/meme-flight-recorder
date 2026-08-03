#!/usr/bin/env python3
"""The forward paper track record, and how far it is from the live gate.

Every expectancy figure this project has produced so far is backward-looking:
drawn from tokens sampled after their outcomes were known. This reads the only
numbers that are not -- positions actually opened forward, in sequence, without
knowing what happened next.

It also states plainly how far the record is from the evidence gate, because the
gate is computed rather than judged and there is no value in guessing at it.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import statistics

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.position_store import closed_positions, open_positions

# The conditions that must all hold before live execution is even discussable.
REQUIRED_TRADES = 30
REQUIRED_PROFIT_FACTOR = 1.2
MAXIMUM_TOP_TRADE_SHARE = 1 / 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    closed = closed_positions(recorder)
    live = open_positions(recorder)

    print(f"open positions   {len(live)}")
    for mint, position in live.items():
        held = position.opened_at.isoformat(timespec="minutes")
        print(f"  {position.symbol or mint[:8]:<12} opened {held}  entry {position.entry_price:.3e}")

    print(f"\nclosed trades    {len(closed)}")
    if not closed:
        print("\nNo completed forward trades yet. Nothing here is a result.")
        print("Run: cli collect --paper-trade")
        return 0

    results = [position.realised_usd for position in closed]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    factor = gross_win / gross_loss if gross_loss > 0 else None
    top_share = (max(wins) / gross_win) if wins and gross_win > 0 else 0.0

    print(f"win rate         {100 * len(wins) / len(closed):.1f}%")
    print(f"total P&L        ${sum(results):+.2f}")
    print(f"expectancy       ${statistics.mean(results):+.4f} per trade")
    print(f"median trade     ${statistics.median(results):+.4f}")
    print(f"profit factor    {factor:.3f}" if factor is not None else "profit factor    n/a (no losses yet)")
    if wins:
        print(f"largest win      ${max(wins):+.2f}  ({100 * top_share:.0f}% of gross profit)")
    if losses:
        print(f"largest loss     ${min(losses):+.2f}")

    reasons: dict[str, int] = {}
    for position in closed:
        key = position.close_reason.value if position.close_reason else "unknown"
        reasons[key] = reasons.get(key, 0) + 1
    print("\nexit reasons")
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"  {count:>4}  {reason}")

    print("\nevidence gate")
    checks = [
        (f">= {REQUIRED_TRADES} complete trades", len(closed) >= REQUIRED_TRADES,
         f"{len(closed)}"),
        ("positive expectancy after costs", statistics.mean(results) > 0,
         f"${statistics.mean(results):+.4f}"),
        (f"profit factor > {REQUIRED_PROFIT_FACTOR}",
         factor is not None and factor > REQUIRED_PROFIT_FACTOR,
         f"{factor:.3f}" if factor is not None else "n/a"),
        ("no trade > 1/3 of profit", top_share <= MAXIMUM_TOP_TRADE_SHARE,
         f"{100 * top_share:.0f}%"),
    ]
    for label, passed, value in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label:<34} {value}")

    if all(passed for _label, passed, _value in checks):
        print("\nEvery condition is met on this sample. That is not permission to")
        print("trade live -- it is permission to have the conversation, with a")
        print("human deciding and a sample this small treated as provisional.")
    else:
        print("\nThe gate is not met. Nothing about live execution should change.")
        print("Failing it is not a reason to look for a more aggressive strategy;")
        print("it is a reason to keep collecting until the answer is unambiguous.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
