"""The readiness invariants, asserted rather than trusted to review.

The bug this module exists to prevent was not a wrong threshold. It was one
field answering three questions, so a maturity label was read as a trading
verdict and 49 of 52 gate-clearing candidates were silently discarded. The tests
that matter here are therefore the ones that fix the *shape* of the decision:
what can never become an entry, whatever anyone later configures.
"""

from __future__ import annotations

import itertools

import pytest

from meme_flight_recorder.models import CandidateStatus
from meme_flight_recorder.readiness import (
    LifecycleState,
    ReadinessPolicy,
    SafetyVerdict,
    StrategyReadiness,
    assess,
    assess_candidate,
    lifecycle_state,
    verdict_from_failures,
    verdict_from_status,
)

PERMISSIVE = ReadinessPolicy(entry_states=frozenset(LifecycleState))


def test_no_failures_is_a_pass() -> None:
    assert verdict_from_failures(()) is SafetyVerdict.PASS
    assert verdict_from_failures(None) is SafetyVerdict.PASS


def test_missing_evidence_is_unknown_not_failure() -> None:
    """A provider outage is a collection problem, not a verdict about a token."""
    assert verdict_from_failures(("entry_route_unknown",)) is SafetyVerdict.UNKNOWN
    assert verdict_from_failures(("cluster_evidence_insufficient",)) is SafetyVerdict.UNKNOWN
    assert verdict_from_failures(("cluster_evidence_missing",)) is SafetyVerdict.UNKNOWN


def test_a_real_failure_outranks_missing_evidence() -> None:
    verdict = verdict_from_failures(("entry_route_unknown", "holder_concentration_excessive"))
    assert verdict is SafetyVerdict.FAIL


def test_rejected_with_no_recorded_reason_is_unknown_not_pass() -> None:
    """A lost reason is not evidence of safety."""
    assert verdict_from_status(CandidateStatus.REJECT, ()) is SafetyVerdict.UNKNOWN


def test_failures_win_when_status_disagrees() -> None:
    verdict = verdict_from_status(CandidateStatus.MONITOR, ("developer_selling",))
    assert verdict is SafetyVerdict.FAIL


@pytest.mark.parametrize("lifecycle", list(LifecycleState))
def test_failed_safety_can_never_be_entry(lifecycle: LifecycleState) -> None:
    result = assess(SafetyVerdict.FAIL, lifecycle, 10_000.0, PERMISSIVE)
    assert result.readiness is StrategyReadiness.BLOCK


@pytest.mark.parametrize("lifecycle", list(LifecycleState))
def test_unknown_safety_can_never_be_entry(lifecycle: LifecycleState) -> None:
    """Fail closed: missing critical evidence rejects, at every maturity."""
    result = assess(SafetyVerdict.UNKNOWN, lifecycle, 10_000.0, PERMISSIVE)
    assert result.readiness is StrategyReadiness.BLOCK


@pytest.mark.parametrize("verdict", list(SafetyVerdict))
def test_dead_can_never_be_entry(verdict: SafetyVerdict) -> None:
    result = assess(verdict, LifecycleState.DEAD, 10_000.0, PERMISSIVE)
    assert result.readiness is StrategyReadiness.BLOCK


def test_no_configuration_admits_fail_or_unknown() -> None:
    """The exclusion is structural, not a default that can be widened away."""
    for verdict, lifecycle in itertools.product(
        (SafetyVerdict.FAIL, SafetyVerdict.UNKNOWN), LifecycleState
    ):
        policy = ReadinessPolicy(entry_states=frozenset(LifecycleState), minimum_entry_age_minutes=0)
        assert assess(verdict, lifecycle, 99_999.0, policy).readiness is StrategyReadiness.BLOCK


def test_safe_and_graduated_is_watch_not_entry() -> None:
    """Item 9, and the measured record: 0 of 51 safety-clean reached 2x."""
    result = assess(SafetyVerdict.PASS, LifecycleState.GRADUATED, 3.0)
    assert result.readiness is StrategyReadiness.WATCH


