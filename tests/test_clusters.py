from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import UTC, datetime

from meme_flight_recorder.clusters import (
    ClusterAssessment,
    ClusterVerdict,
    LaunchBundleEvidence,
    assess_funding_graph,
    assess_vendor_labels,
    build_funding_clusters,
    combine,
)
from meme_flight_recorder.config import ClusterLimits, SafetyLimits
from meme_flight_recorder.models import (
    CandidateStatus,
    TokenIdentity,
    TokenSnapshot,
    Universe,
)
from meme_flight_recorder.safety import SafetyEngine


@dataclass(frozen=True)
class Labels:
    """Minimal stand-in for a discovery feed's label payload."""

    insider_pct: float | None = None
    sniper_pct: float | None = None
    bundler_pct: float | None = None
    new_wallet_pct: float | None = None
    dev_sell_pct: float | None = None
    dev_migrate_count: int | None = None
    dev_wash_trading: bool | None = None
    insider_wash_trading: bool | None = None


CLEAN_LABELS = Labels(
    insider_pct=1.0,
    sniper_pct=0.5,
    bundler_pct=0.0,
    new_wallet_pct=2.0,
    dev_sell_pct=0.0,
    dev_migrate_count=0,
    dev_wash_trading=False,
    insider_wash_trading=False,
)


class VendorLabelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = ClusterLimits()

    def test_clean_labels_are_cleared(self):
        result = assess_vendor_labels(CLEAN_LABELS, self.limits)
        self.assertEqual(result.verdict, ClusterVerdict.CLEAR)
        self.assertFalse(result.blocks_entry)

    def test_excessive_insider_share_disqualifies(self):
        labels = Labels(**{**CLEAN_LABELS.__dict__, "insider_pct": 42.0})
        result = assess_vendor_labels(labels, self.limits)
        self.assertEqual(result.verdict, ClusterVerdict.DISQUALIFIED)
        self.assertIn("vendor_insider_concentration", result.failures)

    def test_wash_trading_tag_disqualifies(self):
        labels = Labels(**{**CLEAN_LABELS.__dict__, "insider_wash_trading": True})
        result = assess_vendor_labels(labels, self.limits)
        self.assertEqual(result.verdict, ClusterVerdict.DISQUALIFIED)

    def test_missing_labels_are_not_treated_as_zero(self):
        """Absent evidence must not read as a clean result."""
        result = assess_vendor_labels(Labels(), self.limits)
        self.assertEqual(result.verdict, ClusterVerdict.INSUFFICIENT_EVIDENCE)
        self.assertTrue(result.blocks_entry)
        self.assertEqual(result.confidence, 0.0)

    def test_serial_launcher_warns_without_disqualifying(self):
        labels = Labels(**{**CLEAN_LABELS.__dict__, "dev_migrate_count": 25})
        result = assess_vendor_labels(labels, self.limits)
        self.assertEqual(result.verdict, ClusterVerdict.SUSPECT)
        self.assertIn("developer_is_serial_launcher", result.warnings)


class FundingGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = ClusterLimits()

    def test_shared_funder_merges_holders_into_one_cluster(self):
        holders = {"a": 4.0, "b": 4.0, "c": 4.0, "d": 1.0}
        edges = [("a", "funder"), ("b", "funder"), ("c", "funder")]
        clusters = build_funding_clusters(holders, edges)
        self.assertEqual(clusters[0]["size"], 3)
        self.assertAlmostEqual(clusters[0]["supply_pct"], 12.0)

    def test_infrastructure_never_bridges_unrelated_holders(self):
        """A shared pool address must not merge the whole holder base."""
        pool = "So11111111111111111111111111111111111111112"
        holders = {"a": 5.0, "b": 5.0}
        clusters = build_funding_clusters(holders, [("a", pool), ("b", pool)])
        self.assertEqual([cluster["size"] for cluster in clusters], [1, 1])

    def test_large_linked_cluster_disqualifies(self):
        holders = {"a": 20.0, "b": 20.0}
        result = assess_funding_graph(
            holders, [("a", "f"), ("b", "f")], self.limits, holder_coverage_pct=100.0
        )
        self.assertEqual(result.verdict, ClusterVerdict.DISQUALIFIED)
        self.assertIn("linked_cluster_concentration", result.failures)

    def test_single_large_holder_is_not_a_linked_cluster(self):
        """One visible whale is a concentration issue, not a hidden cluster."""
        result = assess_funding_graph(
            {"whale": 40.0}, [], self.limits, holder_coverage_pct=100.0
        )
        self.assertNotIn("linked_cluster_concentration", result.failures)

    def test_fresh_wallets_from_one_funder_disqualify(self):
        result = assess_funding_graph(
            {"a": 1.0},
            [],
            self.limits,
            fresh_wallet_funders={"w1": "f", "w2": "f", "w3": "f", "w4": "other"},
            holder_coverage_pct=100.0,
        )
        self.assertIn("fresh_wallets_share_single_funder", result.failures)

    def test_bundled_launch_disqualifies(self):
        bundle = LaunchBundleEvidence(
            bundle_id="0xbundle",
            liquidity_add_in_bundle=True,
            buys_in_same_bundle=11,
            buyer_supply_pct_in_bundle=38.0,
        )
        result = assess_funding_graph(
            {"a": 1.0}, [], self.limits, bundle=bundle, holder_coverage_pct=100.0
        )
        self.assertIn("launch_liquidity_and_buys_bundled", result.failures)

    def test_partial_holder_coverage_lowers_confidence(self):
        full = assess_funding_graph({"a": 1.0}, [], self.limits, holder_coverage_pct=100.0)
        partial = assess_funding_graph({"a": 1.0}, [], self.limits, holder_coverage_pct=40.0)
        self.assertLess(partial.confidence, full.confidence)
        self.assertIn("holder_enumeration_incomplete", partial.warnings)

    def test_empty_holder_set_is_insufficient_not_clear(self):
        result = assess_funding_graph({}, [], self.limits)
        self.assertEqual(result.verdict, ClusterVerdict.INSUFFICIENT_EVIDENCE)


