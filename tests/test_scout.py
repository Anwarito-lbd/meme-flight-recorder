"""Tests for the scout's decision logic.

`evaluate` is pure, so all of this runs without a network. The properties being
defended are the three the module claims: anything but an explicit pass blocks
entry, a passing candidate defaults to WATCH rather than ENTER, and the journal
payload carries the parameters that produced the verdict.
"""

from __future__ import annotations

from typing import ClassVar

from meme_flight_recorder.config import ScoutFilters, StrictnessLevel
from meme_flight_recorder.confluence import ConfluenceEvidence
from meme_flight_recorder.scout import (
    CandidateEvidence,
    ScoutVerdict,
    evaluate,
)

FILTERS = ScoutFilters(
    maximum_token_age_hours=48.0,
    minimum_volume_usd=5_000.0,
    minimum_liquidity_usd=5_000.0,
    minimum_market_cap_usd=20_000.0,
    maximum_market_cap_usd=5_000_000.0,
    maximum_spread_pct=2.0,
    slippage_tolerance_pct=2.0,
    maximum_daily_loss_usd=10.0,
    trade_duration="intraday",
    universe="both",
    forbid_reentry=True,
    maximum_first_candle_multiple=3.0,
)

CLOSED = StrictnessLevel(
    minimum_confluence_signals=4,
    minimum_volume_expansion=3.0,
    maximum_trades_per_session=2,
    consecutive_loss_stop=1,
    risk_pct_of_equity=1.0,
)
OPEN = StrictnessLevel(
    minimum_confluence_signals=4,
    minimum_volume_expansion=3.0,
    maximum_trades_per_session=2,
    consecutive_loss_stop=1,
    risk_pct_of_equity=1.0,
    readiness_policy=("GRADUATED",),
)

