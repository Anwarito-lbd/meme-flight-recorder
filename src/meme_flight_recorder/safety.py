from __future__ import annotations

from datetime import UTC, datetime

from .clusters import ClusterAssessment, ClusterVerdict
from .config import ClusterLimits, SafetyLimits
from .models import CandidateStatus, SafetyDecision, TokenSnapshot, Universe


def _required_bool(value: bool | None, failure: str, unknown: str, failures: list[str]) -> None:
    if value is False:
        failures.append(failure)
    elif value is None:
        failures.append(unknown)


class SafetyEngine:
    """Fail closed when critical identity, authority, liquidity, or exit evidence is absent."""

    def __init__(
        self,
        cex_limits: SafetyLimits,
        solana_limits: SafetyLimits,
        stale_after_seconds: int,
        cluster_limits: ClusterLimits | None = None,
    ) -> None:
        self.cex_limits = cex_limits
        self.solana_limits = solana_limits
        self.stale_after_seconds = stale_after_seconds
        # Opt-in: when no cluster limits are configured the engine behaves
        # exactly as before. Once configured, on-chain candidates must carry a
        # cluster assessment or they are rejected for missing evidence.
        self.cluster_limits = cluster_limits

    def evaluate(
        self,
        snapshot: TokenSnapshot,
        now: datetime | None = None,
        cluster: ClusterAssessment | None = None,
    ) -> SafetyDecision:
        now = now or datetime.now(UTC)
        limits = (
            self.cex_limits if snapshot.universe == Universe.CEX_ESTABLISHED else self.solana_limits
        )
        failures: list[str] = []
        warnings: list[str] = []

        if not snapshot.identity.verified or not snapshot.identity.address:
            failures.append("identity_unverified")
        age = (now - snapshot.observed_at).total_seconds()
        if age > self.stale_after_seconds:
            failures.append("snapshot_stale")
        if snapshot.provider_observed_at is None:
            warnings.append("provider_timestamp_missing")

        if snapshot.age_minutes is None:
            failures.append("token_age_unknown")
        elif snapshot.age_minutes < limits.minimum_age_minutes:
            failures.append("token_too_young_for_universe")

        if snapshot.liquidity_usd is None:
            failures.append("liquidity_unknown")
        elif snapshot.liquidity_usd < limits.minimum_liquidity_usd:
            failures.append("liquidity_below_minimum")

        if snapshot.universe != Universe.CEX_ESTABLISHED:
            _required_bool(
                snapshot.mint_authority_disabled,
                "mint_authority_active",
                "mint_authority_unknown",
                failures,
            )
            _required_bool(
                snapshot.freeze_authority_disabled,
                "freeze_authority_active",
                "freeze_authority_unknown",
                failures,
            )
            if snapshot.developer_selling is True:
                failures.append("developer_selling")
            elif snapshot.developer_selling is None:
                failures.append("developer_activity_unknown")
            if snapshot.connected_wallet_risk is True:
                failures.append("connected_wallet_concentration")

        if snapshot.top10_private_holder_pct is None:
            failures.append("holder_concentration_unknown")
        elif snapshot.top10_private_holder_pct > limits.maximum_top10_private_holder_pct:
            failures.append("holder_concentration_excessive")

        _required_bool(
            snapshot.entry_route_found, "entry_route_failed", "entry_route_unknown", failures
        )
        _required_bool(
            snapshot.exit_route_found, "exit_route_failed", "exit_route_unknown", failures
        )
        if snapshot.universe != Universe.CEX_ESTABLISHED:
            _required_bool(
                snapshot.transaction_simulation_ok,
                "transaction_simulation_failed",
                "transaction_simulation_unknown",
                failures,
            )

        if self.cluster_limits is not None and snapshot.universe != Universe.CEX_ESTABLISHED:
            self._clusters(cluster, failures, warnings)

        self._impact(
            snapshot.entry_price_impact_pct,
            limits.maximum_entry_price_impact_pct,
            "entry",
            failures,
        )
        self._impact(
            snapshot.exit_price_impact_pct,
            limits.maximum_exit_price_impact_pct,
            "exit",
            failures,
        )

        status = (
            CandidateStatus.REJECT if failures else CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW
        )
        if snapshot.universe == Universe.SOLANA_LAUNCH and not failures:
            status = CandidateStatus.MONITOR
        return SafetyDecision(status, tuple(failures), tuple(warnings), now)

    @staticmethod
    def _clusters(
        cluster: ClusterAssessment | None, failures: list[str], warnings: list[str]
    ) -> None:
        """Fold a coordinated-wallet assessment into the gate result.

        A SUSPECT verdict is a warning rather than a rejection: it means the
        structure is unusual but no threshold was breached, and downgrading it
        to a rejection would discard almost every real launch. DISQUALIFIED and
        INSUFFICIENT_EVIDENCE both reject, because the fail-closed rule treats
        "we could not tell" the same as "we found something".
        """
        if cluster is None:
            failures.append("cluster_evidence_missing")
            return
        if cluster.verdict is ClusterVerdict.DISQUALIFIED:
            failures.extend(cluster.failures or ("cluster_disqualified",))
        elif cluster.verdict is ClusterVerdict.INSUFFICIENT_EVIDENCE:
            failures.append("cluster_evidence_insufficient")
        warnings.extend(cluster.warnings)

    @staticmethod
    def _impact(value: float | None, maximum: float, label: str, failures: list[str]) -> None:
        if value is None:
            failures.append(f"{label}_price_impact_unknown")
        elif value < 0:
            failures.append(f"{label}_price_impact_invalid")
        elif value > maximum:
            failures.append(f"{label}_price_impact_excessive")
