#!/usr/bin/env python3
"""Does the holder threshold survive its confound, and does it make money?

`study_holder_threshold.py` returned PROVEN out of sample: above 550 holders at
first observation the held-out winner rate is 46.1% against 3.1% below, with a
lower death rate, surviving the deletion of its best token. That is the first
result in this project that points toward winners rather than away from them, and
it is therefore the one most worth attacking.

Two attacks, both fixed in advance.

**Attack one: the confound.** Holder count grows with time, and a token observed
later has already survived longer. If the effect is really "we happened to look at
this one later", it will collapse once age is held constant. Age alone was already
measured not to separate outcomes -- winner rates of 14.5%, 13.9%, 15.6%, 13.4%
across age quartiles -- so it should not be able to carry this, but "should not" is
a prediction and this checks it. The threshold survives only if the winner-rate
ratio holds **within** age bands, not merely across the pooled sample.

**Attack two: the economics.** A higher winner rate is not a profit. The median
token above the threshold is 0.78x, so the typical trade there still loses money;
the question is whether the winners pay for that. Growth is computed
geometrically, not as arithmetic expectancy, because arithmetic expectancy chose
the 10% position size that decays this account to a quarter of itself over 50
trades.

THE DECISION RULE, FIXED BEFORE THE NUMBERS WERE SEEN
-----------------------------------------------------
The threshold is worth wiring into entry only if **all** of:

  1. the winner-rate ratio stays at or above 2x inside **every** age band that
     holds at least 30 outcomes on both sides;
  2. geometric growth per trade above the threshold is **positive** at the
     configured position size, after costs charged on both legs;
  3. growth stays positive after **deleting the single best trade**;
  4. profit factor above the threshold exceeds 1.2, the same bar `CohortLimits`
     applies to everything else here.

Anything less is UNPROVEN and the threshold stays out of the entry path. Failing
condition 2 or 3 while passing 1 is a specific and useful outcome: it would mean
the signal is real and still not tradeable, which is the shape every other finding
in this project has taken.

TWO CORRECTIONS THAT CHANGED THE ANSWER
---------------------------------------
Both were found by disbelieving the first result, and both are the difference
between a fantasy and a finding.

**1. Exits were unrealisable.** The first run reported a profit factor of 662 and
an equity multiple of 5.9e24 over 50 trades. It was driven by nominal price ratios
with no exit constraint -- WeLM at 10,270x, observed at $0.0000085 in a $16,132
pool, which on a $10 position means selling $102,700 into sixteen thousand
dollars of depth. `realisable_multiple` now caps the exit at the configured share
of pool depth. A liquidity *floor* does not prevent this: the floor screens the
entry, the absurdity is in the exit.

**2. Vanished mints were excluded.** They are now counted as TOTAL LOSSES, which
is what they are. This matters more than the cap: **51.7% of the above-threshold
cohort and 60.0% of the below-threshold cohort vanish**, so excluding them measured
"what happened to the survivors" -- not a question anyone can trade. `CLAUDE.md`
is explicit that no rate from this project may be quoted without stating whether
vanished mints are in the denominator, and the first version of this study broke
that rule.

Together they took growth from +0.249/trade to +0.006, and the equity multiple
over 50 trades from 252,497x to 1.35x.

WHAT THIS ASSUMES, STATED
-------------------------
  * **Buy and hold to today.** The multiple is today's price over the price at
    first observation, so this measures the signal, not an exit policy. Exits were
    separately measured across nine policies and none produced positive growth, so
    layering one here would import a known-negative term.
  * **The exit cap uses pool depth at observation**, the only depth journalled. A
    token that genuinely ran will usually have deepened its pool by the time you
    sell, so the truth sits between the capped and uncapped columns. Nothing
    should be acted on unless the capped case pays.
  * **Holders is a vendor count**, unverified on chain and cheap to manufacture.
    A purchasable signal is not a signal.

Read-only.
"""

from __future__ import annotations

import argparse
import math
import statistics
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

MINIMUM_SIDE_SAMPLE = 30
REQUIRED_WINNER_RATIO = 2.0
MINIMUM_PROFIT_FACTOR = 1.2


