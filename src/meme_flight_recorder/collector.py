"""Continuous capture of candidates and their gate decisions.

This is the piece that makes source scoring possible at all. Expectancy after
costs can only be computed by comparing what a token looked like at the moment
it was observed against what it did afterwards, and the "before" half of that
comparison cannot be reconstructed later. Pool depth, holder distribution and
quoted price impact at 04:12 are simply gone by 04:13. Every hour without a
collector running is an hour of that record permanently lost.

So the collector journals **every** candidate, including rejected ones.
Rejections are not waste: they are the control group. A source whose calls are
consistently rejected for developer distribution is telling you something
precise about that source, and that finding is only available if the rejections
were written down.

The collector cannot open a position. It observes, grades, and records.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from .clusters import assess_vendor_labels
from .confidence import score_confidence
from .config import Settings
from .enrichment import enrich_snapshot
from .journal import FlightRecorder
from .models import CandidateStatus, TokenSnapshot
from .providers.binance_web3 import (
    CHAIN_SOLANA,
    RANK_FINALIZING,
    RANK_MIGRATED,
    RANK_NEW,
    BinanceWeb3Provider,
    MemeRushRow,
)
from .safety import SafetyEngine

STAGES: dict[str, int] = {
    "new": RANK_NEW,
    "finalizing": RANK_FINALIZING,
    "migrated": RANK_MIGRATED,
}

EVENT_CANDIDATE_OBSERVED = "candidate_observed"
EVENT_CYCLE_COMPLETED = "collector_cycle_completed"


@dataclass(frozen=True)
class CollectorConfig:
    stages: tuple[str, ...] = ("new", "finalizing", "migrated")
    chain_id: str = CHAIN_SOLANA
    limit_per_stage: int = 50
    interval_seconds: int = 300
    enrich: bool = True
    intended_order_sol: float = 0.05
    # Enrichment makes two quote calls per candidate, so a 60-candidate cycle
    # bursts roughly 120 requests at the router in about 100 seconds, which
    # exceeds its published allowance and returns 429s. Retries then eat the
    # budget further and the affected candidates end up recorded with unknown
    # routes -- data loss that looks like ordinary rejection. Pacing costs a
    # little wall-clock time in a cycle that is idle two thirds of the time
    # anyway.
    per_candidate_delay_seconds: float = 0.6


@dataclass(frozen=True)
class CycleSummary:
    started_at: datetime
    observed: int = 0
    recorded: int = 0
    eligible: int = 0
    monitor: int = 0
    rejected: int = 0
    errors: tuple[str, ...] = ()
    status_counts: dict[str, int] = field(default_factory=dict)
    failure_counts: dict[str, int] = field(default_factory=dict)


class Collector:
    """Poll the discovery feed, grade each candidate, and journal the result."""

    def __init__(
        self,
        settings: Settings,
        recorder: FlightRecorder,
        discovery: BinanceWeb3Provider | None = None,
        mint_provider: Any | None = None,
        quote_provider: Any | None = None,
        pair_provider: Any | None = None,
        config: CollectorConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.recorder = recorder
        self.discovery = discovery or BinanceWeb3Provider()
        self.mint_provider = mint_provider
        self.quote_provider = quote_provider
        self.pair_provider = pair_provider
        self.config = config or CollectorConfig()
        self.clock = clock
        self.safety = SafetyEngine(
            settings.cex_safety,
            settings.solana_safety,
            settings.stale_after_seconds,
            settings.clusters,
        )

    def run_once(self, sleep: Callable[[float], None] = time.sleep) -> CycleSummary:
        """Run one full poll across configured stages."""
        started = self.clock()
        observed = recorded = 0
        errors: list[str] = []
        status_counts: dict[str, int] = {}
        failure_counts: dict[str, int] = {}

        for stage in self.config.stages:
            try:
                rows = self.discovery.meme_rush(
                    chain_id=self.config.chain_id,
                    rank_type=STAGES[stage],
                    limit=self.config.limit_per_stage,
                )
            except Exception as error:  # noqa: BLE001 - one bad stage must not end the cycle
                errors.append(f"{stage}: {type(error).__name__}: {error}")
                continue

            for index, row in enumerate(rows):
                observed += 1
                if index and self.config.enrich and self.config.per_candidate_delay_seconds > 0:
                    sleep(self.config.per_candidate_delay_seconds)
                try:
                    status, failures = self._record(row, stage, started)
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{row.contract_address}: {type(error).__name__}: {error}")
                    continue
                recorded += 1
                status_counts[status] = status_counts.get(status, 0) + 1
                for failure in failures:
                    failure_counts[failure] = failure_counts.get(failure, 0) + 1

        summary = CycleSummary(
            started_at=started,
            observed=observed,
            recorded=recorded,
            eligible=status_counts.get(CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value, 0),
            monitor=status_counts.get(CandidateStatus.MONITOR.value, 0),
            rejected=status_counts.get(CandidateStatus.REJECT.value, 0),
            errors=tuple(errors),
            status_counts=status_counts,
            failure_counts=failure_counts,
        )
        self.recorder.append(
            EVENT_CYCLE_COMPLETED,
            entity_id="collector",
            payload={
                "started_at": started.isoformat(),
                "observed": observed,
                "recorded": recorded,
                "status_counts": status_counts,
                "failure_counts": failure_counts,
                "errors": list(errors),
            },
        )
        return summary

    def run_forever(
        self,
        cycles: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_cycle: Callable[[CycleSummary], None] | None = None,
    ) -> Iterable[CycleSummary]:
        """Poll on an interval. ``cycles`` bounds the loop for tests."""
        summaries: list[CycleSummary] = []
        completed = 0
        while cycles is None or completed < cycles:
            summary = self.run_once(sleep=sleep)
            summaries.append(summary)
            if on_cycle is not None:
                on_cycle(summary)
            completed += 1
            if cycles is None or completed < cycles:
                sleep(self.config.interval_seconds)
        return summaries

    def _record(
        self, row: MemeRushRow, stage: str, observed_at: datetime
    ) -> tuple[str, tuple[str, ...]]:
        snapshot = row.to_snapshot(observed_at=observed_at)
        coverage: float | None = None
        pair: Any | None = None

        if self.config.enrich:
            snapshot, pair = self._apply_pair_evidence(snapshot, row.contract_address)
            snapshot, report = enrich_snapshot(
                snapshot,
                mint_provider=self.mint_provider,
                quote_provider=self.quote_provider,
                intended_order_sol=self.config.intended_order_sol,
            )
            coverage = report.coverage_pct

        cluster = assess_vendor_labels(row.labels, self.settings.clusters)
        decision = self.safety.evaluate(snapshot, observed_at, cluster=cluster)

        # Recorded, never acted upon. Whether this score predicts anything is a
        # question for calibration once completed trades exist; using it to size
        # today would be a belief rather than a measurement.
        confidence = score_confidence(
            liquidity_usd=snapshot.liquidity_usd,
            exit_impact_pct=snapshot.exit_price_impact_pct,
            cluster_verdict=cluster.verdict.value,
            buy_share_pct=(
                100.0 * row.buy_count / (row.buy_count + row.sell_count)
                if row.buy_count is not None
                and row.sell_count is not None
                and (row.buy_count + row.sell_count) > 0
                else None
            ),
            age_minutes=snapshot.age_minutes,
            evidence_coverage_pct=coverage,
        )

        # append_once keeps a retried or overlapping poll from writing the same
        # observation twice, while still allowing the same mint to be recorded
        # again on the next cycle. That repetition is the time series.
        self.recorder.append_once(
            EVENT_CANDIDATE_OBSERVED,
            entity_id=row.contract_address,
            payload={
                "stage": stage,
                "chain_id": row.chain_id,
                "symbol": row.symbol,
                "observed_at": observed_at.isoformat(),
                # Forward returns are computed against these later, so they must
                # be captured now rather than looked up at scoring time.
                "price_usd": snapshot.price_usd,
                "liquidity_usd": snapshot.liquidity_usd,
                "market_cap_usd": row.market_cap_usd,
                "holders": row.holders,
                "age_minutes": snapshot.age_minutes,
                "entry_price_impact_pct": snapshot.entry_price_impact_pct,
                "exit_price_impact_pct": snapshot.exit_price_impact_pct,
                # Flow inputs. These were previously computed for the confidence
                # score and then discarded, which left the journal holding a
                # conclusion whose evidence no longer existed. Transaction counts
                # are recorded under names that say "txns", never "buyers": one
                # wallet can generate a hundred buys, and the distinction between
                # a count and a unique address is the whole of Stage 6.
                "buy_txns_total": row.buy_count,
                "sell_txns_total": row.sell_count,
                "buy_txns_5m": getattr(pair, "buy_txns_5m", None),
                "sell_txns_5m": getattr(pair, "sell_txns_5m", None),
                "volume_5m_usd": snapshot.volume_5m_usd,
                "volume_1h_usd": getattr(pair, "volume_1h_usd", None),
                "volume_24h_usd": getattr(pair, "volume_24h_usd", None),
                "volume_to_liquidity_5m": getattr(pair, "volume_to_liquidity_5m", None),
                "pair_address": getattr(pair, "pair_address", None),
                "status": decision.status.value,
                "failures": list(decision.failures),
                "warnings": list(decision.warnings),
                "cluster_verdict": cluster.verdict.value,
                "cluster_confidence": cluster.confidence,
                "cluster_metrics": cluster.metrics,
                "confidence": confidence.value,
                "confidence_band": confidence.band.value,
                "confidence_missing": list(confidence.missing),
                "evidence_coverage_pct": coverage,
            },
            idempotency_key=(
                f"{EVENT_CANDIDATE_OBSERVED}:{row.contract_address}:"
                f"{observed_at.isoformat(timespec='seconds')}"
            ),
        )
        return decision.status.value, decision.failures

    def _apply_pair_evidence(
        self, snapshot: TokenSnapshot, mint: str
    ) -> tuple[TokenSnapshot, Any | None]:
        """Overlay pool-level facts, which describe the trade's actual route.

        The pair itself is returned alongside the snapshot rather than discarded,
        because flow analysis needs the volume and transaction-count fields the
        snapshot has no home for. Dropping them here is what left the journal
        unable to answer whether demand was organic.
        """
        if self.pair_provider is None:
            return snapshot, None
        try:
            pair = self.pair_provider.deepest_pair(mint)
        except Exception:  # noqa: BLE001 - a dead provider must not pass a gate
            return snapshot, None
        if pair is None:
            return snapshot, None
        return (
            replace(
                snapshot,
                liquidity_usd=pair.liquidity_usd,
                age_minutes=pair.pair_age_minutes or snapshot.age_minutes,
                price_usd=pair.price_usd or snapshot.price_usd,
                volume_5m_usd=pair.volume_5m_usd,
            ),
            pair,
        )
