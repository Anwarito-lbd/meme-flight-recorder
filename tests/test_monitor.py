"""Tests for the forward paper book: persistence, entries and exits.

The load-bearing tests are the ones protecting properties that are expensive to
get wrong: that a position survives a restart with its fills intact, that a
retried cycle cannot open the same position twice, and that the new event names
do not break the older paper ledger that shares the same journal.
"""

from __future__ import annotations

from dataclasses import replace
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
from meme_flight_recorder.notify import DeliveryResult, DeliveryState
from meme_flight_recorder.paper import PaperBroker
from meme_flight_recorder.position_store import (
    closed_positions,
    open_positions,
    save_closed,
    save_opened,
)
from meme_flight_recorder.positions import apply_exit, open_position
from meme_flight_recorder.readiness import LifecycleState, ReadinessPolicy

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


# Maturity is now a separate gate from safety, and its default admits nothing:
# a safety-clean graduation is WATCH until a measured band says otherwise. The
# tests below exercise entry *mechanics* -- sizing, the book cap, exits -- so
# they opt into a permissive maturity policy and leave the default to the tests
# that exist to pin it down.
ANY_MATURITY = ReadinessPolicy(entry_states=frozenset(LifecycleState))


def monitor(book: FlightRecorder, provider: Any, **config: Any) -> PositionMonitor:
    config.setdefault("readiness_policy", ANY_MATURITY)
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
                accepted_statuses=(CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value,),
                readiness_policy=ANY_MATURITY,
            ),
            clock=lambda: NOW,
        )
        opened, skipped, _ = agent.consider([candidate(status=CandidateStatus.MONITOR.value)])
        assert opened == 0
        assert skipped.get("gates_not_passed") == 1


def test_hard_failure_cannot_open_regardless_of_status() -> None:
    """A status label must never override a recorded hard failure.

    MONITOR is only assigned when there are no failures, so a payload carrying
    both is contradictory -- and the engine must refuse it rather than trust the
    friendlier of the two fields.
    """
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, skipped, _ = monitor(book, FakePairProvider()).consider(
            [
                candidate(
                    status=CandidateStatus.MONITOR.value,
                    failures=["mint_authority_active", "holder_concentration_excessive"],
                )
            ]
        )
        assert opened == 0
        assert skipped.get("hard_failure_recorded") == 1


def test_unknown_evidence_cannot_open() -> None:
    """Missing evidence is a failure, never a pass. Fail-closed depends on it."""
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, skipped, _ = monitor(book, FakePairProvider()).consider(
            [
                candidate(
                    status=CandidateStatus.MONITOR.value,
                    failures=["entry_route_unknown", "exit_price_impact_unknown"],
                )
            ]
        )
        assert opened == 0
        assert skipped.get("hard_failure_recorded") == 1


def test_clean_monitor_candidate_with_empty_failures_opens() -> None:
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, _skipped, errors = monitor(book, FakePairProvider()).consider(
            [candidate(status=CandidateStatus.MONITOR.value, failures=[])]
        )
        assert opened == 1, errors


# ------------------------------------------------------------------- maturity


def test_default_policy_watches_rather_than_enters() -> None:
    """Item 9, enforced at the book: a fresh safety-clean graduation is WATCH.

    The measured record is why. Of 51 safety-clean candidates, none reached 2x
    and 92% are dead or gone, so "passed every gate" carries no positive return
    information on this population. The candidate is still journalled -- it is
    the lifecycle evidence the maturity study needs -- it just does not open.
    """
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        agent = PositionMonitor(
            settings(),
            book,
            FakePairProvider(),
            config=MonitorConfig(),
            clock=lambda: NOW,
        )
        opened, skipped, _ = agent.consider(
            [candidate(status=CandidateStatus.MONITOR.value, failures=[], stage="migrated")]
        )
        assert opened == 0
        assert any(reason.startswith("watch:") for reason in skipped)


def test_maturity_policy_cannot_admit_a_failed_candidate() -> None:
    """Widening maturity must never widen safety."""
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, skipped, _ = monitor(book, FakePairProvider()).consider(
            [
                candidate(
                    status=CandidateStatus.MONITOR.value,
                    failures=["developer_selling"],
                    stage="migrated",
                )
            ]
        )
        assert opened == 0
        assert skipped.get("hard_failure_recorded") == 1


