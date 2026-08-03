from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from meme_flight_recorder.collector import (
    EVENT_CANDIDATE_OBSERVED,
    EVENT_CYCLE_COMPLETED,
    Collector,
    CollectorConfig,
)
from meme_flight_recorder.config import (
    ClusterLimits,
    MicroCapitalLimits,
    RiskLimits,
    SafetyLimits,
    Settings,
)
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.binance_web3 import (
    MemeRushRow,
    VendorClusterLabels,
)

NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)

CLEAN_LABELS = VendorClusterLabels(
    insider_pct=1.0,
    sniper_pct=0.5,
    bundler_pct=0.0,
    new_wallet_pct=2.0,
    dev_sell_pct=0.0,
    dev_migrate_count=0,
    dev_wash_trading=False,
    insider_wash_trading=False,
)

DIRTY_LABELS = VendorClusterLabels(
    insider_pct=60.0,
    sniper_pct=0.5,
    bundler_pct=0.0,
    new_wallet_pct=2.0,
    dev_sell_pct=0.0,
)


def _settings(database_path: Path) -> Settings:
    limits = SafetyLimits(60, 5_000, 2, 3, 30)
    return Settings(
        execution_mode="paper",
        database_path=database_path,
        starting_equity_usd=40.0,
        stale_after_seconds=120,
        cex_safety=limits,
        solana_safety=limits,
        risk=RiskLimits(0.5, 0.25, 1.0, 2, 1.0, 3),
        clusters=ClusterLimits(),
        micro=MicroCapitalLimits(),
    )


def _row(address: str, labels: VendorClusterLabels) -> MemeRushRow:
    return MemeRushRow(
        chain_id="CT_501",
        contract_address=address,
        symbol="MEME",
        name="Meme",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        migrated_at=None,
        price_usd=0.001,
        market_cap_usd=50_000.0,
        liquidity_usd=25_000.0,
        volume_usd=1_000.0,
        holders=200,
        buy_count=10,
        sell_count=5,
        progress_pct=100.0,
        migrated=True,
        labels=labels,
    )


class FakeDiscovery:
    def __init__(self, rows: list[MemeRushRow], fail_stages: set[int] | None = None) -> None:
        self.rows = rows
        self.fail_stages = fail_stages or set()
        self.calls: list[int] = []

    def meme_rush(self, chain_id: str, rank_type: int, limit: int) -> list[MemeRushRow]:
        self.calls.append(rank_type)
        if rank_type in self.fail_stages:
            raise RuntimeError("upstream unavailable")
        return self.rows


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        path = Path(self._temp.name) / "journal.db"
        self.recorder = FlightRecorder(path)
        self.settings = _settings(path)
        self.config = CollectorConfig(stages=("migrated",), enrich=False)

    def _collector(self, discovery: FakeDiscovery) -> Collector:
        return Collector(
            self.settings,
            self.recorder,
            discovery=discovery,
            config=self.config,
            clock=lambda: NOW,
        )

    def test_rejected_candidates_are_still_recorded(self):
        """Rejections are the control group for source scoring, not waste."""
        discovery = FakeDiscovery([_row("BadMint", DIRTY_LABELS)])
        summary = self._collector(discovery).run_once()

        self.assertEqual(summary.observed, 1)
        self.assertEqual(summary.recorded, 1)
        self.assertEqual(summary.rejected, 1)
        events = self.recorder.list_events(50, entity_id="BadMint")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], EVENT_CANDIDATE_OBSERVED)

    def test_observation_captures_the_point_in_time_price_and_liquidity(self):
        """Forward returns cannot be computed if the 'before' was never stored."""
        discovery = FakeDiscovery([_row("Mint1", CLEAN_LABELS)])
        self._collector(discovery).run_once()

        payload = self.recorder.list_events(10, entity_id="Mint1")[0]["payload"]
        self.assertEqual(payload["price_usd"], 0.001)
        self.assertEqual(payload["liquidity_usd"], 25_000.0)
        self.assertEqual(payload["observed_at"], NOW.isoformat())
        self.assertIn("cluster_verdict", payload)

    def test_repeated_poll_in_the_same_second_does_not_duplicate(self):
        discovery = FakeDiscovery([_row("Mint1", CLEAN_LABELS)])
        collector = self._collector(discovery)
        collector.run_once()
        collector.run_once()

        events = self.recorder.list_events(50, entity_id="Mint1")
        self.assertEqual(len(events), 1)

    def test_later_cycle_records_a_new_observation(self):
        """Repetition across cycles is the time series and must be kept."""
        discovery = FakeDiscovery([_row("Mint1", CLEAN_LABELS)])
        times = iter([NOW, NOW.replace(minute=5)])
        collector = Collector(
            self.settings,
            self.recorder,
            discovery=discovery,
            config=self.config,
            clock=lambda: next(times),
        )
        collector.run_once()
        collector.run_once()

        events = self.recorder.list_events(50, entity_id="Mint1")
        self.assertEqual(len(events), 2)

    def test_a_failing_stage_does_not_abort_the_cycle(self):
        discovery = FakeDiscovery([_row("Mint1", CLEAN_LABELS)], fail_stages={10})
        collector = Collector(
            self.settings,
            self.recorder,
            discovery=discovery,
            config=CollectorConfig(stages=("new", "migrated"), enrich=False),
            clock=lambda: NOW,
        )
        summary = collector.run_once()

        self.assertEqual(summary.observed, 1)
        self.assertEqual(len(summary.errors), 1)
        self.assertIn("upstream unavailable", summary.errors[0])

    def test_cycle_summary_is_journalled(self):
        discovery = FakeDiscovery([_row("Mint1", CLEAN_LABELS)])
        self._collector(discovery).run_once()

        events = self.recorder.list_events(50, entity_id="collector")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], EVENT_CYCLE_COMPLETED)

    def test_journal_chain_stays_valid_after_collection(self):
        discovery = FakeDiscovery(
            [_row("Mint1", CLEAN_LABELS), _row("Mint2", DIRTY_LABELS)]
        )
        self._collector(discovery).run_once()
        self.assertTrue(self.recorder.verify_chain())

    def test_candidates_are_paced_to_respect_router_rate_limits(self):
        """Unpaced, a cycle bursts past the quote router's allowance."""
        discovery = FakeDiscovery(
            [_row(f"Mint{index}", CLEAN_LABELS) for index in range(4)]
        )
        collector = Collector(
            self.settings,
            self.recorder,
            discovery=discovery,
            config=CollectorConfig(
                stages=("migrated",), enrich=True, per_candidate_delay_seconds=0.6
            ),
            clock=lambda: NOW,
        )
        slept: list[float] = []
        collector.run_once(sleep=slept.append)

        # One pause between each pair of candidates, never before the first.
        self.assertEqual(slept, [0.6, 0.6, 0.6])

    def test_pacing_is_skipped_when_enrichment_is_off(self):
        """Without enrichment there are no router calls to pace."""
        discovery = FakeDiscovery([_row(f"Mint{i}", CLEAN_LABELS) for i in range(4)])
        slept: list[float] = []
        self._collector(discovery).run_once(sleep=slept.append)
        self.assertEqual(slept, [])

    def test_run_forever_is_bounded_by_cycles(self):
        discovery = FakeDiscovery([_row("Mint1", CLEAN_LABELS)])
        slept: list[float] = []
        collector = self._collector(discovery)
        summaries = collector.run_forever(cycles=3, sleep=slept.append)

        self.assertEqual(len(list(summaries)), 3)
        # Sleeps only between cycles, never after the last one.
        self.assertEqual(len(slept), 2)


