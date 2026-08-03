"""Tests for the fixed-plus-proportional cost model.

The point of this model is that fixed costs stop mattering as positions grow and
dominate as they shrink. The tests pin that behaviour, because a flat-percentage
model gets every one of these cases wrong in the same direction: it makes tiny
positions look efficient.
"""

from __future__ import annotations

import pytest

from meme_flight_recorder.config import CostModel
from meme_flight_recorder.costs import (
    minimum_viable_position_usd,
    round_trip_cost,
)

# Fixed cost per leg at these defaults: (0.000005 + 0.0001) * 150 = $0.01575.
MODEL = CostModel()


def test_fixed_cost_is_charged_on_both_legs() -> None:
    breakdown = round_trip_cost(10.0, MODEL)
    assert breakdown.fixed_usd == pytest.approx(2 * MODEL.fixed_cost_per_leg_usd)


def test_proportional_cost_scales_with_position() -> None:
    small = round_trip_cost(1.0, MODEL)
    large = round_trip_cost(100.0, MODEL)
    assert large.proportional_usd == pytest.approx(100 * small.proportional_usd)


def test_fixed_cost_dominates_below_a_dollar() -> None:
    """The behaviour a flat percentage cannot express."""
    breakdown = round_trip_cost(0.25, MODEL)
    assert breakdown.fixed_usd > breakdown.proportional_usd
    assert breakdown.pct_of_position > 12.0


def test_fixed_cost_is_negligible_on_a_large_position() -> None:
    """At size the cost converges on the proportional terms alone.

    Those are 2 x 0.25% router fee + 0.5% impact each way = 1.5%.
    """
    breakdown = round_trip_cost(1_000.0, MODEL)
    assert breakdown.fixed_usd < breakdown.proportional_usd
    assert breakdown.pct_of_position == pytest.approx(1.5, abs=0.05)


def test_cost_share_falls_monotonically_with_size() -> None:
    shares = [round_trip_cost(size, MODEL).pct_of_position for size in (0.5, 1, 2, 4, 10, 100)]
    assert shares == sorted(shares, reverse=True)


def test_zero_position_raises_rather_than_returning_zero_cost() -> None:
    """Returning zero would let a sweep conclude the best trade is infinitely small."""
    with pytest.raises(ValueError):
        round_trip_cost(0.0, MODEL)
    with pytest.raises(ValueError):
        round_trip_cost(-1.0, MODEL)


def test_quoted_impact_overrides_the_assumption() -> None:
    assumed = round_trip_cost(10.0, MODEL)
    quoted = round_trip_cost(10.0, MODEL, entry_impact_pct=0.0, exit_impact_pct=0.0)
    assert quoted.proportional_usd < assumed.proportional_usd


def test_breakeven_multiple_exceeds_one() -> None:
    breakdown = round_trip_cost(4.0, MODEL)
    assert breakdown.breakeven_multiple > 1.0
    expected = (4.0 + breakdown.total_usd) / 4.0
    assert breakdown.breakeven_multiple == pytest.approx(expected)


def test_minimum_viable_position_satisfies_its_own_constraint() -> None:
    floor = minimum_viable_position_usd(MODEL, maximum_cost_share_pct=10.0)
    assert floor is not None
    assert round_trip_cost(floor, MODEL).pct_of_position == pytest.approx(10.0, abs=1e-3)


def test_positions_below_the_floor_breach_the_constraint() -> None:
    floor = minimum_viable_position_usd(MODEL, maximum_cost_share_pct=10.0)
    assert floor is not None
    assert round_trip_cost(floor * 0.5, MODEL).pct_of_position > 10.0


def test_a_stricter_target_raises_the_floor() -> None:
    lenient = minimum_viable_position_usd(MODEL, maximum_cost_share_pct=10.0)
    strict = minimum_viable_position_usd(MODEL, maximum_cost_share_pct=5.0)
    assert lenient is not None and strict is not None
    assert strict > lenient


def test_impossible_target_returns_none_not_zero() -> None:
    """When percentage costs alone exceed the target, no size works.

    None must not be read as "no minimum". Treating it as zero would size into a
    pool that cannot be traded profitably at any stake.
    """
    assert minimum_viable_position_usd(MODEL, maximum_cost_share_pct=1.0) is None
    expensive = CostModel(assumed_impact_pct=8.0)
    assert minimum_viable_position_usd(expensive, maximum_cost_share_pct=10.0) is None


def test_priority_fee_raises_the_floor_sharply() -> None:
    """Competitive sniping bids priority fees up, which prices out small orders.

    This is the mechanism behind the claim that a $10-40 account cannot win a
    launch race: the fee that buys speed is fixed per transaction, so it is paid
    in full whether the position is $4 or $4,000.
    """
    patient = minimum_viable_position_usd(CostModel(priority_fee_sol=0.0001))
    competitive = minimum_viable_position_usd(CostModel(priority_fee_sol=0.05))
    assert patient is not None and competitive is not None
    assert competitive > 100 * patient


def test_sol_price_scales_fixed_costs() -> None:
    cheap = round_trip_cost(1.0, CostModel(sol_price_usd=100.0))
    dear = round_trip_cost(1.0, CostModel(sol_price_usd=300.0))
    assert dear.fixed_usd == pytest.approx(3 * cheap.fixed_usd)
