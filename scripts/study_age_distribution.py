#!/usr/bin/env python3
"""Verify the claim that the safety-clean cohort is *younger* than the rest.

`analyse_clean_candidates.py` reported "clean median age 4.7 minutes against 1.8
for everything else" and called the clean cohort younger. 4.7 > 1.8, so either
the numbers or the word is wrong, and the mechanism built on it -- "the gates
select for youth, and youth is what dies" -- rests on whichever it is.

Decision rule, fixed before the numbers were seen:

  * If clean median age > rest median age, the word "younger" is withdrawn and
    any claim resting on it must be restated from the corrected direction.
  * Age is reported as a full distribution (p25/p50/p75/p90), not a median, so a
    bimodal population cannot hide behind a single statistic.
  * Every mint lands in exactly one outcome bucket and the buckets reconcile to
    the total. A mint that cannot be priced today is *reported as such*, never
    dropped, because "no pair" is evidence of death, not absence of evidence.

Read-only. Uses the journal for ages and DexScreener for present prices, which
is the same outcome resolution every other study in this project uses.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import defaultdict
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.providers.http import get_json

# A mint quoted below this multiple of its first observed price is dead in the
# sense that matters: the position could not be recovered.
DEAD_BELOW = 0.10

OUTCOME_ORDER = (
    "winner_10x",
    "winner_5x",
    "winner_2x",
    "middling",
    "dead",
    "vanished",
    "no_entry_price",
)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


def first_observations(database_path: str) -> dict[str, dict[str, Any]]:
    """First journalled observation per mint -- the only moment a decision could
    have been made without hindsight."""
    connection = sqlite3.connect(database_path)
    first: dict[str, dict[str, Any]] = {}
    query = (
        "select entity_id, observed_at, payload_json from events "
        "where event_type = 'candidate_observed' order by id"
    )
    for mint, observed_at, payload in connection.execute(query):
        if mint in first:
            continue
        record = json.loads(payload)
        record["observed_at"] = observed_at
        first[mint] = record
    connection.close()
    return first


def present_prices(mints: list[str], chunk: int = 30) -> dict[str, float]:
    prices: dict[str, float] = {}
    best_liquidity: dict[str, float] = {}
    for index in range(0, len(mints), chunk):
        batch = mints[index : index + chunk]
        # DexScreener publishes 300 requests/minute for this endpoint. Pacing is
        # derived from that limit, not guessed: a 429 here would be journalled
        # as "no pair", i.e. a rate limit recorded as a token death.
        time.sleep(0.25)
        try:
            payload = get_json(
                "https://api.dexscreener.com", f"/latest/dex/tokens/{','.join(batch)}"
            )
        except Exception as error:  # noqa: BLE001 - a failed batch is missing data, not zero
            print(f"  batch {index // chunk}: {type(error).__name__} {error}")
            continue
        for pair in payload.get("pairs") or []:
            address = ((pair.get("baseToken") or {}).get("address")) or ""
            price = pair.get("priceUsd")
            if not address or price is None:
                continue
            liquidity = float((pair.get("liquidity") or {}).get("usd") or 0.0)
            if address not in prices or liquidity >= best_liquidity.get(address, -1.0):
                prices[address] = float(price)
                best_liquidity[address] = liquidity
        if index % (chunk * 50) == 0:
            print(f"  priced {len(prices)} of {index + len(batch)} requested")
    return prices


def report(label: str, ages: list[float], total: int) -> None:
    if not ages:
        print(f"{label:<26} n={len(ages):>5} of {total:<6} (no age evidence)")
        return
    print(
        f"{label:<26} n={len(ages):>5} of {total:<6}"
        f"p25={percentile(ages, 0.25):>9.2f}  p50={percentile(ages, 0.50):>9.2f}  "
        f"p75={percentile(ages, 0.75):>9.2f}  p90={percentile(ages, 0.90):>9.2f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Skip outcome cohorts.")
    arguments = parser.parse_args()

    settings = load_settings()
    first = first_observations(str(settings.database_path))
    print(f"mints with a first observation: {len(first)}")

    clean: list[str] = []
    rejected: list[str] = []
    for mint, record in first.items():
        # "Safety clean" is the absence of any recorded failure, which is the
        # definition the paper book was re-wired to use. Status alone is not it:
        # MONITOR is a maturity label, not a verdict.
        if record.get("failures"):
            rejected.append(mint)
        else:
            clean.append(mint)
    print(f"  safety clean (no recorded failure): {len(clean)}")
    print(f"  safety rejected:                    {len(rejected)}")

    ages = {mint: record.get("age_minutes") for mint, record in first.items()}
    print(f"  with age evidence:                  {sum(1 for a in ages.values() if a is not None)}")

    print("\n=== age at first observation, minutes ===")
    report("safety clean", [ages[m] for m in clean if ages.get(m) is not None], len(clean))
    report("safety rejected", [ages[m] for m in rejected if ages.get(m) is not None], len(rejected))

    if arguments.offline:
        return 0

    mints = sorted(first)
    print(f"\nresolving present price for {len(mints)} mints ...")
    prices = present_prices(mints)

    buckets: dict[str, list[str]] = defaultdict(list)
    for mint in mints:
        entry = first[mint].get("price_usd")
        now = prices.get(mint)
        if entry is None or entry <= 0:
            buckets["no_entry_price"].append(mint)
            continue
        if now is None:
            # No pair quoted anywhere today. For a token this is the pool being
            # gone; it is recorded as its own bucket so the reader can see how
            # much of the population it is rather than having it silently
            # dropped from every rate below.
            buckets["vanished"].append(mint)
            continue
        multiple = now / entry
        if multiple >= 10:
            buckets["winner_10x"].append(mint)
        elif multiple >= 5:
            buckets["winner_5x"].append(mint)
        elif multiple >= 2:
            buckets["winner_2x"].append(mint)
        elif multiple < DEAD_BELOW:
            buckets["dead"].append(mint)
        else:
            buckets["middling"].append(mint)

    total = sum(len(members) for members in buckets.values())
    print(f"\n=== outcome reconciliation === buckets sum {total}, population {len(mints)}")
    for name in OUTCOME_ORDER:
        print(f"  {name:<16} {len(buckets[name]):>6}")
    if total != len(mints):
        raise AssertionError("buckets must reconcile to the population")

    print("\n=== age at first observation by outcome, minutes ===")
    for name in OUTCOME_ORDER[:-1]:
        members = buckets[name]
        report(name, [ages[m] for m in members if ages.get(m) is not None], len(members))

    print("\n=== outcome rates within each safety cohort ===")
    lookup = {mint: name for name, members in buckets.items() for mint in members}
    for label, cohort in (("safety clean", clean), ("safety rejected", rejected)):
        counts: dict[str, int] = defaultdict(int)
        for mint in cohort:
            counts[lookup.get(mint, "unclassified")] += 1
        priced = sum(
            counts[key] for key in ("winner_10x", "winner_5x", "winner_2x", "middling", "dead")
        )
        wins = counts["winner_2x"] + counts["winner_5x"] + counts["winner_10x"]
        print(f"  {label}: n={len(cohort)} priced={priced} vanished={counts['vanished']}")
        print(f"      {dict(counts)}")
        if priced:
            dead_rate = counts["dead"] / priced
            print(f"      of priced: dead {dead_rate:.1%}   >=2x {wins / priced:.1%}")
            with_gone = priced + counts["vanished"]
            counted_dead = (counts["dead"] + counts["vanished"]) / with_gone
            print(f"      counting vanished as dead: dead {counted_dead:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
