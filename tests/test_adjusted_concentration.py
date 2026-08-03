"""Tests for the adjusted top-holder concentration.

The gross number this replaces once rejected 25 of 25 candidates because it
counted the AMM pool. These tests pin the difference.
"""

from __future__ import annotations

from meme_flight_recorder.clusters import (
    DEFAULT_INFRASTRUCTURE_ADDRESSES,
    adjusted_top_holder_pct,
)

RAYDIUM = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j"
BURN = "1nc1nerator11111111111111111111111111111111"


def test_pool_is_excluded_from_concentration() -> None:
    """The whole point: a migrated token keeps most supply in the pool."""
    holdings = [(RAYDIUM, 900.0), ("WHALE", 50.0), ("HOLDER", 10.0)]
    assert adjusted_top_holder_pct(holdings, supply=1000.0) == 6.0


def test_gross_would_have_rejected_what_adjusted_accepts() -> None:
    holdings = [(RAYDIUM, 950.0), ("HOLDER", 20.0)]
    gross = 100.0 * (950.0 + 20.0) / 1000.0
    adjusted = adjusted_top_holder_pct(holdings, supply=1000.0)
    assert gross == 97.0
    assert adjusted == 2.0


def test_burn_address_is_excluded() -> None:
    assert adjusted_top_holder_pct([(BURN, 500.0), ("A", 100.0)], supply=1000.0) == 10.0


def test_one_owner_with_several_accounts_counts_once() -> None:
    """Owners, not token accounts. Splitting across accounts must not hide size."""
    combined = adjusted_top_holder_pct([("WHALE", 100.0), ("WHALE", 100.0)], supply=1000.0)
    assert combined == 20.0


def test_only_the_top_n_are_counted() -> None:
    holdings = [(f"H{i}", 10.0) for i in range(20)]
    assert adjusted_top_holder_pct(holdings, supply=1000.0, top_n=10) == 10.0


def test_unknown_supply_returns_none_not_zero() -> None:
    assert adjusted_top_holder_pct([("A", 10.0)], supply=0.0) is None


def test_nothing_left_after_exclusion_is_none_not_zero() -> None:
    """Zero holders is an absence of measurement, not perfect distribution."""
    assert adjusted_top_holder_pct([(RAYDIUM, 1000.0)], supply=1000.0) is None


def test_empty_holdings_are_none() -> None:
    assert adjusted_top_holder_pct([], supply=1000.0) is None


def test_extra_exclusions_are_honoured() -> None:
    holdings = [("POOL_X", 800.0), ("A", 100.0)]
    assert adjusted_top_holder_pct(holdings, supply=1000.0, excluded=["POOL_X"]) == 10.0


def test_unknown_pool_overstates_rather_than_understates() -> None:
    """An incomplete exclusion list must fail toward rejection, never admission.

    A venue whose authority is not in the list counts as a holder, so
    concentration reads high and the candidate is rejected. The opposite error --
    silently excluding a real whale -- would admit a token one wallet controls.
    """
    unknown_pool = "SomeNewAmmAuthorityNotInTheList11111111111"
    assert unknown_pool not in DEFAULT_INFRASTRUCTURE_ADDRESSES
    result = adjusted_top_holder_pct([(unknown_pool, 900.0), ("A", 10.0)], supply=1000.0)
    assert result == 91.0
