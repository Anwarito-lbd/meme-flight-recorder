"""Tests for the Telegram adapter.

Nothing here touches the network. The properties under test are that sending is
off by default, that a missing credential is distinguishable from a failed send,
and that the message never summarises away the unknown count.
"""

from __future__ import annotations

import pytest

from meme_flight_recorder.notify import (
    DeliveryState,
    configured,
    format_decision,
    send,
)


class TestSendingIsOffByDefault:
    def test_send_does_nothing_unless_the_caller_enables_it(self) -> None:
        # Outbound messages are a side effect on the world, so the default is off
        # and a study importing this module cannot message anyone.
        result = send("hello")
        assert result.state is DeliveryState.DISABLED
        assert not result.delivered

    def test_a_missing_token_is_not_configured_rather_than_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Absence of credentials is not a delivery failure. Counting it as one
        # would report a network problem where there is only an unset variable.
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        result = send("hello", enabled=True)
        assert result.state is DeliveryState.NOT_CONFIGURED
        assert "TELEGRAM_BOT_TOKEN" in (result.detail or "")

    def test_a_missing_chat_id_names_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        result = send("hello", enabled=True)
        assert result.state is DeliveryState.NOT_CONFIGURED
        assert "TELEGRAM_CHAT_ID" in (result.detail or "")

    def test_configured_requires_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert not configured()
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        assert configured()


class TestMessageContent:
    def test_the_unknown_count_is_in_the_headline(self) -> None:
        # "3/2 signals" reads as though the rest was checked and found wanting.
        # "3/2, 11 unknown" shows most of the picture was never available.
        message = format_decision(
            "PATE", "MINT_ABC", "watch", present=3, required=2, unknown=11, level="level_3"
        )
        assert "3/2" in message
        assert "11 unknown" in message

    def test_the_full_mint_is_included_not_just_the_ticker(self) -> None:
        message = format_decision(
            "CATE", "MINT_ABC", "watch", present=3, required=2, unknown=1, level="level_1"
        )
        assert "MINT_ABC" in message

    def test_a_non_ascii_symbol_does_not_break_the_message(self) -> None:
        # Meme symbols carry emoji and non-Latin scripts; one crashed a live scan.
        message = format_decision(
            "\U0001f680中", "MINT_ABC", "watch", 3, 2, 1, "level_1"
        )
        assert message.isascii()

    def test_an_empty_symbol_still_produces_a_message(self) -> None:
        message = format_decision("", "MINT_ABC", "watch", 3, 2, 1, "level_1")
        assert "WATCH" in message

    def test_every_message_says_it_is_paper(self) -> None:
        message = format_decision("A", "M", "enter", 4, 4, 0, "level_1")
        assert "PAPER ONLY" in message

    def test_the_position_size_is_shown_when_supplied(self) -> None:
        message = format_decision("A", "M", "enter", 4, 4, 0, "level_1", position_usd=10.0)
        assert "$10.00" in message

    def test_reasons_are_capped_so_one_alert_stays_readable(self) -> None:
        reasons = tuple(f"reason_{n}" for n in range(10))
        message = format_decision("A", "M", "reject", 0, 4, 0, "level_1", reasons=reasons)
        assert message.count("- reason_") == 3
