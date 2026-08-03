from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime

from meme_flight_recorder.config import MicroCapitalLimits, RiskLimits, SafetyLimits
from meme_flight_recorder.models import (
    CandidateStatus,
    TokenIdentity,
    TokenSnapshot,
    Universe,
)
from meme_flight_recorder.risk import PortfolioState, RiskEngine
from meme_flight_recorder.safety import SafetyEngine

RISK = RiskLimits(
    risk_per_cex_trade_pct=0.5,
    capital_at_risk_per_solana_trade_pct=0.25,
    maximum_daily_loss_pct=1.0,
    maximum_open_positions=2,
    maximum_aggregate_open_risk_pct=1.0,
    consecutive_loss_limit=3,
)


class MicroCapitalSizingTests(unittest.TestCase):
    """A $10-40 account cannot use percentage-of-equity risk sizing."""

    def setUp(self) -> None:
        self.micro = MicroCapitalLimits()
        self.engine = RiskEngine(RISK, self.micro)
        self.state = PortfolioState(equity_usd=40.0)

    def _approve(self, **overrides):
        arguments = {
            "pool_liquidity_usd": 10_000.0,
            "estimated_round_trip_cost_pct": 4.0,
            **overrides,
        }
        return self.engine.approve(
            Universe.SOLANA_EMERGING, self.state, entry_price=1.0, **arguments
        )

    def test_position_equals_risk_under_catastrophic_loss_sizing(self):
        decision = self._approve()
        self.assertTrue(decision.approved, decision.reason)
        self.assertEqual(decision.position_value_usd, 10.0)
        self.assertEqual(decision.risk_amount_usd, decision.position_value_usd)

    def test_percentage_sizing_would_have_produced_an_unusable_position(self):
        """Regression guard for the reason micro mode exists."""
        legacy = RiskEngine(RISK).approve(Universe.SOLANA_EMERGING, self.state, 1.0)
        self.assertTrue(legacy.approved)
        self.assertEqual(legacy.position_value_usd, 0.1)
        self.assertGreater(self._approve().position_value_usd, legacy.position_value_usd)

    def test_micro_sizing_does_not_apply_to_larger_accounts(self):
        """Above the threshold, sizing reverts to percentage-of-equity.

        Liquidity discipline is not micro-specific and still applies, so the
        pool arguments remain required.
        """
        state = PortfolioState(equity_usd=10_000.0)
        decision = self.engine.approve(
            Universe.SOLANA_EMERGING,
            state,
            1.0,
            pool_liquidity_usd=1_000_000.0,
            estimated_round_trip_cost_pct=1.0,
        )
        self.assertTrue(decision.approved, decision.reason)
        self.assertEqual(decision.position_value_usd, 25.0)

    def test_liquidity_discipline_applies_above_the_micro_threshold_too(self):
        state = PortfolioState(equity_usd=10_000.0)
        decision = self.engine.approve(Universe.SOLANA_EMERGING, state, 1.0)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "pool_liquidity_unknown")

    def test_account_too_small_for_a_viable_position_is_rejected(self):
        engine = RiskEngine(RISK, self.micro)
        decision = engine.approve(
            Universe.SOLANA_EMERGING,
            PortfolioState(equity_usd=2.0),
            1.0,
            pool_liquidity_usd=10_000.0,
            estimated_round_trip_cost_pct=4.0,
        )
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "position_below_viable_minimum")

    def test_aggregate_open_position_cap_applies_instead_of_risk_cap(self):
        """Risk equals position here, so the 1%-of-equity cap cannot govern."""
        state = replace(self.state, aggregate_open_risk_usd=15.0)
        decision = self.engine.approve(
            Universe.SOLANA_EMERGING,
            state,
            1.0,
            pool_liquidity_usd=10_000.0,
            estimated_round_trip_cost_pct=4.0,
        )
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "aggregate_open_position_exceeded")


class PoolRelativeLiquidityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine(RISK, MicroCapitalLimits())
        self.state = PortfolioState(equity_usd=40.0)

    def _approve(self, **overrides):
        arguments = {
            "pool_liquidity_usd": 10_000.0,
            "estimated_round_trip_cost_pct": 4.0,
            **overrides,
        }
        return self.engine.approve(
            Universe.SOLANA_EMERGING, self.state, entry_price=1.0, **arguments
        )

    def test_unknown_pool_liquidity_is_rejected_not_assumed(self):
        decision = self._approve(pool_liquidity_usd=None)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "pool_liquidity_unknown")

    def test_pool_below_absolute_backstop_is_rejected(self):
        """The ratio alone would allow a $10 order into a $2,000 pool."""
        decision = self._approve(pool_liquidity_usd=2_000.0)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "pool_liquidity_below_backstop")

    def test_order_exceeding_pool_share_is_rejected(self):
        engine = RiskEngine(RISK, MicroCapitalLimits(minimum_pool_liquidity_usd=1_000.0))
        decision = engine.approve(
            Universe.SOLANA_EMERGING,
            self.state,
            1.0,
            pool_liquidity_usd=1_500.0,
            estimated_round_trip_cost_pct=4.0,
        )
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "order_too_large_for_pool")

    def test_unknown_round_trip_cost_is_rejected(self):
        decision = self._approve(estimated_round_trip_cost_pct=None)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "round_trip_cost_unknown")

    def test_cost_dominated_trade_is_rejected(self):
        decision = self._approve(estimated_round_trip_cost_pct=25.0)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.reason, "round_trip_cost_exceeds_limit")

    def test_unconfigured_engine_keeps_original_behaviour(self):
        """Without micro limits nothing new applies, including liquidity checks."""
        decision = RiskEngine(RISK).approve(Universe.SOLANA_EMERGING, self.state, 1.0)
        self.assertTrue(decision.approved, decision.reason)


class AgeGateTests(unittest.TestCase):
    """Too young means unproven, not defective. Watch it, do not discard it."""

    def setUp(self) -> None:
        self.limits = SafetyLimits(60, 50_000, 2, 3, 30)
        self.engine = SafetyEngine(self.limits, self.limits, 120)
        now = datetime.now(UTC)
        self.snapshot = TokenSnapshot(
            identity=TokenIdentity("solana", "Mint123", "MEME", "Meme", verified=True),
            universe=Universe.SOLANA_EMERGING,
            observed_at=now,
            provider_observed_at=now,
            age_minutes=120,
            liquidity_usd=100_000,
            top10_private_holder_pct=20,
            mint_authority_disabled=True,
            freeze_authority_disabled=True,
            developer_selling=False,
            connected_wallet_risk=False,
            entry_route_found=True,
            exit_route_found=True,
            transaction_simulation_ok=True,
            entry_price_impact_pct=0.5,
            exit_price_impact_pct=1.0,
            graduated=True,
        )

    def test_under_age_candidate_is_monitored_not_rejected(self):
        young = replace(self.snapshot, age_minutes=5)
        decision = self.engine.evaluate(young)
        self.assertEqual(decision.status, CandidateStatus.MONITOR)
        self.assertIn("token_too_young_for_universe", decision.warnings)
        self.assertEqual(decision.failures, ())

    def test_under_age_with_a_real_failure_still_rejects(self):
        young_and_broken = replace(self.snapshot, age_minutes=5, exit_route_found=False)
        decision = self.engine.evaluate(young_and_broken)
        self.assertEqual(decision.status, CandidateStatus.REJECT)
        self.assertIn("exit_route_failed", decision.failures)

    def test_unknown_age_still_rejects(self):
        """Unknown is not the same as young; missing evidence fails closed."""
        unknown = replace(self.snapshot, age_minutes=None)
        decision = self.engine.evaluate(unknown)
        self.assertEqual(decision.status, CandidateStatus.REJECT)
        self.assertIn("token_age_unknown", decision.failures)

    def test_old_enough_candidate_is_still_eligible(self):
        decision = self.engine.evaluate(self.snapshot)
        self.assertEqual(decision.status, CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW)


if __name__ == "__main__":
    unittest.main()
