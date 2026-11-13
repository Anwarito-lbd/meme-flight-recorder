from __future__ import annotations

import unittest

from meme_flight_recorder.calibration import (
    Outcome,
    compare,
    confidence_report,
    gate_report,
    group_by,
    outcomes_from_events,
    summarise,
)
from meme_flight_recorder.confidence import ConfidenceBand, score_confidence


def _outcomes(label_status: str, multiples: list[float], band: str = "") -> list[Outcome]:
    return [
        Outcome(
            mint=f"Mint{index}",
            entry_price=1.0,
            later_price=multiple,
            status=label_status,
            confidence_band=band,
        )
        for index, multiple in enumerate(multiples)
    ]


class ConfidenceScoreTests(unittest.TestCase):
    def test_a_strong_candidate_scores_high(self):
        score = score_confidence(
            liquidity_usd=1_500_000.0,
            exit_impact_pct=0.2,
            cluster_verdict="clear",
            buy_share_pct=72.0,
            age_minutes=3_000.0,
            evidence_coverage_pct=100.0,
        )
        self.assertEqual(score.band, ConfidenceBand.HIGH)

    def test_a_disqualified_cluster_drags_the_score_down(self):
        score = score_confidence(
            liquidity_usd=1_500_000.0,
            exit_impact_pct=0.2,
            cluster_verdict="disqualified",
            buy_share_pct=72.0,
            age_minutes=3_000.0,
            evidence_coverage_pct=100.0,
        )
        self.assertLess(score.value, 0.85)

    def test_missing_components_are_excluded_not_zeroed(self):
        """Absent evidence must not silently read as a bad score."""
        partial = score_confidence(liquidity_usd=1_000_000.0, cluster_verdict="clear")
        self.assertGreater(partial.value, 0.5)
        self.assertIn("flow", partial.missing)
        self.assertLess(partial.coverage_pct, 100.0)

    def test_low_evidence_coverage_discounts_the_score(self):
        arguments = {
            "liquidity_usd": 1_500_000.0,
            "exit_impact_pct": 0.2,
            "cluster_verdict": "clear",
            "buy_share_pct": 72.0,
            "age_minutes": 3_000.0,
        }
        full = score_confidence(**arguments, evidence_coverage_pct=100.0)
        thin = score_confidence(**arguments, evidence_coverage_pct=40.0)
        self.assertLess(thin.value, full.value)

    def test_no_evidence_at_all_scores_zero(self):
        self.assertEqual(score_confidence().value, 0.0)


class SummaryTests(unittest.TestCase):
    def test_median_resists_a_single_outlier(self):
        """One 500x drags a mean anywhere; the real data contained exactly that."""
        stats = summarise("x", _outcomes("reject", [0.5, 0.9, 1.0, 1.1, 500.0]))
        self.assertEqual(stats.median_multiple, 1.0)
        self.assertGreater(stats.mean_multiple, 90)

    def test_dead_and_winner_rates_are_reported(self):
        stats = summarise("x", _outcomes("reject", [0.01, 0.05, 1.0, 3.0]))
        self.assertEqual(stats.dead_pct, 50.0)
        self.assertEqual(stats.winner_pct, 25.0)

    def test_small_groups_are_flagged_unreliable(self):
        self.assertFalse(summarise("x", _outcomes("reject", [1.0] * 5)).reliable)
        self.assertTrue(summarise("x", _outcomes("reject", [1.0] * 25)).reliable)


class ComparisonTests(unittest.TestCase):
    def test_small_samples_are_never_declared_an_edge(self):
        result = compare(
            summarise("passed", _outcomes("monitor", [5.0] * 3)),
            summarise("rejected", _outcomes("reject", [0.1] * 3)),
        )
        self.assertEqual(result.verdict, "not_yet_distinguishable")

    def test_a_real_separation_is_reported(self):
        result = compare(
            summarise("passed", _outcomes("monitor", [2.0] * 25)),
            summarise("rejected", _outcomes("reject", [0.5] * 25)),
        )
        self.assertEqual(result.verdict, "separates_as_expected")

    def test_a_backwards_filter_is_named_not_hidden(self):
        """A filter whose rejections outperform is worse than no filter."""
        result = compare(
            summarise("passed", _outcomes("monitor", [0.5] * 25)),
            summarise("rejected", _outcomes("reject", [2.0] * 25)),
        )
        self.assertEqual(result.verdict, "separates_backwards")
        self.assertIn("rejected_group_outperformed", result.notes)

    def test_a_tiny_difference_is_called_noise(self):
        result = compare(
            summarise("passed", _outcomes("monitor", [1.02] * 25)),
            summarise("rejected", _outcomes("reject", [1.0] * 25)),
        )
        self.assertEqual(result.verdict, "no_meaningful_separation")


class EventJoinTests(unittest.TestCase):
    def _event(self, mint: str, price: float, status: str = "reject"):
        return {
            "entity_id": mint,
            "payload": {"price_usd": price, "status": status, "cluster_verdict": "clear"},
        }

    def test_only_the_first_observation_of_a_mint_is_used(self):
        """Later observations grade the system on hindsight."""
        events = [self._event("A", 1.0), self._event("A", 5.0)]
        outcomes = outcomes_from_events(events, {"A": 2.0})
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].entry_price, 1.0)
        self.assertEqual(outcomes[0].multiple, 2.0)

    def test_mints_without_a_later_price_are_skipped(self):
        outcomes = outcomes_from_events([self._event("A", 1.0)], {})
        self.assertEqual(outcomes, [])

    def test_gate_report_compares_passed_against_rejected(self):
        outcomes = _outcomes("monitor", [2.0] * 25) + _outcomes("reject", [0.5] * 25)
        self.assertEqual(gate_report(outcomes).verdict, "separates_as_expected")

    def test_confidence_report_needs_both_bands(self):
        outcomes = _outcomes("monitor", [2.0] * 25, band="high")
        self.assertEqual(confidence_report(outcomes).verdict, "not_yet_distinguishable")


class GroupingTests(unittest.TestCase):
    def test_groups_by_attribute(self):
        outcomes = _outcomes("monitor", [1.0, 2.0]) + _outcomes("reject", [0.5])
        groups = group_by(outcomes, "status")
        self.assertEqual(groups["monitor"].count, 2)
        self.assertEqual(groups["reject"].count, 1)


if __name__ == "__main__":
    unittest.main()
