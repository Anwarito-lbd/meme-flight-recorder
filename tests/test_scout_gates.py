"""Tests for the mandate's two added hard gates.

The property every test here defends: UNKNOWN is not a pass. Each gate has at
least one input that looks benign and must not clear it.
"""

from __future__ import annotations

from meme_flight_recorder.scout_gates import (
    GateOutcome,
    blocking,
    check_first_candle_not_vertical,
    check_market_cap_band,
    check_no_reentry,
    check_token_age,
)


class TestNoReentry:
    def test_a_previously_held_mint_is_rejected(self) -> None:
        verdict = check_no_reentry("MINT_A", frozenset({"MINT_A", "MINT_B"}))
        assert verdict.outcome is GateOutcome.REJECT
        assert verdict.blocks_entry

    def test_an_unseen_mint_passes(self) -> None:
        verdict = check_no_reentry("MINT_C", frozenset({"MINT_A"}))
        assert verdict.outcome is GateOutcome.PASS
        assert not verdict.blocks_entry

    def test_an_empty_history_is_not_the_same_as_an_unreadable_one(self) -> None:
        # An empty set is a measurement: nothing was held. None means the ledger
        # could not be read, and reading that as "nothing was held" would be the
        # most permissive interpretation of the least certain input.
        assert check_no_reentry("MINT_A", frozenset()).outcome is GateOutcome.PASS
        assert check_no_reentry("MINT_A", None).outcome is GateOutcome.UNKNOWN

    def test_an_unreadable_history_blocks_entry(self) -> None:
        assert check_no_reentry("MINT_A", None).blocks_entry

    def test_the_gate_can_be_disabled_by_config(self) -> None:
        verdict = check_no_reentry("MINT_A", frozenset({"MINT_A"}), enabled=False)
        assert verdict.outcome is GateOutcome.PASS

    def test_identity_is_the_mint_not_the_symbol(self) -> None:
        # Five CATE mints appeared in one night. A symbol-keyed gate would reject
        # unrelated tokens and admit the farm token this exists to stop.
        held = frozenset({"CATE_MINT_1"})
        assert check_no_reentry("CATE_MINT_2", held).outcome is GateOutcome.PASS


class TestFirstCandleNotVertical:
    def test_a_vertical_first_minute_is_rejected(self) -> None:
        verdict = check_first_candle_not_vertical(1.0, 12.0, 5_000.0, 3.0)
        assert verdict.outcome is GateOutcome.REJECT
        assert verdict.observed == 12.0

    def test_a_normal_first_minute_passes(self) -> None:
        verdict = check_first_candle_not_vertical(1.0, 1.4, 5_000.0, 3.0)
        assert verdict.outcome is GateOutcome.PASS

    def test_a_zero_volume_first_candle_is_unknown_not_a_pass(self) -> None:
        # This is the important one. A zero-volume bar carries the last close
        # forward, so open == high and the span is exactly 1.0x -- it would sail
        # through a naive check. Birdeye returned 963 such candles for one rugged
        # token, still quoting sixteen hours after its final trade.
        verdict = check_first_candle_not_vertical(1.0, 1.0, 0.0, 3.0)
        assert verdict.outcome is GateOutcome.UNKNOWN
        assert verdict.blocks_entry
        assert "not a price" in verdict.reason

    def test_missing_evidence_is_unknown(self) -> None:
        assert check_first_candle_not_vertical(None, None, None, 3.0).outcome is (
            GateOutcome.UNKNOWN
        )

    def test_a_non_positive_open_cannot_form_a_ratio(self) -> None:
        verdict = check_first_candle_not_vertical(0.0, 5.0, 100.0, 3.0)
        assert verdict.outcome is GateOutcome.UNKNOWN

    def test_the_boundary_passes_rather_than_rejects(self) -> None:
        # Exactly at the limit is within it; the gate rejects only beyond.
        assert check_first_candle_not_vertical(1.0, 3.0, 100.0, 3.0).outcome is GateOutcome.PASS


class TestTokenAge:
    def test_an_older_token_is_rejected(self) -> None:
        assert check_token_age(72.0, 48.0).outcome is GateOutcome.REJECT

    def test_a_young_token_passes(self) -> None:
        assert check_token_age(0.1, 48.0).outcome is GateOutcome.PASS

    def test_unknown_age_is_not_young(self) -> None:
        verdict = check_token_age(None, 48.0)
        assert verdict.outcome is GateOutcome.UNKNOWN
        assert verdict.blocks_entry

    def test_the_cap_is_non_binding_on_this_populations_measured_ages(self) -> None:
        # p90 of the rejected cohort is 7.2 minutes over 18,942 mints, so the
        # 48-hour cap admits everything the feeds return. Asserted so the
        # non-bindingness is a fact in the suite rather than a claim in a comment.
        p90_hours = 7.2 / 60.0
        assert check_token_age(p90_hours, 48.0).outcome is GateOutcome.PASS


class TestMarketCapBand:
    def test_below_and_above_the_band_are_both_rejected(self) -> None:
        assert check_market_cap_band(1_000.0, 20_000.0, 5_000_000.0).outcome is (
            GateOutcome.REJECT
        )
        assert check_market_cap_band(9_000_000.0, 20_000.0, 5_000_000.0).outcome is (
            GateOutcome.REJECT
        )

    def test_inside_the_band_passes(self) -> None:
        assert check_market_cap_band(100_000.0, 20_000.0, 5_000_000.0).outcome is (
            GateOutcome.PASS
        )

    def test_unknown_market_cap_blocks(self) -> None:
        assert check_market_cap_band(None, 20_000.0, 5_000_000.0).blocks_entry


class TestBlocking:
    def test_it_collects_rejections_and_unknowns_together(self) -> None:
        verdicts = (
            check_no_reentry("A", frozenset()),
            check_token_age(None, 48.0),
            check_market_cap_band(1.0, 20_000.0, 5_000_000.0),
        )
        blocked = blocking(verdicts)
        assert {v.gate for v in blocked} == {"token_age", "market_cap_band"}

    def test_all_passing_blocks_nothing(self) -> None:
        verdicts = (
            check_no_reentry("A", frozenset()),
            check_token_age(1.0, 48.0),
        )
        assert blocking(verdicts) == ()
