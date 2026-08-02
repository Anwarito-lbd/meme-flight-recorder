"""Fail-closed qualification for social, event, and smart-wallet observations.

This module never creates an order.  It decides whether an external observation is
good enough to record, reject, or admit to forward *paper* observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExternalSignalKind(StrEnum):
    PUBLIC_FIGURE_LAUNCH = "public_figure_launch"
    SMART_WALLET_CLUSTER = "smart_wallet_cluster"
    INFLUENCER_CLAIM = "influencer_claim"


class ObservationStatus(StrEnum):
    REJECT = "reject"
    MONITOR = "monitor"
    ELIGIBLE_FOR_FORWARD_PAPER_OBSERVATION = "eligible_for_forward_paper_observation"


@dataclass(frozen=True)
class ExternalObservation:
    kind: ExternalSignalKind
    actor: str
    chain: str
    exact_mint: str | None
    source_url: str
    observed_at_ms: int
    source_published_at_ms: int
    official_source: bool = False
    independent_identity_sources: int = 0
    verified_wallets_buying: int = 0
    wallets_with_prior_sample: int = 0
    exit_route_verified: bool = False
    critical_safety_passed: bool = False
    liquidity_expanding: bool = False
    unique_buyers_accelerating: bool = False
    developer_net_selling: bool | None = None


@dataclass(frozen=True)
class ObservationDecision:
    status: ObservationStatus
    reasons: tuple[str, ...]


def qualify_external_observation(observation: ExternalObservation) -> ObservationDecision:
    failures: list[str] = []
    if not observation.source_url.startswith("https://"):
        failures.append("invalid_source_url")
    if not observation.exact_mint:
        failures.append("exact_mint_unknown")
    if observation.observed_at_ms < observation.source_published_at_ms:
        failures.append("observation_precedes_publication")
    if not observation.critical_safety_passed:
        failures.append("critical_safety_not_passed")
    if not observation.exit_route_verified:
        failures.append("exit_route_unverified")
    if observation.developer_net_selling is not False:
        failures.append("developer_selling_unknown_or_detected")

    if observation.kind == ExternalSignalKind.INFLUENCER_CLAIM:
        return ObservationDecision(
            ObservationStatus.REJECT,
            tuple(failures + ["influencer_claim_is_not_a_trade_signal"]),
        )

    if observation.kind == ExternalSignalKind.PUBLIC_FIGURE_LAUNCH:
        if not observation.official_source:
            failures.append("launch_source_not_official")
        if observation.independent_identity_sources < 1:
            failures.append("mint_not_independently_cross_checked")
        # Brand-new launches remain monitor-only even when every observable fact passes.
        if failures:
            return ObservationDecision(ObservationStatus.REJECT, tuple(failures))
        return ObservationDecision(
            ObservationStatus.MONITOR,
            ("brand_new_public_figure_launch_is_monitor_only",),
        )

    if observation.verified_wallets_buying < 3:
        failures.append("fewer_than_three_verified_wallets_buying")
    if observation.wallets_with_prior_sample < 3:
        failures.append("wallets_lack_point_in_time_prior_sample")
    if not observation.liquidity_expanding:
        failures.append("liquidity_not_expanding")
    if not observation.unique_buyers_accelerating:
        failures.append("unique_buyers_not_accelerating")
    if failures:
        return ObservationDecision(ObservationStatus.REJECT, tuple(failures))
    return ObservationDecision(
        ObservationStatus.ELIGIBLE_FOR_FORWARD_PAPER_OBSERVATION,
        ("cluster_confirmation_passed",),
    )