# ------------------------------------------------------------------- exposure
#
# Every test below failed before the portfolio state was wired in. The monitor
# built `PortfolioState(equity_usd=...)` and nothing else, so `open_positions`
# and `aggregate_open_risk_usd` were 0/0.0 on every call and *both* exposure
# checks in the risk engine were unreachable. At $50 equity with $10 positions
# the book could reach $100 open -- 200% of the account.


def book_at_equity(
    folder: str,
    equity_usd: float,
    maximum_open_positions: int | None = None,
    maximum_aggregate_open_position_pct: float | None = None,
) -> tuple[FlightRecorder, Any]:
    """A journal plus settings pinned to a known equity.

    Pinned rather than taken from `.env`, because the aggregate cap is a
    percentage of equity and a test reading the operator's live balance would
    change its own meaning the next time that balance is edited. The same
    applies to the risk limits themselves: the 2026-08-22 record-acceleration
    change moved `maximum_open_positions` and the aggregate percentage in
    config, and every test below pins the numbers it was written against so
    future config edits cannot silently rewrite what they prove.
    """
    config_settings = replace(settings(), starting_equity_usd=equity_usd)
    if maximum_open_positions is not None:
        config_settings = replace(
            config_settings,
            risk=replace(config_settings.risk, maximum_open_positions=maximum_open_positions),
        )
    if maximum_aggregate_open_position_pct is not None:
        config_settings = replace(
            config_settings,
            micro=replace(
                config_settings.micro,
                maximum_aggregate_open_position_pct=maximum_aggregate_open_position_pct,
            ),
        )
    return recorder(folder), config_settings


def sized_monitor(
    book: FlightRecorder, config_settings: Any, position_usd: float, **config: Any
) -> PositionMonitor:
    config.setdefault("readiness_policy", ANY_MATURITY)
    config.setdefault("position_usd_override", position_usd)
    config.setdefault("maximum_open_positions", 10)
    return PositionMonitor(
        config_settings,
        book,
        FakePairProvider(),
        config=MonitorConfig(**config),
        clock=lambda: NOW,
    )


def test_position_count_cap_binds_within_a_single_cycle() -> None:
    """`[risk] maximum_open_positions` must actually stop the next entry.

    The book cannot open its whole capacity in one pass: `live` grows as
    positions open, so the state has to be rebuilt per candidate rather than
    once per cycle.
    """
    with TemporaryDirectory() as folder:
        book, config_settings = book_at_equity(folder, 50.0, maximum_open_positions=2)
        agent = sized_monitor(book, config_settings, 10.0)
        candidates = [candidate(mint=f"MINT_{index}") for index in range(4)]
        opened, skipped, errors = agent.consider(candidates)
        assert opened == 2, (opened, skipped, errors)
        assert skipped.get("risk:maximum_open_positions_reached") == 2, skipped


def test_position_count_cap_counts_positions_held_from_earlier_cycles() -> None:
    """Exposure is a property of the book, not of one cycle."""
    with TemporaryDirectory() as folder:
        book, config_settings = book_at_equity(folder, 50.0, maximum_open_positions=2)
        sized_monitor(book, config_settings, 10.0).consider(
            [candidate(mint="MINT_0"), candidate(mint="MINT_1")]
        )
        assert len(open_positions(book)) == 2

        later = sized_monitor(book, config_settings, 10.0)
        opened, skipped, _ = later.consider([candidate(mint="MINT_2")])
        assert opened == 0
        assert skipped.get("risk:maximum_open_positions_reached") == 1, skipped


def test_aggregate_exposure_cap_binds_on_dollars_not_on_count() -> None:
    """The money cap, isolated from the count cap by raising the latter.

    At $50 equity `maximum_aggregate_open_position_pct = 50.0` leaves $25 of
    room, so the third $10 position must be refused even where the count would
    still allow it.
    """
    with TemporaryDirectory() as folder:
        book, config_settings = book_at_equity(
            folder,
            50.0,
            maximum_open_positions=10,
            maximum_aggregate_open_position_pct=50.0,
        )
        agent = sized_monitor(book, config_settings, 10.0)
        candidates = [candidate(mint=f"MINT_{index}") for index in range(3)]
        opened, skipped, errors = agent.consider(candidates)
        assert opened == 2, (opened, skipped, errors)
        assert skipped.get("risk:aggregate_open_position_exceeded") == 1, skipped


