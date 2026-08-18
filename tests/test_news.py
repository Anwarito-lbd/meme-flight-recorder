"""Tests for the sentiment provider.

Nothing here touches a network or a model. The property under test is the one
the module exists for: an absence of articles never becomes a neutral reading,
and the three distinct kinds of absence stay distinguishable.
"""

from __future__ import annotations

import pytest

from meme_flight_recorder.providers.news import (
    Sentiment,
    SentimentReading,
    classify,
    parse_label,
    reading_for_articles,
)


class TestAbsenceIsNotNeutral:
    def test_no_articles_is_unknown_not_neutral(self) -> None:
        # The whole point. A token four minutes old has nothing written about it,
        # and scoring that as "neutral sentiment" would let an absence of
        # evidence enter the confluence count as a passed check.
        reading = reading_for_articles([], [], "test")
        assert reading.sentiment is Sentiment.UNKNOWN
        assert reading.article_count == 0
        assert not reading.is_evidence

    def test_unreachable_provider_is_distinguishable_from_no_articles(self) -> None:
        # None means "we could not look"; 0 means "we looked and found nothing".
        # Both are UNKNOWN, but they are different facts and the count says which.
        unreachable = reading_for_articles(None, [], "test")
        empty = reading_for_articles([], [], "test")
        assert unreachable.article_count is None
        assert empty.article_count == 0
        assert unreachable.sentiment is empty.sentiment is Sentiment.UNKNOWN

    def test_articles_that_cannot_be_classified_stay_unknown(self) -> None:
        reading = reading_for_articles(["a", "b"], [Sentiment.UNKNOWN, Sentiment.UNKNOWN], "t")
        assert reading.sentiment is Sentiment.UNKNOWN
        assert reading.article_count == 2

    def test_a_tie_is_unknown_rather_than_a_coin_flip(self) -> None:
        reading = reading_for_articles(
            ["a", "b"], [Sentiment.POSITIVE, Sentiment.NEGATIVE], "t"
        )
        assert reading.sentiment is Sentiment.UNKNOWN
        assert "tied" in (reading.detail or "")

    def test_only_a_real_classification_counts_as_evidence(self) -> None:
        assert SentimentReading(Sentiment.POSITIVE, 3, "t").is_evidence
        assert SentimentReading(Sentiment.NEGATIVE, 3, "t").is_evidence
        assert SentimentReading(Sentiment.NEUTRAL, 3, "t").is_evidence
        assert not SentimentReading(Sentiment.UNKNOWN, 3, "t").is_evidence


class TestMajority:
    def test_majority_wins(self) -> None:
        reading = reading_for_articles(
            ["a", "b", "c"],
            [Sentiment.POSITIVE, Sentiment.POSITIVE, Sentiment.NEGATIVE],
            "t",
        )
        assert reading.sentiment is Sentiment.POSITIVE
        assert reading.article_count == 3

    def test_unclassifiable_articles_do_not_dilute_the_majority(self) -> None:
        # An UNKNOWN is not a vote for neutral; it is not a vote at all.
        reading = reading_for_articles(
            ["a", "b", "c"],
            [Sentiment.NEGATIVE, Sentiment.UNKNOWN, Sentiment.UNKNOWN],
            "t",
        )
        assert reading.sentiment is Sentiment.NEGATIVE
        assert reading.article_count == 3


class TestParseLabel:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("POSITIVE", Sentiment.POSITIVE),
            ("negative", Sentiment.NEGATIVE),
            ("  Neutral \n", Sentiment.NEUTRAL),
            ("POSITIVE — strong interest", Sentiment.POSITIVE),
        ],
    )
    def test_recognised_labels(self, raw: str, expected: Sentiment) -> None:
        assert parse_label(raw) is expected

    @pytest.mark.parametrize("raw", ["", "   ", "I think it might be positive", "maybe", "42"])
    def test_anything_unrecognised_is_unknown(self, raw: str) -> None:
        # Guessing at intent would manufacture a signal out of a malformed answer.
        assert parse_label(raw) is Sentiment.UNKNOWN

    def test_none_is_unknown(self) -> None:
        assert parse_label(None) is Sentiment.UNKNOWN  # type: ignore[arg-type]


class TestBackendSelection:
    def test_default_backend_is_none_and_returns_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An operator who configured nothing gets UNKNOWN, not a silent dependency
        # on a model that may not be installed.
        monkeypatch.delenv("SCOUT_SENTIMENT_BACKEND", raising=False)
        reading = classify("AAA", ["some article"])
        assert reading.sentiment is Sentiment.UNKNOWN
        assert reading.backend == "none"
        assert "no sentiment backend configured" in (reading.detail or "")

    def test_no_articles_short_circuits_before_any_model_call(self) -> None:
        # Must not reach the network: there is nothing to classify.
        reading = classify("AAA", [], backend="ollama")
        assert reading.sentiment is Sentiment.UNKNOWN
        assert reading.article_count == 0

    def test_unreachable_articles_short_circuit_too(self) -> None:
        reading = classify("AAA", None, backend="ollama")
        assert reading.article_count is None

    def test_hosted_backend_is_declared_unimplemented_rather_than_faked(self) -> None:
        reading = classify("AAA", ["article"], backend="anthropic")
        assert reading.sentiment is Sentiment.UNKNOWN
        assert "not implemented" in (reading.detail or "")

    def test_an_unknown_backend_name_fails_closed(self) -> None:
        reading = classify("AAA", ["article"], backend="gpt-whatever")
        assert reading.sentiment is Sentiment.UNKNOWN
        assert "unknown backend" in (reading.detail or "")

    def test_the_backend_is_recorded_on_the_reading(self) -> None:
        # So the journal shows which model produced a label, not just the label.
        assert classify("AAA", ["a"], backend="anthropic").backend == "anthropic"
