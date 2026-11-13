"""Tests for the GoPlus token-security adapter.

The load-bearing tests are the ones that keep missing evidence from reading as
safety: unknown must never become sellable, and an unadjustable concentration
figure must never be served under the name "adjusted".
"""

from __future__ import annotations

from meme_flight_recorder.providers.goplus import TokenSecurity


def security(**overrides: object) -> TokenSecurity:
    defaults: dict[str, object] = {"mint": "MINT_A"}
    defaults.update(overrides)
    return TokenSecurity(**defaults)  # type: ignore[arg-type]


def holder(percent: str, tag: str = "", locked: int = 0) -> dict[str, object]:
    return {"account": "A", "percent": percent, "tag": tag, "is_locked": locked}


def test_no_signals_reported_means_sellable_is_unknown() -> None:
    """Absent is not safe. The gates fail closed and depend on this."""
    assert security().sellable is None


def test_non_transferable_blocks_selling() -> None:
    assert security(non_transferable=True).sellable is False


def test_freezable_blocks_selling() -> None:
    assert security(freezable=True).sellable is False


def test_transfer_hook_blocks_selling() -> None:
    assert security(has_transfer_hook=True).sellable is False


def test_all_clear_signals_are_sellable() -> None:
    result = security(non_transferable=False, freezable=False, has_transfer_hook=False)
    assert result.sellable is True


def test_one_known_clear_signal_is_enough_to_answer() -> None:
    """A partial report still answers, because a False is a real observation."""
    assert security(freezable=False).sellable is True


def test_top10_is_summed_from_holder_percentages() -> None:
    result = security(holders=(holder("0.5"), holder("0.25")))
    assert result.top10_holder_pct == 75.0


def test_top10_is_none_without_holders() -> None:
    assert security().top10_holder_pct is None


def test_adjusted_is_none_when_nothing_is_labelled() -> None:
    """The behaviour measured against 14 real mints.

    GoPlus returned an empty tag on every holder and no lp_holders, so there is
    nothing to exclude. Returning the gross figure as "adjusted" would be a
    weaker number wearing a stronger name.
    """
    result = security(holders=(holder("0.9"), holder("0.05")))
    assert result.top10_holder_pct == 95.0
    assert result.adjusted_top10_holder_pct is None


def test_adjusted_excludes_tagged_infrastructure() -> None:
    result = security(holders=(holder("0.9", tag="pool"), holder("0.05")))
    assert result.adjusted_top10_holder_pct == 5.0


def test_adjusted_excludes_locked_supply() -> None:
    result = security(holders=(holder("0.9", locked=1), holder("0.05")))
    assert result.adjusted_top10_holder_pct == 5.0


def test_only_the_first_ten_holders_count() -> None:
    result = security(holders=tuple(holder("0.05") for _ in range(20)))
    assert result.top10_holder_pct == 50.0
