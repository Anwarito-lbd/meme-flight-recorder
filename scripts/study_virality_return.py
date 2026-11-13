#!/usr/bin/env python3
"""Does a social signal at observation time predict return, not just survival?

Companion to `study_virality_value.py`, which asks the same question about death.
Both are needed because a signal can cut death and destroy profit at the same
time, and that has already happened here: the maturity study cut death 44.0% ->
11.4% while the 2x rate went 7.8% -> **0.0%**, every band still negative after
costs. You can make this population safer or keep its upside; neither has been
positive. So a death-rate pass licenses nothing on its own.

The decision rule was fixed in
`docs/superpowers/specs/2026-08-19-virality-return-design.md` and committed
before this file existed:

  * PASS needs the cohort's EV per dollar above the base rate, **and still above
    it after deleting the cohort's top three winners**, on the held-out half,
    with at least `--minimum-cohort` priced mints.
  * Deleting the top three is the control that killed the only filter this
    project ever thought it had. `deep >=$50k` survived it at n=63 and died at
    n=166 when a single 209.7x was removed.
  * Vanished mints score 0.0, not omitted. They are 84.2% of this population.
  * Costs on both legs from `costs.round_trip_cost` at the configured size, never
    a flat percentage: fixed network fees do not shrink with the order.

Read-only.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_age_distribution import present_prices
from study_virality_value import (
    cached_verdicts,
    fit_threshold,
    held_out,
    social_observations,
)

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[index]


def net_multiples(
    mints: list[str], multiples: dict[str, float], cost_fraction: float
) -> list[float]:
    """Outcome multiples with the round trip charged on both legs.

    A gross 1.05x is a net loss once the round trip is paid, and this project has
    reported EV figures that ignored it before.
    """
    return [max(multiples[m] * (1.0 - cost_fraction), 0.0) for m in mints if m in multiples]


def ev_per_dollar(nets: list[float]) -> float | None:
    """Expected value of one dollar committed, net of costs."""
    if not nets:
        return None
    return sum(nets) / len(nets) - 1.0


def ev_dropping_top(nets: list[float], drop: int) -> float | None:
    """EV after removing the largest `drop` outcomes.

    The single most informative number in this file. A mean far above a median is
    a lottery, and a lottery does not survive its own tail being trimmed.
    """
    if len(nets) <= drop:
        return None
    kept = sorted(nets)[: len(nets) - drop]
    return sum(kept) / len(kept) - 1.0


def geometric_growth(nets: list[float], fraction_of_equity: float) -> float | None:
    """Log growth per trade at a given fraction of equity.

    EV says whether a bet is favourable; this says whether an account compounds.
    They disagree exactly when the distribution is skewed, which is the case here.
    """
    if not nets:
        return None
    total = 0.0
    for net in nets:
        outcome = 1.0 + fraction_of_equity * (net - 1.0)
        if outcome <= 0:
            return float("-inf")  # a total loss at this fraction is ruin
        total += math.log(outcome)
    return total / len(nets)


def describe(label: str, mints: list[str], multiples: dict[str, float], cost: float,
             fraction: float) -> dict[str, Any]:
    nets = net_multiples(mints, multiples, cost)
    scored = [m for m in mints if m in multiples]
    stats = {
        "n": len(scored),
        "p25": percentile(nets, 0.25),
        "p50": percentile(nets, 0.50),
        "p75": percentile(nets, 0.75),
        "p90": percentile(nets, 0.90),
        "win2x": 100.0 * sum(1 for n in nets if n >= 2.0) / len(nets) if nets else None,
        "win5x": 100.0 * sum(1 for n in nets if n >= 5.0) / len(nets) if nets else None,
        "ev": ev_per_dollar(nets),
        "ev_drop1": ev_dropping_top(nets, 1),
        "ev_drop3": ev_dropping_top(nets, 3),
        "ev_drop5": ev_dropping_top(nets, 5),
        "growth": geometric_growth(nets, fraction),
    }
    stats["label"] = label
    return stats


def show(stats: dict[str, Any]) -> None:
    def fmt(value: Any, spec: str = "+.4f") -> str:
        if value is None:
            return "   n/a"
        if value == float("-inf"):
            return "  ruin"
        return format(value, spec)

    print(
        f"    {stats['label']:<26} n={stats['n']:<5}"
        f" p50={fmt(stats['p50'], '6.3f')} p90={fmt(stats['p90'], '7.3f')}"
        f" 2x={fmt(stats['win2x'], '5.1f')}%"
    )
    print(
        f"      EV {fmt(stats['ev'])}   -top1 {fmt(stats['ev_drop1'])}"
        f"   -top3 {fmt(stats['ev_drop3'])}   -top5 {fmt(stats['ev_drop5'])}"
        f"   growth {fmt(stats['growth'])}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-age-hours", type=float, default=4.0)
    parser.add_argument("--minimum-cohort", type=int, default=100)
    parser.add_argument("--position-usd", type=float, default=10.0)
    parser.add_argument("--equity-usd", type=float, default=50.0)
    parser.add_argument("--verdict-cache", default="data/narrative_verdicts.db")
    parser.add_argument(
        "--minimum-entry-liquidity-usd",
        type=float,
        default=5_000.0,
        help="tradeable-pool floor at observation; matches study_winner_recall",
    )
    parser.add_argument(
        "--minimum-exit-liquidity-usd",
        type=float,
        default=1_000.0,
        help="a pair below this cannot be sold into, so it is not a price",
    )
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    settings = load_settings()
    observations = social_observations(str(settings.database_path))
    if not observations:
        print("No observation carries the social fields yet. Nothing to study.")
        return 1

    from datetime import UTC, datetime

    now = datetime.now(UTC)
    mature: dict[str, dict[str, Any]] = {}
    too_young = no_entry_price = untradeable = 0
    for mint, payload in observations.items():
        try:
            seen = datetime.fromisoformat(str(payload["observed_at"]))
        except (ValueError, KeyError):
            continue
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        if (now - seen).total_seconds() / 3600.0 < args.minimum_age_hours:
            too_young += 1
        elif not payload.get("price_usd"):
            no_entry_price += 1
        elif (payload.get("liquidity_usd") or 0.0) < args.minimum_entry_liquidity_usd:
            # A return computed against a pool holding a dollar is arithmetic, not
            # a trade. The first run of this study reported EV of +9,241 per
            # dollar, driven by mints whose entry pools held $1.17 and $1.23 --
            # the same defect STATUS.md already records at +130,273/dollar off a
            # $0.00-liquidity token. Unknown liquidity is excluded too: this is a
            # fail-closed floor, not a filter tuned until the number looked sane.
            untradeable += 1
        else:
            mature[mint] = payload

    cost = round_trip_cost(args.position_usd, settings.costs)
    cost_fraction = cost.pct_of_position / 100.0
    fraction = args.position_usd / args.equity_usd

    print(f"\n=== virality vs return, horizon >= {args.minimum_age_hours}h ===\n")
    print(f"  mints carrying social fields      {len(observations):>8,}")
    print(f"  too young for the horizon         {too_young:>8,}")
    print(f"  no entry price                    {no_entry_price:>8,}")
    print(
        f"  pool below"
        f" ${args.minimum_entry_liquidity_usd:,.0f}      {untradeable:>8,}"
    )
    print(f"  measurable                        {len(mature):>8,}")
    total_buckets = too_young + no_entry_price + untradeable + len(mature)
    print(f"  buckets sum                       {total_buckets:>8,} of {len(observations):,}")
    if total_buckets != len(observations):
        print("  DENOMINATOR DOES NOT RECONCILE -- results cannot be trusted.")
        return 1
    print(
        f"\n  costs: round trip ${cost.total_usd:.4f} on ${args.position_usd:.2f}"
        f" = {cost.pct_of_position:.2f}% of position, charged on both legs"
    )
    print(f"  sizing: ${args.position_usd:.2f} of ${args.equity_usd:.2f} equity"
          f" = {100 * fraction:.0f}% per trade")
    if not mature:
        print("\nUNPROVEN: nothing is old enough yet. Re-run as the journal fills.\n")
        return 0

    mints = sorted(mature)
    if args.limit:
        mints = mints[: args.limit]
    print(f"\npricing {len(mints):,} mints against DexScreener...")
    # Floor on **exit** liquidity, which is a different question from the entry
    # floor above. A mint whose only quoting pair holds four cents cannot be
    # sold into, so it is absent here and scored as a total loss below.
    prices = present_prices(
        mints,
        minimum_liquidity_usd=args.minimum_exit_liquidity_usd,
        # Median across qualifying venues, not the deepest one. See the docstring:
        # a single venue quoting 5,000x the other three won an outcome on pool
        # size alone and turned a 5.9x into 29,359x.
        use_median=True,
    )

    # A vanished pair is an outcome of 0.0, not a missing measurement. Dropping
    # them would measure the survivors and call it the market.
    multiples: dict[str, float] = {}
    vanished = 0
    for mint in mints:
        entry = float(mature[mint]["price_usd"])
        if mint not in prices:
            multiples[mint] = 0.0
            vanished += 1
        else:
            multiples[mint] = prices[mint] / entry
    print(f"  unsellable or gone (scored 0.0)   {vanished:>8,}")

    # Printed, never silently dropped. EV over a skewed distribution is
    # decided by a handful of rows, and a reader who cannot see them cannot
    # judge whether the number describes a market or a bad price.
    extremes = sorted(multiples.items(), key=lambda kv: -kv[1])[:5]
    print()
    print("  largest multiples (entry liquidity in brackets):")
    for mint, mult in extremes:
        liq = mature[mint].get("liquidity_usd")
        liq_txt = "unknown" if liq is None else f"${liq:,.0f}"
        print(f"    {mult:>12,.1f}x  {mint[:12]:<12} ({liq_txt})")

    followers = {
        m: int(mature[m]["twitter_followers"])
        for m in mints
        if mature[m].get("twitter_followers") is not None
    }
    has_handle = {m for m in mints if mature[m].get("twitter_handle")}
    verdicts_cache = cached_verdicts(args.verdict_cache)
    model_says = {
        m for m in mints if (verdicts_cache.get(m) or {}).get("accelerating") is True
    }
    model_flat = {
        m for m in mints if (verdicts_cache.get(m) or {}).get("accelerating") is False
    }
    model_positive = {
        m for m in mints if (verdicts_cache.get(m) or {}).get("sentiment") == "positive"
    }

    development = [m for m in mints if not held_out(m)]
    holdout = [m for m in mints if held_out(m)]
    print(f"  split: {len(development):,} development / {len(holdout):,} held out")
    print(f"  coverage: followers {len(followers):,}, handle {len(has_handle):,},"
          f" model {len(model_says) + len(model_flat):,}")

    # The threshold is fitted on death, in the companion study, and reused here.
    # Fitting a *second* threshold on return would be two shots at the same data.
    deaths = {m: multiples[m] < 0.10 for m in mints}
    threshold = fit_threshold(followers, deaths, development)

    print("\n=== base rate ===\n")
    base_out = describe("held out", holdout, multiples, cost_fraction, fraction)
    show(describe("development", development, multiples, cost_fraction, fraction))
    show(base_out)

    if threshold is not None:
        print(f"\n=== arm A: followers >= {threshold:,.0f} ===\n")
        arm_a_out = describe(
            "held out",
            [m for m in holdout if followers.get(m, -1) >= threshold],
            multiples, cost_fraction, fraction,
        )
        show(describe(
            "development",
            [m for m in development if followers.get(m, -1) >= threshold],
            multiples, cost_fraction, fraction,
        ))
        show(arm_a_out)
    else:
        arm_a_out = None
        print("\n  arm A: too few follower counts to fit a threshold")

    print("\n=== arm B: has a twitter handle ===\n")
    show(describe("development", [m for m in development if m in has_handle],
                  multiples, cost_fraction, fraction))
    show(describe("held out", [m for m in holdout if m in has_handle],
                  multiples, cost_fraction, fraction))

    print("\n=== arm C: the model's reading (never gates) ===\n")
    if not verdicts_cache:
        print("  No verdict cache. Run scripts/backfill_narrative_verdicts.py")
    else:
        print("  C1 -- model says spreading:")
        show(describe("development", [m for m in development if m in model_says],
                      multiples, cost_fraction, fraction))
        show(describe("held out", [m for m in holdout if m in model_says],
                      multiples, cost_fraction, fraction))
        print("  C1 -- model says flat:")
        show(describe("held out", [m for m in holdout if m in model_flat],
                      multiples, cost_fraction, fraction))
        print("  C2 -- model reads positive:")
        show(describe("held out", [m for m in holdout if m in model_positive],
                      multiples, cost_fraction, fraction))

    # ---------------------------------------------------------------- verdict
    print("\n=== verdict, against the rule fixed before the numbers ===\n")
    reasons: list[str] = []
    if arm_a_out is None:
        reasons.append("arm A could not be formed")
    else:
        base_ev = base_out["ev"]
        arm_ev = arm_a_out["ev"]
        arm_drop3 = arm_a_out["ev_drop3"]
        print(f"  base EV/$        {base_ev:+.4f}" if base_ev is not None else "  base EV n/a")
        print(f"  arm A EV/$       {arm_ev:+.4f}" if arm_ev is not None else "  arm A EV n/a")
        print(f"  arm A -top3      {arm_drop3:+.4f}" if arm_drop3 is not None
              else "  arm A -top3 n/a")
        print(f"  arm A cohort     {arm_a_out['n']:,}   (need {args.minimum_cohort})")
        if arm_ev is None or base_ev is None or arm_ev <= base_ev:
            reasons.append("arm A does not beat the base rate on EV")
        elif arm_drop3 is None or arm_drop3 <= base_ev:
            reasons.append("arm A does not beat the base rate after deleting its top 3")
        if arm_a_out["n"] < args.minimum_cohort:
            reasons.append(f"cohort {arm_a_out['n']} is below {args.minimum_cohort}")

    if reasons:
        print("\n  UNPROVEN:")
        for reason in reasons:
            print(f"    - {reason}")
        print("\n  Nothing is wired into the entry path.\n")
    else:
        print("\n  PASS on the rule as written. This licenses wiring the computed")
        print("  arm into the confluence count at that threshold and nothing else.\n")

    print(f"  outcomes priced: {len(multiples):,} of {len(mints):,}"
          f"  ({vanished:,} vanished, scored 0.0)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