def realisable_multiple(
    gross: float, position_usd: float, pool_usd: float, pool_share_pct: float
) -> float:
    """The multiple an order this size could actually have taken out of that pool.

    **This is the correction that decides the study.** Without it the first run
    reported a profit factor of 662 and an equity multiple of 5.9e24 over 50
    trades, driven by tokens like WeLM at a nominal 10,270x -- observed at
    $0.0000085 in a $16,132 pool. Realising that on a $10 position means selling
    $102,700 into a pool that held sixteen thousand. The number is arithmetic, not
    a trade.

    That is the defect this project already has on record as "an entry study
    reporting EV of +130,273/dollar off a $0.00-liquidity token", and a liquidity
    *floor* does not prevent it: the floor screens the entry, while the absurdity
    is in the exit.

    The cap is `pool_share_pct` of the pool depth **at observation**, which is the
    only depth this journal records. It is conservative in one direction -- a token
    that genuinely ran will usually have grown its pool by the time you sell -- so
    the honest reading is that the truth sits between the capped and uncapped
    figures, and nothing should be acted on unless the *capped* case pays.
    """
    if pool_usd <= 0 or position_usd <= 0:
        return gross
    ceiling = (pool_share_pct / 100.0) * pool_usd / position_usd
    return min(gross, max(ceiling, 0.0))


def net_multiple(gross: float, position_usd: float, cost_usd: float) -> float:
    """What the position returns after both legs, as a multiple of the stake."""
    return (position_usd * gross - cost_usd) / position_usd


def geometric_growth(net_multiples: list[float]) -> float | None:
    """Mean log growth per trade at full position.

    A total loss makes log growth negative infinity, which is the honest answer for
    an all-in bet and useless for a fractional one. Positions here are a fraction
    of equity, so the fraction is applied before the log: this is growth of the
    *account*, not of the position.
    """
    if not net_multiples:
        return None
    return statistics.fmean(math.log(max(m, 1e-9)) for m in net_multiples)


def fractional_growth(net_multiples: list[float], fraction: float) -> float | None:
    """Account growth per trade when each trade risks `fraction` of equity."""
    if not net_multiples or fraction <= 0:
        return None
    values = []
    for multiple in net_multiples:
        equity_after = 1.0 + fraction * (multiple - 1.0)
        values.append(math.log(max(equity_after, 1e-9)))
    return statistics.fmean(values)


def profit_factor(net_multiples: list[float], position_usd: float) -> float | None:
    wins = [(m - 1.0) * position_usd for m in net_multiples if m > 1.0]
    losses = [(1.0 - m) * position_usd for m in net_multiples if m < 1.0]
    if not losses:
        return None
    return sum(wins) / sum(losses)


