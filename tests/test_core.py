from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from meme_flight_recorder.cex_engine import CexPaperEngine
from meme_flight_recorder.config import RiskLimits, SafetyLimits, Settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.lifecycle import infer_lifecycle
from meme_flight_recorder.models import (
    CandidateStatus,
    Lifecycle,
    TokenIdentity,
    TokenSnapshot,
    Universe,
)
from meme_flight_recorder.paper import PaperBroker
from meme_flight_recorder.providers.helius import HeliusProvider
from meme_flight_recorder.risk import PortfolioState, RiskEngine
from meme_flight_recorder.safety import SafetyEngine
from meme_flight_recorder.shadow import SolanaShadowEngine
from meme_flight_recorder.strategy import (
    Candle,
    confirm_retest,
    detect_breakout,
    established_meme_trend_ok,
)


class CoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = SafetyLimits(60, 50_000, 2, 3, 30)
        now = datetime.now(UTC)
        self.safe_snapshot = TokenSnapshot(
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
            liquidity_growth_pct=20,
        )

    def test_safe_candidate_reaches_strategy_review(self):
        decision = SafetyEngine(self.limits, self.limits, 120).evaluate(self.safe_snapshot)
        self.assertEqual(decision.status, CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW)
        self.assertEqual(infer_lifecycle(self.safe_snapshot), Lifecycle.LIQUIDITY_EXPANDING)

    def test_missing_exit_data_fails_closed(self):
        decision = SafetyEngine(self.limits, self.limits, 120).evaluate(
            replace(self.safe_snapshot, exit_route_found=None, exit_price_impact_pct=None)
        )
        self.assertEqual(decision.status, CandidateStatus.REJECT)
        self.assertIn("exit_route_unknown", decision.failures)
        self.assertIn("exit_price_impact_unknown", decision.failures)

    def test_brand_new_launch_is_monitor_only(self):
        launch_limits = replace(self.limits, minimum_age_minutes=10)
        snapshot = replace(self.safe_snapshot, universe=Universe.SOLANA_LAUNCH, age_minutes=20)
        decision = SafetyEngine(self.limits, launch_limits, 120).evaluate(snapshot)
        self.assertEqual(decision.status, CandidateStatus.MONITOR)

    def test_live_mode_is_impossible(self):
        risk = RiskLimits(0.5, 0.25, 1, 2, 1, 3)
        with (
            tempfile.TemporaryDirectory() as folder,
            self.assertRaisesRegex(ValueError, "Live execution is not supported"),
        ):
            Settings("live", Path(folder) / "x.db", 10_000, 120, self.limits, self.limits, risk)

    def test_hash_chained_journal_detects_tampering(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FlightRecorder(Path(folder) / "events.db")
            recorder.append("one", "asset", {"value": 1})
            recorder.append("two", "asset", {"value": 2})
            self.assertTrue(recorder.verify_chain())
            with recorder._connect() as connection:
                connection.execute(
                    "UPDATE events SET payload_json = ? WHERE id = 1", ('{"value":9}',)
                )
            self.assertFalse(recorder.verify_chain())

    def test_cex_risk_size_and_two_r_target(self):
        risk = RiskLimits(0.5, 0.25, 1, 2, 1, 3)
        decision = RiskEngine(risk).approve(
            Universe.CEX_ESTABLISHED, PortfolioState(10_000), entry_price=100, stop_price=95
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.risk_amount_usd, 50)
        self.assertEqual(decision.position_value_usd, 1000)
        with tempfile.TemporaryDirectory() as folder:
            broker = PaperBroker(FlightRecorder(Path(folder) / "paper.db"))
            position = broker.open_position("DOGE/USDT", Universe.CEX_ESTABLISHED, 100, 1000, 95)
            self.assertEqual(position.target_price, 110)

    def test_cex_spot_position_never_exceeds_equity(self):
        risk = RiskLimits(0.5, 0.25, 1, 2, 1, 3)
        decision = RiskEngine(risk).approve(
            Universe.CEX_ESTABLISHED,
            PortfolioState(10_000),
            entry_price=100,
            stop_price=99.99,
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.position_value_usd, 10_000)

    def test_solana_sizes_as_total_capital_at_risk(self):
        risk = RiskLimits(0.5, 0.25, 1, 2, 1, 3)
        decision = RiskEngine(risk).approve(
            Universe.SOLANA_EMERGING, PortfolioState(10_000), entry_price=0.001
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.position_value_usd, 25)
        self.assertEqual(decision.risk_amount_usd, 25)

    def test_completed_candle_breakout_and_retest(self):
        candles = []
        price = 100.0
        for index in range(60):
            price += 0.05
            candles.append(Candle(index, price - 0.1, price + 0.2, price - 0.2, price, 1000))
        self.assertTrue(established_meme_trend_ok(candles))

        consolidation = [Candle(index, 100.0, 100.5, 99.5, 100.1, 1000) for index in range(25)]
        breakout = Candle(25, 100.2, 102.0, 100.1, 101.0, 2000)
        setup = detect_breakout(consolidation + [breakout], consolidation_candles=16)
        self.assertIsNotNone(setup)
        assert setup is not None
        retest = Candle(26, 100.4, 101.0, 100.3, 100.8, 1100)
        signal = confirm_retest(setup, [retest])
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.target, signal.entry + 2 * (signal.entry - signal.stop))

    def test_cex_engine_waits_for_retest_then_opens(self):
        trend = []
        price = 100.0
        for index in range(60):
            price += 0.05
            trend.append(Candle(index, price - 0.1, price + 0.2, price - 0.2, price, 1000))
        base = [Candle(index, 100, 100.5, 99.5, 100.1, 1000) for index in range(25)]
        breakout = Candle(25, 100.2, 102, 100.1, 101, 2000)
        retest = Candle(26, 100.4, 101, 100.3, 100.8, 1100)
        with tempfile.TemporaryDirectory() as folder:
            recorder = FlightRecorder(Path(folder) / "cex.db")
            broker = PaperBroker(recorder)
            risk = RiskEngine(RiskLimits(0.5, 0.25, 1, 2, 1, 3))
            engine = CexPaperEngine(recorder, broker, risk)
            recorder.append(
                "candidate_evaluated",
                "cex:DOGE/USDT",
                {"status": "eligible_for_strategy_review"},
            )
            state = PortfolioState(10_000)
            first = engine.observe("DOGE/USDT", trend, base + [breakout], state)
            self.assertEqual(first.status, "breakout_detected")
            second = engine.observe("DOGE/USDT", trend, base + [breakout, retest], state)
            self.assertEqual(second.status, "opened")
            self.assertEqual(len(broker.positions), 1)

    def test_solana_shadow_models_costs_and_restores_on_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FlightRecorder(Path(folder) / "shadow.db")
            risk = RiskEngine(RiskLimits(0.5, 0.25, 1, 2, 1, 3))
            broker = PaperBroker(recorder)
            engine = SolanaShadowEngine(recorder, broker, risk)
            recorder.append(
                "candidate_evaluated",
                "solana:Mint123",
                {"status": "eligible_for_strategy_review"},
            )
            result = engine.open("Mint123", 0.001, 0.5, True, True, PortfolioState(10_000))
            self.assertTrue(result.approved)
            assert result.position is not None
            self.assertEqual(result.position.risk_amount_usd, 25)
            self.assertGreater(result.position.entry_price, 0.001)
            restored = PaperBroker(FlightRecorder(Path(folder) / "shadow.db"))
            self.assertIn(result.position.position_id, restored.positions)

    def test_restart_recovers_position_older_than_one_thousand_events(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FlightRecorder(Path(folder) / "long-running.db")
            broker = PaperBroker(recorder)
            position = broker.open_position(
                "DOGE/USD", Universe.CEX_ESTABLISHED, 100, 5, stop_price=95
            )
            for index in range(1_100):
                recorder.append("heartbeat", "collector", {"sequence": index})
            restored = PaperBroker(recorder)
            self.assertIn(position.position_id, restored.positions)

    def test_shadow_rejects_without_independent_exit_route(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FlightRecorder(Path(folder) / "blocked.db")
            risk = RiskEngine(RiskLimits(0.5, 0.25, 1, 2, 1, 3))
            engine = SolanaShadowEngine(recorder, PaperBroker(recorder), risk)
            recorder.append(
                "candidate_evaluated",
                "solana:Mint123",
                {"status": "eligible_for_strategy_review"},
            )
            result = engine.open("Mint123", 0.001, 0.5, False, True, PortfolioState(10_000))
            self.assertFalse(result.approved)
            self.assertEqual(result.reason, "exit_route_not_verified")

    def test_engines_cannot_bypass_candidate_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FlightRecorder(Path(folder) / "gate.db")
            risk = RiskEngine(RiskLimits(0.5, 0.25, 1, 2, 1, 3))
            broker = PaperBroker(recorder)
            shadow = SolanaShadowEngine(recorder, broker, risk)
            result = shadow.open("UnknownMint", 0.001, 0.1, True, True, PortfolioState(10_000))
            self.assertFalse(result.approved)
            self.assertEqual(result.reason, "candidate_safety_gate_missing")

    def test_helius_mint_evidence_keeps_gross_concentration_distinct(self):
        responses = {
            "getAccountInfo": {
                "context": {"slot": 10},
                "value": {
                    "data": {
                        "parsed": {
                            "type": "mint",
                            "info": {
                                "decimals": 6,
                                "mintAuthority": None,
                                "freezeAuthority": None,
                            },
                        }
                    }
                },
            },
            "getTokenSupply": {
                "context": {"slot": 11},
                "value": {"amount": "1000000"},
            },
            "getTokenLargestAccounts": {
                "context": {"slot": 12},
                "value": [{"amount": "250000"}, {"amount": "150000"}],
            },
        }

        def transport(url, payload):
            return {"jsonrpc": "2.0", "id": payload["id"], "result": responses[payload["method"]]}

        provider = HeliusProvider(rpc_url="https://example.invalid", transport=transport)
        evidence = provider.mint_evidence("Mint123")
        self.assertTrue(evidence.mint_authority_disabled)
        self.assertTrue(evidence.freeze_authority_disabled)
        self.assertEqual(evidence.gross_top10_account_pct, 40)
        self.assertIsNone(evidence.largest_accounts_error)
        self.assertEqual(evidence.slot, 12)

    def test_helius_keeps_authorities_when_largest_accounts_is_unavailable(self):
        def transport(url, payload):
            if payload["method"] == "getAccountInfo":
                result = {
                    "context": {"slot": 10},
                    "value": {
                        "data": {
                            "parsed": {
                                "type": "mint",
                                "info": {
                                    "decimals": 6,
                                    "mintAuthority": None,
                                    "freezeAuthority": None,
                                },
                            }
                        }
                    },
                }
            elif payload["method"] == "getTokenSupply":
                result = {"context": {"slot": 11}, "value": {"amount": "1000000"}}
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {"code": -32600, "message": "method unavailable"},
                }
            return {"jsonrpc": "2.0", "id": payload["id"], "result": result}

        provider = HeliusProvider(rpc_url="https://example.invalid", transport=transport)
        evidence = provider.mint_evidence("Mint123")
        self.assertTrue(evidence.mint_authority_disabled)
        self.assertIsNone(evidence.gross_top10_account_pct)
        self.assertIn("method unavailable", evidence.largest_accounts_error or "")

    def test_helius_wallet_history_preserves_cursor_and_failed_transactions(self):
        observed = {}

        def get_transport(base_url, path, params=None, headers=None):
            observed.update(
                {"base_url": base_url, "path": path, "params": params, "headers": headers}
            )
            return {
                "data": [
                    {
                        "signature": "failed-signature",
                        "timestamp": 1_700_000_000,
                        "error": {"InstructionError": [1, "Custom"]},
                        "balanceChanges": [],
                    }
                ],
                "pagination": {"hasMore": True, "nextCursor": "cursor-2"},
            }

        provider = HeliusProvider(
            api_key="test-key",
            rpc_url="https://example.invalid",
            get_transport=get_transport,
        )
        page = provider.wallet_history("wallet-address", before="cursor-1")
        self.assertEqual(page.next_cursor, "cursor-2")
        self.assertTrue(page.has_more)
        self.assertIsNotNone(page.transactions[0]["error"])
        self.assertEqual(observed["params"]["before"], "cursor-1")
        self.assertEqual(observed["params"]["tokenAccounts"], "balanceChanged")
        self.assertEqual(observed["headers"], {"X-Api-Key": "test-key"})
        self.assertNotIn("test-key", observed["path"])

    def test_helius_wallet_history_requires_key_and_valid_filter(self):
        provider = HeliusProvider(rpc_url="https://example.invalid")
        with self.assertRaisesRegex(ValueError, "HELIUS_API_KEY"):
            provider.wallet_history("wallet-address")

        provider = HeliusProvider(api_key="test-key", rpc_url="https://example.invalid")
        with self.assertRaisesRegex(ValueError, "token_accounts"):
            provider.wallet_history("wallet-address", token_accounts="spam-safe")


if __name__ == "__main__":
    unittest.main()
