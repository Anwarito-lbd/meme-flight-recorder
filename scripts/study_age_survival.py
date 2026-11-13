#!/usr/bin/env python3
"""Test whether pool age at observation predicts survival, across all mints.

A single matched pair suggested that older pools survive and newborn pools die.
One pair is an anecdote. This walks every mint the collector has journalled,
looks up what actually happened to it, and reports the outcome distribution by
age bucket.

A pattern is only worth encoding if it repeats or has a mechanism behind it.
This measures the repetition half. It is deliberately capable of showing that
the pattern does not hold.

Read-only.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.http import get_json

BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("<15m", 0.0, 15.0),
    ("15-60m", 15.0, 60.0),
    ("1-6h", 60.0, 360.0),
    ("6-24h", 360.0, 1_440.0),
    (">24h", 1_440.0, float("inf")),
)


def bucket_for(age_minutes: float | None) -> str | None:
    if age_minutes is None:
        return None
    for label, low, high in BUCKETS:
        if low <= age_minutes < high:
            return label
    return None


def current_prices(mints: list[str], chunk: int = 25) -> dict[str, float]:
    """Look up present prices, batched to stay inside the provider's limits."""
    prices: dict[str, float] = {}
    for index in range(0, len(mints), chunk):
        batch = mints[index : index + chunk]
        try:
            payload = get_json(
                "https://api.dexscreener.com", f"/latest/dex/tokens/{','.join(batch)}"
            )
        except Exception as error:  # noqa: BLE001 - a failed batch is missing data, not zero
            print(f"  batch {index // chunk}: {type(error).__name__}")
            continue
        for pair in payload.get("pairs") or []:
            address = ((pair.get("baseToken") or {}).get("address")) or ""
            price = pair.get("priceUsd")
            if not address or price is None:
                continue
            value = float(price)
            # Keep the deepest pair's price for a mint quoted in several pools.
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
            best = prices.get(address)
            if best is None or liquidity > 0:
                prices[address] = value
    return prices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dead-below", type=float, default=0.10, help="Multiple counted as dead.")
    arguments = parser.parse_args()

    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled yet.")
        return 1

    # First observation per mint: the only one a trader could have acted on.
    first: dict[str, dict[str, Any]] = {}
    for event in events:
        mint = event["entity_id"]
        if mint not in first:
            first[mint] = event["payload"]

    usable = {
        mint: payload
        for mint, payload in first.items()
        if payload.get("price_usd") and payload.get("age_minutes") is not None
    }
    print(f"{len(first)} distinct mints, {len(usable)} with a usable entry price and age")

    prices = current_prices(list(usable))
    print(f"resolved current prices for {len(prices)}\n")

    by_bucket: dict[str, list[float]] = defaultdict(list)
    for mint, payload in usable.items():
        now = prices.get(mint)
        if not now:
            continue
        label = bucket_for(payload["age_minutes"])
        if label:
            by_bucket[label].append(now / payload["price_usd"])

    total = sum(len(values) for values in by_bucket.values())
    print(f"outcomes measured for {total} mints\n")
    header = f"{'age at observation':<20} {'n':>4} {'median':>8} {'mean':>9} {'dead':>6} {'>2x':>5}"
    print(header)
    print("-" * len(header))
    for label, _low, _high in BUCKETS:
        values = by_bucket.get(label) or []
        if not values:
            print(f"{label:<20} {0:>4}       --        --     --    --")
            continue
        dead = sum(1 for value in values if value < arguments.dead_below)
        winners = sum(1 for value in values if value >= 2.0)
        print(
            f"{label:<20} {len(values):>4} {statistics.median(values):>8.2f} "
            f"{statistics.mean(values):>9.2f} "
            f"{100 * dead / len(values):>5.0f}% {100 * winners / len(values):>4.0f}%"
        )

    print(
        "\n'dead' = fell below "
        f"{arguments.dead_below:.0%} of entry. Multiples are from the first observed price."
    )
    print("A pattern is only worth encoding if it repeats here or has a mechanism.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