def describe(label: str, net: list[float], position_usd: float, fraction: float) -> None:
    if not net:
        print(f"  {label:<30} no trades")
        return
    growth = fractional_growth(net, fraction)
    factor = profit_factor(net, position_usd)
    print(
        f"  {label:<30}{len(net):>6}"
        f"{statistics.median(net):>11.4g}"
        f"{statistics.fmean(net):>12.4g}"
        f"{(growth if growth is not None else float('nan')):>12.5f}"
        f"{(factor if factor is not None else float('inf')):>10.3f}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=550.0)
    parser.add_argument("--dead-below", type=float, default=0.10)
    parser.add_argument("--winner-at", type=float, default=2.0)
    parser.add_argument("--minimum-liquidity", type=float, default=5_000.0)
    parser.add_argument(
        "--position-usd",
        type=float,
        default=None,
        help="Defaults to the operator's configured size.",
    )
    arguments = parser.parse_args()

    settings = load_settings()
    position_usd = arguments.position_usd or 10.0
    fraction = position_usd / settings.starting_equity_usd
    cost = round_trip_cost(position_usd, settings.costs)

    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled. 'No data', not 'no effect'.")
        return 1

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
        and payload.get("age_minutes") is not None
    }
    prices = DexScreenerProvider().prices_for_tokens(list(eligible))

    # A vanished mint is a TOTAL LOSS, not an excluded row. The first version of
    # this study dropped them and reported an equity multiple of 252,497x over 50
    # trades. Over half of both sides vanish -- 51.7% above the threshold and 60.0%
    # below -- so excluding them measured "what happened to the survivors", which
    # is not a question anyone can trade. CLAUDE.md: do not quote a rate from this
    # project without stating whether vanished mints are in the denominator.
    rows: list[tuple[str, float, float, float, float]] = []  # when, holders, age, gross, pool
    vanished = 0
    for mint, (when, payload) in eligible.items():
        now = prices.get(mint)
        if not now:
            vanished += 1
            rows.append(
                (
                    when,
                    float(payload["holders"]),
                    float(payload["age_minutes"]),
                    0.0,  # the pair is gone; the position is worth nothing
                    float(payload.get("liquidity_usd") or 0.0),
                )
            )
            continue
        rows.append(
            (
                when,
                float(payload["holders"]),
                float(payload["age_minutes"]),
                now / float(payload["price_usd"]),
                float(payload.get("liquidity_usd") or 0.0),
            )
        )

    print(f"{len(first)} distinct mints journalled")
    print(f"  {len(first) - len(eligible):>6} lacking price, depth, holders or age")
    print(f"  {vanished:>6} vanished -- counted as TOTAL LOSSES, not excluded")
    print(f"  {len(rows) - vanished:>6} still quoted, with a measured multiple")
    print(f"  {len(rows):>6} outcomes in total")
    if (len(first) - len(eligible)) + len(rows) != len(first):
        print("  MISMATCH -- do not read below.")
        return 1
    if len(rows) < MINIMUM_SIDE_SAMPLE * 4:
        print("\nToo few outcomes. 'No data', not 'no effect'.")
        return 0

    rows.sort(key=lambda row: row[0])
    heldout = rows[len(rows) // 2 :]
    print(f"\nheld-out half only: {len(heldout)} outcomes")
    print(
        f"position ${position_usd:.2f} on ${settings.starting_equity_usd:.2f} equity"
        f" = {100 * fraction:.1f}% per trade; round trip ${cost.total_usd:.4f}"
        f" ({cost.pct_of_position:.2f}%)"
    )

    # ---- Attack one: does it survive controlling for age? ----
    print("\n=== attack one: the age confound ===")
    ages = sorted(age for _, _, age, _, _p in heldout)
    edges = statistics.quantiles(ages, n=3) if len(ages) >= 3 else []
    if not edges:
        print("  too few outcomes to band by age")
        bands: list[tuple[str, list[tuple[str, float, float, float]]]] = [("all", heldout)]
    else:
        bands = [
            (f"age <= {edges[0]:.1f}m", [r for r in heldout if r[2] <= edges[0]]),
            (
                f"age {edges[0]:.1f}-{edges[1]:.1f}m",
                [r for r in heldout if edges[0] < r[2] <= edges[1]],
            ),
            (f"age > {edges[1]:.1f}m", [r for r in heldout if r[2] > edges[1]]),
        ]
    header = f"  {'age band':<22}{'n>thr':>7}{'win%>':>8}{'n<=thr':>8}{'win%<=':>8}{'ratio':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    confound_failures: list[str] = []
    for label, band in bands:
        above = [g for _, h, _, g, _p in band if h > arguments.threshold]
        below = [g for _, h, _, g, _p in band if h <= arguments.threshold]
        wa = 100.0 * sum(1 for g in above if g >= arguments.winner_at) / len(above) if above else 0.0
        wb = 100.0 * sum(1 for g in below if g >= arguments.winner_at) / len(below) if below else 0.0
        ratio = (wa / wb) if wb > 0 else None
        shown = "inf" if ratio is None and wa > 0 else ("n/a" if ratio is None else f"{ratio:.2f}")
        print(
            f"  {label:<22}{len(above):>7}{wa:>8.1f}{len(below):>8}{wb:>8.1f}{shown:>8}"
        )
        if len(above) >= MINIMUM_SIDE_SAMPLE and len(below) >= MINIMUM_SIDE_SAMPLE:
            if ratio is not None and ratio < REQUIRED_WINNER_RATIO:
                confound_failures.append(f"{label}: ratio {ratio:.2f}x below {REQUIRED_WINNER_RATIO}x")
        else:
            print(f"    (not scored: needs {MINIMUM_SIDE_SAMPLE} on both sides)")

    # ---- Attack two: does it make money? ----
    print("\n=== attack two: the economics, held-out, after costs ===")
    header = (
        f"  {'cohort':<30}{'n':>6}{'median':>11}{'mean':>12}"
        f"{'growth/trade':>12}{'PF':>10}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
        # Both readings are computed. The capped one decides; the uncapped one is
    # printed beside it so the size of the correction is visible rather than
    # hidden inside a single number.
    pool_share = settings.micro.maximum_pool_share_pct
    above_rows = [r for r in heldout if r[1] > arguments.threshold]
    below_rows = [r for r in heldout if r[1] <= arguments.threshold]
    above_gross = [g for _, _, _, g, _p in above_rows]
    below_gross = [g for _, _, _, g, _p in below_rows]
    above_net_raw = [net_multiple(g, position_usd, cost.total_usd) for g in above_gross]
    below_net_raw = [net_multiple(g, position_usd, cost.total_usd) for g in below_gross]
    above_net = [
        net_multiple(
            realisable_multiple(g, position_usd, pool, pool_share), position_usd, cost.total_usd
        )
        for _, _, _, g, pool in above_rows
    ]
    below_net = [
        net_multiple(
            realisable_multiple(g, position_usd, pool, pool_share), position_usd, cost.total_usd
        )
        for _, _, _, g, pool in below_rows
    ]
    print(f"  exit capped at {pool_share}% of pool depth at observation")
    describe("UNCAPPED above (not evidence)", above_net_raw, position_usd, fraction)
    describe("UNCAPPED below (not evidence)", below_net_raw, position_usd, fraction)
    describe(f"holders > {arguments.threshold:,.0f}", above_net, position_usd, fraction)
    describe(f"holders <= {arguments.threshold:,.0f}", below_net, position_usd, fraction)
    ranked = sorted(above_net, reverse=True)
    without_best = ranked[1:]
    describe("above, minus best trade", without_best, position_usd, fraction)
    # Drop-2 and drop-3 are printed because drop-1 alone is a weak test when the
    # margin is thin. The `deep >=$50k` filter was withdrawn on exactly this
    # check, and a rule that survives losing one token but not three is a rule
    # about three tokens.
    describe("above, minus best 2", ranked[2:], position_usd, fraction)
    describe("above, minus best 3", ranked[3:], position_usd, fraction)

    growth = fractional_growth(above_net, fraction)
    growth_wb = fractional_growth(without_best, fraction)
    factor = profit_factor(above_net, position_usd)

    failures = list(confound_failures)
    if growth is None or growth <= 0:
        failures.append(f"growth/trade {growth if growth is None else f'{growth:.5f}'} <= 0")
    if growth_wb is None or growth_wb <= 0:
        failures.append(
            f"growth without best trade {growth_wb if growth_wb is None else f'{growth_wb:.5f}'} <= 0"
        )
    if factor is None or factor <= MINIMUM_PROFIT_FACTOR:
        shown = "no losing trades" if factor is None else f"{factor:.3f}"
        failures.append(f"profit factor {shown} <= {MINIMUM_PROFIT_FACTOR}")

    if growth is not None:
        print(
            f"\n  equity multiple after 50 trades above the threshold:"
            f" {math.exp(growth * 50):.3f}x"
        )

    # Tail fragility, reported alongside the verdict rather than folded into it.
    # Condition 3 as pre-registered only requires surviving the loss of ONE trade,
    # and that turned out to be too weak: this cohort clears it at +0.0006 and goes
    # negative at drop-2. Rewriting condition 3 now so the verdict comes out
    # differently would be retro-fitting a rule to a result, so the verdict below
    # still answers the rule as written and this states what the rule failed to ask.
    fragility: list[str] = []
    for label, subset in (("2", ranked[2:]), ("3", ranked[3:])):
        value = fractional_growth(subset, fraction)
        if value is not None and value <= 0:
            fragility.append(
                f"growth turns negative after deleting the best {label} trades ({value:.5f})"
            )

    print("\n=== verdict, against the rule fixed in advance ===")
    if failures:
        print("UNPROVEN. Failing conditions:")
        for failure in failures:
            print(f"  - {failure}")
        print("\nThe threshold stays out of the entry path.")
        if not confound_failures:
            print(
                "Note it survived the age confound. A real signal that is still not"
                "\ntradeable is the shape every other finding in this project has taken."
            )
    else:
        print("PROVEN: the holder threshold survives its confound and pays after costs.")
        if fragility:
            print()
            print("*** BUT READ THIS BEFORE ACTING ON IT ***")
            for line in fragility:
                print(f"  - {line}")
            print(
                f"  The edge is carried by a handful of tokens out of {len(above_net)}."
                "\n  Condition 3 only required surviving the loss of one trade, which"
                "\n  is weaker than the test that withdrew the deep>=$50k filter. Treat"
                "\n  this as a lottery with better pricing, not as an edge, and do not"
                "\n  size on it until a forward sample reproduces it."
            )
        print("Before it sizes anything, verify the holder count on chain -- a vendor")
        print("number that can be manufactured cheaply is a purchasable signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
