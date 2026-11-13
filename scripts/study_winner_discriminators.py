#!/usr/bin/env python3
"""Is there anything in the journalled evidence that separates winners from deaths?

`study_winner_recall.py` answers "given a winner, what did we say?" and the answer
is damning: 0% of 2x winners accepted, against a 3% acceptance rate among deaths.
That tells us the gates do not select for return. It does **not** tell us whether
anything *could*.

This study asks the prior question. For each feature the collector journals at the
moment a decision could be made, it bands the population and reports the winner
rate and death rate in each band. If no feature separates them, then the target
"catch winners, avoid deaths" is not reachable from this evidence at all, and no
threshold tuning will find it. That is worth knowing before building anything else.

THE DECISION RULE, FIXED BEFORE THE NUMBERS WERE SEEN
-----------------------------------------------------
A feature is reported as a **candidate discriminator** only if all four hold:

  1. at least 30 measurable outcomes in both its top and bottom band, so the
     comparison is not one token wearing a percentage;
  2. the winner rate in the best band is at least **twice** the winner rate in the
     worst band -- a real separation, not a rounding difference;
  3. the death rate moves in the **opposite** direction to the winner rate, so the
     feature is not merely selecting volatile tokens that do more of everything;
  4. the winner rate is **monotone** across all bands. A feature that helps in
     band 2, hurts in band 3 and helps again in band 4 is fitting noise, and this
     project has already withdrawn one filter that looked good in a single band.

Anything less is reported as NOT A DISCRIMINATOR. A feature failing only condition
1 is reported separately as UNMEASURABLE, because too little data is a different
statement from no effect -- conflating them is the error this project has
committed three times.

WHAT THIS STUDY CANNOT SEE
--------------------------
  * **Survivorship.** Outcomes come from tokens DexScreener still quotes. The
    majority of this journal no longer quotes at all, and those are deaths that do
    not appear. Every rate here is therefore optimistic, and the winner rates most
    of all.
  * **Observation timing.** Features are taken from the *first* journalled
    observation, which is the only moment a decision could have used them. A
    feature that would separate outcomes an hour later is invisible here, and
    correctly so -- it could not have been traded on.
  * **A discriminator is not an edge.** Even a feature clearing all four
    conditions has to survive costs and the tail-dependence test before it means
    money. This study is a filter on what is worth measuring next, nothing more.

Read-only. Reads the journal and one price endpoint; writes nothing.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter
from itertools import pairwise
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

# Fixed in advance; see the decision rule above.
MINIMUM_BAND_SAMPLE = 30
REQUIRED_WINNER_RATIO = 2.0

# Feature name -> (journal key, whether a higher value is hypothesised to be better).
# The hypothesis direction is recorded so the result can disagree with it. The
# deployer gate ran backwards for weeks because nobody wrote down which way it was
# supposed to point before looking.
FEATURES: tuple[tuple[str, str], ...] = (
    ("liquidity_usd", "pool depth at observation"),
    ("market_cap_usd", "market capitalisation (context only, not exit liquidity)"),
    ("holders", "holder count"),
    ("age_minutes", "age when first seen"),
    ("entry_price_impact_pct", "quoted entry impact"),
    ("exit_price_impact_pct", "quoted exit impact"),
    ("cluster_confidence", "confidence in the cluster verdict"),
    ("evidence_coverage_pct", "share of evidence actually resolved"),
)


def band_label(index: int, edges: list[float]) -> str:
    if index == 0:
        return f"<= {edges[0]:,.4g}"
    if index >= len(edges):
        return f"> {edges[-1]:,.4g}"
    return f"{edges[index - 1]:,.4g}..{edges[index]:,.4g}"


def quartile_edges(values: list[float]) -> list[float]:
    """Three cut points, so every band holds roughly a quarter of the population.

    Quantiles rather than fixed thresholds on purpose: a fixed threshold encodes a
    guess about where the interesting boundary sits, and the whole question here is
    whether there *is* one.
    """
    ordered = sorted(values)
    if len(ordered) < 4:
        return []
    return [
        statistics.quantiles(ordered, n=4)[0],
        statistics.quantiles(ordered, n=4)[1],
        statistics.quantiles(ordered, n=4)[2],
    ]


def assess(bands: list[dict[str, Any]]) -> tuple[str, str]:
    """Apply the four conditions. Returns (verdict, why)."""
    populated = [band for band in bands if band["n"] >= MINIMUM_BAND_SAMPLE]
    if len(populated) < 2:
        return "UNMEASURABLE", f"fewer than 2 bands reach n={MINIMUM_BAND_SAMPLE}"

    winner_rates = [band["winner_pct"] for band in populated]
    death_rates = [band["dead_pct"] for band in populated]
    best = max(winner_rates)
    worst = min(winner_rates)
    if worst <= 0:
        if best <= 0:
            return "NOT A DISCRIMINATOR", "no winners in any band"
        return "NOT A DISCRIMINATOR", "worst band has no winners; ratio undefined"
    if best / worst < REQUIRED_WINNER_RATIO:
        return (
            "NOT A DISCRIMINATOR",
            f"winner rate spread {best / worst:.2f}x < {REQUIRED_WINNER_RATIO}x required",
        )

    ascending = all(a <= b for a, b in pairwise(winner_rates))
    descending = all(a >= b for a, b in pairwise(winner_rates))
    if not (ascending or descending):
        return "NOT A DISCRIMINATOR", "winner rate is not monotone across bands"

    death_ascending = all(a <= b for a, b in pairwise(death_rates))
    death_descending = all(a >= b for a, b in pairwise(death_rates))
    if ascending and not death_descending:
        return "NOT A DISCRIMINATOR", "winners rise but deaths do not fall; selects volatility"
    if descending and not death_ascending:
        return "NOT A DISCRIMINATOR", "winners fall but deaths do not rise; selects volatility"

    return "CANDIDATE DISCRIMINATOR", "all four conditions hold"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dead-below", type=float, default=0.10)
    parser.add_argument("--winner-at", type=float, default=2.0)
    parser.add_argument(
        "--minimum-liquidity",
        type=float,
        default=5_000.0,
        help=(
            "Only count candidates whose pool held at least this much at observation. "
            "Without it the winner bands fill with bonding-curve artifacts: a token "
            "observed at a near-zero price in a $0.00 pool shows a nominal 80,000,000x "
            "that no order could have touched."
        ),
    )
    arguments = parser.parse_args()

    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled yet. This is 'no data', not 'no effect'.")
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
    thin = sum(
        1
        for payload in first.values()
        if payload.get("price_usd")
        and float(payload["price_usd"]) > 0
        and float(payload.get("liquidity_usd") or 0.0) < arguments.minimum_liquidity
    )
    prices = DexScreenerProvider().prices_for_tokens(list(priced))
    unresolved = sum(1 for mint in priced if mint not in prices)
    measured = len(priced) - unresolved

    # Reconciling denominator: every journalled mint lands in exactly one bucket.
    print(f"{len(first)} distinct mints journalled")
    print(f"  {len(first) - len(priced) - thin:>6} with no usable observation price")
    print(f"  {thin:>6} below the ${arguments.minimum_liquidity:,.0f} liquidity floor")
    print(f"  {unresolved:>6} whose current price could not be resolved (vanished)")
    print(f"  {measured:>6} with a measurable outcome")
    total = (len(first) - len(priced) - thin) + thin + unresolved + measured
    if total != len(first):
        print("  MISMATCH -- do not read the tables below.")
        return 1
    if measured < MINIMUM_BAND_SAMPLE * 2:
        print("\nToo few measurable outcomes to band. This is 'no data', not 'no effect'.")
        return 0

    outcomes: list[tuple[dict[str, Any], float]] = []
    for mint, payload in priced.items():
        now = prices.get(mint)
        if not now:
            continue
        outcomes.append((payload, now / float(payload["price_usd"])))

    winners = sum(1 for _, m in outcomes if m >= arguments.winner_at)
    deaths = sum(1 for _, m in outcomes if m < arguments.dead_below)
    print(
        f"\nbase rates over {len(outcomes)} measurable outcomes:"
        f" winner {100.0 * winners / len(outcomes):.1f}%,"
        f" dead {100.0 * deaths / len(outcomes):.1f}%"
    )
    print(
        "NOTE: the vanished mints above are excluded from these rates and are"
        " overwhelmingly deaths, so every winner rate here is optimistic."
    )

    verdicts: Counter[str] = Counter()
    for key, description in FEATURES:
        values = [
            float(payload[key])
            for payload, _ in outcomes
            if payload.get(key) is not None
        ]
        print(f"\n=== {key} -- {description} ===")
        if len(values) < MINIMUM_BAND_SAMPLE * 2:
            print(f"  only {len(values)} of {len(outcomes)} outcomes carry this field")
            print("  UNMEASURABLE: absent is not zero, so no band is computed.")
            verdicts["UNMEASURABLE"] += 1
            continue

        edges = quartile_edges(values)
        if not edges or len(set(edges)) < len(edges):
            print(f"  {len(values)} values but the quartiles are not distinct")
            print("  UNMEASURABLE: the feature is near-constant on this population.")
            verdicts["UNMEASURABLE"] += 1
            continue

        bands: list[dict[str, Any]] = []
        for index in range(4):
            members = []
            for payload, multiple in outcomes:
                raw = payload.get(key)
                if raw is None:
                    continue
                value = float(raw)
                lower = edges[index - 1] if index > 0 else float("-inf")
                upper = edges[index] if index < 3 else float("inf")
                if (value > lower or index == 0) and (value <= upper or index == 3):
                    members.append(multiple)
            n = len(members)
            bands.append(
                {
                    "label": band_label(index, edges),
                    "n": n,
                    "winner_pct": (
                        100.0 * sum(1 for m in members if m >= arguments.winner_at) / n
                        if n
                        else 0.0
                    ),
                    "dead_pct": (
                        100.0 * sum(1 for m in members if m < arguments.dead_below) / n
                        if n
                        else 0.0
                    ),
                    "median": statistics.median(members) if members else 0.0,
                }
            )

        header = f"  {'band':<28}{'n':>7}{'winner%':>10}{'dead%':>9}{'median x':>11}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for band in bands:
            print(
                f"  {band['label']:<28}{band['n']:>7}{band['winner_pct']:>10.1f}"
                f"{band['dead_pct']:>9.1f}{band['median']:>11.4g}"
            )
        counted = sum(band["n"] for band in bands)
        if counted != len(values):
            print(f"  MISMATCH: bands hold {counted} of {len(values)} values")
        verdict, why = assess(bands)
        verdicts[verdict] += 1
        print(f"  -> {verdict}: {why}")

    print("\n=== summary ===")
    for verdict, count in sorted(verdicts.items()):
        print(f"  {verdict:<26}{count:>4}")
    if not verdicts.get("CANDIDATE DISCRIMINATOR"):
        print(
            "\nNo journalled feature separates winners from deaths under the rule"
            "\nfixed in advance. 'Catch the winners, avoid the deaths' is therefore"
            "\nnot reachable from this evidence, and no threshold tuning will find"
            "\nit -- the information is not in the data the collector currently"
            "\nrecords. The next move is new evidence, not a new cutoff."
        )
    else:
        print(
            "\nAt least one feature separates. It is a candidate, not an edge:"
            "\nit still has to survive costs and the loss of its best trade."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
