"""Score a candidate against the mandate's confluence signals.

The operating mandate requires a minimum number of aligned signals before entry:
four at Level 1, three at Level 2, two at Level 3. That only means something if
"aligned" is computed rather than judged, and if a signal nobody could evaluate
is not quietly counted as agreement.

So every signal resolves to exactly one of three states:

    PRESENT   the evidence exists and supports entry
    ABSENT    the evidence exists and does not support entry
    UNKNOWN   the evidence was not available

**`UNKNOWN` never counts toward the confluence total.** This is the whole reason
the module exists. A missing value serialises as `None`, never `0`, and reading
"no data" as "no problem" is the error this project has committed three times --
a timeframe sweep reporting missing candles as "no qualifying setup", a flow
study showing zero suspicious tokens when the inputs were never journalled, and
an entry study reporting EV of +130,273/dollar off a $0.00-liquidity token. A
confluence count that includes unknowns is the same mistake wearing a threshold.

**Chart signals are expected to be UNKNOWN on this population, by design.** The
scout hunts tokens under 48 hours old, and 13 of 21 sampled tokens had fewer than
30 traded candles -- there is no consolidation range, no volume baseline and no
VWAP to reclaim on a token minutes old. Those signals are still defined here,
because the mandate defines them and no parameter is being deleted, but they will
report UNKNOWN and contribute nothing until the token is old enough to chart.
That is the honest reading, not a gap to paper over.

No composite score. `ConfluenceResult` reports the per-signal breakdown alongside
the count, because a single number hides which term did the work and which was
noise -- and this project's record is that the hidden term is usually the one
that turns out to be wrong.

Pure. No network, no I/O, no clock. The caller fetches evidence and this assesses
it, so it tests without a network and cannot spend API budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SignalState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class Signal(StrEnum):
    """Every signal named in the mandate, grouped by whether it needs a chart.

    Kept exhaustive on purpose: the operator's instruction was explicitly not to
    delete any parameter, so a signal that cannot be computed on this population
    is present here and reports UNKNOWN rather than being silently dropped.
    """

    # Evidence available on a newborn token.
    LIQUIDITY_SUFFICIENT = "liquidity_sufficient"
    SPREAD_ACCEPTABLE = "spread_acceptable"
    VOLUME_ABOVE_FLOOR = "volume_above_floor"
    CONCENTRATION_ACCEPTABLE = "concentration_acceptable"
    DEVELOPER_NOT_DISTRIBUTING = "developer_not_distributing"
    BONDING_CURVE_ACCELERATING = "bonding_curve_accelerating"
    NARRATIVE_ACCELERATING = "narrative_accelerating"
    BUY_SIDE_IMBALANCE = "buy_side_imbalance"
    WHALE_ACCUMULATION = "whale_accumulation"

    # Evidence that requires candle history. Expected UNKNOWN under 48 hours.
    VOLUME_EXPANSION = "volume_expansion"
    CONSOLIDATION_BREAKOUT = "consolidation_breakout"
    BREAKOUT_RETEST_HELD = "breakout_retest_held"
    HIGHER_LOW = "higher_low"
    VWAP_RECLAIM = "vwap_reclaim"


CHART_SIGNALS = frozenset(
    {
        Signal.VOLUME_EXPANSION,
        Signal.CONSOLIDATION_BREAKOUT,
        Signal.BREAKOUT_RETEST_HELD,
        Signal.HIGHER_LOW,
        Signal.VWAP_RECLAIM,
    }
)


@dataclass(frozen=True)
class SignalVerdict:
    """One signal's state, with the value that decided it.

    `observed` is carried so a rejection is attributable after the fact. A gate
    that records only its verdict and throws away the evidence cannot be
    re-examined later, which is exactly why the concentration study could not be
    backfilled and had to start its sample from scratch.
    """

    signal: Signal
    state: SignalState
    observed: float | None = None
    threshold: float | None = None
    detail: str | None = None

    @property
    def counts(self) -> bool:
        return self.state is SignalState.PRESENT


@dataclass(frozen=True)
class ConfluenceResult:
    verdicts: tuple[SignalVerdict, ...] = field(default_factory=tuple)

    @property
    def present_count(self) -> int:
        """Signals supporting entry. The only number a threshold may read."""
        return sum(1 for verdict in self.verdicts if verdict.state is SignalState.PRESENT)

    @property
    def absent_count(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.state is SignalState.ABSENT)

    @property
    def unknown_count(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.state is SignalState.UNKNOWN)

    @property
    def evaluated_count(self) -> int:
        """Signals that could be assessed at all. Present + absent, never unknown."""
        return self.present_count + self.absent_count

    def reconciles(self) -> bool:
        """Every verdict lands in exactly one bucket and they sum to the total."""
        return (
            self.present_count + self.absent_count + self.unknown_count == len(self.verdicts)
        )

    def by_state(self, state: SignalState) -> tuple[Signal, ...]:
        return tuple(v.signal for v in self.verdicts if v.state is state)

    def meets(self, minimum_signals: int) -> bool:
        """Whether the mandate's confluence minimum is satisfied.

        Reads `present_count` only. An unknown signal cannot help a candidate
        clear this bar, which is what makes the gate fail closed.
        """
        return self.present_count >= minimum_signals


def _minimum(
    signal: Signal, value: float | None, floor: float, *, detail: str | None = None
) -> SignalVerdict:
    """PRESENT when `value` is at or above `floor`; UNKNOWN when value is None."""
    if value is None:
        return SignalVerdict(signal, SignalState.UNKNOWN, None, floor, detail or "no value")
    state = SignalState.PRESENT if value >= floor else SignalState.ABSENT
    return SignalVerdict(signal, state, value, floor, detail)


def _maximum(
    signal: Signal, value: float | None, ceiling: float, *, detail: str | None = None
) -> SignalVerdict:
    """PRESENT when `value` is at or below `ceiling`; UNKNOWN when value is None."""
    if value is None:
        return SignalVerdict(signal, SignalState.UNKNOWN, None, ceiling, detail or "no value")
    state = SignalState.PRESENT if value <= ceiling else SignalState.ABSENT
    return SignalVerdict(signal, state, value, ceiling, detail)


@dataclass(frozen=True)
class ConfluenceEvidence:
    """What a caller managed to fetch. Every field defaults to None.

    Defaulting to None rather than to a neutral number is deliberate: a caller
    that forgets to supply a field gets UNKNOWN, which cannot pass a gate. The
    failure mode of forgetfulness is therefore a rejected candidate, never an
    accepted one.
    """

    liquidity_usd: float | None = None
    spread_pct: float | None = None
    volume_usd: float | None = None
    adjusted_top_holder_pct: float | None = None
    developer_sold_pct: float | None = None
    # Percentage points of bonding-curve progress per minute, from successive
    # observations. Never measured against outcomes yet -- see
    # scripts/study_bonding_acceleration.py.
    bonding_progress_per_minute: float | None = None
    topic_net_inflow_1h_usd: float | None = None
    # Share of transactions that are buys. Note this is transactions, not unique
    # buyers: one wallet makes a hundred, and `unique_buyer_acceleration` is
    # permanently None here because the data cannot supply it.
    buy_transaction_share_pct: float | None = None
    qualified_whale_holders: float | None = None

    # Chart evidence. Expected None on a token under 48 hours old.
    volume_ratio_to_baseline: float | None = None
    consolidation_breakout: bool | None = None
    retest_held: bool | None = None
    higher_low_formed: bool | None = None
    vwap_reclaimed: bool | None = None


def _boolean(signal: Signal, value: bool | None) -> SignalVerdict:
    if value is None:
        return SignalVerdict(signal, SignalState.UNKNOWN, None, None, "no candle history")
    return SignalVerdict(
        signal,
        SignalState.PRESENT if value else SignalState.ABSENT,
        1.0 if value else 0.0,
    )


def score(
    evidence: ConfluenceEvidence,
    *,
    minimum_liquidity_usd: float,
    maximum_spread_pct: float,
    minimum_volume_usd: float,
    maximum_concentration_pct: float,
    maximum_developer_sold_pct: float,
    minimum_bonding_progress_per_minute: float,
    minimum_topic_inflow_usd: float,
    minimum_buy_share_pct: float,
    minimum_whale_holders: float,
    minimum_volume_expansion: float,
) -> ConfluenceResult:
    """Assess every mandate signal against the active level's thresholds.

    Ordering is stable so journalled results are comparable across runs.
    """
    verdicts = [
        _minimum(Signal.LIQUIDITY_SUFFICIENT, evidence.liquidity_usd, minimum_liquidity_usd),
        _maximum(Signal.SPREAD_ACCEPTABLE, evidence.spread_pct, maximum_spread_pct),
        _minimum(Signal.VOLUME_ABOVE_FLOOR, evidence.volume_usd, minimum_volume_usd),
        _maximum(
            Signal.CONCENTRATION_ACCEPTABLE,
            evidence.adjusted_top_holder_pct,
            maximum_concentration_pct,
        ),
        # A developer who has fully exited holds no supply left to dump, which
        # measured as the *safest* state across 871 mints: 7% death against 28%
        # for developers who had sold nothing. So this is a ceiling on the middle
        # band rather than a preference for zero selling.
        _maximum(
            Signal.DEVELOPER_NOT_DISTRIBUTING,
            evidence.developer_sold_pct,
            maximum_developer_sold_pct,
            detail="band gate: full exit is safer than no exit",
        ),
        _minimum(
            Signal.BONDING_CURVE_ACCELERATING,
            evidence.bonding_progress_per_minute,
            minimum_bonding_progress_per_minute,
            detail="unproven: no outcome study yet",
        ),
        _minimum(
            Signal.NARRATIVE_ACCELERATING,
            evidence.topic_net_inflow_1h_usd,
            minimum_topic_inflow_usd,
            detail="unproven: no outcome study yet",
        ),
        _minimum(
            Signal.BUY_SIDE_IMBALANCE,
            evidence.buy_transaction_share_pct,
            minimum_buy_share_pct,
            detail="transaction share, not unique buyers",
        ),
        _minimum(
            Signal.WHALE_ACCUMULATION,
            evidence.qualified_whale_holders,
            minimum_whale_holders,
            detail="wallet coverage measured at 0.17-12% of transactions",
        ),
        _minimum(
            Signal.VOLUME_EXPANSION,
            evidence.volume_ratio_to_baseline,
            minimum_volume_expansion,
        ),
        _boolean(Signal.CONSOLIDATION_BREAKOUT, evidence.consolidation_breakout),
        _boolean(Signal.BREAKOUT_RETEST_HELD, evidence.retest_held),
        _boolean(Signal.HIGHER_LOW, evidence.higher_low_formed),
        _boolean(Signal.VWAP_RECLAIM, evidence.vwap_reclaimed),
    ]
    return ConfluenceResult(tuple(verdicts))
