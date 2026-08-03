from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from meme_flight_recorder.config import ExitLimits
from meme_flight_recorder.exits import (
    ExitReason,
    PositionObservation,
    PositionView,
    evaluate_exit,
    realistic_fill_price,
)

OPENED = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
LIMITS = ExitLimits()

POSITION = PositionView(
    mint="Mint1",
    opened_at=OPENED,
    entry_price=1.00,
    stop_price=0.90,
    breakout_level=0.95,
    entry_liquidity_usd=500_000.0,
    atr=0.05,
    high_water_price=1.00,
    peak_liquidity_usd=500_000.0,
)


def obs(minutes: float = 1.0, **kwargs) -> PositionObservation:
    defaults = {
        "price_usd": 1.00,
        "liquidity_usd": 500_000.0,
        "exit_impact_pct": 0.5,
        "authorities_changed": False,
        "developer_selling": False,
        "buy_share_pct": 55.0,
        "holder_growth_pct": 5.0,
    }
    defaults.update(kwargs)
    return PositionObservation(observed_at=OPENED + timedelta(minutes=minutes), **defaults)


class HoldTests(unittest.TestCase):
    def test_a_healthy_position_is_held(self):
        decision = evaluate_exit(POSITION, obs(), LIMITS)
        self.assertFalse(decision.should_exit)


class StaleTests(unittest.TestCase):
    def test_unobservable_position_is_closed_not_held(self):
        """Missing evidence blocks new risk; it also retires existing risk."""
        decision = evaluate_exit(POSITION, obs(consecutive_failed_checks=3), LIMITS)
        self.assertEqual(decision.reason, ExitReason.STALE_UNOBSERVABLE)
        self.assertTrue(decision.urgent)
        self.assertEqual(decision.fraction, 1.0)

    def test_stale_check_precedes_every_other_rule(self):
        """Rules below depend on data a stale position no longer has."""
        decision = evaluate_exit(
            POSITION,
            obs(consecutive_failed_checks=5, price_usd=None, liquidity_usd=None),
            LIMITS,
        )
        self.assertEqual(decision.reason, ExitReason.STALE_UNOBSERVABLE)

    def test_one_missed_check_is_not_stale(self):
        self.assertFalse(evaluate_exit(POSITION, obs(consecutive_failed_checks=1), LIMITS).should_exit)


class SecurityTests(unittest.TestCase):
    def test_authority_change_exits_urgently(self):
        decision = evaluate_exit(POSITION, obs(authorities_changed=True), LIMITS)
        self.assertEqual(decision.reason, ExitReason.SECURITY_AUTHORITY)
        self.assertTrue(decision.urgent)

    def test_developer_selling_exits_urgently(self):
        decision = evaluate_exit(POSITION, obs(developer_selling=True), LIMITS)
        self.assertEqual(decision.reason, ExitReason.SECURITY_DEVELOPER)

    def test_liquidity_withdrawal_exits(self):
        decision = evaluate_exit(POSITION, obs(liquidity_usd=400_000.0), LIMITS)
        self.assertEqual(decision.reason, ExitReason.SECURITY_LIQUIDITY_PULL)

    def test_security_outranks_profit(self):
        """A pool being drained beats a position being up."""
        decision = evaluate_exit(
            POSITION, obs(price_usd=1.30, liquidity_usd=300_000.0), LIMITS
        )
        self.assertEqual(decision.reason, ExitReason.SECURITY_LIQUIDITY_PULL)

    def test_drop_is_measured_from_peak_not_entry(self):
        """A pool that grew then collapsed to entry level is still a collapse."""
        grown = replace(POSITION, peak_liquidity_usd=1_000_000.0)
        decision = evaluate_exit(grown, obs(liquidity_usd=500_000.0), LIMITS)
        self.assertEqual(decision.reason, ExitReason.SECURITY_LIQUIDITY_PULL)


class ThesisTests(unittest.TestCase):
    def test_stop_hit_exits(self):
        decision = evaluate_exit(POSITION, obs(price_usd=0.89), LIMITS)
        self.assertEqual(decision.reason, ExitReason.STOP_HIT)

    def test_losing_the_breakout_level_exits_before_the_stop(self):
        decision = evaluate_exit(POSITION, obs(price_usd=0.93), LIMITS)
        self.assertEqual(decision.reason, ExitReason.THESIS_BROKEN)


class LiquidityTests(unittest.TestCase):
    def test_pool_below_backstop_exits(self):
        thin = replace(POSITION, entry_liquidity_usd=4_000.0, peak_liquidity_usd=4_000.0)
        decision = evaluate_exit(thin, obs(liquidity_usd=4_000.0), LIMITS)
        self.assertEqual(decision.reason, ExitReason.LIQUIDITY_THIN)

    def test_expensive_exit_impact_exits(self):
        decision = evaluate_exit(POSITION, obs(exit_impact_pct=9.0), LIMITS)
        self.assertEqual(decision.reason, ExitReason.LIQUIDITY_IMPACT)


