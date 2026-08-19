"""Tests for the confluence scorer.

The load-bearing property is that an unknown signal cannot help a candidate pass.
Everything else here exists to stop that property being eroded by a later change
that looks harmless.
"""

from __future__ import annotations

from meme_flight_recorder.confluence import (
    CHART_SIGNALS,
    ConfluenceEvidence,
    ConfluenceResult,
    Signal,
    SignalState,
    SignalVerdict,
    score,
)

# A candidate that clears every non-chart threshold below.
STRONG = ConfluenceEvidence(
    liquidity_usd=20_000.0,
    spread_pct=1.0,
    volume_usd=50_000.0,
    adjusted_top_holder_pct=20.0,
    developer_sold_pct=0.0,
    bonding_progress_per_minute=2.0,
    topic_net_inflow_1h_usd=10_000.0,
    buy_transaction_share_pct=65.0,
    qualified_whale_holders=3.0,
)

THRESHOLDS = {
    "minimum_liquidity_usd": 5_000.0,
    "maximum_spread_pct": 2.0,
    "minimum_volume_usd": 10_000.0,
    "maximum_concentration_pct": 30.0,
    "maximum_developer_sold_pct": 1.0,
    "exhausted_developer_sold_pct": 50.0,
    "minimum_bonding_progress_per_minute": 1.0,
    "minimum_topic_inflow_usd": 5_000.0,
    "minimum_buy_share_pct": 60.0,
    "minimum_whale_holders": 1.0,
    "minimum_volume_expansion": 2.0,
}


class TestUnknownNeverCounts:
    """The property the module exists for."""

    def test_empty_evidence_yields_no_present_signals(self) -> None:
        result = score(ConfluenceEvidence(), **THRESHOLDS)
        assert result.present_count == 0
        assert result.absent_count == 0
        assert result.unknown_count == len(result.verdicts)

    def test_empty_evidence_cannot_meet_even_a_minimum_of_one(self) -> None:
        # The whole point: a candidate about which nothing is known must not pass.
        result = score(ConfluenceEvidence(), **THRESHOLDS)
        assert not result.meets(1)

    def test_unknown_is_not_counted_as_absent_either(self) -> None:
        # Both directions matter. Unknown is a third state, not a synonym for
        # failure, because a caller reporting "0 of 14 failed" would be wrong too.
        result = score(ConfluenceEvidence(), **THRESHOLDS)
        assert result.evaluated_count == 0

    def test_a_missing_value_does_not_borrow_a_neighbours_verdict(self) -> None:
        evidence = ConfluenceEvidence(liquidity_usd=20_000.0)
        result = score(evidence, **THRESHOLDS)
        assert result.present_count == 1
        assert result.by_state(SignalState.PRESENT) == (Signal.LIQUIDITY_SUFFICIENT,)


class TestChartSignalsOnAYoungToken:
    """Under 48 hours there is no chart, and that must read as UNKNOWN."""

    def test_every_chart_signal_is_unknown_without_candle_evidence(self) -> None:
        result = score(STRONG, **THRESHOLDS)
        unknown = set(result.by_state(SignalState.UNKNOWN))
        assert unknown == set(CHART_SIGNALS)

    def test_non_chart_evidence_alone_can_satisfy_level_one(self) -> None:
        # Level 1 requires four aligned signals. This is the design decision the
        # spec records: the young-token population must clear it on non-chart
        # evidence, because the chart half cannot be computed at that age.
        result = score(STRONG, **THRESHOLDS)
        assert result.present_count == 9
        assert result.meets(4)

    def test_a_false_chart_signal_is_absent_not_unknown(self) -> None:
        evidence = ConfluenceEvidence(consolidation_breakout=False)
        result = score(evidence, **THRESHOLDS)
        verdict = next(
            v for v in result.verdicts if v.signal is Signal.CONSOLIDATION_BREAKOUT
        )
        assert verdict.state is SignalState.ABSENT

    def test_a_true_chart_signal_counts_when_evidence_exists(self) -> None:
        evidence = ConfluenceEvidence(consolidation_breakout=True, retest_held=True)
        result = score(evidence, **THRESHOLDS)
        assert result.present_count == 2


class TestThresholdDirections:
    def test_liquidity_is_a_floor(self) -> None:
        low = score(ConfluenceEvidence(liquidity_usd=4_999.0), **THRESHOLDS)
        assert low.by_state(SignalState.ABSENT) == (Signal.LIQUIDITY_SUFFICIENT,)
        at = score(ConfluenceEvidence(liquidity_usd=5_000.0), **THRESHOLDS)
        assert at.by_state(SignalState.PRESENT) == (Signal.LIQUIDITY_SUFFICIENT,)

    def test_spread_is_a_ceiling(self) -> None:
        wide = score(ConfluenceEvidence(spread_pct=2.01), **THRESHOLDS)
        assert wide.by_state(SignalState.ABSENT) == (Signal.SPREAD_ACCEPTABLE,)
        tight = score(ConfluenceEvidence(spread_pct=0.1), **THRESHOLDS)
        assert tight.by_state(SignalState.PRESENT) == (Signal.SPREAD_ACCEPTABLE,)

    def test_concentration_is_a_ceiling(self) -> None:
        concentrated = score(ConfluenceEvidence(adjusted_top_holder_pct=95.0), **THRESHOLDS)
        assert concentrated.by_state(SignalState.ABSENT) == (Signal.CONCENTRATION_ACCEPTABLE,)

    def test_a_zero_value_is_evaluated_not_treated_as_missing(self) -> None:
        # Absent is not zero, and the converse also has to hold: a real measured
        # zero must be assessed rather than silently becoming UNKNOWN.
        result = score(ConfluenceEvidence(topic_net_inflow_1h_usd=0.0), **THRESHOLDS)
        verdict = next(
            v for v in result.verdicts if v.signal is Signal.NARRATIVE_ACCELERATING
        )
        assert verdict.state is SignalState.ABSENT
        assert verdict.observed == 0.0