class CombineTests(unittest.TestCase):
    def test_worst_verdict_wins_and_confidence_is_floored(self):
        clean = ClusterAssessment(ClusterVerdict.CLEAR, 0.9)
        bad = ClusterAssessment(ClusterVerdict.DISQUALIFIED, 0.4, ("boom",))
        merged = combine(clean, bad)
        self.assertEqual(merged.verdict, ClusterVerdict.DISQUALIFIED)
        self.assertEqual(merged.confidence, 0.4)
        self.assertIn("boom", merged.failures)


class SafetyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.limits = SafetyLimits(60, 50_000, 2, 3, 30)
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

    def _engine(self) -> SafetyEngine:
        return SafetyEngine(self.limits, self.limits, 120, ClusterLimits())

    def test_unconfigured_engine_ignores_cluster_gates(self):
        decision = SafetyEngine(self.limits, self.limits, 120).evaluate(self.snapshot)
        self.assertEqual(decision.status, CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW)

    def test_configured_engine_rejects_missing_cluster_evidence(self):
        decision = self._engine().evaluate(self.snapshot)
        self.assertEqual(decision.status, CandidateStatus.REJECT)
        self.assertIn("cluster_evidence_missing", decision.failures)

    def test_clear_cluster_assessment_passes(self):
        cluster = assess_vendor_labels(CLEAN_LABELS, ClusterLimits())
        decision = self._engine().evaluate(self.snapshot, cluster=cluster)
        self.assertEqual(decision.status, CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW)

    def test_disqualified_cluster_rejects_otherwise_safe_token(self):
        labels = Labels(**{**CLEAN_LABELS.__dict__, "bundler_pct": 60.0})
        cluster = assess_vendor_labels(labels, ClusterLimits())
        decision = self._engine().evaluate(self.snapshot, cluster=cluster)
        self.assertEqual(decision.status, CandidateStatus.REJECT)
        self.assertIn("vendor_bundler_concentration", decision.failures)

    def test_cex_universe_is_exempt_from_cluster_gates(self):
        from dataclasses import replace

        cex = replace(self.snapshot, universe=Universe.CEX_ESTABLISHED)
        decision = self._engine().evaluate(cex)
        self.assertNotIn("cluster_evidence_missing", decision.failures)

    def test_onchain_cluster_assessment_clear(self):
        from meme_flight_recorder.clusters import assess_onchain_cluster

        cluster = assess_onchain_cluster(top10_private_pct=15.0, limits=ClusterLimits())
        self.assertEqual(cluster.verdict, ClusterVerdict.CLEAR)
        self.assertEqual(cluster.confidence, 1.0)
        self.assertEqual(cluster.failures, ())

    def test_onchain_cluster_assessment_excessive(self):
        from meme_flight_recorder.clusters import assess_onchain_cluster

        cluster = assess_onchain_cluster(
            top10_private_pct=45.0, limits=ClusterLimits(), maximum_top10_pct=30.0
        )
        self.assertEqual(cluster.verdict, ClusterVerdict.DISQUALIFIED)
        self.assertIn("onchain_top_holder_concentration_excessive", cluster.failures)


if __name__ == "__main__":
    unittest.main()

