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

from dataclasses import replace

from meme_flight_recorder.clusters import assess_vendor_labels
from meme_flight_recorder.config import load_settings
from meme_flight_recorder.enrichment import enrich_snapshot
from meme_flight_recorder.models import CandidateStatus
from meme_flight_recorder.providers.binance_web3 import (
    CHAIN_SOLANA,
    RANK_FINALIZING,
    RANK_MIGRATED,
    RANK_NEW,
    BinanceWeb3Provider,
)
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider
from meme_flight_recorder.providers.helius import HeliusProvider
from meme_flight_recorder.providers.jupiter import JupiterQuoteProvider
from meme_flight_recorder.safety import SafetyEngine

STAGES = {"new": RANK_NEW, "finalizing": RANK_FINALIZING, "migrated": RANK_MIGRATED}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=sorted(STAGES), default="migrated")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--chain", default=CHAIN_SOLANA)
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Resolve identity, authorities, routes and impact before gating.",
    )
    parser.add_argument("--order-sol", type=float, default=0.05)
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

    mint_provider = None
    quote_provider = None
    dex_provider = None
    if arguments.enrich:
        quote_provider = JupiterQuoteProvider()
        dex_provider = DexScreenerProvider()
        try:
            mint_provider = HeliusProvider()
        except ValueError as error:
            # Enrichment degrades rather than guessing: without a Solana RPC the
            # authority and identity fields stay unknown and keep failing closed.
            print(f"note: Helius unavailable ({error}); authority evidence stays unknown.\n")

    now = datetime.now(UTC)
    failures: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    cluster_verdicts: Counter[str] = Counter()
    coverage: list[float] = []

    for row in rows:
        cluster = assess_vendor_labels(row.labels, settings.clusters)
        cluster_verdicts[cluster.verdict.value] += 1
        snapshot = row.to_snapshot(observed_at=now)

        if arguments.enrich:
            if dex_provider is not None:
                try:
                    pair = dex_provider.deepest_pair(row.contract_address)
                except Exception:  # noqa: BLE001 - a dead provider must not pass a gate
                    pair = None
                if pair is not None:
                    # Pair age and pool depth are what a trade actually routes
                    # through, so they override the feed's token-level numbers.
                    snapshot = replace(
                        snapshot,
                        liquidity_usd=pair.liquidity_usd,
                        age_minutes=pair.pair_age_minutes or snapshot.age_minutes,
                        price_usd=pair.price_usd or snapshot.price_usd,
                        volume_5m_usd=pair.volume_5m_usd,
                    )
            snapshot, report = enrich_snapshot(
                snapshot,
                mint_provider=mint_provider,
                quote_provider=quote_provider,
                intended_order_sol=arguments.order_sol,
            )
            coverage.append(report.coverage_pct)

        decision = engine.evaluate(snapshot, now, cluster=cluster)
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

    if coverage:
        average = sum(coverage) / len(coverage)
        print(f"\nevidence coverage: {average:.1f}% average across candidates")
        print("  (low coverage means the data was bad, not that the tokens were)")

    survivors = statuses.get(CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value, 0)
    monitors = statuses.get(CandidateStatus.MONITOR.value, 0)
    print(f"\neligible={survivors}  monitor={monitors}  of {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
