#!/usr/bin/env python3
"""Does clearing every hard safety gate carry any information about return?

Fifty-two mints out of 18,846 cleared every gate. That is the population the
paper engine would actually trade, and until now nobody has asked what happened
to them. The question is narrow and important: **is "safety clean" predictive of
anything, or is it orthogonal to return?**

The system already knows the answer for its other gates. The cluster gate
rejects winners and earns its place by cutting death rate. The deployer gate ran
backwards until it was measured. Recall across all candidates is zero. So there
is no reason to assume this set is different, and every reason to check.

**No look-ahead.** Each candidate's verdict and price come from its *first*
journalled observation -- the only moment it could have been acted on. The
outcome is measured from that price forward.

**The comparison group matters.** A clean set that dies less than the rest is
doing its job even if it never produces a winner. So this reports the clean
cohort against every other journalled mint, not against nothing.

**Untradeable candidates are excluded from the return figures**, because a
nominal multiple on a pool holding nothing is an artifact rather than a result.
They are counted separately rather than dropped.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.models import CandidateStatus
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

CLEAN_STATUSES = {
    CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value,
    CandidateStatus.MONITOR.value,
}


def safe(text: str) -> str:
    """Token symbols are attacker-controlled and routinely contain emoji."""
    return str(text).encode("ascii", "replace").decode("ascii")


def is_clean(payload: dict[str, Any]) -> bool:
    """Cleared every hard gate: a permitted status AND no recorded failure."""
    return payload.get("status") in CLEAN_STATUSES and not (payload.get("failures") or [])


def describe(label: str, multiples: list[float], dead_below: float) -> dict[str, Any]:
    if not multiples:
        return {"group": label, "n": 0}
    ordered = sorted(multiples)
    return {
        "group": label,
        "n": len(ordered),
        "median": round(statistics.median(ordered), 4),
        "mean": round(statistics.mean(ordered), 4),
        "dead_pct": round(100.0 * sum(1 for v in ordered if v < dead_below) / len(ordered), 2),
        "win_pct": round(100.0 * sum(1 for v in ordered if v > 1.0) / len(ordered), 2),
        "two_x_pct": round(100.0 * sum(1 for v in ordered if v >= 2.0) / len(ordered), 2),
        "five_x_pct": round(100.0 * sum(1 for v in ordered if v >= 5.0) / len(ordered), 2),
        "max": round(max(ordered), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dead-below", type=float, default=0.10)
    parser.add_argument("--minimum-liquidity", type=float, default=5_000.0)
    arguments = parser.parse_args()

    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled.")
        return 1

    first: dict[str, dict[str, Any]] = {}
    for event in events:
        first.setdefault(event["entity_id"], event["payload"])

    clean = {m: p for m, p in first.items() if is_clean(p)}
    others = {m: p for m, p in first.items() if not is_clean(p)}
    print(f"{len(first):,} distinct mints | clean {len(clean)} | not clean {len(others):,}")

    prices = DexScreenerProvider().prices_for_tokens(list(first))

    def outcomes(group: dict[str, dict[str, Any]]) -> tuple[list[float], int, int]:
        """Return multiples for tradeable members, plus counts of the excluded."""
        multiples: list[float] = []
        untradeable = unresolved = 0
        for mint, payload in group.items():
            entry = payload.get("price_usd")
            if not entry or float(entry) <= 0:
                unresolved += 1
                continue
            if float(payload.get("liquidity_usd") or 0.0) < arguments.minimum_liquidity:
                untradeable += 1
                continue
            now = prices.get(mint)
            if not now:
                unresolved += 1
                continue
            multiples.append(now / float(entry))
        return multiples, untradeable, unresolved

    clean_m, clean_untradeable, clean_unresolved = outcomes(clean)
    other_m, other_untradeable, other_unresolved = outcomes(others)

    print(
        f"clean: {len(clean_m)} measurable, {clean_untradeable} untradeable, "
        f"{clean_unresolved} unresolved"
    )
    print(
        f"rest : {len(other_m)} measurable, {other_untradeable} untradeable, "
        f"{other_unresolved} unresolved\n"
    )

    rows = [
        describe("safety clean", clean_m, arguments.dead_below),
        describe("everything else", other_m, arguments.dead_below),
    ]
    header = (
        f"{'group':<18} {'n':>5} {'median':>8} {'mean':>8} "
        f"{'dead':>7} {'win':>7} {'>=2x':>7} {'>=5x':>7} {'max':>9}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        if not row["n"]:
            print(f"{row['group']:<18} {0:>5}  (no measurable outcomes)")
            continue
        print(
            f"{row['group']:<18} {row['n']:>5} {row['median']:>8.3f} {row['mean']:>8.2f} "
            f"{row['dead_pct']:>6.1f}% {row['win_pct']:>6.1f}% {row['two_x_pct']:>6.1f}% "
            f"{row['five_x_pct']:>6.1f}% {row['max']:>9.1f}"
        )

    # Per-candidate detail for the clean cohort, worst first so the deaths lead.
    detail: list[dict[str, Any]] = []
    for mint, payload in clean.items():
        entry = payload.get("price_usd")
        now = prices.get(mint)
        multiple = (now / float(entry)) if now and entry and float(entry) > 0 else None
        detail.append(
            {
                "mint": mint,
                "symbol": safe(payload.get("symbol") or ""),
                "source": payload.get("stage"),
                "age_minutes": payload.get("age_minutes"),
                "liquidity_usd": payload.get("liquidity_usd"),
                "market_cap_usd": payload.get("market_cap_usd"),
                "status": payload.get("status"),
                "entry_price_impact_pct": payload.get("entry_price_impact_pct"),
                "exit_price_impact_pct": payload.get("exit_price_impact_pct"),
                "top10_private_holder_pct": payload.get("top10_private_holder_pct"),
                "cluster_verdict": payload.get("cluster_verdict"),
                "warnings": payload.get("warnings"),
                "multiple_now": round(multiple, 4) if multiple is not None else None,
            }
        )
    detail.sort(key=lambda row: (row["multiple_now"] is None, row["multiple_now"] or 0.0))

    print("\nclean cohort, worst outcome first")
    print(f"{'symbol':<14} {'source':<12} {'liq $':>10} {'age m':>8} {'multiple':>10}")
    for row in detail[:20]:
        liquidity = row["liquidity_usd"] or 0.0
        age = row["age_minutes"]
        multiple = row["multiple_now"]
        print(
            f"{row['symbol'][:14]:<14} {str(row['source'])[:12]:<12} {liquidity:>10,.0f} "
            f"{(age if age is not None else -1):>8.0f} "
            f"{(f'{multiple:.3f}' if multiple is not None else 'unresolved'):>10}"
        )

    payload_out = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "distinct_mints": len(first),
        "clean_count": len(clean),
        "excluded": {
            "clean_untradeable": clean_untradeable,
            "clean_unresolved": clean_unresolved,
            "other_untradeable": other_untradeable,
            "other_unresolved": other_unresolved,
        },
        "summary": rows,
        "clean_candidates": detail,
    }
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/clean_candidate_analysis.json").write_text(
        json.dumps(payload_out, indent=2, default=str), encoding="utf-8"
    )
    print("\nwrote artifacts/clean_candidate_analysis.json")

    if len(clean_m) < 10:
        print("\nVERDICT: UNPROVEN -- too few measurable clean outcomes to compare.")
        return 0
    clean_stats, other_stats = rows[0], rows[1]
    print()
    if clean_stats["dead_pct"] < other_stats["dead_pct"]:
        print(
            f"The clean cohort dies less ({clean_stats['dead_pct']}% vs "
            f"{other_stats['dead_pct']}%). The gates buy survival, which is what "
            "they are for."
        )
    else:
        print(
            f"The clean cohort does NOT die less ({clean_stats['dead_pct']}% vs "
            f"{other_stats['dead_pct']}%). On death rate -- the only thing a risk "
            "gate is for -- clearing every gate carries no information here."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
