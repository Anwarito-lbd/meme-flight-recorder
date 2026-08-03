"""When to leave a position.

Entry is a decision made once, deliberately, with full attention. Exit is a
decision that has to survive being made badly: at four in the morning, on
partial data, while the position is moving against you and every instinct argues
for waiting one more candle. So every rule here is absolute and pre-committed,
and the engine returns a decision rather than a suggestion.

The hierarchy is ordered by what the trader can least afford to get wrong, and
the first match wins:

1. **Security** -- the pool is being dismantled. Nothing else matters, because
   the ability to sell at all is disappearing.
2. **Thesis** -- the structure that justified the entry has broken.
3. **Liquidity** -- exiting has itself become expensive.
4. **Flow** -- demand has turned over.
5. **Time** -- the move never came. A position held past its thesis is a
   different, unexamined trade.
6. **Profit** -- scale against the risk that was taken, not against hope.

Two decisions in here are deliberately counter-intuitive.

**A stale position is closed, not held.** Everywhere else in this system,
missing evidence prevents a new position. For money already at risk the rule
inverts: an asset that can no longer be observed is not a position, it is a
hope. Waiting for the data to come back is how a manageable loss becomes a total
one.

**Trailing exits fill at observed depth, never at the trigger price.** In a thin
pool the difference between those two numbers is the entire result, and a
backtest that assumes the trigger price will report profits that could never
have been collected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from .config import ExitLimits


class ExitReason(StrEnum):
    SECURITY_AUTHORITY = "security_authority_changed"
    SECURITY_DEVELOPER = "security_developer_dumping"
    SECURITY_LIQUIDITY_PULL = "security_liquidity_withdrawn"
    THESIS_BROKEN = "thesis_structure_broken"
    STOP_HIT = "stop_price_hit"
    LIQUIDITY_THIN = "liquidity_below_backstop"
    LIQUIDITY_IMPACT = "exit_impact_excessive"
    FLOW_REVERSAL = "flow_sellers_dominant"
    TIME_LIMIT = "time_limit_reached"
    TIME_NO_PROGRESS = "no_progress_in_window"
    PROFIT_FIRST_SCALE = "profit_scale_first"
    PROFIT_SECOND_SCALE = "profit_scale_second"
    PROFIT_TRAIL = "profit_trail_stop"
    STALE_UNOBSERVABLE = "position_unobservable"


# Reasons that require leaving immediately and completely, accepting whatever
# depth exists. Waiting for a better price in these states is how a partial loss
# becomes a total one.
URGENT_REASONS: frozenset[ExitReason] = frozenset(
    {
        ExitReason.SECURITY_AUTHORITY,
        ExitReason.SECURITY_DEVELOPER,
        ExitReason.SECURITY_LIQUIDITY_PULL,
        ExitReason.STALE_UNOBSERVABLE,
    }
)


@dataclass(frozen=True)
class PositionView:
    """The parts of an open position the exit engine needs."""

    mint: str
    opened_at: datetime
    entry_price: float
    stop_price: float
    breakout_level: float
    entry_liquidity_usd: float
    atr: float = 0.0
    high_water_price: float = 0.0
    scaled_out_fraction: float = 0.0
    peak_liquidity_usd: float = 0.0

    @property
    def risk_per_unit(self) -> float:
        """Distance from entry to stop: one unit of R."""
        return max(self.entry_price - self.stop_price, 0.0)


@dataclass(frozen=True)
class PositionObservation:
    """A point-in-time read of a held position. Absent fields are None."""

    observed_at: datetime
    price_usd: float | None = None
    liquidity_usd: float | None = None
    exit_impact_pct: float | None = None
    authorities_changed: bool | None = None
    developer_selling: bool | None = None
    buy_share_pct: float | None = None
    holder_growth_pct: float | None = None
    consecutive_failed_checks: int = 0


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: ExitReason | None = None
    fraction: float = 0.0
    urgent: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_partial(self) -> bool:
        return self.should_exit and 0.0 < self.fraction < 1.0


def _hold(*notes: str) -> ExitDecision:
    return ExitDecision(False, None, 0.0, False, notes)


def _exit(reason: ExitReason, fraction: float = 1.0, *notes: str) -> ExitDecision:
    return ExitDecision(True, reason, fraction, reason in URGENT_REASONS, notes)


def evaluate_exit(
    position: PositionView,
    observation: PositionObservation,
    limits: ExitLimits,
) -> ExitDecision:
    """Return the first exit condition that fires, in priority order."""

    # 0. Unobservable. Checked before anything else, because every rule below
    #    depends on data this position no longer has.
    if observation.consecutive_failed_checks >= limits.stale_after_failed_checks:
        return _exit(
            ExitReason.STALE_UNOBSERVABLE,
            1.0,
            f"failed_checks={observation.consecutive_failed_checks}",
        )

    # 1. Security. The pool is being dismantled.
    if observation.authorities_changed is True:
        return _exit(ExitReason.SECURITY_AUTHORITY)
    if observation.developer_selling is True:
        return _exit(ExitReason.SECURITY_DEVELOPER)

    liquidity = observation.liquidity_usd
    reference = max(position.peak_liquidity_usd, position.entry_liquidity_usd)
    if liquidity is not None and reference > 0:
        drop_pct = 100.0 * (reference - liquidity) / reference
        if drop_pct >= limits.liquidity_drop_pct:
            return _exit(
                ExitReason.SECURITY_LIQUIDITY_PULL,
                1.0,
                f"liquidity_down_{drop_pct:.1f}pct",
            )

    price = observation.price_usd

    # 2. Thesis. The level that justified the entry no longer holds.
    if price is not None:
        if position.stop_price > 0 and price <= position.stop_price:
            return _exit(ExitReason.STOP_HIT, 1.0, f"price={price}")
        if position.breakout_level > 0 and price < position.breakout_level:
            return _exit(ExitReason.THESIS_BROKEN, 1.0, f"below_level={position.breakout_level}")

    # 3. Liquidity. Getting out has become expensive.
    if liquidity is not None and liquidity < limits.minimum_pool_liquidity_usd:
        return _exit(ExitReason.LIQUIDITY_THIN, 1.0, f"liquidity={liquidity:.0f}")
    if (
        observation.exit_impact_pct is not None
        and observation.exit_impact_pct > limits.maximum_exit_impact_pct
    ):
        return _exit(ExitReason.LIQUIDITY_IMPACT, 1.0, f"impact={observation.exit_impact_pct:.2f}")

    # 4. Flow. Demand has turned over. Requires both signals rather than either,
    #    because a single quiet interval is noise, not distribution.
    if (
        observation.buy_share_pct is not None
        and observation.buy_share_pct < limits.minimum_buy_share_pct
        and observation.holder_growth_pct is not None
        and observation.holder_growth_pct < 0
    ):
        return _exit(
            ExitReason.FLOW_REVERSAL,
            1.0,
            f"buy_share={observation.buy_share_pct:.1f}",
        )

    held = observation.observed_at - position.opened_at

    # 5. Profit, checked before the time stop so a working trade is not closed
    #    for being slow.
    if price is not None and position.risk_per_unit > 0:
        r_multiple = (price - position.entry_price) / position.risk_per_unit
        if (
            r_multiple >= limits.second_scale_r
            and position.scaled_out_fraction < limits.first_scale_fraction
            + limits.second_scale_fraction
        ):
            if position.scaled_out_fraction < limits.first_scale_fraction:
                return _exit(
                    ExitReason.PROFIT_SECOND_SCALE,
                    limits.first_scale_fraction + limits.second_scale_fraction,
                    f"r={r_multiple:.2f}",
                )
            return _exit(
                ExitReason.PROFIT_SECOND_SCALE, limits.second_scale_fraction, f"r={r_multiple:.2f}"
            )
        if r_multiple >= limits.first_scale_r and position.scaled_out_fraction <= 0:
            return _exit(
                ExitReason.PROFIT_FIRST_SCALE, limits.first_scale_fraction, f"r={r_multiple:.2f}"
            )

        # Trail only what is left after scaling, and only once in profit.
        if position.scaled_out_fraction > 0 and position.high_water_price > 0 and position.atr > 0:
            trail = position.high_water_price - limits.trail_atr_multiple * position.atr
            if price <= trail and trail > position.entry_price:
                return _exit(ExitReason.PROFIT_TRAIL, 1.0, f"trail={trail:.10f}")

    # 6. Time. The move never came.
    if held >= timedelta(minutes=limits.maximum_hold_minutes):
        return _exit(ExitReason.TIME_LIMIT, 1.0, f"held_minutes={held.total_seconds() / 60:.0f}")
    if held >= timedelta(minutes=limits.no_progress_minutes) and price is not None:
        move_pct = 100.0 * (price - position.entry_price) / position.entry_price
        if abs(move_pct) < limits.no_progress_threshold_pct:
            return _exit(ExitReason.TIME_NO_PROGRESS, 1.0, f"move={move_pct:.2f}pct")

    return _hold()


def realistic_fill_price(
    quoted_price: float,
    exit_impact_pct: float | None,
    urgent: bool,
    urgency_penalty_pct: float = 2.0,
) -> float:
    """Model what an exit would actually fill at, never the quoted price.

    Quoted price is what the pool shows before the order moves it. Subtracting
    measured impact is the minimum honest adjustment. An urgent exit pays more
    still, because it is placed into the same moment everyone else is leaving.
    """
    impact = max(exit_impact_pct or 0.0, 0.0)
    if urgent:
        impact += urgency_penalty_pct
    return max(quoted_price * (1 - impact / 100.0), 0.0)
