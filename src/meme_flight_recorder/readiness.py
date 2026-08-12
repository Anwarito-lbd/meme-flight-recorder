"""Three questions that were being answered by one field.

`CandidateStatus` has been carrying three different meanings at once, and the
paper book stayed empty for weeks because of it. `SafetyEngine` assigns MONITOR
when a token passed every hard gate but is young or from the launchpad universe
-- a *maturity* statement -- while `PositionMonitor` read the same field as a
*trading readiness* statement and discarded 49 of the 52 candidates that had
cleared every gate.

The fix is not a better mapping. It is three separate answers:

  * ``SafetyVerdict``   -- is it dangerous?          (risk)
  * ``LifecycleState``  -- how far along is it?      (maturity)
  * ``StrategyReadiness`` -- should we trade it now? (policy)

Safety and maturity are *evidence*. Readiness is a *decision* taken from them,
and keeping it separate is what lets the maturity threshold move as the survival
study measures it without anyone touching a safety gate to do it.

The invariants below are the point of the module, and they are asserted by
tests rather than trusted to review:

  * a FAIL verdict can never produce ENTRY;
  * UNKNOWN critical evidence can never produce ENTRY -- fail closed, as
    everywhere else in this system;
  * a DEAD lifecycle can never produce ENTRY;
  * ``PASS`` at ``GRADUATED`` is **WATCH**, not ENTRY. A fresh graduation is
    unproven rather than safe, and the measured record is unambiguous about it:
    of 51 safety-clean candidates, 0 reached 2x and 92% are dead or gone.

This module is pure. It assesses evidence a caller already fetched, so it tests
without a network and cannot spend API budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import CandidateStatus, SafetyDecision

# Failure suffixes that mean "we could not tell", as opposed to "we looked and
# it was bad". Kept as suffixes because every such failure in `safety.py` is
# named `<subject>_unknown` / `_missing` / `_insufficient`, and a new one should
# be classified correctly the day it is added rather than the day someone
# remembers to update a list.
MISSING_EVIDENCE_SUFFIXES: tuple[str, ...] = (
    "_unknown",
    "_missing",
    "_insufficient",
    "_invalid",
)


class SafetyVerdict(StrEnum):
    """Is this token dangerous? Nothing about whether it is worth trading."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class LifecycleState(StrEnum):
    """How far along is this token? Nothing about whether it is safe."""

    NEW = "new"
    BONDING = "bonding"
    NEAR_GRADUATION = "near_graduation"
    GRADUATED = "graduated"
    SURVIVING = "surviving"
    MATURE = "mature"
    ESTABLISHED = "established"
    DEAD = "dead"


class StrategyReadiness(StrEnum):
    """Should we trade it now? A decision, taken from the two above."""

    WATCH = "watch"
    ENTRY = "entry"
    BLOCK = "block"


# Age boundaries between the post-graduation states, in minutes.
#
# These are deliberately *not* the thresholds that decide entry -- that is the
# policy below, and it defaults to admitting none of them. They exist so the
# survival study has stable labels to report against. The boundaries themselves
# are conventional round numbers chosen before the study ran, so that the study
# cannot be accused of having picked the bands that flattered it.
SURVIVING_AFTER_MINUTES = 30.0
MATURE_AFTER_MINUTES = 360.0  # six hours
ESTABLISHED_AFTER_MINUTES = 1_440.0  # one day


def verdict_from_failures(failures: tuple[str, ...] | list[str] | None) -> SafetyVerdict:
    """Split a failure list into "found something" and "could not tell".

    FAIL outranks UNKNOWN: a token with both a real failure and a missing field
    is dangerous, not unmeasured. Neither can reach ENTRY, but the distinction
    decides what to *do* about it -- a FAIL is a verdict and is final, while an
    UNKNOWN is a collection problem and is fixed by supplying the evidence from
    chain. Conflating them is how 13 of 56 winners came to be recorded as risk
    decisions when they were really provider outages.
    """
    if not failures:
        return SafetyVerdict.PASS
    for failure in failures:
        if not failure.endswith(MISSING_EVIDENCE_SUFFIXES):
            return SafetyVerdict.FAIL
    return SafetyVerdict.UNKNOWN


def verdict_from_decision(decision: SafetyDecision) -> SafetyVerdict:
    """Bridge from the existing engine, which is deliberately left unchanged."""
    return verdict_from_failures(decision.failures)


def verdict_from_status(
    status: str | CandidateStatus, failures: tuple[str, ...] | list[str] | None = None
) -> SafetyVerdict:
    """Read a journalled row, where only the status and failures were stored.

    Failures win when the two disagree. Trusting the friendlier of two
    contradictory fields is how a fail-closed gate quietly stops being one.
    """
    if failures:
        return verdict_from_failures(failures)
    value = status.value if isinstance(status, CandidateStatus) else status
    if value == CandidateStatus.REJECT.value:
        # Rejected with no recorded failure should be impossible. If it happens,
        # the reason was lost, and a lost reason is not evidence of safety.
        return SafetyVerdict.UNKNOWN
    return SafetyVerdict.PASS


