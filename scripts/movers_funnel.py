#!/usr/bin/env python3
"""Why do 86 observed movers become one replayable trade?

The established-movers replay produced n=1, which tells us nothing about movers
and everything about the pipeline feeding them. This walks the funnel stage by
stage so the loss is attributable to a specific missing field rather than to
"not enough data".

The rule while fixing it, stated because it is the tempting shortcut: **fix data
and wiring, never the safety gates.** A mover that fails a hard gate is a real
rejection and stays rejected. A mover that fails because nobody recorded its
liquidity is a collection defect and is the thing to fix.

Every mover lands in exactly one bucket and the buckets sum to the total.
Read-only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from typing import Any

from meme_flight_recorder.config import load_settings

# The replay's movers arm, reproduced exactly so the funnel explains *it*.
MOVERS_MINIMUM_LIQUIDITY_USD = 50_000.0
MOVERS_WAIT_MINUTES = 360.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/pool_history.db")
    arguments = parser.parse_args()
    settings = load_settings()

    journal = sqlite3.connect(f"file:{settings.database_path}?mode=ro", uri=True)
    rows = journal.execute(
        "select entity_id, observed_at, payload_json from events "
        "where event_type = 'candidate_observed' order by id"
    )

    movers: dict[str, dict[str, Any]] = {}
    stage_counts: dict[str, int] = defaultdict(int)
    for mint, observed_at, payload in rows:
        record = json.loads(payload)
        stage_counts[str(record.get("stage"))] += 1
        if record.get("stage") != "mover":
            continue
        if mint in movers:
            continue
        record["observed_at"] = observed_at
        movers[mint] = record
    journal.close()

    print("=== every journalled observation by stage ===")
    total_obs = sum(stage_counts.values())
    for name, count in sorted(stage_counts.items(), key=lambda item: -item[1]):
        print(f"  {name:<14}{count:>8}  {100.0 * count / total_obs:5.2f}%")
    print(f"  {'TOTAL':<14}{total_obs:>8}")

    cache = sqlite3.connect(f"file:{arguments.cache}?mode=ro", uri=True)
    cached = {
        mint: status
        for mint, status in cache.execute("select mint, status from fetches")
    }
    cache.close()

    print(f"\n=== movers funnel === distinct mover mints: {len(movers)}")

    buckets: dict[str, list[str]] = defaultdict(list)
    for mint, record in movers.items():
        # Ordered exactly as the replay applies them, so the first failure is
        # the one that actually cost the trade.
        if record.get("failures"):
            buckets["1_hard_safety_failure"].append(mint)
            continue
        if record.get("liquidity_usd") is None:
            buckets["2_liquidity_unknown"].append(mint)
            continue
        if float(record["liquidity_usd"]) < MOVERS_MINIMUM_LIQUIDITY_USD:
            buckets["3_pool_below_50k"].append(mint)
            continue
        if not record.get("price_usd"):
            buckets["4_no_price"].append(mint)
            continue
        status = cached.get(mint)
        if status is None:
            buckets["5_no_cached_history"].append(mint)
            continue
        if status != "ok":
            buckets[f"6_history_{status}"].append(mint)
            continue
        buckets["7_replayable"].append(mint)

    total = sum(len(members) for members in buckets.values())
    for name in sorted(buckets):
        count = len(buckets[name])
        print(f"  {name:<26}{count:>6}  {100.0 * count / max(1, len(movers)):5.1f}%")
    print(f"  {'TOTAL':<26}{total:>6}")
    if total != len(movers):
        raise AssertionError("funnel must reconcile to the mover population")

    # The specific failure reasons, so a wiring gap is distinguishable from a
    # genuine rejection. Movers come from a trending feed with no vendor
    # cluster or deployer labels, which fails closed by design.
    print("\n=== why movers fail safety (reasons, not mints) ===")
    reasons: dict[str, int] = defaultdict(int)
    for mint in buckets["1_hard_safety_failure"]:
        for failure in movers[mint].get("failures") or []:
            reasons[failure] += 1
    for name, count in sorted(reasons.items(), key=lambda item: -item[1])[:12]:
        kind = "MISSING EVIDENCE" if name.endswith(("_unknown", "_missing", "_insufficient")) else "real rejection"
        print(f"  {name:<40}{count:>5}  {kind}")

    print("\n=== diagnosis ===")
    unknown_share = sum(
        1
        for mint in buckets["1_hard_safety_failure"]
        if all(
            failure.endswith(("_unknown", "_missing", "_insufficient"))
            for failure in (movers[mint].get("failures") or [])
        )
    )
    print(f"  movers rejected ONLY on missing evidence: {unknown_share}")
    print(f"  movers with no cached price history:      {len(buckets['5_no_cached_history'])}")
    print(f"  movers replayable today:                  {len(buckets['7_replayable'])}")
    print(
        "\n  Fix data and wiring, never the gates: a mover rejected only on\n"
        "  *_unknown is a collection defect; one rejected on a real failure stays\n"
        "  rejected."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
