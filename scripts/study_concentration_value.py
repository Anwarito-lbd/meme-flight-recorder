#!/usr/bin/env python3
"""Does holder concentration predict death, or just youth?

The recall study found this gate rejecting 36 of 56 winners -- the single largest
cause of the system never having accepted one. That is not, by itself, evidence
the gate is wrong. The cluster gate rejects winners too and earns its place by
cutting the death rate, which is what a risk gate is for.

So this asks the same question that corrected the deployer gate: **does the
rejected band actually die more often?**

A young launchpad token has concentrated supply as a matter of course -- the
bonding curve or the first buyers hold most of it. If death rate is flat across
concentration bands, the 30% threshold is measuring how young a token is rather
than how dangerous, and it is discarding winners for nothing.

**Decision rule, fixed before any number is seen.** The gate is earning its place
only if the bands above the threshold die materially more often than the bands
below it. If they do not, it is filtering population rather than danger, and the
threshold should be re-derived from where the death rate actually turns.

**Judged on death rate, not return.** If high-concentration tokens both die more
*and* return more, the gate stays: that is the expected shape in this market and
is not grounds to loosen it. Return is not what this gate measures.

**This cannot be backfilled.** Concentration at the moment of observation is gone
for anything journalled before the field was added, so the sample starts from
that commit forward.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import statistics
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

BANDS: tuple[tuple[str, float, float], ...] = (
    ("<10%", 0.0, 10.0),
    ("10-30%", 10.0, 30.0),
    ("30-50%", 30.0, 50.0),
    ("50-80%", 50.0, 80.0),
    (">=80%", 80.0, float("inf")),
)


def band_for(value: float) -> str | None:
    for label, low, high in BANDS:
        if low <= value < high:
            return label
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dead-below", type=float, default=0.10)
    parser.add_argument(
        "--minimum-liquidity",
        type=float,
        default=5000.0,
        help="Exclude untradeable pools, whose nominal multiples are artifacts.",
    )
    arguments = parser.parse_args()

    settings = load_settings()
    threshold = settings.solana_safety.maximum_top10_private_holder_pct
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled yet.")
        return 1

    first: dict[str, dict[str, Any]] = {}
    for event in events:
        first.setdefault(event["entity_id"], event["payload"])

    with_field = {
        mint: payload
        for mint, payload in first.items()
        if payload.get("top10_private_holder_pct") is not None
    }
    tradeable = {
        mint: payload
        for mint, payload in with_field.items()
        if payload.get("price_usd")
        and float(payload["price_usd"]) > 0
        and float(payload.get("liquidity_usd") or 0.0) >= arguments.minimum_liquidity
    }

    print(f"{len(first)} distinct mints journalled")
    print(f"  {len(first) - len(with_field)} without a concentration value recorded")
    print(f"  {len(with_field) - len(tradeable)} with one, but untradeable or unpriced")
    print(f"  {len(tradeable)} usable")

    if not with_field:
        print("\nNo concentration values journalled yet. This is 'no data', not 'no effect'.")
        print("The field is written from the commit that added it onward and cannot be")
        print("backfilled. Run the collector, then re-run this study.")
        return 0
    if not tradeable:
        print("\nConcentration is recorded, but no candidate carrying it was tradeable.")
        return 0

    prices = DexScreenerProvider().prices_for_tokens(list(tradeable))
    unresolved = sum(1 for mint in tradeable if mint not in prices)
    print(f"  {unresolved} of those whose current price could not be resolved\n")

    banded: dict[str, list[float]] = {label: [] for label, _low, _high in BANDS}
    unbanded = 0
    for mint, payload in tradeable.items():
        now = prices.get(mint)
        if not now:
            continue
        label = band_for(float(payload["top10_private_holder_pct"]))
        if label is None:
            unbanded += 1
            continue
        banded[label].append(now / float(payload["price_usd"]))

    measured = sum(len(values) for values in banded.values())
    if measured + unresolved + unbanded != len(tradeable):
        print("MISMATCH -- candidates unaccounted for; do not read the table below.")
        return 1
    if not measured:
        print("No outcomes measurable yet. Collect for longer.")
        return 0

    print(f"gate rejects above {threshold:.0f}%\n")
    header = f"{'top-10 concentration':<22} {'n':>5} {'dead':>6} {'median':>8} {'>=2x':>6}"
    print(header)
    print("-" * len(header))
    for label, low, _high in BANDS:
        values = banded[label]
        marker = "  <- rejected" if low >= threshold else ""
        if not values:
            print(f"{label:<22} {0:>5}     --       --     --{marker}")
            continue
        dead = sum(1 for value in values if value < arguments.dead_below)
        winners = sum(1 for value in values if value >= 2.0)
        print(
            f"{label:<22} {len(values):>5} {100 * dead / len(values):>5.0f}% "
            f"{statistics.median(values):>8.3f} {100 * winners / len(values):>5.0f}%{marker}"
        )

    below = [v for label, low, _h in BANDS if low < threshold for v in banded[label]]
    above = [v for label, low, _h in BANDS if low >= threshold for v in banded[label]]

    print(f"\n'dead' = fell below {arguments.dead_below:.0%} of the observed price.")
    if len(below) < 20 or len(above) < 20:
        print("\nVERDICT: UNPROVEN -- fewer than 20 outcomes on one side of the threshold.")
        print("Keep collecting. Do not change the gate on this sample.")
        return 0

    below_death = 100.0 * sum(1 for v in below if v < arguments.dead_below) / len(below)
    above_death = 100.0 * sum(1 for v in above if v < arguments.dead_below) / len(above)
    print(
        f"death rate {above_death:.0f}% above the threshold (n={len(above)}) "
        f"vs {below_death:.0f}% below it (n={len(below)})"
    )
    if above_death > below_death:
        print("\nVERDICT: the gate buys survival, which is what it is for. It costs")
        print("winners, and that cost is the price of the protection rather than a")
        print("defect. Leave it alone.")
    else:
        print("\nVERDICT: the rejected band does NOT die more often. On death rate --")
        print("the only thing a risk gate is for -- this threshold is filtering")
        print("population rather than danger, and it is the largest single cause of")
        print("zero recall. Re-derive it from where the death rate actually turns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
