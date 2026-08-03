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

The collector can now open a *paper* position, when a ``monitor`` is supplied.
Without one it observes, grades and records exactly as before.

That is a change of behaviour and worth being precise about. It still cannot
sign or broadcast anything: the monitor writes journal rows and nothing else,
and ``tests/test_no_live_execution.py`` stays green and unmodified. What it adds
is a forward track record, which the journal has never held -- thousands of
observations and, until now, zero positions. Every expectancy figure in this
project is backward-looking because of that gap.

The monitor's exits run *before* discovery, so a failing feed can never prevent
a held position from being closed.
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
        monitor: Any | None = None,
        movers_provider: Any | None = None,
    ) -> None:
        self.settings = settings
        self.recorder = recorder
        # Established movers, when supplied. Both feeds run through the same
        # grading path; only the population differs.
        self.movers_provider = movers_provider
        # Optional so every existing caller and test keeps working unchanged.
        # Absent, the collector observes and records exactly as before.
        self.monitor = monitor
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
        eligible: list[dict[str, Any]] = []

        # Held positions are managed *before* discovery, so a failing feed can
        # never stop a position being exited. A monitor that only ran after a
        # successful poll would hold through exactly the outages that matter.
        if self.monitor is not None:
            try:
                self.monitor.manage_open()
            except Exception as error:  # noqa: BLE001 - the book must not end the cycle
                errors.append(f"monitor: {type(error).__name__}: {error}")

        def absorb(status: str, failures: tuple[str, ...], candidate: dict[str, Any]) -> None:
            nonlocal recorded
            recorded += 1
            status_counts[status] = status_counts.get(status, 0) + 1
            for failure in failures:
                failure_counts[failure] = failure_counts.get(failure, 0) + 1
            if status == CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value:
                eligible.append(candidate)

        if self.movers_provider is not None:
            # Established movers. This is the population the deep-pool filter was
            # measured on: launchpad newborns had a median pool of $26, so a
            # >=$50k rule almost never fires there and the paper book would sit
            # empty regardless of how well the filter works.
            try:
                pools, skipped = self._trending_pools()
            except Exception as error:  # noqa: BLE001
                errors.append(f"trending: {type(error).__name__}: {error}")
                pools, skipped = [], {}
            if skipped:
                failure_counts.update({f"mover_{k}": v for k, v in skipped.items()})
            for index, pool in enumerate(pools):
                observed += 1
                if index and self.config.enrich and self.config.per_candidate_delay_seconds > 0:
                    sleep(self.config.per_candidate_delay_seconds)
                try:
                    absorb(*self._record_pool(pool, started))
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{pool.mint}: {type(error).__name__}: {error}")

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
                    absorb(*self._record(row, stage, started))
                except Exception as error:  # noqa: BLE001
                    errors.append(f"{row.contract_address}: {type(error).__name__}: {error}")
                    continue

        if self.monitor is not None and eligible:
            try:
                self.monitor.consider(eligible)
            except Exception as error:  # noqa: BLE001
                errors.append(f"monitor_entry: {type(error).__name__}: {error}")

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
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        return self._grade(
            row.to_snapshot(observed_at=observed_at),
            mint=row.contract_address,
            symbol=row.symbol,
            stage=stage,
            observed_at=observed_at,
            labels=row.labels,
            buy_count=row.buy_count,
            sell_count=row.sell_count,
            market_cap_usd=row.market_cap_usd,
            holders=row.holders,
            chain_id=row.chain_id,
        )

    def _trending_pools(self) -> tuple[list[Any], dict[str, int]]:
        """Fetch and filter established movers."""
        from .discovery import select_movers

        pools, skipped = select_movers(self.movers_provider.trending_solana_pools())
        return list(pools)[: self.config.limit_per_stage], skipped

    def _record_pool(self, pool: Any, observed_at: datetime) -> tuple[str, tuple[str, ...], dict]:
        """Grade one trending pool through the same gates as a launchpad row.

        The trending feed supplies no vendor cluster labels, so cluster evidence
        stays unknown and the gates keep failing closed on it rather than
        treating silence as a pass. That is the intended behaviour: this feed
        buys a better population, not a relaxed standard.
        """
        from .providers.binance_web3 import VendorClusterLabels

        return self._grade(
            pool.to_snapshot(observed_at=observed_at),
            mint=pool.mint,
            symbol=pool.symbol,
            stage="mover",
            observed_at=observed_at,
            labels=VendorClusterLabels(),
            buy_count=pool.buys_5m,
            sell_count=pool.sells_5m,
            market_cap_usd=pool.market_cap_usd,
            holders=None,
        )

    def _grade(
        self,
        snapshot: TokenSnapshot,
        *,
        mint: str,
        symbol: str,
        stage: str,
        observed_at: datetime,
        labels: Any,
        buy_count: int | None = None,
        sell_count: int | None = None,
        market_cap_usd: float | None = None,
        holders: int | None = None,
        chain_id: str = CHAIN_SOLANA,
    ) -> tuple[str, tuple[str, ...], dict[str, Any]]:
        """Enrich, gate and journal one candidate, whatever feed produced it.

        Shared by both discovery sources on purpose. The safety argument does not
        change with the feed -- only the population does -- so a second grading
        path would be a second place for the gates to drift.
        """
        coverage: float | None = None
        pair: Any | None = None

        if self.config.enrich:
            snapshot, pair = self._apply_pair_evidence(snapshot, mint)
            snapshot, report = enrich_snapshot(
                snapshot,
                mint_provider=self.mint_provider,
                quote_provider=self.quote_provider,
                intended_order_sol=self.config.intended_order_sol,
            )
            coverage = report.coverage_pct

        cluster = assess_vendor_labels(labels, self.settings.clusters)
        decision = self.safety.evaluate(snapshot, observed_at, cluster=cluster)

        # Recorded, never acted upon. Whether this score predicts anything is a
        # question for calibration once completed trades exist; using it to size
        # today would be a belief rather than a measurement.
        confidence = score_confidence(
            liquidity_usd=snapshot.liquidity_usd,
            exit_impact_pct=snapshot.exit_price_impact_pct,
            cluster_verdict=cluster.verdict.value,
            buy_share_pct=(
                100.0 * buy_count / (buy_count + sell_count)
                if buy_count is not None
                and sell_count is not None
                and (buy_count + sell_count) > 0
                else None
            ),
            age_minutes=snapshot.age_minutes,
            evidence_coverage_pct=coverage,
        )

        # append_once keeps a retried or overlapping poll from writing the same
        # observation twice, while still allowing the same mint to be recorded
        # again on the next cycle. That repetition is the time series.
        payload = {
                "stage": stage,
                "chain_id": chain_id,
                "symbol": symbol,
                "observed_at": observed_at.isoformat(),
                # Forward returns are computed against these later, so they must
                # be captured now rather than looked up at scoring time.
                "price_usd": snapshot.price_usd,
                "liquidity_usd": snapshot.liquidity_usd,
                "market_cap_usd": market_cap_usd,
                "holders": holders,
                "age_minutes": snapshot.age_minutes,
                "entry_price_impact_pct": snapshot.entry_price_impact_pct,
                "exit_price_impact_pct": snapshot.exit_price_impact_pct,
                # Flow inputs. These were previously computed for the confidence
                # score and then discarded, which left the journal holding a
                # conclusion whose evidence no longer existed. Transaction counts
                # are recorded under names that say "txns", never "buyers": one
                # wallet can generate a hundred buys, and the distinction between
                # a count and a unique address is the whole of Stage 6.
                "buy_txns_total": buy_count,
                "sell_txns_total": sell_count,
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
        }
        self.recorder.append_once(
            EVENT_CANDIDATE_OBSERVED,
            entity_id=mint,
            payload=payload,
            idempotency_key=(
                f"{EVENT_CANDIDATE_OBSERVED}:{mint}:"
                f"{observed_at.isoformat(timespec='seconds')}"
            ),
        )
        # The monitor needs the mint alongside the journalled fields; the
        # payload itself is keyed by entity_id in the journal and does not
        # carry it.
        return decision.status.value, decision.failures, payload | {"mint": mint}

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
