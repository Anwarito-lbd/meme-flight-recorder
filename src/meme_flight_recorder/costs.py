"""What a trade costs, and the smallest position worth opening.

Every backtest in this project charged a flat 3% per leg. The figure was never
measured, and a flat percentage cannot answer the question that now matters.

The measured return distribution is a lottery: median 0.255, positive expectancy
carried by a handful of very large winners. Surviving that requires a *smaller*
position than the configured 10% of equity, because a fraction optimised for
arithmetic expectancy produces negative geometric growth. But positions cannot
shrink indefinitely: network fees are fixed per transaction and do not shrink
with the order, so below some size the fee is a material share of the stake.

That tension is the entire question. A flat percentage hides it -- under a 3%
model a $0.10 position costs $0.006 and looks perfectly efficient, which is
nonsense, because the signature fee alone exceeds it. Splitting fixed from
proportional is what makes the floor visible.

Pure and closed-form. No network, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import CostModel


@dataclass(frozen=True)
class CostBreakdown:
    """The cost of one round trip, with the two components kept apart."""

    position_usd: float
    fixed_usd: float
    proportional_usd: float

    @property
    def total_usd(self) -> float:
        return round(self.fixed_usd + self.proportional_usd, 9)

    @property
    def pct_of_position(self) -> float:
        """Round-trip cost as a share of the position.

        This is the number a trade must clear before it makes anything, so it is
        the honest headline: a position paying 20% round trip needs a 25% move
        just to return the stake.
        """
        if self.position_usd <= 0:
            return 0.0
        return round(100.0 * self.total_usd / self.position_usd, 6)

    @property
    def breakeven_multiple(self) -> float:
        """The price multiple at which this trade returns exactly the stake."""
        if self.position_usd <= 0:
            return 0.0
        return round((self.position_usd + self.total_usd) / self.position_usd, 6)


def round_trip_cost(
    position_usd: float,
    model: CostModel,
    *,
    entry_impact_pct: float | None = None,
    exit_impact_pct: float | None = None,
) -> CostBreakdown:
    """Cost of entering and leaving a position of ``position_usd``.

    Impact percentages should come from live router quotes where available;
    ``None`` falls back to the model's assumption, which is a guess and is
    labelled as one. Both legs pay a fixed network cost, so the fixed term is
    charged twice.
    """
    if position_usd <= 0:
        # Returning zero here would let a sweep walk position size toward zero
        # and report ever-improving efficiency, which is how a model concludes
        # that the best trade is an infinitely small one.
        raise ValueError("position_usd must be positive")

    entry_impact = model.assumed_impact_pct if entry_impact_pct is None else entry_impact_pct
    exit_impact = model.assumed_impact_pct if exit_impact_pct is None else exit_impact_pct

    fixed = 2.0 * model.fixed_cost_per_leg_usd
    proportional_pct = 2.0 * model.dex_fee_pct + entry_impact + exit_impact
    proportional = position_usd * proportional_pct / 100.0

    return CostBreakdown(
        position_usd=position_usd,
        fixed_usd=round(fixed, 9),
        proportional_usd=round(proportional, 9),
    )


def minimum_viable_position_usd(
    model: CostModel,
    *,
    maximum_cost_share_pct: float = 10.0,
    entry_impact_pct: float | None = None,
    exit_impact_pct: float | None = None,
) -> float | None:
    """Smallest position whose round-trip cost stays within a share of itself.

    Closed form. Cost as a share of position is ``fixed / p + proportional_pct``,
    which falls monotonically with ``p``, so the constraint
    ``fixed / p + prop <= target`` solves directly to
    ``p >= fixed / (target - prop)``.

    Returns ``None`` when the proportional cost alone already exceeds the target.
    That is not an edge case to paper over: it means no position of any size
    satisfies the constraint, because the percentage terms do not shrink. A
    caller that treats None as zero would size into a pool that cannot be traded
    profitably at any stake.
    """
    entry_impact = model.assumed_impact_pct if entry_impact_pct is None else entry_impact_pct
    exit_impact = model.assumed_impact_pct if exit_impact_pct is None else exit_impact_pct

    proportional_pct = 2.0 * model.dex_fee_pct + entry_impact + exit_impact
    headroom_pct = maximum_cost_share_pct - proportional_pct
    if headroom_pct <= 0:
        return None

    fixed = 2.0 * model.fixed_cost_per_leg_usd
    return round(fixed / (headroom_pct / 100.0), 6)
