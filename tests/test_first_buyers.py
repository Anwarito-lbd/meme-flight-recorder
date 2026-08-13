"""First-buyer features, and the absent-is-not-zero rule they depend on.

These features describe an adversarial population -- a launch's first buyers are
frequently one actor in many wallets -- so the tests are mostly about refusing
to flatter it: an unresolved funder must not read as "no shared funding", and a
missing wallet fact must not read as a clean one.
"""

from __future__ import annotations

from meme_flight_recorder.first_buyers import Buy, WalletFacts, compute


def buys(*pairs: tuple[str, int]) -> list[Buy]:
    return [
        Buy(wallet=wallet, slot=slot, order=index, seconds_since_first=float(index))
        for index, (wallet, slot) in enumerate(pairs)
    ]


def test_no_buys_returns_unknown_not_zero() -> None:
    features = compute("M", [])
    assert features.buyers_considered == 0
    assert features.independent_wallet_count is None
    assert features.shared_funder_pct is None
    assert features.same_slot_concentration_pct is None


def test_same_slot_concentration_detects_a_bundle() -> None:
    """Several 'different' buyers in one slot is one batch, not a crowd."""
    bundled = compute("M", buys(("A", 100), ("B", 100), ("C", 100), ("D", 101)))
    spread = compute("M", buys(("A", 100), ("B", 101), ("C", 102), ("D", 103)))
    assert bundled.same_slot_concentration_pct == 75.0
    assert spread.same_slot_concentration_pct == 25.0


def test_same_slot_concentration_needs_no_extra_data() -> None:
    """It is the cheapest clustering signal available, so it must always work."""
    features = compute("M", buys(("A", 1), ("B", 1)))
    assert features.same_slot_concentration_pct is not None


def test_shared_funder_is_unknown_without_funder_data() -> None:
    """The conservative direction: never manufacture independence."""
    features = compute("M", buys(("A", 1), ("B", 2)))
    assert features.shared_funder_pct is None
    assert features.independent_wallet_count is None
    assert features.coverage["funder"] == 0.0


def test_shared_funder_collapses_sibling_wallets() -> None:
    facts = {
        "A": WalletFacts(funder="F1"),
        "B": WalletFacts(funder="F1"),
        "C": WalletFacts(funder="F2"),
    }
    features = compute("M", buys(("A", 1), ("B", 2), ("C", 3)), facts)
    # A and B are one actor, so two independent actors, not three.
    assert features.independent_wallet_count == 2
    assert features.shared_funder_pct is not None
    assert abs(features.shared_funder_pct - 66.6667) < 0.01
    assert features.coverage["funder"] == 100.0


def test_percentages_are_computed_over_known_wallets_and_coverage_is_reported() -> None:
    """A verdict must state its own scope, as the wallet study forced elsewhere."""
    facts = {"A": WalletFacts(creator_linked=True)}
    features = compute("M", buys(("A", 1), ("B", 2), ("C", 3)), facts)
    assert features.creator_linked_pct == 100.0
    # ...but only one of three wallets was resolvable, and that is stated.
    assert abs(features.coverage["creator_linked"] - 33.3333) < 0.01


def test_early_sell_counts_first_buyers_who_left() -> None:
    features = compute("M", buys(("A", 1), ("B", 2), ("C", 3), ("D", 4)), sellers={"A", "B"})
    assert features.early_sell_pct == 50.0


def test_qualified_within_windows_respect_the_clock() -> None:
    facts = {"A": WalletFacts(qualified=True), "C": WalletFacts(qualified=True)}
    rows = [
        Buy(wallet="A", slot=1, order=0, seconds_since_first=5.0),
        Buy(wallet="B", slot=2, order=1, seconds_since_first=20.0),
        Buy(wallet="C", slot=3, order=2, seconds_since_first=120.0),
    ]
    features = compute("M", rows, facts)
    assert features.qualified_within_30s == 1
    assert features.qualified_within_1m == 1
    assert features.qualified_within_5m == 2


def test_qualified_windows_are_unknown_without_timestamps() -> None:
    rows = [Buy(wallet="A", slot=1, order=0)]
    features = compute("M", rows, {"A": WalletFacts(qualified=True)})
    assert features.qualified_within_30s is None


def test_repeated_wallets_count_once_as_actors() -> None:
    features = compute("M", buys(("A", 1), ("A", 2), ("A", 3), ("B", 4)))
    assert features.buyers_considered == 4
    assert features.unique_wallets == 2
