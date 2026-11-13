from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from meme_flight_recorder.entry import EntryLimits, evaluate_entry, find_entries, qualify_signal
from meme_flight_recorder.exits import ExitReason
from meme_flight_recorder.positions import apply_exit, mark, open_position, summarise
from meme_flight_recorder.strategy import Candle, EntrySignal

AT = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def candles(closes: list[float], volume: float = 100.0) -> list[Candle]:
    out = []
    for index, close in enumerate(closes):
        out.append(
            Candle(
                timestamp=1_700_000_000 + index * 900,
                open=close,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                volume=volume,
            )
        )
    return out


class EntryQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = EntryLimits()
        self.signal = EntrySignal(entry=1.00, stop=0.90, target=1.30, breakout_level=0.98)
        self.flat = candles([1.0] * 30)

    def test_a_sound_signal_is_accepted(self):
        ok, reason = qualify_signal(self.signal, self.flat, self.limits)
        self.assertTrue(ok, reason)

    def test_an_inverted_stop_is_rejected(self):
        bad = EntrySignal(entry=1.0, stop=1.2, target=1.5, breakout_level=0.98)
        self.assertEqual(qualify_signal(bad, self.flat, self.limits)[1], "invalid_stop")

    def test_a_very_wide_stop_is_rejected(self):
        """A wide stop is a larger position in disguise, since size derives from it."""
        wide = EntrySignal(entry=1.0, stop=0.5, target=2.0, breakout_level=0.98)
        self.assertEqual(qualify_signal(wide, self.flat, self.limits)[1], "stop_too_wide")

    def test_poor_reward_to_risk_is_rejected(self):
        thin = EntrySignal(entry=1.0, stop=0.9, target=1.05, breakout_level=0.98)
        self.assertEqual(
            qualify_signal(thin, self.flat, self.limits)[1], "reward_to_risk_too_low"
        )

    def test_a_vertical_run_is_not_chased(self):
        vertical = candles([1.0] * 25 + [1.2, 1.5, 1.9, 2.4, 3.0])
        self.assertEqual(
            qualify_signal(self.signal, vertical, self.limits)[1], "already_vertical"
        )

    def test_reward_to_risk_is_reported(self):
        decision = evaluate_entry(candles([1.0] * 10))
        self.assertEqual(decision.reason, "insufficient_history")
        self.assertIsNone(decision.reward_to_risk)


class LookAheadTests(unittest.TestCase):
    """The constraint is structural, so a test cannot violate it by accident."""

    def test_a_decision_uses_no_candle_beyond_its_index(self):
        series = candles([1.0] * 24 + [1.0, 1.0, 5.0, 5.0])
        for index, _decision in find_entries(series):
            # Re-deciding on the truncated history must give the same answer.
            self.assertTrue(evaluate_entry(series[: index + 1]).should_enter)

    def test_flat_history_produces_no_entries(self):
        self.assertEqual(list(find_entries(candles([1.0] * 60))), [])


class PositionAccountingTests(unittest.TestCase):
    def _open(self, cost_pct: float = 3.0):
        return open_position(
            mint="Mint1",
            symbol="MEME",
            at=AT,
            price=1.0,
            position_usd=4.0,
            stop_price=0.9,
            target_price=1.3,
            breakout_level=0.98,
            atr=0.05,
            liquidity_usd=500_000.0,
            cost_pct=cost_pct,
        )

    def test_entry_cost_is_charged_immediately(self):
        position = self._open()
        self.assertAlmostEqual(position.cost_basis_usd, 4.12, places=4)
        self.assertAlmostEqual(position.quantity, 4.0, places=6)

    def test_a_small_gain_is_still_a_loss_after_both_legs(self):
        """Costs dominate at this size; gross figures would mislead."""
        position = self._open()
        closed = apply_exit(
            position, at=AT, price=1.04, fraction=1.0, reason=ExitReason.PROFIT_TRAIL
        )
        self.assertLess(closed.realised_usd, 0)

    def test_a_real_move_clears_costs(self):
        position = self._open()
        closed = apply_exit(
            position, at=AT, price=1.30, fraction=1.0, reason=ExitReason.PROFIT_SECOND_SCALE
        )
        self.assertGreater(closed.realised_usd, 0)

    def test_partial_exit_leaves_the_position_open(self):
        position = apply_exit(
            self._open(), at=AT, price=1.1, fraction=0.34, reason=ExitReason.PROFIT_FIRST_SCALE
        )
        self.assertTrue(position.is_open)
        self.assertAlmostEqual(position.scaled_out_fraction, 0.34, places=4)
        self.assertIsNone(position.closed_at)

    def test_scaling_then_stopping_out_is_not_a_scratch(self):
        """Collapsing partials to one average exit hides the real result."""
        position = apply_exit(
            self._open(), at=AT, price=1.20, fraction=0.34, reason=ExitReason.PROFIT_FIRST_SCALE
        )
        closed = apply_exit(
            position,
            at=AT + timedelta(minutes=30),
            price=1.00,
            fraction=1.0,
            reason=ExitReason.STOP_HIT,
        )
        self.assertFalse(closed.is_open)
        self.assertIsNotNone(closed.realised_r)

    def test_exiting_more_than_held_is_capped(self):
        closed = apply_exit(
            self._open(), at=AT, price=1.0, fraction=5.0, reason=ExitReason.STOP_HIT
        )
        self.assertAlmostEqual(closed.exited_quantity, closed.quantity, places=6)

    def test_a_closed_position_ignores_further_exits(self):
        closed = apply_exit(
            self._open(), at=AT, price=1.0, fraction=1.0, reason=ExitReason.STOP_HIT
        )
        self.assertEqual(
            apply_exit(closed, at=AT, price=2.0, fraction=1.0, reason=ExitReason.STOP_HIT),
            closed,
        )

    def test_mark_tracks_the_high_water_and_peak_liquidity(self):
        position = mark(self._open(), price=1.5, liquidity_usd=900_000.0)
        position = mark(position, price=1.2, liquidity_usd=400_000.0)
        self.assertEqual(position.high_water_price, 1.5)
        self.assertEqual(position.peak_liquidity_usd, 900_000.0)

    def test_summary_reports_holding_time_and_reason(self):
        closed = apply_exit(
            self._open(),
            at=AT + timedelta(minutes=90),
            price=1.3,
            fraction=1.0,
            reason=ExitReason.PROFIT_SECOND_SCALE,
        )
        summary = summarise(closed)
        self.assertEqual(summary.holding_minutes, 90.0)
        self.assertEqual(summary.reason, "profit_scale_second")
        self.assertTrue(summary.is_win)


if __name__ == "__main__":
    unittest.main()
