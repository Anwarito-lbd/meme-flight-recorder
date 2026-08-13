"""Stream filtering, which is the cost model of the whole pipeline.

At ~500-2,500 events/sec, the predicate deciding what gets an RPC call *is* the
budget. These tests pin that predicate and the counting around it, because a
filter that quietly widens turns a free feed into an unaffordable one.
"""

from __future__ import annotations

from meme_flight_recorder.stream_discovery import (
    DiscoveryStats,
    MintRegistry,
    StreamEvent,
    instructions_in,
    parse_notification,
)
from meme_flight_recorder.venues import Venue


def logs(*names: str) -> list[str]:
    return [f"Program log: Instruction: {name}" for name in names]


def notification(signature: str, slot: int, names: list[str], subscription: int = 1) -> dict:
    return {
        "params": {
            "subscription": subscription,
            "result": {
                "context": {"slot": slot},
                "value": {"signature": signature, "logs": logs(*names), "err": None},
            },
        }
    }


def test_instruction_names_are_read_from_the_log_lines() -> None:
    assert instructions_in(logs("Create", "Buy")) == ["Create", "Buy"]
    assert instructions_in([]) == []
    assert instructions_in(["Program log: something else"]) == []


def test_only_creations_justify_a_fetch() -> None:
    """The cost gate. Trades are ~95% of traffic and are counted, not fetched."""
    creation = StreamEvent("s", 1, Venue.PUMP_FUN, ("Create",))
    trade = StreamEvent("s", 1, Venue.PUMP_FUN, ("Buy",))
    noise = StreamEvent("s", 1, Venue.PUMP_FUN, ("GetFees",))
    assert creation.worth_fetching
    assert not trade.worth_fetching
    assert not noise.worth_fetching


def test_trades_are_classified_not_discarded() -> None:
    """First buys are the first-buyer signal; losing them here loses it forever."""
    trade = StreamEvent("s", 1, Venue.PUMP_FUN, ("Buy",))
    assert trade.is_trade
    assert not trade.is_creation


def test_venue_comes_from_the_subscription_not_the_log_text() -> None:
    """Scanning logs for a program id mislabelled 13,771 of 25,671 events."""
    message = notification("sig", 100, ["Buy"], subscription=7)
    event = parse_notification(message, {7: Venue.PUMP_SWAP})
    assert event is not None
    assert event.venue is Venue.PUMP_SWAP


def test_an_unmapped_subscription_is_unknown_not_guessed() -> None:
    event = parse_notification(notification("sig", 100, ["Buy"], subscription=99), {7: Venue.PUMP_FUN})
    assert event is not None
    assert event.venue is Venue.UNKNOWN


def test_a_message_without_signature_or_slot_is_not_an_event() -> None:
    assert parse_notification({"params": {"result": {"value": {}}}}) is None
    assert parse_notification({}) is None


def test_buckets_reconcile_to_the_parsed_total() -> None:
    stats = DiscoveryStats()
    for names in (["Create"], ["Buy"], ["Sell"], ["GetFees"], ["TransferChecked"]):
        event = parse_notification(notification("s", 1, names))
        assert event is not None
        stats.observe(event)
    assert stats.parsed == 5
    assert stats.creations == 1
    assert stats.trades == 2
    assert stats.other == 2
    assert stats.reconciles()


def test_a_mint_is_registered_once_and_repeats_are_counted() -> None:
    """Dedup before enrichment: forty sightings must cost one enrichment."""
    registry = MintRegistry()
    assert registry.add("MINT_A")
    assert not registry.add("MINT_A")
    assert not registry.add("MINT_A")
    assert len(registry) == 1
    assert registry.duplicates == 2
