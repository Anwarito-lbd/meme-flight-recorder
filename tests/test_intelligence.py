"""Social, narrative, trader and rotation invariants.

These modules operate on adversarial data, so the tests are mostly about what
must *not* happen: a ticker must never become an identity, a forwarded message
must never count as a second voice, sibling wallets must never count as
agreement, and a missing measurement must never become a zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from meme_flight_recorder.capital_rotation import aggregate, find_rotations, net_flow
from meme_flight_recorder.narratives import (
    NarrativeState,
    compute_features,
    resolve_to_mint,
)
from meme_flight_recorder.social_intelligence import (
    SocialEvent,
    SocialIngest,
    SocialSource,
    build_event,
    extract_mints,
    spam_probability,
)
from meme_flight_recorder.tracked_traders import (
    ObservedTrade,
    TrackedWallet,
    TraderTier,
    find_confluence,
    independent_subset,
    profile_trader,
)
from meme_flight_recorder.trader_influence import measure_reaction, profile_influence

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
MINT = "3jKtWLTa8hhDzC1N3gAbU7aFMJFw5jTej3Avruavpump"
OTHER = "UqrVTrZ6W9DjJCpWERbud5CaqDv574C7v4EBWQypump"


# ------------------------------------------------------------------- social


def test_a_mint_is_extracted_only_when_stated() -> None:
    assert extract_mints(f"buying {MINT} now") == (MINT,)
    # A ticker is not an identity: five CATE mints appeared in one night.
    assert extract_mints("$CATE to the moon") == ()


def test_known_mint_restriction_removes_lookalike_addresses() -> None:
    text = f"{MINT} and 5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"
    assert extract_mints(text, known={MINT}) == (MINT,)


def test_symbols_are_kept_but_are_not_identity() -> None:
    event = build_event(SocialSource.TELEGRAM, "a", "$WIF and $BONK pumping")
    assert set(event.symbols) == {"WIF", "BONK"}
    assert event.mint is None


def test_forwarded_messages_collapse_to_one_voice() -> None:
    """Forty channels relaying one post is one mention, not forty."""
    ingest = SocialIngest()
    for channel in range(5):
        ingest.add(
            SocialEvent(
                source=SocialSource.TELEGRAM,
                author=f"channel_{channel}",
                text="same call",
                observed_at=NOW,
                forwarded_from="origin_channel",
                external_id="post-1",
            )
        )
    assert len(ingest.events) == 1
    assert ingest.duplicates == 4
    reconciliation = ingest.reconciliation()
    assert reconciliation["accepted"] + reconciliation["duplicates"] == reconciliation["total_offered"]


def test_promotional_copy_scores_as_spam() -> None:
    assert spam_probability("guaranteed 100x dont miss ape in") > 0.4
    assert spam_probability("liquidity looks thin, watching for now") < 0.2


def test_shotgunning_tickers_scores_as_spam() -> None:
    assert spam_probability("$A $B $C $D $E") >= 0.2


# ---------------------------------------------------------------- narrative


def events_at(times: list[datetime], authors: list[str]) -> list[SocialEvent]:
    return [
        SocialEvent(
            source=SocialSource.TELEGRAM,
            author=author,
            text="something",
            observed_at=moment,
            published_at=moment,
            author_credibility=0.9,
            spam_probability=0.0,
        )
        for moment, author in zip(times, authors, strict=True)
    ]


def test_features_never_read_events_after_the_cutoff() -> None:
    """Point-in-time, which is what makes these usable in the replay."""
    times = [NOW - timedelta(minutes=5), NOW + timedelta(minutes=5)]
    features = compute_features("n1", events_at(times, ["a", "b"]), NOW)
    assert features.mentions == 1


def test_an_empty_narrative_is_discovered_not_crashing() -> None:
    features = compute_features("n1", [], NOW)
    assert features.state is NarrativeState.DISCOVERED
    assert features.mentions == 0
    # Absent is not zero: unmeasurable rates are None.
    assert features.attention_velocity is None


def test_silence_decays_then_kills_a_narrative() -> None:
    old = [NOW - timedelta(minutes=400)]
    features = compute_features("n1", events_at(old, ["a"]), NOW)
    assert features.state is NarrativeState.DEAD

    quieter = [NOW - timedelta(minutes=90)]
    assert compute_features("n1", events_at(quieter, ["a"]), NOW).state is (
        NarrativeState.DECAYING
    )


def test_saturation_is_unknown_without_a_universe() -> None:
    features = compute_features("n1", events_at([NOW], ["a"]), NOW)
    assert features.saturation is None


def test_narrative_resolves_to_a_mint_only_when_unambiguous() -> None:
    stated = [
        SocialEvent(SocialSource.TELEGRAM, "a", "x", NOW, mint=MINT),
        SocialEvent(SocialSource.REDDIT, "b", "x", NOW, mint=MINT),
    ]
    assert resolve_to_mint(stated) == MINT

    tied = [
        SocialEvent(SocialSource.TELEGRAM, "a", "x", NOW, mint=MINT),
        SocialEvent(SocialSource.REDDIT, "b", "x", NOW, mint=OTHER),
    ]
    assert resolve_to_mint(tied) is None


# ------------------------------------------------------------------ traders


def test_a_thin_sample_is_unqualified_rather_than_scored() -> None:
    profile = profile_trader("W", [2.0, 3.0, 4.0])
    assert profile.tier is TraderTier.UNQUALIFIED


def test_one_trade_carrying_the_profit_is_outlier_dependent() -> None:
    returns = [50.0] + [0.9] * 40
    profile = profile_trader("W", returns)
    assert profile.tier is TraderTier.OUTLIER_DEPENDENT
    assert profile.best_trade_dependency is not None
    assert profile.best_trade_dependency > 0.5


def test_coverage_is_carried_so_a_verdict_states_its_own_scope() -> None:
    profile = profile_trader("W", [1.1] * 30, coverage_pct=12.07)
    assert profile.coverage_pct == 12.07


def test_sibling_wallets_are_not_independent() -> None:
    registry = {
        "A": TrackedWallet("A", "a", funded_by="F1"),
        "B": TrackedWallet("B", "b", funded_by="F1"),
        "C": TrackedWallet("C", "c", funded_by="F2"),
    }
    assert set(independent_subset(["A", "B", "C"], registry)) == {"A", "C"}


def test_confluence_requires_two_independent_qualified_wallets() -> None:
    registry = {
        "A": TrackedWallet("A", "a", funded_by="F1"),
        "B": TrackedWallet("B", "b", funded_by="F1"),
    }
    trades = [
        ObservedTrade("A", MINT, "buy", NOW),
        ObservedTrade("B", MINT, "buy", NOW + timedelta(seconds=30)),
    ]
    events = find_confluence(trades, registry, qualified={"A", "B"})
    assert len(events) == 1
    # Both wallets share a funder, so this is one actor and not confirmation.
    assert not events[0].is_confirmation


def test_influence_is_separated_from_selection_skill() -> None:
    path = [(NOW + timedelta(seconds=s), 1.5) for s in (1, 3, 5, 300)]
    curve = measure_reaction("W", MINT, NOW, 1.0, path)
    profile = profile_influence("W", [curve])
    # The whole move happened inside the reflex window, so it is influence.
    assert profile.is_influence_driven


def test_a_horizon_with_no_print_is_none_not_flat() -> None:
    curve = measure_reaction("W", MINT, NOW, 1.0, [])
    assert all(value is None for value in curve.reactions.values())


# ----------------------------------------------------------------- rotation


def test_rotation_pairs_a_sell_with_the_next_buy() -> None:
    trades = [
        ObservedTrade("A", MINT, "sell", NOW, sol_amount=2.0),
        ObservedTrade("A", OTHER, "buy", NOW + timedelta(seconds=60), sol_amount=1.9),
    ]
    rotations = find_rotations(trades)
    assert len(rotations) == 1
    assert rotations[0].from_mint == MINT
    assert rotations[0].to_mint == OTHER


def test_a_distant_buy_is_not_a_rotation() -> None:
    trades = [
        ObservedTrade("A", MINT, "sell", NOW),
        ObservedTrade("A", OTHER, "buy", NOW + timedelta(hours=3)),
    ]
    assert find_rotations(trades) == []


def test_a_single_rotation_has_no_velocity() -> None:
    """One trade is not a rate, and reporting it as one invents a torrent."""
    trades = [
        ObservedTrade("A", MINT, "sell", NOW, sol_amount=2.0),
        ObservedTrade("A", OTHER, "buy", NOW + timedelta(seconds=60), sol_amount=2.0),
    ]
    edges = aggregate(find_rotations(trades))
    assert edges[0].flow_velocity is None


def test_net_flow_separates_inflow_from_outflow() -> None:
    trades = [
        ObservedTrade("A", MINT, "sell", NOW, sol_amount=2.0),
        ObservedTrade("A", OTHER, "buy", NOW + timedelta(seconds=60), sol_amount=2.0),
    ]
    totals = net_flow(aggregate(find_rotations(trades)))
    assert totals[OTHER]["net"] > 0
    assert totals[MINT]["net"] < 0
