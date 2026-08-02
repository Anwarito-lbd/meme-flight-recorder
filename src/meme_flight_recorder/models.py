from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Universe(StrEnum):
    CEX_ESTABLISHED = "cex_established"
    SOLANA_EMERGING = "solana_emerging"
    SOLANA_LAUNCH = "solana_launch"


class Lifecycle(StrEnum):
    LAUNCHED = "launched"
    BONDING_CURVE_ACTIVE = "bonding_curve_active"
    NEAR_GRADUATION = "near_graduation"
    GRADUATED = "graduated"
    LIQUIDITY_EXPANDING = "liquidity_expanding"
    MOMENTUM_CONFIRMED = "momentum_confirmed"
    DISTRIBUTION_DETECTED = "distribution_detected"
    COLLAPSED_OR_INACTIVE = "collapsed_or_inactive"
    ESTABLISHED = "established"


class CandidateStatus(StrEnum):
    REJECT = "reject"
    MONITOR = "monitor"
    ELIGIBLE_FOR_STRATEGY_REVIEW = "eligible_for_strategy_review"


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TokenIdentity:
    chain: str
    address: str
    symbol: str
    name: str
    source_ids: dict[str, str] = field(default_factory=dict)
    verified: bool = False


@dataclass(frozen=True)
class TokenSnapshot:
    identity: TokenIdentity
    universe: Universe
    observed_at: datetime
    provider_observed_at: datetime | None = None
    age_minutes: float | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    volume_5m_usd: float | None = None
    unique_buyers_5m: int | None = None
    unique_sellers_5m: int | None = None
    holder_count: int | None = None
    top10_private_holder_pct: float | None = None
    mint_authority_disabled: bool | None = None
    freeze_authority_disabled: bool | None = None
    developer_selling: bool | None = None
    connected_wallet_risk: bool | None = None
    entry_route_found: bool | None = None
    exit_route_found: bool | None = None
    transaction_simulation_ok: bool | None = None
    entry_price_impact_pct: float | None = None
    exit_price_impact_pct: float | None = None
    bonding_curve_progress_pct: float | None = None
    graduated: bool | None = None
    liquidity_growth_pct: float | None = None
    buyer_growth_pct: float | None = None
    holder_growth_pct: float | None = None
    social_acceleration: float | None = None
    price_momentum: float | None = None
    market_regime: float | None = None
    raw_evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["universe"] = self.universe.value
        result["observed_at"] = self.observed_at.isoformat()
        result["provider_observed_at"] = (
            self.provider_observed_at.isoformat() if self.provider_observed_at else None
        )
        return result


@dataclass(frozen=True)
class SafetyDecision:
    status: CandidateStatus
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    checked_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ScoreResult:
    score: float
    confidence: float
    components: dict[str, float | None]
    missing: tuple[str, ...]
