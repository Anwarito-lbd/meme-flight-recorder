#!/usr/bin/env python3
"""Survey live launchpad candidates and report why each one is rejected.

This answers the question the project's first backtest left open: were there no
completed trades because the gates are too tight, or because nothing worth
trading ever showed up? Run it against the live feed and read the rejection
histogram. A distribution dominated by ``liquidity_below_minimum`` means the
candidate flow is genuinely unsuitable; one dominated by ``*_unknown`` means the
pipeline is starving the gates of evidence it could actually collect.

Read-only. It discovers, grades, and prints. It never opens a position.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime

from meme_flight_recorder.clusters import assess_vendor_labels
from meme_flight_recorder.config import load_settings
from meme_flight_recorder.models import CandidateStatus
from meme_flight_recorder.providers.binance_web3 import (
    CHAIN_SOLANA,
    RANK_FINALIZING,
    RANK_MIGRATED,
    RANK_NEW,
    BinanceWeb3Provider,
)
from meme_flight_recorder.safety import SafetyEngine

STAGES = {"new": RANK_NEW, "finalizing": RANK_FINALIZING, "migrated": RANK_MIGRATED}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=sorted(STAGES), default="migrated")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--chain", default=CHAIN_SOLANA)
    parser.add_argument("--config", default=None)
    arguments = parser.parse_args()

    settings = load_settings(arguments.config)
    engine = SafetyEngine(
        settings.cex_safety,
        settings.solana_safety,
        settings.stale_after_seconds,
        settings.clusters,
    )
    provider = BinanceWeb3Provider()
    rows = provider.meme_rush(
        chain_id=arguments.chain, rank_type=STAGES[arguments.stage], limit=arguments.limit
    )

    now = datetime.now(UTC)
    failures: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    cluster_verdicts: Counter[str] = Counter()

    for row in rows:
        cluster = assess_vendor_labels(row.labels, settings.clusters)
        cluster_verdicts[cluster.verdict.value] += 1
        decision = engine.evaluate(row.to_snapshot(observed_at=now), now, cluster=cluster)
        statuses[decision.status.value] += 1
        failures.update(decision.failures)

    print(f"stage={arguments.stage} chain={arguments.chain} candidates={len(rows)}\n")

    print("cluster verdicts")
    for verdict, count in cluster_verdicts.most_common():
        print(f"  {count:4d}  {verdict}")

    print("\ngate outcomes")
    for status, count in statuses.most_common():
        print(f"  {count:4d}  {status}")

    print("\nrejection reasons (most frequent first)")
    for reason, count in failures.most_common():
        print(f"  {count:4d}  {reason}")

    survivors = statuses.get(CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value, 0)
    monitors = statuses.get(CandidateStatus.MONITOR.value, 0)
    print(f"\neligible={survivors}  monitor={monitors}  of {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
