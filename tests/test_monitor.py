"""Tests for the forward paper book: persistence, entries and exits.

The load-bearing tests are the ones protecting properties that are expensive to
get wrong: that a position survives a restart with its fills intact, that a
retried cycle cannot open the same position twice, and that the new event names
do not break the older paper ledger that shares the same journal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.exits import ExitReason
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.models import CandidateStatus
from meme_flight_recorder.monitor import MonitorConfig, PositionMonitor
from meme_flight_recorder.paper import PaperBroker
from meme_flight_recorder.position_store import (
    closed_positions,
    open_positions,
    save_closed,
    save_opened,
)
from meme_flight_recorder.positions import apply_exit, open_position

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


class FakePair:
    def __init__(self, price: float | None, liquidity: float | None):
        self.price_usd = price
        self.liquidity_usd = liquidity


class FakePairProvider:
    """Returns a configured pair, or raises to simulate an outage."""

    def __init__(self, pair: FakePair | None = None, fail: bool = False):
        self.pair = pair
        self.fail = fail

    def deepest_pair(self, mint: str) -> FakePair | None:
        if self.fail:
            raise RuntimeError("provider down")
        return self.pair


def settings():
    return load_settings()


def recorder(folder: str) -> FlightRecorder:
    return FlightRecorder(Path(folder) / "book.db")


def candidate(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "mint": "MINT_A",
        "symbol": "AAA",
        "status": CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value,
        "price_usd": 0.001,
        "liquidity_usd": 120_000.0,
    }
    base.update(overrides)
    return base


def monitor(book: FlightRecorder, provider: Any, **config: Any) -> PositionMonitor:
    return PositionMonitor(
        settings(),
        book,
        provider,
        config=MonitorConfig(**config),
        clock=lambda: NOW,
    )


# --------------------------------------------------------------- persistence


def test_position_round_trips_with_fills_intact() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        position = open_position(
            mint="MINT_A",
            symbol="AAA",
            at=NOW,
            price=0.001,
            position_usd=4.0,
            stop_price=0.0,
            target_price=0.0,
            breakout_level=0.0,
            liquidity_usd=120_000.0,
            cost_pct=1.14,
        )
        save_opened(book, position)
        restored = open_positions(book)["MINT_A"]
        assert restored.quantity == pytest.approx(position.quantity)
        assert len(restored.fills) == 1
        assert restored.fills[0].cost_usd == pytest.approx(position.fills[0].cost_usd)


def test_reopening_the_same_position_is_idempotent() -> None:
    """A retried cycle must not double the recorded exposure."""
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        position = open_position(
            mint="MINT_A",
            symbol="AAA",
            at=NOW,
            price=0.001,
            position_usd=4.0,
            stop_price=0.0,
            target_price=0.0,
            breakout_level=0.0,
        )
        save_opened(book, position)
        save_opened(book, position)
        assert len(open_positions(book)) == 1


def test_closed_position_leaves_the_open_book() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        position = open_position(
            mint="MINT_A",
            symbol="AAA",
            at=NOW,
            price=0.001,
            position_usd=4.0,
            stop_price=0.0,
            target_price=0.0,
            breakout_level=0.0,
        )
        save_opened(book, position)
        exited = apply_exit(
            position, at=NOW, price=0.002, fraction=1.0, reason=ExitReason.TIME_LIMIT
        )
        save_closed(book, exited)
        assert open_positions(book) == {}
        record = closed_positions(book)
        assert len(record) == 1
        assert record[0].realised_usd > 0


def test_save_closed_rejects_a_still_open_position() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        position = open_position(
            mint="MINT_A",
            symbol="AAA",
            at=NOW,
            price=0.001,
            position_usd=4.0,
            stop_price=0.0,
            target_price=0.0,
            breakout_level=0.0,
        )
        with pytest.raises(ValueError):
            save_closed(book, position)


def test_new_event_names_do_not_break_the_older_paper_ledger() -> None:
    """The reason these events are not called paper_position_opened.

    PaperBroker replays that event name into a different dataclass. If this
    model wrote under the same name, the CEX and shadow engines would crash on
    their next restart -- in a different component, long after the write.
    """
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        save_opened(
            book,
            open_position(
                mint="MINT_A",
                symbol="AAA",
                at=NOW,
                price=0.001,
                position_usd=4.0,
                stop_price=0.0,
                target_price=0.0,
                breakout_level=0.0,
            ),
        )
        # Must not raise.
        assert PaperBroker(book).positions == {}


def test_journal_chain_survives_position_events() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        save_opened(
            book,
            open_position(
                mint="MINT_A",
                symbol="AAA",
                at=NOW,
                price=0.001,
                position_usd=4.0,
                stop_price=0.0,
                target_price=0.0,
                breakout_level=0.0,
            ),
        )
        assert book.verify_chain() is True


# -------------------------------------------------------------------- entries


def test_deep_pool_candidate_opens_a_position() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, skipped, errors = monitor(book, FakePairProvider()).consider([candidate()])
        assert opened == 1, (skipped, errors)
        assert "MINT_A" in open_positions(book)


def test_shallow_pool_is_skipped() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, skipped, _ = monitor(book, FakePairProvider()).consider(
            [candidate(liquidity_usd=10_000.0)]
        )
        assert opened == 0
        assert skipped.get("pool_too_shallow") == 1


def test_candidate_failing_the_gates_is_skipped() -> None:
    """The structural filter is an addition to the safety gates, not a bypass."""
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, skipped, _ = monitor(book, FakePairProvider()).consider(
            [candidate(status=CandidateStatus.REJECT.value)]
        )
        assert opened == 0
        assert skipped.get("gates_not_passed") == 1


def test_one_position_per_mint() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        agent = monitor(book, FakePairProvider())
        agent.consider([candidate()])
        opened, skipped, _ = agent.consider([candidate()])
        assert opened == 0
        assert skipped.get("already_open") == 1


def test_book_size_is_capped() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        agent = monitor(book, FakePairProvider(), maximum_open_positions=2)
        candidates = [candidate(mint=f"MINT_{index}") for index in range(5)]
        opened, skipped, _ = agent.consider(candidates)
        assert opened == 2
        assert skipped.get("book_full") == 3


def test_position_opens_with_no_stop_because_the_policy_is_hold() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        monitor(book, FakePairProvider()).consider([candidate()])
        position = open_positions(book)["MINT_A"]
        assert position.stop_price == 0.0
        assert position.breakout_level == 0.0
        assert position.metadata["entry_rule"] == "structural_deep_pool"


# --------------------------------------------------------------------- exits


def open_one(book: FlightRecorder, price: float = 0.001) -> None:
    monitor(book, FakePairProvider()).consider([candidate(price_usd=price)])


def test_healthy_position_is_held() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        open_one(book)
        agent = monitor(book, FakePairProvider(FakePair(0.002, 120_000.0)))
        _live, closed, errors = agent.manage_open()
        assert closed == 0, errors
        assert len(open_positions(book)) == 1


def test_liquidity_collapse_closes_the_position() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        open_one(book)
        agent = monitor(book, FakePairProvider(FakePair(0.001, 1_000.0)))
        _live, closed, _ = agent.manage_open()
        assert closed == 1
        assert open_positions(book) == {}
        assert closed_positions(book)[0].close_reason is not None


def test_unobservable_position_is_closed_after_repeated_failures() -> None:
    """Missing evidence blocks a new position; for a held one it forces an exit."""
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        open_one(book)
        agent = monitor(book, FakePairProvider(fail=True), stale_after_failed_checks=3)
        for _ in range(2):
            _live, closed, _ = agent.manage_open()
            assert closed == 0
        _live, closed, _ = agent.manage_open()
        assert closed == 1
        assert closed_positions(book)[0].close_reason is ExitReason.STALE_UNOBSERVABLE


def test_time_limit_closes_a_stagnant_position() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        open_one(book)
        later = PositionMonitor(
            settings(),
            book,
            FakePairProvider(FakePair(0.001, 120_000.0)),
            config=MonitorConfig(maximum_hold_minutes=60),
            clock=lambda: NOW + timedelta(hours=3),
        )
        _live, closed, _ = later.manage_open()
        assert closed == 1
        assert closed_positions(book)[0].close_reason is ExitReason.TIME_LIMIT


def test_run_cycle_manages_the_book_before_considering_entries() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        agent = monitor(book, FakePairProvider(FakePair(0.001, 120_000.0)))
        summary = agent.run_cycle([candidate(mint="MINT_B")])
        assert summary.opened == 1
        assert summary.still_open == 1
        assert summary.considered == 1


def test_monitor_status_can_open_because_it_means_every_gate_passed() -> None:
    """The wiring bug that kept the paper book empty for the whole project.

    SafetyEngine assigns MONITOR only when there are no failures at all -- it is
    a maturity label for young or launchpad-universe tokens, not a safety
    verdict. Requiring ELIGIBLE discarded 49 of the 52 candidates that had
    cleared every gate across 18,846 journalled mints.
    """
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, skipped, errors = monitor(book, FakePairProvider()).consider(
            [candidate(status=CandidateStatus.MONITOR.value)]
        )
        assert opened == 1, (skipped, errors)


def test_reject_status_still_cannot_open() -> None:
    """The fix must not become a general relaxation."""
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, skipped, _ = monitor(book, FakePairProvider()).consider(
            [candidate(status=CandidateStatus.REJECT.value)]
        )
        assert opened == 0
        assert skipped.get("gates_not_passed") == 1


def test_accepted_statuses_is_configurable() -> None:
    """Narrowing it back to ELIGIBLE-only must remain possible."""
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        agent = PositionMonitor(
            settings(),
            book,
            FakePairProvider(),
            config=MonitorConfig(
                accepted_statuses=(CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value,)
            ),
            clock=lambda: NOW,
        )
        opened, skipped, _ = agent.consider([candidate(status=CandidateStatus.MONITOR.value)])
        assert opened == 0
        assert skipped.get("gates_not_passed") == 1
