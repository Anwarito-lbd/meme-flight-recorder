"""A confidence score attached to every observation.

The score exists to be tested, not to be trusted. Nothing in this module changes
position size, and that restraint is the whole point: the system's most
confident candidate to date was a token with a clear cluster verdict, 100%
evidence coverage and $519,203 of liquidity, which then fell to zero through its
own stop inside a single five-minute candle. Had size scaled with confidence,
the largest position would have been placed on the only total loss.

Scaling size with edge is a sound idea. It requires a *measured* relationship
between the score and the outcome, and that relationship does not exist yet:
there are no completed trades to calibrate against. Any multiplier chosen today
would be invented. So the score is recorded now, and `calibration.py` decides
later whether it means anything.

Components are weighted evenly on purpose. Unequal weights would encode a belief
about which evidence matters most, and that belief is exactly what has not been
measured. An even split is the honest prior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ConfidenceBand(StrEnum):
    """Coarse buckets, chosen so calibration has enough members per band."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ConfidenceScore:
    value: float
    band: ConfidenceBand
    components: dict[str, float | None] = field(default_factory=dict)
    missing: tuple[str, ...] = ()

    @property
    def coverage_pct(self) -> float:
        """How much of the score rests on observed rather than absent evidence."""
        total = len(self.components)
        if total == 0:
            return 0.0
        known = sum(1 for value in self.components.values() if value is not None)
        return round(100.0 * known / total, 1)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _liquidity_component(liquidity_usd: float | None) -> float | None:
    """Depth, scaled so a million dollars is full marks.

    Deliberately saturating: beyond roughly $1M the marginal safety of more
    depth is small for a position measured in single dollars.
    """
    if liquidity_usd is None:
        return None
    return _clamp(liquidity_usd / 1_000_000.0)


def _impact_component(exit_impact_pct: float | None) -> float | None:
    """Cost of leaving. The exit side is scored, never the entry side.

    Entry impact is optional -- a trade can simply not be taken. Exit impact is
    compulsory and is paid at the worst possible moment.
    """
    if exit_impact_pct is None:
        return None
    return _clamp(1.0 - exit_impact_pct / 5.0)


def _cluster_component(verdict: str | None) -> float | None:
    if verdict is None:
        return None
    return {
        "clear": 1.0,
        "suspect": 0.4,
        "insufficient_evidence": 0.1,
        "disqualified": 0.0,
    }.get(verdict)


def _flow_component(buy_share_pct: float | None) -> float | None:
    """Buy pressure, centred at parity.

    Trade counts rather than unique addresses, so this measures pressure and not
    participation. One wallet can produce many trades.
    """
    if buy_share_pct is None:
        return None
    return _clamp((buy_share_pct - 40.0) / 30.0)


def _age_component(age_minutes: float | None) -> float | None:
    """Time survived, saturating at one day.

    A study across 350 journalled mints found older pools die less but also stop
    moving, and the population this would most reward was absent from that
    sample. So age contributes to confidence without any claim that it predicts
    return, and it is weighted no more heavily than anything else.
    """
    if age_minutes is None:
        return None
    return _clamp(age_minutes / 1_440.0)


def score_confidence(
    *,
    liquidity_usd: float | None = None,
    exit_impact_pct: float | None = None,
    cluster_verdict: str | None = None,
    buy_share_pct: float | None = None,
    age_minutes: float | None = None,
    evidence_coverage_pct: float | None = None,
    high_band: float = 0.70,
    medium_band: float = 0.45,
) -> ConfidenceScore:
    """Score one observation between 0 and 1.

    Missing components are excluded from the average rather than counted as
    zero, and the count of them is returned. A high score resting on two
    observed fields is not the same claim as the same score resting on six, and
    calibration needs to be able to tell those apart.
    """
    components: dict[str, float | None] = {
        "liquidity": _liquidity_component(liquidity_usd),
        "exit_impact": _impact_component(exit_impact_pct),
        "cluster": _cluster_component(cluster_verdict),
        "flow": _flow_component(buy_share_pct),
        "age": _age_component(age_minutes),
    }

    present = [value for value in components.values() if value is not None]
    missing = tuple(name for name, value in components.items() if value is None)
    raw = sum(present) / len(present) if present else 0.0

    # Evidence coverage discounts the result rather than contributing to it. A
    # confident-looking score built on half the pipeline's evidence should not
    # rank alongside one built on all of it.
    if evidence_coverage_pct is not None:
        raw *= _clamp(evidence_coverage_pct / 100.0)

    value = round(raw, 4)
    if value >= high_band:
        band = ConfidenceBand.HIGH
    elif value >= medium_band:
        band = ConfidenceBand.MEDIUM
    else:
        band = ConfidenceBand.LOW
    return ConfidenceScore(value, band, components, missing)