def lifecycle_state(
    age_minutes: float | None,
    *,
    graduated: bool | None = None,
    bonding_curve_progress_pct: float | None = None,
    liquidity_usd: float | None = None,
    still_trading: bool | None = None,
    minimum_liquidity_usd: float = 5_000.0,
) -> LifecycleState:
    """Place a token on the maturity axis from evidence, never from an opinion.

    `still_trading` is the survival signal the candle history supplies: a pool
    with no trades after some point has stopped existing in the only sense that
    matters to a position. It is separated from liquidity because the two fail
    differently -- liquidity can be missing while the pool trades happily, and
    that must not read as death.
    """
    if still_trading is False:
        return LifecycleState.DEAD
    if liquidity_usd is not None and liquidity_usd < minimum_liquidity_usd:
        return LifecycleState.DEAD

    if graduated is not True:
        progress = bonding_curve_progress_pct
        if progress is None or progress < 5:
            return LifecycleState.NEW
        if progress >= 85:
            return LifecycleState.NEAR_GRADUATION
        return LifecycleState.BONDING

    # Absent is not zero. An unknown age cannot be promoted past the state it
    # has demonstrably reached, so it stays at GRADUATED rather than being
    # treated as freshly born or as long-lived.
    if age_minutes is None:
        return LifecycleState.GRADUATED
    if age_minutes >= ESTABLISHED_AFTER_MINUTES:
        return LifecycleState.ESTABLISHED
    if age_minutes >= MATURE_AFTER_MINUTES:
        return LifecycleState.MATURE
    if age_minutes >= SURVIVING_AFTER_MINUTES:
        return LifecycleState.SURVIVING
    return LifecycleState.GRADUATED


@dataclass(frozen=True)
class ReadinessPolicy:
    """Which maturity states a safe token may be entered in.

    The default admits **nothing**, and that is the whole point of the type.
    Until the maturity study identifies a band that is positive after costs on
    a held-out split, every safe token is WATCH. A fresh safety-clean graduation
    is unproven, not tradeable: 0 of 51 reached 2x and 92% are dead or gone.

    Widening this tuple is the single, visible act that turns research into
    trading, and it is meant to require a study to justify it.
    """

    entry_states: frozenset[LifecycleState] = frozenset()

    # A safe token below this age is watched regardless of its state. Held
    # separately from `entry_states` because age and state answer different
    # questions and a study may move one without the other.
    minimum_entry_age_minutes: float = 0.0

    # Readiness never relaxes a safety gate, so there is no switch here to
    # admit FAIL or UNKNOWN. Their exclusion is structural, in `assess` below.


@dataclass(frozen=True)
class ReadinessAssessment:
    verdict: SafetyVerdict
    lifecycle: LifecycleState
    readiness: StrategyReadiness
    reason: str


def assess(
    verdict: SafetyVerdict,
    lifecycle: LifecycleState,
    age_minutes: float | None = None,
    policy: ReadinessPolicy | None = None,
) -> ReadinessAssessment:
    """Decide what to do, and record why in a form that survives journalling."""
    policy = policy or ReadinessPolicy()

    def result(readiness: StrategyReadiness, reason: str) -> ReadinessAssessment:
        return ReadinessAssessment(verdict, lifecycle, readiness, reason)

    # Order matters: each of these three is an absolute veto, and none of them
    # can be reached by widening a policy.
    if verdict is SafetyVerdict.FAIL:
        return result(StrategyReadiness.BLOCK, "safety_failed")
    if verdict is SafetyVerdict.UNKNOWN:
        return result(StrategyReadiness.BLOCK, "safety_evidence_missing")
    if lifecycle is LifecycleState.DEAD:
        return result(StrategyReadiness.BLOCK, "lifecycle_dead")

    if lifecycle not in policy.entry_states:
        return result(StrategyReadiness.WATCH, f"lifecycle_{lifecycle.value}_not_an_entry_state")
    if policy.minimum_entry_age_minutes > 0:
        if age_minutes is None:
            # Fail closed: an unmeasured age cannot satisfy an age requirement.
            return result(StrategyReadiness.WATCH, "age_unknown")
        if age_minutes < policy.minimum_entry_age_minutes:
            return result(StrategyReadiness.WATCH, "below_minimum_entry_age")
    return result(StrategyReadiness.ENTRY, "safe_and_mature_enough")


def assess_candidate(
    candidate: dict[str, object],
    policy: ReadinessPolicy | None = None,
    still_trading: bool | None = None,
) -> ReadinessAssessment:
    """Convenience wrapper over a journalled candidate row.

    Kept here rather than in the monitor so that the studies, the replay and the
    live book all classify a row through exactly the same code. The same
    question having three implementations is a defect this project has already
    paid for once.
    """
    failures = candidate.get("failures") or ()
    verdict = verdict_from_status(
        str(candidate.get("status") or ""),
        tuple(failures) if isinstance(failures, list | tuple) else (),
    )
    age = candidate.get("age_minutes")
    liquidity = candidate.get("liquidity_usd")
    # The launchpad feed reports `stage`, and only two of its values imply a
    # pool that has left the bonding curve. `mover` comes from the trending
    # feed, which by construction only contains pools that already exist.
    stage = candidate.get("stage")
    graduated = True if stage in {"migrated", "mover"} else None if stage is None else False
    lifecycle = lifecycle_state(
        float(age) if isinstance(age, int | float) else None,
        graduated=graduated,
        bonding_curve_progress_pct=None,
        liquidity_usd=float(liquidity) if isinstance(liquidity, int | float) else None,
        still_trading=still_trading,
    )
    return assess(
        verdict, lifecycle, float(age) if isinstance(age, int | float) else None, policy
    )
