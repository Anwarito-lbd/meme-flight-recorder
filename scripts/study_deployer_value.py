#!/usr/bin/env python3
"""Does the deployer verdict predict death?

**This study judges a risk gate on risk.** It reports death rate first and
return second, and that ordering is the point rather than a presentation
choice. The project already measured what happens when a risk gate is graded on
return: cluster-disqualified tokens outperformed cluster-clear ones, and the
largest winner ever recorded was rejected for developer selling. Had that been
read as "the gate is wrong", the gate protecting this account from serial
deployers would have been removed on the strength of one outlier.

So the question here is narrow and deliberately unflattering to the gate's
apparent performance: **do tokens with an ADVERSE deployer die more often than
tokens without one?** If they also produce higher returns, that is the expected
result for this market and is not a reason to loosen anything.

Runs on vendor-reported deployer fields already in the journal -- developer
distribution, prior migrations, wash-trading -- so it costs no API budget and
needs no Helius key. Chain-derived deployer history is a forward-looking
addition; it cannot be backfilled onto candidates already observed, because the
wallet endpoint returns the wallet's state now rather than at observation time.

**No look-ahead.** The verdict comes from the *first* observation of each mint,
which is the only moment a trader could have acted on it.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from typing import Any

from meme_flight_recorder.config import DeployerLimits, load_settings
from meme_flight_recorder.deployer import (
    DeployerEvidence,
    DeployerVerdict,
    assess_deployer,
)
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

VERDICT_ORDER: tuple[DeployerVerdict, ...] = (
    DeployerVerdict.CLEAN,
    DeployerVerdict.UNRESOLVED,
    DeployerVerdict.ADVERSE,
)


def evidence_from_payload(payload: dict[str, Any]) -> DeployerEvidence | None:
    """Rebuild vendor-only deployer evidence from a journalled observation.

    Returns None when the vendor reported none of the deployer fields, so that
    "the vendor said nothing" stays distinguishable from "the vendor said the
    developer is clean". Collapsing those two is how an unexamined token joins
    the examined ones and dilutes whatever effect exists.
    """
    metrics = payload.get("cluster_metrics") or {}
    reported = {
        key: metrics.get(key)
        for key in ("dev_sell_pct", "dev_migrate_count", "dev_wash_trading")
        if metrics.get(key) is not None
    }
    if not reported:
        return None
    return DeployerEvidence(
        address=str(payload.get("dev_address") or "unknown"),
        # Vendor labels carry no transaction history, so this evidence can never
        # reach CLEAN -- only ADVERSE or UNRESOLVED. That is correct: a label is
        # not an audit.
        transactions_observed=0,
        history_truncated=True,
        vendor_dev_sell_pct=reported.get("dev_sell_pct"),
        vendor_prior_migrations=(
            int(reported["dev_migrate_count"]) if "dev_migrate_count" in reported else None
        ),
        vendor_wash_trading=reported.get("dev_wash_trading"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dead-below", type=float, default=0.10, help="Multiple counted as dead.")
    arguments = parser.parse_args()

    settings = load_settings()
    limits: DeployerLimits = settings.deployer
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

    no_vendor_fields: list[str] = []
    no_entry_price: list[str] = []
    verdicts: dict[str, DeployerVerdict] = {}
    entry_prices: dict[str, float] = {}

    for mint, payload in first.items():
        evidence = evidence_from_payload(payload)
        if evidence is None:
            no_vendor_fields.append(mint)
            continue
        price = payload.get("price_usd")
        if not price or float(price) <= 0:
            no_entry_price.append(mint)
            continue
        verdicts[mint] = assess_deployer(evidence, limits, current_mint=mint).verdict
        entry_prices[mint] = float(price)

    print(f"{len(first)} distinct mints journalled")
    print(f"  {len(no_vendor_fields)} with no vendor deployer fields at all")
    print(f"  {len(no_entry_price)} with no usable entry price")
    print(f"  {len(verdicts)} with a verdict, awaiting outcome lookup\n")

    prices = DexScreenerProvider().prices_for_tokens(list(verdicts))
    unresolved = [mint for mint in verdicts if mint not in prices]
    print(f"resolved current prices for {len(prices)}, unresolved {len(unresolved)}\n")

    by_verdict: dict[DeployerVerdict, list[float]] = defaultdict(list)
    for mint, verdict in verdicts.items():
        now = prices.get(mint)
        if not now:
            continue
        by_verdict[verdict].append(now / entry_prices[mint])

    measured = sum(len(values) for values in by_verdict.values())
    reconciled = len(no_vendor_fields) + len(no_entry_price) + len(unresolved) + measured
    print(
        f"denominator: {len(no_vendor_fields)} + {len(no_entry_price)} "
        f"+ {len(unresolved)} + {measured}"
    )
    print(f"           = {reconciled}, total mints {len(first)}")
    if reconciled != len(first):
        print("  MISMATCH -- some mints are unaccounted for; do not read the table below.")
        return 1
    print()

    if not measured:
        print("No outcomes could be measured. This is 'no data', not 'no effect'.")
        return 0

    header = f"{'deployer verdict':<20} {'n':>4} {'dead':>6} {'survived':>9} {'median':>8} {'>2x':>5}"
    print("judged on survival, which is what a risk gate is for")
    print(header)
    print("-" * len(header))
    for verdict in VERDICT_ORDER:
        values = by_verdict.get(verdict) or []
        if not values:
            print(f"{verdict.value:<20} {0:>4}     --        --       --    --")
            continue
        dead = sum(1 for value in values if value < arguments.dead_below)
        winners = sum(1 for value in values if value >= 2.0)
        print(
            f"{verdict.value:<20} {len(values):>4} {100 * dead / len(values):>5.0f}% "
            f"{100 * (len(values) - dead) / len(values):>8.0f}% "
            f"{statistics.median(values):>8.2f} {100 * winners / len(values):>4.0f}%"
        )

    adverse = by_verdict.get(DeployerVerdict.ADVERSE) or []
    benign = [
        value
        for verdict, values in by_verdict.items()
        if verdict is not DeployerVerdict.ADVERSE
        for value in values
    ]

    print(f"\n'dead' = fell below {arguments.dead_below:.0%} of the first observed price.")
    print("CLEAN cannot appear above: vendor labels carry no wallet history, and")
    print("history is what clearing a deployer requires.\n")

    if len(adverse) < 20 or len(benign) < 20:
        print("VERDICT: UNPROVEN -- sample too small on one side to separate the groups.")
        print("Keep the gate (it costs nothing to keep) and collect more observations.")
        return 0

    adverse_death = sum(1 for value in adverse if value < arguments.dead_below) / len(adverse)
    benign_death = sum(1 for value in benign if value < arguments.dead_below) / len(benign)

    if adverse_death > benign_death:
        print(
            f"VERDICT: the gate earns its place. ADVERSE deployers died "
            f"{100 * adverse_death:.0f}% of the time versus {100 * benign_death:.0f}%."
        )
    else:
        print(
            f"VERDICT: UNPROVEN on survival. ADVERSE deployers died "
            f"{100 * adverse_death:.0f}% versus {100 * benign_death:.0f}%."
        )
        print("This does not automatically mean remove it. Check whether the deaths it")
        print("does catch are total losses rather than ordinary ones -- a gate that")
        print("prevents rare catastrophes will not show up in an average.")

    if adverse and benign and statistics.median(adverse) > statistics.median(benign):
        print()
        print("Note: ADVERSE tokens also returned more. That is the expected shape in")
        print("this market -- a developer selling is a developer promoting -- and it is")
        print("NOT a reason to loosen the gate. Return is not what this gate measures.")

    _coverage_matched_comparison(first, prices, entry_prices, arguments.dead_below)
    return 0


def _coverage_matched_comparison(
    first: dict[str, dict[str, Any]],
    prices: dict[str, float],
    entry_prices: dict[str, float],
    dead_below: float,
) -> None:
    """Compare like with like on the one field that drives most rejections.

    The headline table above has a selection problem that would otherwise be
    read as a finding. Its non-adverse group is dominated by tokens where the
    vendor reported almost nothing -- and a vendor reports nothing about a token
    nobody is trading. So that comparison is partly "active token versus inert
    token", not "bad developer versus good developer", and the inert group dying
    more is close to a tautology.

    The honest test holds coverage constant: among mints where developer selling
    was *reported at all*, compare the ones where it was nonzero against the ones
    where it was zero. Both groups were examined; only the finding differs.
    """
    selling: list[float] = []
    not_selling: list[float] = []

    for mint, payload in first.items():
        value = (payload.get("cluster_metrics") or {}).get("dev_sell_pct")
        now = prices.get(mint)
        entry = entry_prices.get(mint)
        if value is None or now is None or not entry:
            continue
        (selling if float(value) > 0 else not_selling).append(now / entry)

    print("\ncoverage-matched: mints where developer selling was reported either way")
    header = f"{'developer selling':<20} {'n':>4} {'dead':>6} {'median':>8} {'>2x':>5}"
    print(header)
    print("-" * len(header))
    for label, values in (("reported > 0", selling), ("reported 0", not_selling)):
        if not values:
            print(f"{label:<20} {0:>4}     --       --    --")
            continue
        dead = sum(1 for value in values if value < dead_below)
        winners = sum(1 for value in values if value >= 2.0)
        print(
            f"{label:<20} {len(values):>4} {100 * dead / len(values):>5.0f}% "
            f"{statistics.median(values):>8.2f} {100 * winners / len(values):>4.0f}%"
        )

    if len(selling) < 20 or len(not_selling) < 20:
        print("\nToo few on one side to read. This is the comparison that matters;")
        print("collect until both groups pass 20 before drawing any conclusion.")
        return

    selling_death = sum(1 for value in selling if value < dead_below) / len(selling)
    clean_death = sum(1 for value in not_selling if value < dead_below) / len(not_selling)
    print(
        f"\ndeath rate {100 * selling_death:.0f}% when selling vs "
        f"{100 * clean_death:.0f}% when not."
    )
    if selling_death <= clean_death:
        print("The gate does not buy survival on this sample. Record that plainly.")
        print("Keep it only if a mechanism justifies it, not because it feels prudent.")
    else:
        print("The gate buys survival on this sample, which is what it is for.")

    _by_selling_band(first, prices, entry_prices, dead_below)


DEV_SELL_BANDS: tuple[tuple[str, float, float], ...] = (
    ("zero", 0.0, 0.0),
    ("dust (<0.01%)", 0.0, 0.01),
    ("partial (<50%)", 0.01, 50.0),
    ("dumped (>=50%)", 50.0, float("inf")),
)


def _by_selling_band(
    first: dict[str, dict[str, Any]],
    prices: dict[str, float],
    entry_prices: dict[str, float],
    dead_below: float,
) -> None:
    """Split developer selling by magnitude rather than treating it as a flag.

    The gate's threshold is ``maximum_dev_sell_pct = 0.0``, so *any* nonzero
    value rejects. But the reported values are bimodal: roughly a fifth are
    dust -- one observed candidate was rejected on 1.47e-08 percent of supply --
    and most of the rest are a developer who has sold essentially everything.
    Those are opposite situations being handed the same verdict, and a single
    boolean cannot tell them apart.

    The distinction has a mechanism behind it, which is the standard this
    project requires before a pattern is allowed to become a rule: a developer
    who has already exited holds no supply to sell later. The overhang is spent.
    A token whose developer has *not* sold still has that supply pointed at it,
    and no stop survives the moment it arrives.
    """
    banded: dict[str, list[float]] = {label: [] for label, _low, _high in DEV_SELL_BANDS}

    for mint, payload in first.items():
        raw = (payload.get("cluster_metrics") or {}).get("dev_sell_pct")
        now = prices.get(mint)
        entry = entry_prices.get(mint)
        if raw is None or now is None or not entry:
            continue
        value = float(raw)
        for label, low, high in DEV_SELL_BANDS:
            if (value == 0.0 and label == "zero") or (value > 0 and low < value <= high):
                banded[label].append(now / entry)
                break

    print("\nby magnitude of developer selling, not as a flag")
    header = f"{'dev_sell_pct band':<18} {'n':>5} {'dead':>6} {'median':>8} {'>2x':>5}"
    print(header)
    print("-" * len(header))
    for label, _low, _high in DEV_SELL_BANDS:
        values = banded[label]
        if not values:
            print(f"{label:<18} {0:>5}     --       --    --")
            continue
        dead = sum(1 for value in values if value < dead_below)
        winners = sum(1 for value in values if value >= 2.0)
        print(
            f"{label:<18} {len(values):>5} {100 * dead / len(values):>5.0f}% "
            f"{statistics.median(values):>8.2f} {100 * winners / len(values):>4.0f}%"
        )

    dumped = banded["dumped (>=50%)"]
    zero = banded["zero"]
    if len(dumped) < 20 or len(zero) < 20:
        print("\nToo few in one band to read.")
        return

    dumped_death = sum(1 for value in dumped if value < dead_below) / len(dumped)
    zero_death = sum(1 for value in zero if value < dead_below) / len(zero)
    print(
        f"\ndeath rate {100 * dumped_death:.0f}% once the developer has exited vs "
        f"{100 * zero_death:.0f}% while the supply is still held."
    )
    if dumped_death < zero_death:
        print("The current gate rejects the band that survives most and keeps the band")
        print("that dies most. On survival -- the only thing a risk gate is for -- it is")
        print("running backwards. Note the >2x column before acting: the surviving band")
        print("also runs least, so this buys lower variance, not higher return.")


if __name__ == "__main__":
    raise SystemExit(main())
