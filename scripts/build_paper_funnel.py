#!/usr/bin/env python3
"""Why is the paper-trade count zero? A stage-by-stage funnel over the journal.

The system holds 40,000+ observations and no paper positions. That could mean the
gates are working exactly as designed on a population that deserves rejection, or
that candidates pass and never reach the paper engine, or that a field is UNKNOWN
because collection never gathered it. Those need very different responses, and a
trade count of zero cannot distinguish them.

So this walks every journalled observation through the stages a candidate must
clear, reports how many survive each one, and names the reasons for the drop.

**A stage passes only on positive evidence.** UNKNOWN is a failure, never a pass.
That is what fail-closed means, and a funnel treating missing evidence as "fine so
far" would hide the exact problem it exists to find.

**Nothing here loosens anything.** It is a read-only diagnosis. Where a field is
UNKNOWN because collection never gathered it, the fix is to collect it -- not to
stop requiring it.

Writes artifacts/paper_trade_funnel.json and artifacts/paper_trade_funnel.md.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.models import CandidateStatus

ELIGIBLE = CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value
DEEP_POOL_USD = 50_000.0


def present(payload: dict[str, Any], field: str) -> bool:
    return payload.get(field) is not None


def without(payload: dict[str, Any], *needles: str) -> bool:
    """True when no journalled failure matches any needle."""
    failures = [str(item) for item in (payload.get("failures") or [])]
    return not any(needle in failure for failure in failures for needle in needles)


# (label, predicate, failure substrings that explain a drop at this stage)
STAGES: list[tuple[str, Any, tuple[str, ...]]] = [
    ("discovered", lambda p: True, ()),
    ("identity resolved (exact mint)", lambda p: present(p, "symbol"), ("identity",)),
    ("enriched (pool evidence)", lambda p: present(p, "liquidity_usd"), ("liquidity_unknown",)),
    (
        "mint/freeze authority known",
        lambda p: without(p, "mint_authority_unknown", "freeze_authority_unknown"),
        ("mint_authority_unknown", "freeze_authority_unknown"),
    ),
    (
        "transferability known",
        lambda p: without(p, "transferability_unknown", "sell_simulation_unknown"),
        ("transferability_unknown", "sell_simulation_unknown"),
    ),
    (
        "concentration known",
        lambda p: without(p, "holder_concentration_unknown"),
        ("holder_concentration_unknown",),
    ),
    (
        "cluster evidence sufficient",
        lambda p: without(p, "cluster_evidence_insufficient"),
        ("cluster_evidence_insufficient",),
    ),
    (
        "liquidity accepted",
        lambda p: without(p, "liquidity_below_minimum", "liquidity_unknown"),
        ("liquidity_below_minimum", "liquidity_unknown"),
    ),
    ("buy route known", lambda p: without(p, "entry_route_unknown"), ("entry_route_unknown",)),
    ("sell route known", lambda p: without(p, "exit_route_unknown"), ("exit_route_unknown",)),
    (
        "price impact known",
        lambda p: without(p, "price_impact_unknown"),
        ("entry_price_impact_unknown", "exit_price_impact_unknown"),
    ),
    (
        "price impact accepted",
        lambda p: without(p, "price_impact_excessive"),
        ("entry_price_impact_excessive", "exit_price_impact_excessive"),
    ),
    (
        "concentration accepted",
        lambda p: without(p, "holder_concentration_excessive"),
        ("holder_concentration_excessive",),
    ),
    (
        "developer/cluster accepted",
        lambda p: without(
            p,
            "developer_selling",
            "vendor_developer_distribution",
            "vendor_sniper",
            "vendor_fresh_wallet",
            "vendor_insider",
            "connected_wallet",
        ),
        ("developer_selling", "vendor_sniper_concentration", "vendor_fresh_wallet_concentration"),
    ),
    ("HARD SAFETY PASS (status eligible)", lambda p: p.get("status") == ELIGIBLE, ()),
    (
        "deep-pool filter (>=$50k)",
        lambda p: float(p.get("liquidity_usd") or 0.0) >= DEEP_POOL_USD,
        (),
    ),
]


def venue_of(payload: dict[str, Any]) -> str:
    stage = str(payload.get("stage") or "")
    if stage == "mover":
        return "movers (trending pools)"
    if stage in ("new", "finalizing"):
        return "launchpad pre-graduation"
    if stage == "migrated":
        return "launchpad migrated"
    return "unknown venue"


def run_funnel(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    surviving = rows
    previous = len(rows)
    report: list[dict[str, Any]] = []
    for label, predicate, reasons in STAGES:
        passed: list[dict[str, Any]] = []
        top: Counter[str] = Counter()
        for payload in surviving:
            if predicate(payload):
                passed.append(payload)
                continue
            for failure in payload.get("failures") or []:
                text = str(failure)
                if not reasons or any(reason in text for reason in reasons):
                    top[text] += 1
        report.append(
            {
                "stage": label,
                "count": len(passed),
                "pct_of_previous": round(100.0 * len(passed) / previous, 2) if previous else 0.0,
                "dropped": previous - len(passed),
                "top_reasons": top.most_common(5),
            }
        )
        previous = len(passed)
        surviving = passed
    return report


def main() -> int:
    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled.")
        return 1

    first: dict[str, dict[str, Any]] = {}
    earliest: str | None = None
    latest: str | None = None
    for event in events:
        first.setdefault(event["entity_id"], event["payload"])
        stamp = str(event["observed_at"])
        earliest = stamp if earliest is None or stamp < earliest else earliest
        latest = stamp if latest is None or stamp > latest else latest

    rows = list(first.values())
    overall = run_funnel(rows)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for payload in rows:
        grouped.setdefault(venue_of(payload), []).append(payload)
    venues = {name: run_funnel(group) for name, group in sorted(grouped.items())}

    eligible_ever = sum(1 for p in rows if p.get("status") == ELIGIBLE)
    monitor_ever = sum(1 for p in rows if p.get("status") == CandidateStatus.MONITOR.value)
    deep = sum(1 for p in rows if float(p.get("liquidity_usd") or 0.0) >= DEEP_POOL_USD)
    eligible_and_deep = sum(
        1
        for p in rows
        if p.get("status") == ELIGIBLE and float(p.get("liquidity_usd") or 0.0) >= DEEP_POOL_USD
    )
    concentration_recorded = sum(1 for p in rows if p.get("top10_private_holder_pct") is not None)

    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "observations": len(events),
        "distinct_mints": len(first),
        "window": {"earliest": earliest, "latest": latest},
        "funnel_overall": overall,
        "funnel_by_venue": venues,
        "diagnosis": {
            "eligible_ever": eligible_ever,
            "monitor_ever": monitor_ever,
            "deep_pool_candidates": deep,
            "eligible_AND_deep_pool": eligible_and_deep,
            "concentration_value_recorded": concentration_recorded,
        },
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/paper_trade_funnel.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )

    lines = [
        "# Why the paper-trade count is zero",
        "",
        f"Generated {report['generated_at']}",
        "",
        (
            f"{len(events):,} observations over {len(first):,} distinct mints, "
            f"{earliest} to {latest}."
        ),
        "",
        "Each stage passes only on positive evidence. UNKNOWN is a failure, never a pass.",
        "",
        "| stage | surviving | % of previous | dropped | top reasons |",
        "|---|---:|---:|---:|---|",
    ]
    for row in overall:
        reasons = ", ".join(f"{n} ({c})" for n, c in row["top_reasons"][:3]) or "-"
        lines.append(
            f"| {row['stage']} | {row['count']:,} | {row['pct_of_previous']}% | "
            f"{row['dropped']:,} | {reasons} |"
        )

    lines += ["", "## By venue", ""]
    for name, rowset in venues.items():
        hard = next(r for r in rowset if r["stage"].startswith("HARD SAFETY"))
        lines.append(
            f"- **{name}** — {rowset[0]['count']:,} discovered, "
            f"{hard['count']:,} hard-safety PASS, {rowset[-1]['count']:,} after deep-pool filter"
        )

    lines += [
        "",
        "## Diagnosis",
        "",
        f"- reached `eligible`: **{eligible_ever:,}**",
        f"- reached `monitor`: {monitor_ever:,}",
        f"- pool >= ${DEEP_POOL_USD:,.0f}: {deep:,}",
        f"- **eligible AND deep pool (what the paper engine requires): {eligible_and_deep:,}**",
        f"- observations carrying a concentration value: {concentration_recorded:,}",
        "",
    ]
    Path("artifacts/paper_trade_funnel.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for row in overall:
        reasons = ", ".join(f"{n}({c})" for n, c in row["top_reasons"][:2]) or "-"
        print(
            f"{row['stage']:<38} {row['count']:>7,} {row['pct_of_previous']:>7.2f}%  {reasons[:50]}"
        )
    print()
    print(f"eligible ever {eligible_ever} | monitor ever {monitor_ever}")
    print(f"deep pools {deep} | eligible AND deep {eligible_and_deep}")
    print(f"concentration value recorded {concentration_recorded}")
    print("\nwrote artifacts/paper_trade_funnel.{json,md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