class FlowTests(unittest.TestCase):
    def test_sellers_dominant_with_shrinking_holders_exits(self):
        decision = evaluate_exit(
            POSITION, obs(buy_share_pct=30.0, holder_growth_pct=-4.0), LIMITS
        )
        self.assertEqual(decision.reason, ExitReason.FLOW_REVERSAL)

    def test_one_quiet_signal_alone_is_noise_not_distribution(self):
        self.assertFalse(
            evaluate_exit(POSITION, obs(buy_share_pct=30.0, holder_growth_pct=3.0), LIMITS).should_exit
        )


class ProfitTests(unittest.TestCase):
    def test_first_scale_is_partial(self):
        decision = evaluate_exit(POSITION, obs(price_usd=1.10), LIMITS)
        self.assertEqual(decision.reason, ExitReason.PROFIT_FIRST_SCALE)
        self.assertTrue(decision.is_partial)
        self.assertAlmostEqual(decision.fraction, 0.34)

    def test_second_scale_after_first(self):
        scaled = replace(POSITION, scaled_out_fraction=0.34)
        decision = evaluate_exit(scaled, obs(price_usd=1.20), LIMITS)
        self.assertEqual(decision.reason, ExitReason.PROFIT_SECOND_SCALE)

    def test_profit_is_checked_before_the_time_stop(self):
        """A working trade must not be closed merely for being slow."""
        decision = evaluate_exit(POSITION, obs(minutes=300, price_usd=1.15), LIMITS)
        self.assertEqual(decision.reason, ExitReason.PROFIT_FIRST_SCALE)

    def test_trailing_stop_governs_the_remainder_after_both_scales(self):
        trailing = replace(POSITION, scaled_out_fraction=0.67, high_water_price=1.30)
        decision = evaluate_exit(trailing, obs(price_usd=1.20), LIMITS)
        self.assertEqual(decision.reason, ExitReason.PROFIT_TRAIL)

    def test_scaling_outranks_the_trail_while_scales_remain(self):
        """At 2R with a scale still owed, take the scale rather than trail."""
        trailing = replace(POSITION, scaled_out_fraction=0.34, high_water_price=1.30)
        decision = evaluate_exit(trailing, obs(price_usd=1.20), LIMITS)
        self.assertEqual(decision.reason, ExitReason.PROFIT_SECOND_SCALE)

    def test_trail_never_fires_below_entry(self):
        """A 'trailing profit' exit under water is a loss wearing a nice name."""
        trailing = replace(POSITION, scaled_out_fraction=0.67, high_water_price=1.02)
        decision = evaluate_exit(trailing, obs(price_usd=0.98), LIMITS)
        self.assertNotEqual(decision.reason, ExitReason.PROFIT_TRAIL)


class TimeTests(unittest.TestCase):
    def test_maximum_hold_exits(self):
        decision = evaluate_exit(POSITION, obs(minutes=1_441), LIMITS)
        self.assertEqual(decision.reason, ExitReason.TIME_LIMIT)

    def test_flat_position_exits_on_no_progress(self):
        decision = evaluate_exit(POSITION, obs(minutes=250, price_usd=1.005), LIMITS)
        self.assertEqual(decision.reason, ExitReason.TIME_NO_PROGRESS)

    def test_a_moving_position_survives_the_no_progress_window(self):
        self.assertFalse(evaluate_exit(POSITION, obs(minutes=250, price_usd=1.04), LIMITS).should_exit)


class FillPriceTests(unittest.TestCase):
    def test_fill_is_worse_than_the_quote(self):
        self.assertLess(realistic_fill_price(1.0, 3.0, urgent=False), 1.0)

    def test_urgent_exit_pays_more(self):
        calm = realistic_fill_price(1.0, 3.0, urgent=False)
        panic = realistic_fill_price(1.0, 3.0, urgent=True)
        self.assertLess(panic, calm)

    def test_fill_never_goes_negative(self):
        self.assertEqual(realistic_fill_price(1.0, 500.0, urgent=True), 0.0)


class CollapseRegressionTests(unittest.TestCase):
    """Replays the real CATE collapse: $519,203 of liquidity to $1,587."""

    def test_the_cate_collapse_would_have_been_exited(self):
        position = PositionView(
            mint="9SNEJJGhpVVSmj8vJp2pxdn5prUtoJ7iZetbisNZpump",
            opened_at=OPENED,
            entry_price=0.05268,
            stop_price=0.047,
            breakout_level=0.050,
            entry_liquidity_usd=519_203.0,
            peak_liquidity_usd=519_203.0,
        )
        # First observation showing the pool draining, well before price died.
        decision = evaluate_exit(
            position,
            PositionObservation(
                observed_at=OPENED + timedelta(minutes=30),
                price_usd=0.0500,
                liquidity_usd=430_000.0,
            ),
            LIMITS,
        )
        self.assertTrue(decision.should_exit)
        self.assertEqual(decision.reason, ExitReason.SECURITY_LIQUIDITY_PULL)
        self.assertTrue(decision.urgent)


if __name__ == "__main__":
    unittest.main()
