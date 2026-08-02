from meme_flight_recorder.external_signals import (
    ExternalObservation,
    ExternalSignalKind,
    ObservationStatus,
    qualify_external_observation,
)


def observation(kind: ExternalSignalKind, **overrides) -> ExternalObservation:
    values = {
        "kind": kind,
        "actor": "test",
        "chain": "solana",
        "exact_mint": "mint",
        "source_url": "https://example.test/source",
        "observed_at_ms": 2_000,
        "source_published_at_ms": 1_000,
        "official_source": True,
        "independent_identity_sources": 1,
        "verified_wallets_buying": 3,
        "wallets_with_prior_sample": 3,
        "exit_route_verified": True,
        "critical_safety_passed": True,
        "liquidity_expanding": True,
        "unique_buyers_accelerating": True,
        "developer_net_selling": False,
    }
    values.update(overrides)
    return ExternalObservation(**values)


def test_influencer_claim_can_never_become_a_signal() -> None:
    decision = qualify_external_observation(observation(ExternalSignalKind.INFLUENCER_CLAIM))
    assert decision.status == ObservationStatus.REJECT
    assert "influencer_claim_is_not_a_trade_signal" in decision.reasons


def test_public_figure_launch_is_monitor_only_even_when_verified() -> None:
    decision = qualify_external_observation(
        observation(ExternalSignalKind.PUBLIC_FIGURE_LAUNCH)
    )
    assert decision.status == ObservationStatus.MONITOR


def test_one_smart_wallet_is_not_copyable() -> None:
    decision = qualify_external_observation(
        observation(ExternalSignalKind.SMART_WALLET_CLUSTER, verified_wallets_buying=1)
    )
    assert decision.status == ObservationStatus.REJECT
    assert "fewer_than_three_verified_wallets_buying" in decision.reasons


def test_wallet_cluster_requires_market_confirmation_and_full_safety() -> None:
    decision = qualify_external_observation(observation(ExternalSignalKind.SMART_WALLET_CLUSTER))
    assert decision.status == ObservationStatus.ELIGIBLE_FOR_FORWARD_PAPER_OBSERVATION


def test_external_observation_cannot_use_future_publication_time() -> None:
    decision = qualify_external_observation(
        observation(
            ExternalSignalKind.SMART_WALLET_CLUSTER,
            observed_at_ms=999,
            source_published_at_ms=1_000,
        )
    )
    assert decision.status == ObservationStatus.REJECT
    assert "observation_precedes_publication" in decision.reasons
