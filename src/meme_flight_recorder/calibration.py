"""Does the system's own judgement predict anything?

This closes the loop. Everything upstream observes, grades and decides;
nothing until now checked whether those decisions were any good. Without this
step tomorrow's filters are exactly as good as today's, forever, and the
journal is an expensive diary.

The method is a control group, which is the part almost no trader has. Every
candidate the gates *rejected* is recorded alongside every one they passed, so
the question "did the filter add value?" is answerable rather than a matter of
opinion. A filter that rejects losers is worth keeping. A filter whose
rejections outperform its approvals is worse than no filter, and only a control
group can tell those apart.

Two guards against fooling ourselves:

**Medians, not means.** One token that goes up 500x drags a mean anywhere. The
project's own data contains exactly that: a bucket with a median of 0.85 and a
mean of 1964.

**Separation is reported, never asserted.** The report shows the numbers and how
many observations back them. It does not declare an edge. With samples this
small the honest output is usually "not yet distinguishable", and printing that
is the point.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Outcome:
    """What happened to one candidate after it was observed."""

    mint: str
    entry_price: float
    later_price: float
    status: str = ""
    cluster_verdict: str = ""
    confidence: float | None = None
    confidence_band: str = ""

    @property
    def multiple(self) -> float:
        return self.later_price / self.entry_price if self.entry_price else 0.0

    @property
    def is_dead(self) -> bool:
        return self.multiple < 0.10

    @property
    def is_winner(self) -> bool:
        return self.multiple >= 2.0


@dataclass(frozen=True)
class GroupStats:
    label: str
    count: int
    median_multiple: float | None = None
    mean_multiple: float | None = None
    dead_pct: float | None = None
    winner_pct: float | None = None

    @property
    def reliable(self) -> bool:
        """Whether the group is large enough to read anything into."""
        return self.count >= 20


def summarise(label: str, outcomes: Sequence[Outcome]) -> GroupStats:
    if not outcomes:
        return GroupStats(label, 0)
    multiples = [outcome.multiple for outcome in outcomes]
    return GroupStats(
        label=label,
        count=len(outcomes),
        median_multiple=round(statistics.median(multiples), 4),
        mean_multiple=round(statistics.mean(multiples), 4),
        dead_pct=round(100.0 * sum(o.is_dead for o in outcomes) / len(outcomes), 1),
        winner_pct=round(100.0 * sum(o.is_winner for o in outcomes) / len(outcomes), 1),
    )


def group_by(outcomes: Iterable[Outcome], attribute: str) -> dict[str, GroupStats]:
    buckets: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        key = str(getattr(outcome, attribute, "") or "unknown")
        buckets.setdefault(key, []).append(outcome)
    return {key: summarise(key, values) for key, values in sorted(buckets.items())}


@dataclass(frozen=True)
class SeparationResult:
    """Whether one group's outcomes differ from another's."""

    better_label: str
    worse_label: str
    better: GroupStats
    worse: GroupStats
    verdict: str
    notes: tuple[str, ...] = field(default_factory=tuple)


def compare(better: GroupStats, worse: GroupStats, minimum_effect: float = 0.15) -> SeparationResult:
    """Compare two groups without overclaiming.

    ``minimum_effect`` is the median difference below which the two are called
    indistinguishable. Small samples produce differences constantly; requiring a
    visible effect keeps noise from being read as skill.
    """
    notes: list[str] = []
    if not better.reliable or not worse.reliable:
        notes.append(f"sample_too_small({better.count},{worse.count})")
        verdict = "not_yet_distinguishable"
    elif better.median_multiple is None or worse.median_multiple is None:
        verdict = "not_yet_distinguishable"
    else:
        difference = better.median_multiple - worse.median_multiple
        if abs(difference) < minimum_effect:
            verdict = "no_meaningful_separation"
        elif difference > 0:
            verdict = "separates_as_expected"
        else:
            # The filter is selecting the worse half. Worth knowing early.
            verdict = "separates_backwards"
            notes.append("rejected_group_outperformed")
    return SeparationResult(better.label, worse.label, better, worse, verdict, tuple(notes))


def outcomes_from_events(
    events: Iterable[Mapping[str, Any]],
    later_prices: Mapping[str, float],
) -> list[Outcome]:
    """Join first observations to a later price lookup.

    Only the first observation of a mint is used, because that is the only one a
    trader could have acted on. Later observations of the same token are the
    system watching a decision it already made, and scoring against them would
    quietly grade the system on hindsight.
    """
    seen: set[str] = set()
    outcomes: list[Outcome] = []
    for event in events:
        mint = str(event.get("entity_id") or "")
        payload = event.get("payload") or {}
        if not mint or mint in seen:
            continue
        entry = payload.get("price_usd")
        later = later_prices.get(mint)
        if not entry or not later:
            continue
        seen.add(mint)
        outcomes.append(
            Outcome(
                mint=mint,
                entry_price=float(entry),
                later_price=float(later),
                status=str(payload.get("status") or ""),
                cluster_verdict=str(payload.get("cluster_verdict") or ""),
                confidence=payload.get("confidence"),
                confidence_band=str(payload.get("confidence_band") or ""),
            )
        )
    return outcomes


def gate_report(outcomes: Sequence[Outcome]) -> SeparationResult:
    """The central question: did passing the gates beat being rejected?"""
    passed = [o for o in outcomes if o.status in {"monitor", "eligible_for_strategy_review"}]
    rejected = [o for o in outcomes if o.status == "reject"]
    return compare(summarise("gate_passed", passed), summarise("gate_rejected", rejected))


def confidence_report(outcomes: Sequence[Outcome]) -> SeparationResult:
    """Did high-confidence candidates beat low-confidence ones?

    This is the test that must pass before position size is ever allowed to
    scale with confidence. Until it does, scaling would be a belief rather than
    a measurement.
    """
    high = [o for o in outcomes if o.confidence_band == "high"]
    low = [o for o in outcomes if o.confidence_band == "low"]
    return compare(summarise("confidence_high", high), summarise("confidence_low", low))