def test_default_policy_admits_nothing() -> None:
    for lifecycle in LifecycleState:
        result = assess(SafetyVerdict.PASS, lifecycle, 10_000.0)
        assert result.readiness is not StrategyReadiness.ENTRY


def test_entry_requires_both_a_permitted_state_and_the_minimum_age() -> None:
    policy = ReadinessPolicy(
        entry_states=frozenset({LifecycleState.MATURE}), minimum_entry_age_minutes=360.0
    )
    assert assess(SafetyVerdict.PASS, LifecycleState.MATURE, 400.0, policy).readiness is (
        StrategyReadiness.ENTRY
    )
    assert assess(SafetyVerdict.PASS, LifecycleState.SURVIVING, 400.0, policy).readiness is (
        StrategyReadiness.WATCH
    )
    assert assess(SafetyVerdict.PASS, LifecycleState.MATURE, 100.0, policy).readiness is (
        StrategyReadiness.WATCH
    )


def test_unknown_age_cannot_satisfy_an_age_requirement() -> None:
    """Absent is not zero, and it is certainly not "old enough"."""
    policy = ReadinessPolicy(
        entry_states=frozenset({LifecycleState.MATURE}), minimum_entry_age_minutes=60.0
    )
    result = assess(SafetyVerdict.PASS, LifecycleState.MATURE, None, policy)
    assert result.readiness is StrategyReadiness.WATCH
    assert result.reason == "age_unknown"


def test_lifecycle_bands_are_ordered_by_age() -> None:
    common = {"graduated": True, "liquidity_usd": 100_000.0}
    assert lifecycle_state(5.0, **common) is LifecycleState.GRADUATED
    assert lifecycle_state(60.0, **common) is LifecycleState.SURVIVING
    assert lifecycle_state(400.0, **common) is LifecycleState.MATURE
    assert lifecycle_state(5_000.0, **common) is LifecycleState.ESTABLISHED


def test_unknown_age_is_not_promoted_past_what_it_demonstrated() -> None:
    state = lifecycle_state(None, graduated=True, liquidity_usd=100_000.0)
    assert state is LifecycleState.GRADUATED


def test_a_silent_pool_is_dead_even_with_liquidity_reported() -> None:
    """No trades is death in the only sense that matters to a position."""
    state = lifecycle_state(
        1_000.0, graduated=True, liquidity_usd=100_000.0, still_trading=False
    )
    assert state is LifecycleState.DEAD


def test_missing_liquidity_does_not_read_as_death() -> None:
    """The failure mode this project names: absent is not zero."""
    state = lifecycle_state(1_000.0, graduated=True, liquidity_usd=None)
    assert state is not LifecycleState.DEAD
    assert state is LifecycleState.MATURE


def test_pre_graduation_states_come_from_curve_progress() -> None:
    assert lifecycle_state(1.0, graduated=False, bonding_curve_progress_pct=None) is (
        LifecycleState.NEW
    )
    assert lifecycle_state(1.0, graduated=False, bonding_curve_progress_pct=50.0) is (
        LifecycleState.BONDING
    )
    assert lifecycle_state(1.0, graduated=False, bonding_curve_progress_pct=90.0) is (
        LifecycleState.NEAR_GRADUATION
    )


def test_assess_candidate_reads_a_journalled_row() -> None:
    row = {
        "status": CandidateStatus.MONITOR.value,
        "failures": [],
        "age_minutes": 4.7,
        "liquidity_usd": 60_000.0,
        "stage": "migrated",
    }
    result = assess_candidate(row)
    assert result.verdict is SafetyVerdict.PASS
    assert result.lifecycle is LifecycleState.GRADUATED
    # The exact case that emptied the paper book, now landing on WATCH by
    # policy rather than being discarded by a status comparison.
    assert result.readiness is StrategyReadiness.WATCH


def test_assess_candidate_blocks_a_rejected_row() -> None:
    row = {
        "status": CandidateStatus.REJECT.value,
        "failures": ["holder_concentration_excessive"],
        "age_minutes": 4.7,
        "liquidity_usd": 60_000.0,
        "stage": "migrated",
    }
    assert assess_candidate(row, PERMISSIVE).readiness is StrategyReadiness.BLOCK
