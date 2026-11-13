"""Venue decoding, tested against transactions saved from real mainnet.

The fixtures in `tests/fixtures/venues/` were captured from live mainnet by
`scripts/build_system_matrix.py`'s sibling probe, not hand-written. That matters:
a hand-written fixture encodes whatever the author believed the shape was, and
this project has already had 326 tests pass against a fixture that agreed with
the code and disagreed with the endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meme_flight_recorder.venues import (
    PROGRAM_IDS,
    WSOL_MINT,
    Action,
    DecodeStats,
    Venue,
    base58_decode,
    decode_transaction,
    discriminator,
)

FIXTURES = Path(__file__).parent / "fixtures" / "venues"


def fixture_files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json")) if FIXTURES.is_dir() else []


# ------------------------------------------------------------------ base58


def test_base58_round_trips_a_known_value() -> None:
    # "1" is the base58 encoding of a single zero byte.
    assert base58_decode("1") == b"\x00"
    assert base58_decode("") == b""


def test_leading_zero_bytes_are_preserved() -> None:
    """A dropped leading zero corrupts any discriminator starting with 0x00."""
    assert base58_decode("11") == b"\x00\x00"
    assert base58_decode("112") == b"\x00\x00\x01"


def test_invalid_characters_are_refused_not_ignored() -> None:
    with pytest.raises(ValueError, match="invalid base58"):
        base58_decode("0OIl")


# ---------------------------------------------------------- discriminators


def test_discriminators_are_derived_not_pasted() -> None:
    """Anchor's rule: first 8 bytes of sha256("global:<name>")."""
    assert discriminator("create").hex() == "181ec828051c0777"
    assert discriminator("buy").hex() == "66063d1201daebea"
    assert discriminator("sell").hex() == "33e685a4017f83ad"
    assert len(discriminator("anything")) == 8


def test_every_registered_program_maps_to_a_venue() -> None:
    assert all(isinstance(venue, Venue) for venue in PROGRAM_IDS.values())
    assert len(set(PROGRAM_IDS)) == len(PROGRAM_IDS)


# -------------------------------------------------------- real transactions


@pytest.mark.skipif(not fixture_files(), reason="no captured mainnet fixtures")
def test_real_transactions_decode_without_raising() -> None:
    for path in fixture_files():
        decoded = decode_transaction(json.loads(path.read_text(encoding="utf-8")))
        assert decoded.signature
        assert decoded.slot > 0


@pytest.mark.skipif(not fixture_files(), reason="no captured mainnet fixtures")
def test_identity_is_slot_index_signature() -> None:
    for path in fixture_files():
        decoded = decode_transaction(json.loads(path.read_text(encoding="utf-8")))
        slot, index, signature = decoded.identity
        assert slot == decoded.slot
        assert index == decoded.transaction_index
        assert signature == decoded.signature


@pytest.mark.skipif(not fixture_files(), reason="no captured mainnet fixtures")
def test_a_real_transaction_resolves_a_mint_that_is_not_wrapped_sol() -> None:
    """Wrapped SOL is the quote leg of nearly every swap and is never the token."""
    resolved = [
        decode_transaction(json.loads(path.read_text(encoding="utf-8"))).primary_mint
        for path in fixture_files()
    ]
    found = [mint for mint in resolved if mint]
    assert found, "no fixture resolved a mint"
    assert all(mint != WSOL_MINT for mint in found)


@pytest.mark.skipif(not fixture_files(), reason="no captured mainnet fixtures")
def test_failed_transactions_are_decoded_rather_than_skipped() -> None:
    """A revert still tells us somebody tried to buy."""
    stats = DecodeStats()
    for path in fixture_files():
        stats.observe(decode_transaction(json.loads(path.read_text(encoding="utf-8"))))
    assert stats.seen == len(fixture_files())
    # Every seen transaction is accounted for, succeeded or not.
    assert stats.failed_transactions <= stats.seen


# --------------------------------------------------------------- synthetic


def synthetic(program: str, data: str, mint: str, ok: bool = True) -> dict:
    return {
        "slot": 1,
        "blockTime": 1,
        "transaction": {
            "signatures": ["SIG"],
            "message": {
                "accountKeys": [{"pubkey": "FEE"}, {"pubkey": mint}],
                "instructions": [
                    {"programId": program, "data": data, "accounts": ["FEE", mint]}
                ],
            },
        },
        "meta": {
            "err": None if ok else {"InstructionError": [0, {"Custom": 6001}]},
            "postTokenBalances": [{"mint": mint}],
            "preBalances": [10, 0],
            "postBalances": [5, 0],
        },
    }


def test_an_unknown_discriminator_classifies_as_unknown_not_as_a_guess() -> None:
    transaction = synthetic("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "2", "MINTAAA")
    decoded = decode_transaction(transaction)
    assert decoded.instructions[0].venue is Venue.PUMP_FUN
    assert decoded.instructions[0].action is Action.UNKNOWN


def test_a_failed_transaction_keeps_its_error_and_its_mint() -> None:
    transaction = synthetic(
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "2", "MINTAAA", ok=False
    )
    decoded = decode_transaction(transaction)
    assert not decoded.succeeded
    assert decoded.error
    assert decoded.primary_mint == "MINTAAA"


def test_an_unregistered_program_produces_no_instructions() -> None:
    decoded = decode_transaction(synthetic("SomeOtherProgram1111111111", "2", "MINTAAA"))
    assert decoded.instructions == ()
    # The mint is still recoverable from the runtime balance records.
    assert decoded.primary_mint == "MINTAAA"