STRONG_CONFLUENCE = ConfluenceEvidence(
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


def candidate(**overrides: object) -> CandidateEvidence:
    base = {
        "mint": "MINT_A",
        "symbol": "AAA",
        "age_hours": 0.2,
        "market_cap_usd": 100_000.0,
        "first_candle_open": 1.0,
        "first_candle_high": 1.3,
        "first_candle_volume": 900.0,
        "lifecycle_state": "GRADUATED",
        "confluence": STRONG_CONFLUENCE,
    }
    base.update(overrides)
    return CandidateEvidence(**base)  # type: ignore[arg-type]


class TestFailClosedByDefault:
    def test_a_fully_clean_candidate_watches_rather_than_enters(self) -> None:
        # The shipped levels admit no lifecycle state, so clearing every gate
        # produces WATCH. Widening readiness_policy needs a study, and this is
        # the assertion that stops that happening by accident.
        decision = evaluate(candidate(), FILTERS, CLOSED, "level_1", frozenset())
        assert decision.verdict is ScoutVerdict.WATCH
        assert "admits no lifecycle state" in decision.reasons[0]

    def test_a_widened_level_can_reach_enter(self) -> None:
        decision = evaluate(candidate(), FILTERS, OPEN, "level_1", frozenset())
        assert decision.verdict is ScoutVerdict.ENTER

    def test_a_widened_level_still_checks_the_lifecycle_state(self) -> None:
        decision = evaluate(
            candidate(lifecycle_state="BONDING"), FILTERS, OPEN, "level_1", frozenset()
        )
        assert decision.verdict is ScoutVerdict.WATCH

    def test_an_empty_candidate_is_rejected(self) -> None:
        decision = evaluate(
            CandidateEvidence(mint="MINT_Z"), FILTERS, OPEN, "level_1", frozenset()
        )
        assert decision.verdict is ScoutVerdict.REJECT


class TestGatesBlock:
    def test_a_vertical_first_candle_rejects(self) -> None:
        decision = evaluate(
            candidate(first_candle_high=20.0), FILTERS, OPEN, "level_1", frozenset()
        )
        assert decision.verdict is ScoutVerdict.REJECT
        assert any("first_candle_vertical" in r for r in decision.reasons)

    def test_a_previously_held_mint_rejects(self) -> None:
        decision = evaluate(
            candidate(), FILTERS, OPEN, "level_1", frozenset({"MINT_A"})
        )
        assert decision.verdict is ScoutVerdict.REJECT
        assert any("no_reentry" in r for r in decision.reasons)

    def test_a_zero_volume_first_candle_blocks_as_unknown(self) -> None:
        # A carried-forward bar has open == high, so its span is 1.0x and it
        # would pass a naive check. It must block instead.
        decision = evaluate(
            candidate(first_candle_volume=0.0), FILTERS, OPEN, "level_1", frozenset()
        )
        assert decision.verdict is ScoutVerdict.REJECT
        assert any("not a price" in r for r in decision.reasons)

    def test_an_unreadable_position_history_blocks(self) -> None:
        decision = evaluate(candidate(), FILTERS, OPEN, "level_1", None)
        assert decision.verdict is ScoutVerdict.REJECT

    def test_an_out_of_band_market_cap_rejects(self) -> None:
        decision = evaluate(
            candidate(market_cap_usd=1_000.0), FILTERS, OPEN, "level_1", frozenset()
        )
        assert decision.verdict is ScoutVerdict.REJECT

    def test_a_token_older_than_the_cap_rejects(self) -> None:
        decision = evaluate(
            candidate(age_hours=100.0), FILTERS, OPEN, "level_1", frozenset()
        )
        assert decision.verdict is ScoutVerdict.REJECT

    def test_gates_short_circuit_before_confluence_is_spent(self) -> None:
        # The mandate: "Token age must be verified before any further analysis."
        decision = evaluate(
            candidate(age_hours=100.0, confluence=ConfluenceEvidence()),
            FILTERS,
            OPEN,
            "level_1",
            frozenset(),
        )
        assert all("confluence" not in r for r in decision.reasons)


class TestConfluenceThreshold:
    def test_too_few_present_signals_rejects_and_reports_the_count(self) -> None:
        thin = ConfluenceEvidence(liquidity_usd=20_000.0, spread_pct=1.0)
        decision = evaluate(candidate(confluence=thin), FILTERS, OPEN, "level_1", frozenset())
        assert decision.verdict is ScoutVerdict.REJECT
        reason = next(r for r in decision.reasons if r.startswith("confluence"))
        assert "2/4" in reason
        assert "unknown" in reason

    def test_unknown_signals_are_reported_alongside_the_shortfall(self) -> None:
        decision = evaluate(
            candidate(confluence=ConfluenceEvidence()), FILTERS, OPEN, "level_1", frozenset()
        )
        reason = next(r for r in decision.reasons if r.startswith("confluence"))
        assert "0/4" in reason


class TestJournalPayload:
    def test_the_payload_carries_the_parameters_that_produced_it(self) -> None:
        # A decision pointing at "the config" is not reproducible, because the
        # config can be edited between runs.
        decision = evaluate(candidate(), FILTERS, OPEN, "level_1", frozenset())
        payload = decision.to_payload(FILTERS, OPEN, candidate())
        assert payload["parameters"]["filters"]["maximum_token_age_hours"] == 48.0
        assert payload["parameters"]["level"]["minimum_confluence_signals"] == 4
        assert payload["parameters"]["level"]["readiness_policy"] == ["GRADUATED"]

    def test_the_payload_records_unknown_signals_by_name(self) -> None:
        decision = evaluate(candidate(), FILTERS, OPEN, "level_1", frozenset())
        payload = decision.to_payload(FILTERS, OPEN, candidate())
        assert payload["confluence"]["unknown"] == 5
        assert "vwap_reclaim" in payload["confluence"]["unknown_signals"]

    def test_the_payload_keeps_market_cap_and_liquidity_separate(self) -> None:
        # Market capitalisation is not exit liquidity, so both are recorded and
        # neither substitutes for the other.
        decision = evaluate(candidate(), FILTERS, OPEN, "level_1", frozenset())
        payload = decision.to_payload(FILTERS, OPEN, candidate())
        assert payload["evidence"]["market_cap_usd"] == 100_000.0
        assert payload["evidence"]["liquidity_usd"] == 20_000.0

    def test_every_gate_verdict_is_recorded_not_just_the_blocking_one(self) -> None:
        decision = evaluate(
            candidate(first_candle_high=20.0), FILTERS, OPEN, "level_1", frozenset()
        )
        payload = decision.to_payload(FILTERS, OPEN, candidate())
        assert {g["gate"] for g in payload["gates"]} == {
            "token_age",
            "no_reentry",
            "first_candle_vertical",
            "market_cap_band",
        }


class TestTheModelCannotGate:
    """The invariant that makes an LLM safe to have in this system at all.

    `CLAUDE.md` requires gates be computed. A model asked the same question twice
    can answer differently, so if any of its output reached a verdict there would
    be no way to tell whether a verdict changed because the market moved or
    because the model did. These fields are journalled so a later study can
    measure whether the model was worth listening to -- and that study is the
    only route by which its judgement could ever earn a place in the entry path.
    """

    MODEL_FIELDS: ClassVar[dict[str, object]] = {
        "narrative_text": "Glitter TikTok Meme, Viral Fragrance",
        "narrative_sentiment": "positive",
        "narrative_backend": "ollama:qwen2.5:3b",
        "model_says_accelerating": True,
        "twitter_handle": "Devilantesol",
        "twitter_followers": 92_326_827,
    }

    def test_no_model_field_changes_a_verdict(self) -> None:
        for level, name in ((CLOSED, "level_1"), (OPEN, "level_1")):
            plain = evaluate(candidate(), FILTERS, level, name, frozenset())
            loud = evaluate(
                candidate(**self.MODEL_FIELDS), FILTERS, level, name, frozenset()
            )
            assert loud.verdict is plain.verdict
            assert loud.reasons == plain.reasons
            assert loud.confluence.present_count == plain.confluence.present_count
            assert loud.confluence.unknown_count == plain.confluence.unknown_count

    def test_the_most_enthusiastic_model_cannot_rescue_a_rejection(self) -> None:
        """A candidate the gates reject stays rejected however the model reads it."""
        doomed = candidate(age_hours=99.0, **self.MODEL_FIELDS)
        decision = evaluate(doomed, FILTERS, OPEN, "level_1", frozenset())
        assert decision.verdict is ScoutVerdict.REJECT

    def test_model_output_is_journalled_so_it_can_be_measured(self) -> None:
        decision = evaluate(
            candidate(**self.MODEL_FIELDS), FILTERS, OPEN, "level_1", frozenset()
        )
        payload = decision.to_payload(FILTERS, OPEN, candidate(**self.MODEL_FIELDS))
        evidence = payload["evidence"]
        assert evidence["model_says_accelerating"] is True
        assert evidence["narrative_sentiment"] == "positive"
        assert evidence["twitter_followers"] == 92_326_827