if __name__ == "__main__":
    unittest.main()


class PacingTests(unittest.TestCase):
    """The delay must be derived from a rate, not a constant.

    The previous flat 0.6s was justified by arithmetic that did not hold: 60
    candidates at 0.6s is 36 seconds, not the ~100 the comment claimed, so the
    real rate was 200 requests per minute against a provider allowing far less.
    Every 429 in this project traced back to it, and a 429 is invisible damage --
    route and impact read unknown, the gates fail closed, and the candidate is
    journalled as an ordinary rejection.
    """

    def test_delay_keeps_the_default_config_inside_the_target_rate(self) -> None:
        config = CollectorConfig()
        candidates = config.limit_per_stage * len(config.stages)
        calls = candidates * config.quote_calls_per_candidate
        seconds = candidates * config.pacing_delay_seconds
        self.assertGreater(seconds, 0)
        self.assertLessEqual(calls / (seconds / 60.0), config.target_requests_per_minute + 1e-6)

    def test_old_constant_would_have_breached_the_rate(self) -> None:
        """Guards the regression rather than merely describing it."""
        config = CollectorConfig(per_candidate_delay_seconds=0.6)
        candidates = config.limit_per_stage * len(config.stages)
        calls = candidates * config.quote_calls_per_candidate
        seconds = candidates * config.pacing_delay_seconds
        self.assertGreater(calls / (seconds / 60.0), config.target_requests_per_minute)

    def test_explicit_override_is_honoured(self) -> None:
        self.assertEqual(CollectorConfig(per_candidate_delay_seconds=1.5).pacing_delay_seconds, 1.5)

    def test_zero_target_disables_pacing_rather_than_dividing_by_zero(self) -> None:
        self.assertEqual(CollectorConfig(target_requests_per_minute=0).pacing_delay_seconds, 0.0)

    def test_a_stricter_allowance_produces_a_longer_delay(self) -> None:
        lenient = CollectorConfig(target_requests_per_minute=120).pacing_delay_seconds
        strict = CollectorConfig(target_requests_per_minute=30).pacing_delay_seconds
        self.assertGreater(strict, lenient)
