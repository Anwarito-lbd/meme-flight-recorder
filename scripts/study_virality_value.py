#!/usr/bin/env python3
"""Does a social signal at observation time predict whether a coin dies?

The operator's thesis is that meme coins are driven by virality rather than by
on-chain structure. Everything this project has measured and rejected tests a
different hypothesis: concentration, liquidity, routes, developer behaviour, age,
maturity bands, exits and sizing all describe chain structure or price shape.
**No study here has ever joined a social signal to an outcome.** So "no edge
found" is a true statement about somebody else's hypothesis.

The decision rule for this one was fixed in
`docs/superpowers/specs/2026-08-19-virality-value-design.md` and committed before
this file existed. Read that first; the summary is:

  * Verdict is on **death rate**, because this is a risk question and a risk gate
    is judged on death. Return is a separate and harder question.
  * Split by `sha256(mint)` rather than at random, so a re-run cannot reshuffle
    the halves until one comes out favourable.
  * Fit the follower threshold on the development half. Apply it **once** to the
    held-out half.
  * PASS needs the high-virality cohort at least `--minimum-edge-points` below
    the base rate on the held-out half, `--minimum-cohort` mints in it, and the
    same direction on both halves. Anything else is UNPROVEN.
  * Vanished mints count as dead. They are 84.2% of this population, and a study
    restricted to what still prices measures the survivors and calls it the
    population.

Arm C is the thesis stated precisely -- the local model's reading of the
narrative text -- and it is reported beside the arithmetic arms. It can never
gate, because a model asked the same question twice may answer differently. If it
wins, the honest response is to find the computable feature it is reacting to.

Read-only. It reads the journal and asks DexScreener what each mint quotes today.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from study_age_distribution import present_prices

from meme_flight_recorder.config import load_settings

# A mint quoted below this multiple of its first observed price is dead in the
# sense that matters: the position could not be recovered. Same figure the rest
# of this project uses, so results are comparable.
DEAD_BELOW = 0.10


def held_out(mint: str) -> bool:
    """Which half a mint belongs to.

    Hashed rather than randomised so the split is identical on every run. A
    random split re-rolled per run is an invitation to keep running until the
    halves agree, which is the failure this whole method exists to prevent.
    """
    return hashlib.sha256(mint.encode()).digest()[0] % 2 == 1


def social_observations(database_path: str) -> dict[str, dict[str, Any]]:
    """First observation per mint that carried the social fields at all.

    First, not best: it is the only moment a decision could have been made
    without hindsight. Mints observed before 2026-08-19 have no social fields and
    are absent from this dict entirely -- they are not scored as "no followers".
    """
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    first: dict[str, dict[str, Any]] = {}
    try:
        rows = connection.execute(
            "SELECT entity_id, observed_at, payload_json FROM events "
            "WHERE event_type = 'candidate_observed' ORDER BY id"
        )
        for mint, observed_at, payload_json in rows:
            if mint in first:
                continue
            payload = json.loads(payload_json)
            if "narrative_text_en" not in payload:
                continue  # pre-2026-08-19 row; the fields did not exist yet
            payload["observed_at"] = observed_at
            first[mint] = payload
    finally:
        connection.close()
    return first


def cached_verdicts(cache_path: str) -> dict[str, dict[str, Any]]:
    """The local model's reading of each mint's narrative text, if backfilled.

    Populated offline by `backfill_narrative_verdicts.py`, because the live
    verdict is journalled on `scout_candidate_evaluated` while the social fields
    are journalled on `candidate_observed` -- the two never meet on the same
    mint, and only 28 scout decisions exist against 2,150 texts.

    A missing cache is not an empty opinion: the arm reports that it could not be
    measured rather than scoring every mint as "the model said no".
    """
    if not Path(cache_path).exists():
        return {}
    connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select mint, sentiment, accelerating, model from verdicts"
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        connection.close()
    return {
        mint: {
            "sentiment": sentiment,
            # None stays None. "The model declined" is not "the model said flat".
            "accelerating": None if accelerating is None else bool(accelerating),
            "model": model,
        }
        for mint, sentiment, accelerating, model in rows
    }


def death_rate(mints: list[str], deaths: dict[str, bool]) -> tuple[float | None, int]:
    """Share of a cohort that died, and the cohort size."""
    scored = [m for m in mints if m in deaths]
    if not scored:
        return None, 0
    return 100.0 * sum(deaths[m] for m in scored) / len(scored), len(scored)


def fit_threshold(
    followers: dict[str, int], deaths: dict[str, bool], development: list[str]
) -> float | None:
    """The follower threshold that best separates death, on the development half.

    Fitted here and applied *once* to the held-out half. Candidate thresholds are
    the deciles of the observed distribution rather than round numbers, because a
    round number is a preference and a decile is a property of the data.
    """
    values = sorted(followers[m] for m in development if m in followers)
    if len(values) < 20:
        return None
    best_threshold: float | None = None
    best_gap = 0.0
    for fraction in range(1, 10):
        candidate = float(values[len(values) * fraction // 10])
        above = [m for m in development if followers.get(m, -1) >= candidate]
        below = [m for m in development if m in followers and followers[m] < candidate]
        above_rate, above_n = death_rate(above, deaths)
        below_rate, below_n = death_rate(below, deaths)
        if above_rate is None or below_rate is None or above_n < 20 or below_n < 20:
            continue
        gap = below_rate - above_rate  # positive means high-follower coins die less
        if gap > best_gap:
            best_gap = gap
            best_threshold = candidate
    return best_threshold


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minimum-age-hours", type=float, default=2.0)
    parser.add_argument("--minimum-cohort", type=int, default=100)
    parser.add_argument("--minimum-edge-points", type=float, default=10.0)
    parser.add_argument("--limit", type=int, default=0, help="cap mints priced (0 = all)")
    parser.add_argument(
        "--verdict-cache",
        default="data/narrative_verdicts.db",
        help="cache written by backfill_narrative_verdicts.py",
    )
    args = parser.parse_args()

    settings = load_settings()
    observations = social_observations(str(settings.database_path))
    if not observations:
        print("No observation carries the social fields yet. Nothing to study.")
        return 1

    now = datetime.now(UTC)
    mature: dict[str, dict[str, Any]] = {}
    too_young = 0
    no_entry_price = 0
    for mint, payload in observations.items():
        try:
            seen = datetime.fromisoformat(str(payload["observed_at"]))
        except (ValueError, KeyError):
            continue
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        if (now - seen).total_seconds() / 3600.0 < args.minimum_age_hours:
            too_young += 1
            continue
        if not payload.get("price_usd"):
            no_entry_price += 1
            continue
        mature[mint] = payload

    print(f"\n=== virality vs death, horizon >= {args.minimum_age_hours}h ===\n")
    print(f"  mints carrying social fields      {len(observations):>8,}")
    print(f"  too young for the horizon         {too_young:>8,}")
    print(f"  no entry price to compare against {no_entry_price:>8,}")
    print(f"  measurable                        {len(mature):>8,}")
    reconciled = too_young + no_entry_price + len(mature)
    print(f"  buckets sum                       {reconciled:>8,} of {len(observations):,}")
    if reconciled != len(observations):
        print("  DENOMINATOR DOES NOT RECONCILE -- results below cannot be trusted.")
        return 1
    if not mature:
        print("\nUNPROVEN: nothing is old enough yet. Re-run as the journal fills.\n")
        return 0

    mints = sorted(mature)
    if args.limit:
        mints = mints[: args.limit]
    print(f"\npricing {len(mints):,} mints against DexScreener...")
    prices = present_prices(mints)

    # Vanished counts as dead. It is the single most consequential choice here:
    # 84.2% of this population no longer quotes anywhere, and dropping them would
    # measure the survivors and call it the population.
    deaths: dict[str, bool] = {}
    vanished = 0
    for mint in mints:
        entry = float(mature[mint]["price_usd"])
        if mint not in prices:
            deaths[mint] = True
            vanished += 1
        else:
            deaths[mint] = (prices[mint] / entry) < DEAD_BELOW
    print(f"  vanished (no pair quoted today)   {vanished:>8,}  counted as dead")

    followers = {
        m: int(mature[m]["twitter_followers"])
        for m in mints
        if mature[m].get("twitter_followers") is not None
    }
    has_handle = {m for m in mints if mature[m].get("twitter_handle")}
    verdicts_cache = cached_verdicts(args.verdict_cache)
    # Only mints the model actually answered for. A declined answer is excluded
    # from both sides rather than counted as "not spreading".
    model_says = {
        m for m in mints if (verdicts_cache.get(m) or {}).get("accelerating") is True
    }
    model_flat = {
        m for m in mints if (verdicts_cache.get(m) or {}).get("accelerating") is False
    }
    model_positive = {
        m for m in mints if (verdicts_cache.get(m) or {}).get("sentiment") == "positive"
    }

    base_rate, base_n = death_rate(mints, deaths)
    print(f"\n  BASE RATE  death {base_rate:.1f}%  over {base_n:,} mints")
    print(f"  coverage: followers on {len(followers):,}, handle on {len(has_handle):,},"
          f" model verdict on {len(model_says):,}")

    development = [m for m in mints if not held_out(m)]
    holdout = [m for m in mints if held_out(m)]
    print(f"  split: {len(development):,} development / {len(holdout):,} held out\n")

    threshold = fit_threshold(followers, deaths, development)
    if threshold is None:
        print("UNPROVEN: too few mints carry a follower count to fit a threshold.\n")
        return 0
    print(f"=== arm A: followers >= {threshold:,.0f} (fitted on development only) ===\n")

    verdicts: dict[str, tuple[float | None, int]] = {}
    for label, cohort in (
        ("development", development),
        ("held out", holdout),
    ):
        above = [m for m in cohort if followers.get(m, -1) >= threshold]
        below = [m for m in cohort if m in followers and followers[m] < threshold]
        a_rate, a_n = death_rate(above, deaths)
        b_rate, b_n = death_rate(below, deaths)
        verdicts[label] = (a_rate, a_n)
        a_txt = "n/a" if a_rate is None else f"{a_rate:5.1f}%"
        b_txt = "n/a" if b_rate is None else f"{b_rate:5.1f}%"
        print(f"  {label:<12} above {a_txt} (n={a_n:,})   below {b_txt} (n={b_n:,})")

    print("\n=== arm B: has a twitter handle at all ===\n")
    for label, cohort in (("development", development), ("held out", holdout)):
        with_h = [m for m in cohort if m in has_handle]
        without = [m for m in cohort if m not in has_handle]
        w_rate, w_n = death_rate(with_h, deaths)
        o_rate, o_n = death_rate(without, deaths)
        w_txt = "n/a" if w_rate is None else f"{w_rate:5.1f}%"
        o_txt = "n/a" if o_rate is None else f"{o_rate:5.1f}%"
        print(f"  {label:<12} handle {w_txt} (n={w_n:,})   none {o_txt} (n={o_n:,})")

    print()
    print("=== arm C: the model's reading (ranks only, can never gate) ===")
    print()
    if not verdicts_cache:
        print("  No verdict cache. Run:")
        print("    scripts/backfill_narrative_verdicts.py")
        print("  An absent cache is 'not measured', never 'the model said no'.")
    else:
        models = sorted({v["model"] for v in verdicts_cache.values()})
        answered = len(model_says) + len(model_flat)
        print(f"  model(s): {models}")
        print(
            f"  answered for {answered:,} of {len(mints):,} mints;"
            f" declined on {len(mints) - answered:,}"
        )
        print()
        print("  C1 -- the model says the story is spreading:")
        for label, cohort in (("development", development), ("held out", holdout)):
            said = [m for m in cohort if m in model_says]
            flat = [m for m in cohort if m in model_flat]
            s_rate, s_n = death_rate(said, deaths)
            f_rate, f_n = death_rate(flat, deaths)
            s_txt = "n/a" if s_rate is None else f"{s_rate:5.1f}%"
            f_txt = "n/a" if f_rate is None else f"{f_rate:5.1f}%"
            print(
                f"    {label:<12} spreading {s_txt} (n={s_n:,})"
                f"   flat {f_txt} (n={f_n:,})"
            )
        print()
        print("  C2 -- the model reads the text as positive:")
        for label, cohort in (("development", development), ("held out", holdout)):
            pos = [m for m in cohort if m in model_positive]
            rest = [m for m in cohort if m in verdicts_cache and m not in model_positive]
            p_rate, p_n = death_rate(pos, deaths)
            r_rate, r_n = death_rate(rest, deaths)
            p_txt = "n/a" if p_rate is None else f"{p_rate:5.1f}%"
            r_txt = "n/a" if r_rate is None else f"{r_rate:5.1f}%"
            print(
                f"    {label:<12} positive  {p_txt} (n={p_n:,})"
                f"   rest {r_txt} (n={r_n:,})"
            )
        print()
        print("  Reported, never wired. A model asked the same question twice may")
        print("  answer differently; if this arm beats arm A, the honest response")
        print("  is to find the computable feature it reacts to and gate on that.")

    # ---------------------------------------------------------------- verdict
    dev_rate, _dev_n = verdicts["development"]
    out_rate, out_n = verdicts["held out"]
    print("\n=== verdict, against the rule fixed before the numbers ===\n")
    reasons: list[str] = []
    if out_rate is None or base_rate is None:
        reasons.append("no held-out cohort could be scored")
    else:
        edge = base_rate - out_rate
        print(f"  held-out edge over base rate      {edge:>6.1f} points"
              f"  (need {args.minimum_edge_points})")
        print(f"  held-out cohort size              {out_n:>6,}"
              f"        (need {args.minimum_cohort})")
        if edge < args.minimum_edge_points:
            reasons.append(f"edge {edge:.1f} points is below {args.minimum_edge_points}")
        if out_n < args.minimum_cohort:
            reasons.append(f"cohort {out_n} is below {args.minimum_cohort}")
        if dev_rate is not None and (dev_rate - base_rate) * (out_rate - base_rate) <= 0:
            reasons.append("the two halves disagree on direction")

    if reasons:
        print("\n  UNPROVEN:")
        for reason in reasons:
            print(f"    - {reason}")
        print("\n  Nothing is wired into the entry path. Re-run as the journal fills.\n")
    else:
        print("\n  PASS on the rule as written.")
        print("  This licenses exactly one thing: wiring the *computed* arm into")
        print("  the confluence count at the threshold above. It does not license")
        print("  larger positions, a wider readiness_policy, or live execution.\n")

    counts = Counter("dead" if deaths[m] else "alive" for m in mints)
    print(f"  outcome buckets: {dict(counts)}  summing to {sum(counts.values()):,}"
          f" of {len(mints):,}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
