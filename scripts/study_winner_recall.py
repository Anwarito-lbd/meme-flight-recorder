#!/usr/bin/env python3
"""Did this system see the winners, and what did it say about them?

The stated goal has always been to catch early winners. Every study so far has
asked the opposite question -- given a verdict, what happened next -- which
measures whether a gate is *safe*. This asks the question the goal actually
implies: **given a winner, what verdict did we give it?**

That is recall, and it has never been measured despite the data existing. The
journal holds thousands of observations, each with a status, a set of failure
reasons and a price at the moment it was seen.

The distinction matters because a gate can look excellent on the first question
and be worthless on the second. A filter that rejects everything has a perfect
death rate among what it accepts and catches no winners at all.

**No look-ahead.** The verdict comes from each mint's *first* observation, which
is the only moment it could have been acted on. The outcome is measured from that
observation's price to now.

**Decision rule, fixed before any number is seen.** If the acceptance rate among
big winners is not materially higher than among total losses, the gates have no
power to select for return, and recall against this universe is effectively zero.
That would not make the gates wrong -- they were built to measure risk -- but it
would mean the system cannot pursue "catch the winners" without something new.

**What this can and cannot measure.** It measures recall over *observed*
candidates: winners the collector saw. It cannot measure winners the collector
never observed, because a token absent from the journal leaves no trace in it.
So this is an upper bound on recall, and the coverage question is stated
separately rather than folded in silently.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
from collections import Counter
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

STATUS_ORDER = ("eligible_for_strategy_review", "monitor", "reject")


def safe(text: str) -> str:
    """Render a token symbol on a console that may not be UTF-8.

    Symbols are attacker-controlled text. They routinely contain emoji and
    right-to-left marks, and on a cp1252 terminal an unescaped one crashes the
    whole study after the analysis has already run -- losing the result to a
    presentation detail.
    """
    return text.encode("ascii", "replace").decode("ascii")


def band_for(multiple: float, dead_below: float, winner_at: float, big_at: float) -> str:
    if multiple >= big_at:
        return f"big winner (>={big_at:.0f}x)"
    if multiple >= winner_at:
        return f"winner (>={winner_at:.0f}x)"
    if multiple < dead_below:
        return f"dead (<{dead_below:.0%})"
    return "middling"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dead-below", type=float, default=0.10)
    parser.add_argument("--winner-at", type=float, default=2.0)
    parser.add_argument("--big-at", type=float, default=5.0)
    parser.add_argument(
        "--minimum-liquidity",
        type=float,
        default=0.0,
        help=(
            "Only count candidates whose pool held at least this much at observation. "
            "Without it the winner bands are dominated by bonding-curve artifacts: a "
            "token observed at a near-zero price in a $0.00 pool shows a nominal "
            "80,000,000x that no order could ever have touched."
        ),
    )
    arguments = parser.parse_args()

    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled yet.")
        return 1

    first: dict[str, dict[str, Any]] = {}
    for event in events:
        first.setdefault(event["entity_id"], event["payload"])

    priced = {
        mint: payload
        for mint, payload in first.items()
        if payload.get("price_usd")
        and float(payload["price_usd"]) > 0
        and float(payload.get("liquidity_usd") or 0.0) >= arguments.minimum_liquidity
    }
    excluded_thin = sum(
        1
        for payload in first.values()
        if payload.get("price_usd")
        and float(payload["price_usd"]) > 0
        and float(payload.get("liquidity_usd") or 0.0) < arguments.minimum_liquidity
    )
    prices = DexScreenerProvider().prices_for_tokens(list(priced))
    unresolved = sum(1 for mint in priced if mint not in prices)
    measured = len(priced) - unresolved

    print(f"{len(first)} distinct mints journalled")
    print(f"  {len(first) - len(priced) - excluded_thin} with no usable observation price")
    if arguments.minimum_liquidity > 0:
        print(f"  {excluded_thin} below the ${arguments.minimum_liquidity:,.0f} liquidity floor")
    print(f"  {unresolved} whose current price could not be resolved")
    print(f"  {measured} with a measurable outcome")
    reconciled = (len(first) - len(priced)) + unresolved + measured
    if reconciled != len(first):
        print("  MISMATCH -- mints unaccounted for; do not read the tables below.")
        return 1
    if not measured:
        print("\nNo outcomes measurable. This is 'no data', not 'no effect'.")
        return 0

    # band -> status -> count, plus the failure reasons seen among winners.
    bands: dict[str, Counter[str]] = {}
    winner_failures: Counter[str] = Counter()
    big_winner_rows: list[tuple[float, str, str, str]] = []

    for mint, payload in priced.items():
        now = prices.get(mint)
        if not now:
            continue
        multiple = now / float(payload["price_usd"])
        band = band_for(multiple, arguments.dead_below, arguments.winner_at, arguments.big_at)
        status = str(payload.get("status") or "unknown")
        bands.setdefault(band, Counter())[status] += 1

        if multiple >= arguments.winner_at:
            for failure in payload.get("failures") or []:
                winner_failures[str(failure)] += 1
        if multiple >= arguments.big_at:
            big_winner_rows.append(
                (
                    multiple,
                    str(payload.get("symbol") or mint[:8]),
                    status,
                    ", ".join(list(payload.get("failures") or [])[:3]) or "-",
                )
            )

    order = [
        f"big winner (>={arguments.big_at:.0f}x)",
        f"winner (>={arguments.winner_at:.0f}x)",
        "middling",
        f"dead (<{arguments.dead_below:.0%})",
    ]

    print("\nwhat verdict did each outcome band receive?")
    header = f"{'outcome band':<22} {'n':>5} {'eligible':>9} {'monitor':>9} {'reject':>9}"
    print(header)
    print("-" * len(header))
    for band in order:
        counts = bands.get(band)
        if not counts:
            print(f"{band:<22} {0:>5}        --        --        --")
            continue
        total = sum(counts.values())
        cells = []
        for status in STATUS_ORDER:
            value = counts.get(status, 0)
            cells.append(f"{value:>4} ({100 * value / total:>3.0f}%)")
        print(f"{band:<22} {total:>5} {cells[0]:>9} {cells[1]:>9} {cells[2]:>9}")

    def accept_rate(band: str) -> tuple[float | None, int]:
        counts = bands.get(band)
        if not counts:
            return None, 0
        total = sum(counts.values())
        accepted = counts.get("eligible_for_strategy_review", 0) + counts.get("monitor", 0)
        return (100.0 * accepted / total), total

    big_rate, big_n = accept_rate(order[0])
    dead_rate, dead_n = accept_rate(order[3])

    if winner_failures:
        print(f"\nwhy winners (>= {arguments.winner_at:.0f}x) were rejected, most common first")
        for reason, count in winner_failures.most_common(10):
            print(f"  {count:>5}  {reason}")

    if big_winner_rows:
        big_winner_rows.sort(reverse=True)
        print(f"\nthe biggest winners we saw, and what we said (top {min(12, len(big_winner_rows))})")
        print(f"{'multiple':>10} {'symbol':<14} {'verdict':<26} first failures")
        for multiple, symbol, status, failures in big_winner_rows[:12]:
            print(f"{multiple:>10.1f} {safe(symbol)[:14]:<14} {status:<26} {safe(failures)[:52]}")

    print("\nRecall is measured over candidates the collector *observed*. Winners it")
    print("never saw leave no trace in the journal, so this is an upper bound.\n")

    if big_rate is None or dead_rate is None or big_n < 10 or dead_n < 10:
        print("VERDICT: UNPROVEN -- too few outcomes in one band to compare.")
        return 0

    print(
        f"acceptance rate: {big_rate:.0f}% among big winners (n={big_n}) "
        f"vs {dead_rate:.0f}% among deaths (n={dead_n})"
    )
    if big_rate > dead_rate:
        print("The gates accept winners more often than deaths, so they carry some")
        print("signal for return. Measure how much before relying on it.")
    else:
        print("VERDICT: the gates do NOT accept winners more often than deaths.")
        print("Recall against this universe is effectively zero: the system is not")
        print("selecting for return, it is selecting for survival, and those have")
        print("already been shown to be different things here.")
        print()
        print("This does not make the gates wrong -- they were built to measure risk.")
        print("It means 'catch the winners' needs evidence the gates do not carry,")
        print("and the rejection reasons above say which gate is doing the damage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
