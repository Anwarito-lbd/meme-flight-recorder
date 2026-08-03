"""Stage 6: is the demand real, or is it being manufactured?

Every gate in this system so far answers a question about *risk* -- can this
token be sold, is the supply held by one actor, will the pool still be there.
None of them answers whether anyone actually wants the token. That gap is not
cosmetic: measured across 402 outcomes, cluster-clear candidates died more often
than cluster-disqualified ones and reached 2x less often. The safety gates were
never the return thesis and the data said so. This module is the first attempt
at the other half.

It is deliberately weaker than the framework it implements, for a reason worth
stating in full.

**Unique buyers cannot be observed here.** The strongest organic-flow signal is
acceleration in the number of *distinct addresses* buying. The free pool APIs
publish transaction counts, and a transaction count is not a buyer -- one wallet
running a script produces a hundred of them, which is precisely the pattern a
manipulator generates and the pattern this signal is supposed to catch. Getting
real unique buyers means parsing every swap against the pool through an
archival RPC, at a cost far beyond a $10-40 account. So
``unique_buyer_acceleration`` is always ``None``. It is not approximated from
transaction counts, because renaming a weak number into a strong one is how a
gate silently stops working while continuing to report passes.

What remains observable is holder growth, liquidity persistence, and the
manipulation tells -- and those come free, from the collector's own journal.
Repeated observations of the same mint are already a time series; nothing new
has to be fetched to read it.

**A verdict of ORGANIC is therefore a claim about what was not found, not proof
of genuine demand.** Missing evidence returns INSUFFICIENT_EVIDENCE and never a
pass, matching how the safety engine already fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from .config import FlowLimits


class FlowVerdict(StrEnum):
    ORGANIC = "organic"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SUSPICIOUS = "suspicious"


@dataclass(frozen=True)
class FlowObservation:
    """One journalled read of a token, reduced to the fields flow needs.

    Decoding the journal payload happens here and nowhere else, so a change to
    what the collector writes has exactly one place to be reflected.
    """

    observed_at: str
    price_usd: float | None = None
    liquidity_usd: float | None = None
    holders: int | None = None
    volume_5m_usd: float | None = None
    volume_to_liquidity_5m: float | None = None
    buy_txns_5m: int | None = None
    sell_txns_5m: int | None = None
    buy_txns_total: int | None = None
    sell_txns_total: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> FlowObservation:
        """Build from a ``candidate_observed`` payload.

        Every flow field is read with ``.get``. Observations journalled before
        Stage 6 existed simply carry ``None``, which resolves to
        INSUFFICIENT_EVIDENCE rather than an exception or a false pass.
        """
        return cls(
            observed_at=str(payload.get("observed_at") or ""),
            price_usd=_as_float(payload.get("price_usd")),
            liquidity_usd=_as_float(payload.get("liquidity_usd")),
            holders=_as_int(payload.get("holders")),
            volume_5m_usd=_as_float(payload.get("volume_5m_usd")),
            volume_to_liquidity_5m=_as_float(payload.get("volume_to_liquidity_5m")),
            buy_txns_5m=_as_int(payload.get("buy_txns_5m")),
            sell_txns_5m=_as_int(payload.get("sell_txns_5m")),
            buy_txns_total=_as_int(payload.get("buy_txns_total")),
            sell_txns_total=_as_int(payload.get("sell_txns_total")),
        )

    @property
    def buy_share_pct(self) -> float | None:
        """Share of transactions that were buys, preferring the 5m window.

        This is a share of *transactions*, not of buyers or of volume. The name
        says so, and it must keep saying so wherever it is carried.
        """
        for buys, sells in (
            (self.buy_txns_5m, self.sell_txns_5m),
            (self.buy_txns_total, self.sell_txns_total),
        ):
            if buys is None or sells is None:
                continue
            total = buys + sells
            if total > 0:
                return round(100.0 * buys / total, 4)
        return None


@dataclass(frozen=True)
class FlowAssessment:
    """What the observation history says about demand."""

    verdict: FlowVerdict
    observations: int
    holder_growth_pct: float | None = None
    liquidity_growth_pct: float | None = None
    price_change_pct: float | None = None
    volume_change_ratio: float | None = None
    volume_to_liquidity_5m: float | None = None
    buy_share_pct: float | None = None
    # Always None. Kept as an explicit field so that "we did not measure this"
    # is visible in the journal rather than being an absence someone later
    # mistakes for zero. See the module docstring.
    unique_buyer_acceleration: float | None = None
    wash_flags: tuple[str, ...] = field(default_factory=tuple)
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocks_entry(self) -> bool:
        """Whether this assessment should veto a structural entry signal.

        Only positive manipulation evidence vetoes. INSUFFICIENT_EVIDENCE does
        not, because on this data most tokens will land there and a gate that
        rejects everything is indistinguishable from a broken one -- the
        inherited breakout threshold that admitted 1.3% of windows is the
        cautionary example. Whether that is the right call is what
        ``scripts/study_flow_value.py`` decides; until it reports, this
        assessment is journalled and not consulted.
        """
        return self.verdict is FlowVerdict.SUSPICIOUS


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _growth_pct(start: float | None, end: float | None) -> float | None:
    """Percentage change, or None when it cannot be computed honestly.

    A start of zero has no percentage change -- returning 0.0 there would report
    "no growth" for a pool that went from nothing to something, and returning a
    large number would invent one.
    """
    if start is None or end is None or start <= 0:
        return None
    return round(100.0 * (end - start) / start, 4)


def _ratio(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start <= 0:
        return None
    return round(end / start, 4)


def assess_flow(
    observations: list[FlowObservation],
    limits: FlowLimits | None = None,
) -> FlowAssessment:
    """Judge one mint's demand from its ordered observation history.

    ``observations`` must be oldest first. Growth is measured across the full
    span rather than between the last two reads, because the collector polls on
    a five-minute interval and a single interval is noise.
    """
    limits = limits or FlowLimits()
    count = len(observations)

    if count < limits.minimum_observations:
        return FlowAssessment(
            verdict=FlowVerdict.INSUFFICIENT_EVIDENCE,
            observations=count,
            missing=("observation_history",),
        )

    first, last = observations[0], observations[-1]

    holder_growth = _growth_pct(first.holders, last.holders)
    liquidity_growth = _growth_pct(first.liquidity_usd, last.liquidity_usd)
    price_change = _growth_pct(first.price_usd, last.price_usd)
    volume_ratio = _ratio(first.volume_5m_usd, last.volume_5m_usd)
    turnover = last.volume_to_liquidity_5m
    buy_share = last.buy_share_pct

    missing: list[str] = []
    if holder_growth is None:
        missing.append("holder_growth")
    if liquidity_growth is None:
        missing.append("liquidity_growth")
    # Recorded on every assessment: the metric the framework leans on hardest is
    # the one this data source cannot supply.
    missing.append("unique_buyers")

    wash_flags: list[str] = []

    # Volume exploded while ownership did not. The buying is circulating among
    # the same wallets.
    if (
        volume_ratio is not None
        and volume_ratio >= limits.wash_volume_spike_multiple
        and holder_growth is not None
        and holder_growth < limits.wash_flat_holder_growth_pct
    ):
        wash_flags.append("volume_spike_without_holders")

    # Volume exploded and price did not move. Buys and sells are matching each
    # other rather than consuming depth.
    if (
        volume_ratio is not None
        and volume_ratio >= limits.wash_volume_spike_multiple
        and price_change is not None
        and abs(price_change) < limits.wash_flat_price_move_pct
    ):
        wash_flags.append("volume_spike_without_price_response")

    # Turnover far beyond what a pool this deep can support organically.
    if turnover is not None and turnover >= limits.wash_volume_to_liquidity_5m:
        wash_flags.append("extreme_volume_to_liquidity")

    assessment = FlowAssessment(
        verdict=FlowVerdict.INSUFFICIENT_EVIDENCE,
        observations=count,
        holder_growth_pct=holder_growth,
        liquidity_growth_pct=liquidity_growth,
        price_change_pct=price_change,
        volume_change_ratio=volume_ratio,
        volume_to_liquidity_5m=turnover,
        buy_share_pct=buy_share,
        unique_buyer_acceleration=None,
        wash_flags=tuple(wash_flags),
        missing=tuple(missing),
    )

    # Manipulation evidence wins outright. It is a finding, not an absence, so
    # it is reported even when the required growth signals are also present --
    # a token can be both growing and manufactured.
    if wash_flags:
        return _with_verdict(assessment, FlowVerdict.SUSPICIOUS)

    # Both required signals must be present. Absent evidence never passes.
    if holder_growth is None or liquidity_growth is None:
        return assessment

    if holder_growth < limits.minimum_holder_growth_pct:
        return assessment
    if liquidity_growth < -limits.maximum_liquidity_decline_pct:
        return assessment
    # Known and adverse blocks; unknown does not, because the two signals above
    # already had to be present on their own.
    if buy_share is not None and buy_share < limits.minimum_buy_share_pct:
        return assessment

    return _with_verdict(assessment, FlowVerdict.ORGANIC)


def _with_verdict(assessment: FlowAssessment, verdict: FlowVerdict) -> FlowAssessment:
    return replace(assessment, verdict=verdict)


def observations_from_events(events: list[dict[str, Any]]) -> dict[str, list[FlowObservation]]:
    """Group journalled ``candidate_observed`` events into per-mint histories.

    Events arrive oldest first from ``FlightRecorder.events_by_type``; that
    order is preserved because every growth figure depends on it.
    """
    histories: dict[str, list[FlowObservation]] = {}
    for event in events:
        mint = str(event.get("entity_id") or "")
        if not mint:
            continue
        histories.setdefault(mint, []).append(FlowObservation.from_payload(event["payload"]))
    return histories
