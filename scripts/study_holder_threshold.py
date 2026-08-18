#!/usr/bin/env python3
"""Does a holder-count threshold at first observation predict winners out of sample?

`study_winner_discriminators.py` found no feature that separates winners from
deaths under its pre-registered rule. But holder count failed that rule on
*monotonicity* while showing a shape the rule was not written for: winner rates of
8.8%, 5.1%, 4.4% across the first three quartiles and then **42.1%** in the top
one, with the lowest death rate of the four and a median multiple of 0.43 against
0.09-0.13 elsewhere. Flat, flat, flat, then a cliff. That is a threshold, not a
gradient.

Rewriting the earlier rule to admit that result would be the exact move this
project's standard exists to prevent, so the hypothesis gets its own test instead,
pre-registered and out of sample.

THE DECISION RULE, FIXED BEFORE THE HELD-OUT NUMBERS WERE SEEN
--------------------------------------------------------------
**The threshold is derived from the development half only** -- specifically, the
75th percentile of holder count among development-half outcomes. It is not
searched for, not tuned, and never computed on the held-out half. A number chosen
after seeing the data it is scored against is not a finding.

The split is **chronological**, at the median first-observation time. Development
is everything earlier, held-out everything later. A random split would leak: these
tokens arrive in correlated waves, and neighbours in time share market conditions.

On the **held-out half only**, the hypothesis is PROVEN if all four hold:

  1. at least 30 outcomes on each side of the threshold;
  2. the winner rate above the threshold is at least **2x** the rate below;
  3. the death rate above the threshold is **lower** than below;
  4. condition 2 still holds after **deleting the single best token above the
     threshold**. This is the test that killed the `deep >=$50k` filter at n=166,
     where removing one 209.7x token flipped growth negative at every size.

Anything less is UNPROVEN and the threshold stays out of the entry path.

WHAT THIS CANNOT SEE
--------------------
  * **Survivorship, and it cuts one way.** Outcomes come from tokens still quoted
    today. Vanished mints are excluded and are overwhelmingly deaths, so every
    winner rate here is optimistic in absolute terms. The *comparison* between
    bands is more robust than the levels, because both sides are biased the same
    way -- unless holder count itself predicts vanishing, which it may.
  * **Holders is a vendor count.** It is not verified on chain here, and a token
    can manufacture holders cheaply. If this survives, that is the next thing to
    check, because a purchasable signal is not a signal.
  * **A discriminator is still not an edge.** Even PROVEN, it has to clear costs
    and produce positive geometric growth at the configured position size before
    it means money.

Read-only.
"""

from __future__ import annotations

import argparse
import statistics
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

MINIMUM_SIDE_SAMPLE = 30
REQUIRED_WINNER_RATIO = 2.0
DEVELOPMENT_PERCENTILE = 75


