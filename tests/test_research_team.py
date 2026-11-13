"""Tests for the Multi-Agent Meme-Coin Research Team and Desk UI."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from meme_flight_recorder import api
from meme_flight_recorder.config import (
    CostModel,
    MicroCapitalLimits,
    RiskLimits,
    SafetyLimits,
)
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.models import (
    CandidateStatus,
    TokenIdentity,
    TokenSnapshot,
    Universe,
)
from meme_flight_recorder.providers.binance_web3 import VendorClusterLabels
from meme_flight_recorder.providers.media_feeds import MediaItem
from meme_flight_recorder.readiness import SafetyVerdict
from meme_flight_recorder.research_team import (
    AdversarialOnChainInvestigator,
    AutonomyLevel,
    PaperRiskGate,
    ReadOnlyBridge,
    ResearchTeamCoordinator,
    ViralityNarrativeAnalyst,
    generate_desk_diary,
    normalize_media_items,
)
from meme_flight_recorder.risk import PortfolioState, RiskEngine
from meme_flight_recorder.safety import SafetyEngine
from meme_flight_recorder.social_intelligence import SocialEvent, SocialSource
from meme_flight_recorder.sources import SourceRole

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
MINT = "5z7gxdrf8NpBrELaB29kXAk7SHBiNG5u726LtyYvpump"


def _safety_engine() -> SafetyEngine:
    cex = SafetyLimits(
        minimum_age_minutes=10080,
        minimum_liquidity_usd=1_000_000.0,
        maximum_entry_price_impact_pct=1.0,
        maximum_exit_price_impact_pct=1.5,
        maximum_top10_private_holder_pct=25.0,
    )
    sol = SafetyLimits(
        minimum_age_minutes=60,
        minimum_liquidity_usd=5000.0,
        maximum_entry_price_impact_pct=2.0,
        maximum_exit_price_impact_pct=3.0,
        maximum_top10_private_holder_pct=30.0,
    )
    return SafetyEngine(cex, sol, stale_after_seconds=120)


def _risk_engine() -> RiskEngine:
    limits = RiskLimits(
        risk_per_cex_trade_pct=0.5,
        capital_at_risk_per_solana_trade_pct=0.25,
        maximum_daily_loss_pct=1.0,
        maximum_open_positions=4,
        maximum_aggregate_open_risk_pct=1.0,
        consecutive_loss_limit=3,
    )
    micro = MicroCapitalLimits(
        equity_threshold_usd=100.0,
        max_position_usd=10.0,
        position_pct_of_equity=20.0,
        minimum_viable_position_usd=0.5,
        maximum_round_trip_cost_pct=8.0,
        maximum_pool_share_pct=0.5,
        minimum_pool_liquidity_usd=5000.0,
        maximum_aggregate_open_position_pct=80.0,
    )
    return RiskEngine(limits, micro=micro)


def _clean_snapshot(**overrides: object) -> TokenSnapshot:
    base: dict[str, object] = {
        "identity": TokenIdentity("solana", MINT, "HAROLD", "Harold The Duck", verified=True),
        "universe": Universe.SOLANA_EMERGING,
        "observed_at": NOW,
        "provider_observed_at": NOW,
        "age_minutes": 180.0,
        "price_usd": 0.025,
        "liquidity_usd": 85_000.0,
        "volume_5m_usd": 14_000.0,
        "holder_count": 640,
        "top10_private_holder_pct": 16.5,
        "mint_authority_disabled": True,
        "freeze_authority_disabled": True,
        "developer_selling": False,
        "connected_wallet_risk": False,
        "entry_route_found": True,
        "exit_route_found": True,
        "transaction_simulation_ok": True,
        "entry_price_impact_pct": 0.35,
        "exit_price_impact_pct": 0.45,
        "graduated": True,
    }
    base.update(overrides)
    return TokenSnapshot(**base)


class ResearchTeamTests(unittest.TestCase):
    def test_virality_analyst_dedups_relays_and_discounts_spam(self) -> None:
        items = [
            MediaItem(
                origin="r/SolanaMemeCoins",
                external_id="p1",
                author="alice",
                text=f"Organic breakdown of {MINT} liquidity and holders",
                published_at=NOW - timedelta(minutes=5),
                engagement=45,
            ),
            MediaItem(
                origin="@public_alpha",
                external_id="tg1",
                author="bob",
                text=f"100x GUARANTEED APE IN BUY NOW $HAROLD {MINT}",
                published_at=NOW - timedelta(minutes=3),
                engagement=5,
            ),
            MediaItem(
                origin="gnews:CoinDesk",
                external_id="gn1",
                author="carol",
                text="Solana meme volume ticks up on $HAROLD",
                published_at=NOW - timedelta(minutes=2),
            ),
        ]
        events = normalize_media_items(items, NOW)
        # Ticker-only news mention must NOT be assigned a mint address.
        self.assertIsNone(events[2].mint)
        self.assertEqual(events[2].symbols, ("HAROLD",))

        analyst = ViralityNarrativeAnalyst()
        # Add credible events from distinct sources & authors
        credible_events = [
            SocialEvent(
                source=SocialSource.WEB_NEWS if i == 2 else SocialSource.REDDIT,
                author=f"analyst_{i}",
                text=f"Checking pool depth for {MINT}",
                observed_at=NOW - timedelta(minutes=10 - i),
                published_at=NOW - timedelta(minutes=10 - i),
                mint=MINT,
                engagement=80,
                author_credibility=0.8,
                spam_probability=0.05,
                external_id=f"c_{i}",
            )
            for i in range(3)
        ] + [
            SocialEvent(
                source=SocialSource.TELEGRAM,
                author="relay_1",
                text="Forwarded analysis",
                observed_at=NOW - timedelta(minutes=4),
                mint=MINT,
                author_credibility=0.75,
                spam_probability=0.0,
                forwarded_from="analyst_0",
                external_id="c_0",
            )
        ]
        report = analyst.analyze(
            MINT,
            "HAROLD",
            credible_events,
            as_of=NOW,
            source_roles={"reddit:analyst_0": SourceRole.ORIGINATOR},
        )
        self.assertTrue(report.advisory_only)
        self.assertEqual(report.mentions, 3)  # relay deduplicated
        self.assertEqual(report.advisory_stance, "strong_discovery")
        self.assertGreater(report.momentum_score, 40.0)

    def test_investigator_overlays_vendor_and_fails_closed_on_concentration(self) -> None:
        investigator = AdversarialOnChainInvestigator(_safety_engine())
        snap = _clean_snapshot(developer_selling=None, top10_private_holder_pct=None)
        labels = VendorClusterLabels(dev_sell_pct=100.0, top10_pct=36.0)
        enriched, verdict = investigator.investigate(snap, now=NOW, vendor_labels=labels)
        self.assertEqual(enriched.developer_selling, False)
        self.assertEqual(verdict.candidate_status, CandidateStatus.REJECT)
        self.assertEqual(verdict.safety_verdict, SafetyVerdict.FAIL)
        self.assertIn("holder_concentration_excessive", verdict.failures)
        self.assertEqual(verdict.gate_breakdown["concentration_and_clusters"], "fail")
        self.assertEqual(verdict.gate_breakdown["authority_and_rug_flags"], "pass")

    def test_risk_gate_blocks_when_safety_failed_even_if_virality_high(self) -> None:
        investigator = AdversarialOnChainInvestigator(_safety_engine())
        gate = PaperRiskGate(_risk_engine(), CostModel())
        bad_snap = _clean_snapshot(mint_authority_disabled=False)
        _, verdict = investigator.investigate(bad_snap, now=NOW)
        risk = gate.evaluate(verdict, bad_snap, PortfolioState(equity_usd=50.0))
        self.assertFalse(risk.approved)
        self.assertEqual(risk.reason, "safety_gate_reject")

    def test_coordinator_executes_pipeline_journals_chain_and_renders_diary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recorder = FlightRecorder(Path(tmp) / "desk.db")
            coord = ResearchTeamCoordinator(
                recorder, _safety_engine(), _risk_engine(), cost_model=CostModel()
            )
            state = PortfolioState(equity_usd=50.0)
            events = [
                SocialEvent(
                    source=SocialSource.REDDIT,
                    author="user_1",
                    text=f"Clean pool on {MINT}",
                    observed_at=NOW - timedelta(minutes=3),
                    mint=MINT,
                    author_credibility=0.8,
                    spam_probability=0.05,
                    external_id="ev1",
                )
            ]
            packet = coord.evaluate_candidate(
                _clean_snapshot(),
                events,
                state,
                as_of=NOW,
                execute_paper_open=True,
            )
            self.assertEqual(packet.final_action, "PAPER_OPEN")
            self.assertIsNotNone(packet.paper_position)
            self.assertTrue(packet.risk.approved)
            self.assertEqual(packet.risk.position_value_usd, 10.0)
            self.assertTrue(recorder.verify_chain())

            # Close paper position and verify diary markdown
            assert packet.paper_position is not None
            coord.broker.close_position(packet.paper_position.position_id, 0.032, "take_profit")
            diary = generate_desk_diary(recorder, as_of=NOW)
            self.assertIn("Hash Chain Integrity:** `VERIFIED`", diary)
            self.assertIn("HAROLD", diary)
            self.assertIn("PAPER_OPEN", diary)
            self.assertIn("take_profit", diary)

    def test_autonomy_levels_and_read_only_bridge(self) -> None:
        manifest = ReadOnlyBridge.manifest()
        self.assertFalse(manifest["write_capable"])
        self.assertFalse(manifest["signing_capable"])
        names = {item["name"] for item in manifest["ecosystem_references"]}
        self.assertTrue({"gmgn-skills", "pump-public-docs", "solana-agent-kit"} <= names)

        with tempfile.TemporaryDirectory() as tmp:
            recorder = FlightRecorder(Path(tmp) / "autonomy.db")
            coord = ResearchTeamCoordinator(
                recorder, _safety_engine(), _risk_engine(), cost_model=CostModel()
            )
            state = PortfolioState(equity_usd=50.0)
            p_l0 = coord.evaluate_candidate(
                _clean_snapshot(),
                [],
                state,
                as_of=NOW,
                autonomy_level=AutonomyLevel.L0_OBSERVE_ONLY,
                execute_paper_open=True,
            )
            self.assertEqual(p_l0.final_action, "ELIGIBLE_OBSERVED")
            self.assertIsNone(p_l0.paper_position)

            p_l2 = coord.evaluate_candidate(
                _clean_snapshot(),
                [],
                state,
                as_of=NOW,
                autonomy_level=AutonomyLevel.L2_HUMAN_APPROVAL_DRAFT,
            )
            self.assertEqual(p_l2.final_action, "HUMAN_APPROVAL_DRAFT")
            self.assertIsNone(p_l2.paper_position)
            self.assertIsNotNone(p_l2.human_approval_draft)
            assert p_l2.human_approval_draft is not None
            self.assertTrue(p_l2.human_approval_draft.requires_human_confirmation)
            self.assertTrue(p_l2.human_approval_draft.execution_blocked)
            self.assertFalse(p_l2.human_approval_draft.signing_capable)


class DeskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._environ = dict(os.environ)
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "api_desk.db"
        os.environ["MFR_DATABASE_PATH"] = self._db_path.as_posix()
        os.environ["MFR_STARTING_EQUITY_USD"] = "50.0"
        api.components.cache_clear()

    def tearDown(self) -> None:
        api.components.cache_clear()
        os.environ.clear()
        os.environ.update(self._environ)
        self._tmp.cleanup()

    def test_desk_endpoints_render_and_evaluate(self) -> None:
        client = TestClient(api.app)
        resp_eval = client.post(
            "/api/desk/evaluate",
            json={
                "chain": "solana",
                "address": MINT,
                "symbol": "HAROLD",
                "name": "Harold The Duck",
                "verified": True,
                "universe": "solana_emerging",
                "observed_at": datetime.now(UTC).isoformat(),
                "facts": {
                    "age_minutes": 180.0,
                    "price_usd": 0.025,
                    "liquidity_usd": 85000.0,
                    "top10_private_holder_pct": 16.5,
                    "mint_authority_disabled": True,
                    "freeze_authority_disabled": True,
                    "developer_selling": False,
                    "entry_route_found": True,
                    "exit_route_found": True,
                    "transaction_simulation_ok": True,
                    "entry_price_impact_pct": 0.35,
                    "exit_price_impact_pct": 0.45,
                    "graduated": True,
                },
                "social_events": [
                    {
                        "source": "reddit",
                        "author": "desk_analyst",
                        "text": f"Tracking {MINT}",
                        "engagement": 50,
                        "account_age_days": 200,
                    }
                ],
                "execute_paper_open": True,
            },
        )
        self.assertEqual(resp_eval.status_code, 200)
        body = resp_eval.json()
        self.assertEqual(body["final_action"], "PAPER_OPEN")
        self.assertTrue(body["risk"]["approved"])

        state_resp = client.get("/api/desk/state")
        self.assertEqual(state_resp.status_code, 200)
        state = state_resp.json()
        self.assertTrue(state["chain_verified"])
        self.assertEqual(state["counts"]["packets"], 1)
        self.assertEqual(state["counts"]["open_positions"], 1)

        html_resp = client.get("/desk")
        self.assertEqual(html_resp.status_code, 200)
        self.assertIn("5-Agent Paper Research Desk", html_resp.text)

        diary_resp = client.get("/api/desk/diary")
        self.assertEqual(diary_resp.status_code, 200)
        self.assertIn("PAPER_OPEN", diary_resp.text)

        # Also test L2 Human-Approval Draft -> Single-Tap Paper Approve flow
        draft_resp = client.post(
            "/api/desk/evaluate",
            json={
                "chain": "solana",
                "address": MINT,
                "symbol": "HAROLD",
                "name": "Harold The Duck",
                "verified": True,
                "universe": "solana_emerging",
                "observed_at": datetime.now(UTC).isoformat(),
                "autonomy_level": "L2_human_approval_draft",
                "facts": {
                    "age_minutes": 180.0,
                    "price_usd": 0.025,
                    "liquidity_usd": 85000.0,
                    "top10_private_holder_pct": 16.5,
                    "mint_authority_disabled": True,
                    "freeze_authority_disabled": True,
                    "developer_selling": False,
                    "entry_route_found": True,
                    "exit_route_found": True,
                    "transaction_simulation_ok": True,
                    "entry_price_impact_pct": 0.35,
                    "exit_price_impact_pct": 0.45,
                    "graduated": True,
                },
            },
        )
        self.assertEqual(draft_resp.status_code, 200)
        draft_body = draft_resp.json()
        self.assertEqual(draft_body["final_action"], "HUMAN_APPROVAL_DRAFT")

        decision_resp = client.post(
            "/api/desk/decision",
            json={"packet_event_hash": draft_body["event_hash"], "decision": "approve"},
        )
        self.assertEqual(decision_resp.status_code, 200)
        self.assertIsNotNone(decision_resp.json()["paper_position_id"])


class VaultIntegrityTests(unittest.TestCase):
    def test_obsidian_vault_has_zero_orphans_and_zero_broken_wikilinks(self) -> None:
        import re

        vault_root = Path(__file__).parents[1] / "vault"
        notes = {p.stem: p for p in vault_root.rglob("*.md")}
        self.assertIn("Home", notes)
        wikilink_re = re.compile(r"\[\[([^\]|#]+)")

        adjacency: dict[str, set[str]] = {stem: set() for stem in notes}
        inbound: dict[str, int] = {stem: 0 for stem in notes}
        for stem, path in notes.items():
            text = path.read_text(encoding="utf-8")
            for target in wikilink_re.findall(text):
                self.assertIn(target, notes, f"Broken wikilink [[{target}]] in {path}")
                adjacency[stem].add(target)
                adjacency[target].add(stem)
                if target != stem:
                    inbound[target] += 1

        # BFS from Home to verify every note is reachable (zero disconnected components)
        visited = {"Home"}
        queue = ["Home"]
        while queue:
            cur = queue.pop(0)
            for nxt in adjacency[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        self.assertEqual(visited, set(notes), f"Orphan notes: {set(notes) - visited}")
        for stem in notes:
            if stem != "Home":
                self.assertGreater(inbound[stem], 0, f"Note {stem} has zero inbound links")


if __name__ == "__main__":
    unittest.main()
