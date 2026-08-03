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