def summarise(
    multiples: list[float], winner_at: float, dead_below: float
) -> dict[str, Any]:
    if not multiples:
        return {"n": 0, "winner_pct": 0.0, "dead_pct": 0.0, "median": 0.0, "best": 0.0}
    return {
        "n": len(multiples),
        "winner_pct": 100.0 * sum(1 for m in multiples if m >= winner_at) / len(multiples),
        "dead_pct": 100.0 * sum(1 for m in multiples if m < dead_below) / len(multiples),
        "median": statistics.median(multiples),
        "best": max(multiples),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dead-below", type=float, default=0.10)
    parser.add_argument("--winner-at", type=float, default=2.0)
    parser.add_argument("--minimum-liquidity", type=float, default=5_000.0)
    arguments = parser.parse_args()

    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled. This is 'no data', not 'no effect'.")
        return 1

    # First observation per mint, with its timestamp -- the only moment a decision
    # could have used this evidence.
    first: dict[str, tuple[str, dict[str, Any]]] = {}
    for event in events:
        first.setdefault(event["entity_id"], (event["observed_at"], event["payload"]))

    eligible = {
        mint: (when, payload)
        for mint, (when, payload) in first.items()
        if payload.get("price_usd")
        and float(payload["price_usd"]) > 0
        and float(payload.get("liquidity_usd") or 0.0) >= arguments.minimum_liquidity
        and payload.get("holders") is not None
    }
    prices = DexScreenerProvider().prices_for_tokens(list(eligible))

    rows: list[tuple[str, float, float]] = []  # (observed_at, holders, multiple)
    vanished = 0
    for mint, (when, payload) in eligible.items():
        now = prices.get(mint)
        if not now:
            vanished += 1
            continue
        rows.append((when, float(payload["holders"]), now / float(payload["price_usd"])))

    print(f"{len(first)} distinct mints journalled")
    print(f"  {len(first) - len(eligible):>6} lacking price, depth or holder count")
    print(f"  {vanished:>6} whose price could not be resolved (vanished)")
    print(f"  {len(rows):>6} with a measurable outcome")
    if (len(first) - len(eligible)) + vanished + len(rows) != len(first):
        print("  MISMATCH -- do not read below.")
        return 1
    if len(rows) < MINIMUM_SIDE_SAMPLE * 4:
        print("\nToo few outcomes to split and test. 'No data', not 'no effect'.")
        return 0

    rows.sort(key=lambda row: row[0])
    cut = len(rows) // 2
    development = rows[:cut]
    heldout = rows[cut:]
    print(f"\nchronological split at {development[-1][0][:19]}")
    print(f"  development {len(development)}   held-out {len(heldout)}")

    # The threshold comes from development only. Never recomputed on held-out.
    dev_holders = [holders for _, holders, _ in development]
    threshold = statistics.quantiles(sorted(dev_holders), n=100)[DEVELOPMENT_PERCENTILE - 1]
    print(
        f"\nthreshold = p{DEVELOPMENT_PERCENTILE} of development holder count"
        f" = {threshold:,.0f} holders"
    )
    print("Derived from the development half only, before the held-out half was scored.")

    def report(label: str, sample: list[tuple[str, float, float]]) -> dict[str, Any]:
        above = [m for _, h, m in sample if h > threshold]
        below = [m for _, h, m in sample if h <= threshold]
        stats_above = summarise(above, arguments.winner_at, arguments.dead_below)
        stats_below = summarise(below, arguments.winner_at, arguments.dead_below)
        print(f"\n=== {label} ===")
        header = f"  {'side':<26}{'n':>7}{'winner%':>10}{'dead%':>9}{'median x':>11}{'best x':>12}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for name, stats in ((f"holders > {threshold:,.0f}", stats_above),
                            (f"holders <= {threshold:,.0f}", stats_below)):
            print(
                f"  {name:<26}{stats['n']:>7}{stats['winner_pct']:>10.1f}"
                f"{stats['dead_pct']:>9.1f}{stats['median']:>11.4g}{stats['best']:>12.4g}"
            )
        if stats_above["n"] + stats_below["n"] != len(sample):
            print("  MISMATCH: sides do not reconcile against the sample")
        return {"above": above, "below": below, "sa": stats_above, "sb": stats_below}

    report("development half (threshold derived here; not evidence)", development)
    held = report("HELD-OUT half (the only half that decides)", heldout)

    failures: list[str] = []
    sa, sb = held["sa"], held["sb"]
    if sa["n"] < MINIMUM_SIDE_SAMPLE or sb["n"] < MINIMUM_SIDE_SAMPLE:
        failures.append(f"n above={sa['n']}, below={sb['n']}; need {MINIMUM_SIDE_SAMPLE} each")
    ratio = (sa["winner_pct"] / sb["winner_pct"]) if sb["winner_pct"] > 0 else None
    if ratio is None:
        failures.append("no winners below the threshold; ratio undefined")
    elif ratio < REQUIRED_WINNER_RATIO:
        failures.append(f"winner ratio {ratio:.2f}x < {REQUIRED_WINNER_RATIO}x")
    if sa["dead_pct"] >= sb["dead_pct"]:
        failures.append(
            f"death rate above ({sa['dead_pct']:.1f}%) not below that under it"
            f" ({sb['dead_pct']:.1f}%)"
        )

    # Condition 4: delete the single best token above the threshold.
    above = sorted(held["above"], reverse=True)
    if above:
        without_best = above[1:]
        stats_wb = summarise(without_best, arguments.winner_at, arguments.dead_below)
        print(
            f"\nheld-out, above threshold, deleting the single best ({above[0]:,.4g}x):"
            f" n={stats_wb['n']} winner {stats_wb['winner_pct']:.1f}%"
        )
        ratio_wb = (
            stats_wb["winner_pct"] / sb["winner_pct"] if sb["winner_pct"] > 0 else None
        )
        if ratio_wb is None or ratio_wb < REQUIRED_WINNER_RATIO:
            shown = "undefined" if ratio_wb is None else f"{ratio_wb:.2f}x"
            failures.append(f"after deleting the best token the ratio is {shown}")

    print("\n=== verdict, on the held-out half, against the rule fixed in advance ===")
    if failures:
        print("UNPROVEN. Failing conditions:")
        for failure in failures:
            print(f"  - {failure}")
        print("\nThe threshold stays out of the entry path.")
    else:
        print("PROVEN: the holder threshold separates winners from deaths out of sample.")
        print("It is a candidate, not an edge. Before it may size anything it must")
        print("clear costs and show positive geometric growth at the configured size,")
        print("and the holder count must be verified on chain -- a vendor count that")
        print("can be manufactured cheaply is a purchasable signal, not a signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