class TestReconciliation:
    def test_every_verdict_lands_in_exactly_one_bucket(self) -> None:
        for evidence in (ConfluenceEvidence(), STRONG):
            result = score(evidence, **THRESHOLDS)
            assert result.reconciles()

    def test_every_mandate_signal_is_scored(self) -> None:
        # No parameter may be dropped: the operator's instruction was explicit.
        result = score(STRONG, **THRESHOLDS)
        assert {v.signal for v in result.verdicts} == set(Signal)

    def test_the_breakdown_is_reported_not_just_the_count(self) -> None:
        # A single number hides which term did the work. Every verdict carries
        # the value that decided it so a rejection stays attributable.
        result = score(STRONG, **THRESHOLDS)
        liquidity = next(v for v in result.verdicts if v.signal is Signal.LIQUIDITY_SUFFICIENT)
        assert liquidity.observed == 20_000.0
        assert liquidity.threshold == 5_000.0


class TestVerdictCounts:
    def test_only_present_counts(self) -> None:
        assert SignalVerdict(Signal.HIGHER_LOW, SignalState.PRESENT).counts
        assert not SignalVerdict(Signal.HIGHER_LOW, SignalState.ABSENT).counts
        assert not SignalVerdict(Signal.HIGHER_LOW, SignalState.UNKNOWN).counts

    def test_an_empty_result_reconciles_and_meets_nothing(self) -> None:
        empty = ConfluenceResult()
        assert empty.reconciles()
        assert not empty.meets(1)
        assert empty.present_count == 0


class TestDeveloperBand:
    """The signal is a band, not a ceiling, and the difference is the finding.

    Across 871 mints a developer who had **fully exited** died 7% of the time
    against 28% for one who had sold nothing: holding no supply means having
    nothing left to dump. What is dangerous is the middle -- still holding, and
    selling into the buyers the launch attracted.

    Before 2026-08-19 this module carried a comment describing exactly that band
    and a detail string reading "band gate", over an implementation that was a
    plain `_maximum` with no upper bound. The mismatch survived because
    `developer_sold_pct` was always None, so the verdict was UNKNOWN and the
    number was never read.
    """

    def verdict(self, dev_sold_pct):
        result = score(
            ConfluenceEvidence(developer_sold_pct=dev_sold_pct), **THRESHOLDS
        )
        return next(
            v for v in result.verdicts if v.signal is Signal.DEVELOPER_NOT_DISTRIBUTING
        )

    def test_a_developer_who_never_sold_is_present(self):
        assert self.verdict(0.0).state is SignalState.PRESENT

    def test_a_developer_who_fully_exited_is_present(self):
        """The state the data says is safest must not read as a failure."""
        assert self.verdict(80.0).state is SignalState.PRESENT

    def test_a_developer_selling_into_the_buyers_is_absent(self):
        assert self.verdict(25.0).state is SignalState.ABSENT

    def test_dust_is_not_distribution(self):
        """One candidate was once rejected on 1.47e-08 percent of supply."""
        assert self.verdict(1.47e-08).state is SignalState.PRESENT

    def test_unknown_stays_unknown_and_cannot_help(self):
        assert self.verdict(None).state is SignalState.UNKNOWN
        result = score(ConfluenceEvidence(developer_sold_pct=None), **THRESHOLDS)
        assert Signal.DEVELOPER_NOT_DISTRIBUTING in {
            v.signal for v in result.verdicts if v.state is SignalState.UNKNOWN
        }

    def test_the_verdict_agrees_with_the_single_source_of_truth(self):
        """The rule must not acquire a fourth implementation.

        `developer_is_distributing` is the one place this question is answered;
        it previously existed in three copies and fixing two left the third
        rejecting live candidates on floating-point dust.
        """
        from meme_flight_recorder.config import DeployerLimits
        from meme_flight_recorder.deployer import developer_is_distributing

        limits = DeployerLimits(
            maximum_dev_sell_pct=THRESHOLDS["maximum_developer_sold_pct"],
            exhausted_dev_sell_pct=THRESHOLDS["exhausted_developer_sold_pct"],
        )
        for value in (0.0, 0.5, 1.0, 1.5, 25.0, 49.9, 50.0, 80.0, 100.0):
            distributing = developer_is_distributing(value, limits)
            expected = SignalState.ABSENT if distributing else SignalState.PRESENT
            assert self.verdict(value).state is expected, value