def test_pool_share_is_checked_against_the_size_that_actually_opens() -> None:
    """An override replaces the engine's sizing, so approval must use it too.

    `--position-usd` was applied *after* approval, so the pool-share and
    aggregate checks ran against the engine's own (much smaller) number while a
    far larger position opened. The comment in `consider()` says an override
    replaces sizing and never approval; this is the test that makes it true.

    $30 into a $5,500 pool is 0.545% of it, above the 0.5% ceiling.
    """
    with TemporaryDirectory() as folder:
        book, config_settings = book_at_equity(folder, 90.0)
        agent = sized_monitor(
            book, config_settings, 30.0, minimum_pool_liquidity_usd=5_000.0
        )
        opened, skipped, _ = agent.consider([candidate(liquidity_usd=5_500.0)])
        assert opened == 0
        assert skipped.get("risk:order_too_large_for_pool") == 1, skipped


# -------------------------------------------------------------------- alerts


def alerting_monitor(
    book: FlightRecorder,
    provider: Any,
    clock: Any,
    **config: Any,
) -> PositionMonitor:
    """A monitor with alerts on; tests monkeypatch `notify.send` to observe."""
    config.setdefault("readiness_policy", ANY_MATURITY)
    return PositionMonitor(
        settings(),
        book,
        provider,
        config=MonitorConfig(**config),
        clock=clock,
        alerts_enabled=True,
    )


def test_an_opened_position_alerts_with_identity_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    def capture(message: str, **_: Any) -> Any:
        sent.append(message)
        return DeliveryResult(DeliveryState.SENT)

    monkeypatch.setattr("meme_flight_recorder.notify.send", capture)
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, _, errors = alerting_monitor(
            book, FakePairProvider(), lambda: NOW
        ).consider([candidate()])
        assert opened == 1, errors
        assert errors == []
        assert len(sent) == 1
        assert "OPENED AAA" in sent[0]
        # Identity is the mint address, never the ticker.
        assert "MINT_A" in sent[0]
        assert "PAPER ONLY" in sent[0]


def test_a_closed_position_alerts_with_pnl_and_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    def capture(message: str, **_: Any) -> Any:
        sent.append(message)
        return DeliveryResult(DeliveryState.SENT)

    monkeypatch.setattr("meme_flight_recorder.notify.send", capture)
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        agent = alerting_monitor(
            book,
            FakePairProvider(FakePair(0.001, 120_000.0)),
            lambda: NOW,
            maximum_hold_minutes=1,
        )
        agent.consider([candidate()])

        later = NOW + timedelta(minutes=5)
        agent.clock = lambda: later
        live, closed, errors = agent.manage_open()
        assert closed == 1, (live, errors)
        assert errors == []
        close_messages = [m for m in sent if m.startswith("CLOSED")]
        assert len(close_messages) == 1
        assert "MINT_A" in close_messages[0]
        # The pnl sign and the reason are the whole content of a close alert.
        assert "pnl -$" in close_messages[0]
        assert "reason time_limit_reached" in close_messages[0]


def test_a_dropped_alert_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silence about a failed send reads as a quiet market."""

    def fail(message: str, **_: Any) -> Any:
        return DeliveryResult(DeliveryState.FAILED, "HTTP 500: down")

    monkeypatch.setattr("meme_flight_recorder.notify.send", fail)
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, _, errors = alerting_monitor(
            book, FakePairProvider(), lambda: NOW
        ).consider([candidate()])
        assert opened == 1
        assert any("alert failed" in error for error in errors), errors


def test_alerts_are_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default monitor must not message anyone."""

    def explode(message: str, **_: Any) -> Any:
        raise AssertionError("send must not be called when alerts are disabled")

    monkeypatch.setattr("meme_flight_recorder.notify.send", explode)
    with TemporaryDirectory() as folder:
        book = recorder(folder)
        opened, _, _ = monitor(book, FakePairProvider()).consider([candidate()])
        assert opened == 1
