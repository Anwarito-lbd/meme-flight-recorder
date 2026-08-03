"""Read-only adapter for Binance's public Web3 discovery and audit endpoints.

These endpoints back the ``meme-rush`` and ``query-token-audit`` agent skills.
They need no authentication, and they are the only candidate-flow source in this
project that reports precomputed insider, sniper, bundler and wash-trading
metrics for a brand-new mint.

Those metrics are *third-party labels*, not ground truth. The vendor does not
publish its methodology, its coverage, or its false-negative rate, and its own
audit documentation states that a LOW risk result does not mean safe. So this
module deliberately does not decide anything. It returns evidence, and
``clusters.py`` grades that evidence against independent on-chain verification
before any gate acts on it.

The adapter is read-only by construction: it can discover and describe tokens,
and it has no code path that builds, signs, or broadcasts a transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..deployer import developer_is_distributing
from ..models import TokenIdentity, TokenSnapshot, Universe
from .http import get_json, post_json

# Binance's chain identifiers, which are not EVM chain IDs for Solana.
CHAIN_SOLANA = "CT_501"
CHAIN_BSC = "56"
CHAIN_BASE = "8453"

MEME_RUSH_CHAINS = frozenset({CHAIN_SOLANA, CHAIN_BSC, CHAIN_BASE})
TOPIC_RUSH_CHAINS = frozenset({CHAIN_SOLANA, CHAIN_BSC})

# meme-rush rankType: launchpad lifecycle stage.
RANK_NEW = 10
RANK_FINALIZING = 20
RANK_MIGRATED = 30

_USER_AGENT = "binance-web3/2.0 (Skill)"
_BASE = "https://web3.binance.com/bapi/defi"


def _as_float(value: Any) -> float | None:
    """Coerce the API's stringly-typed numerics, preserving None for absent."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


@dataclass(frozen=True)
class VendorClusterLabels:
    """Precomputed concentration labels for one mint, as reported by the vendor.

    Every field is optional because the feed routinely omits them for very new
    tokens. Absent is not zero, and conflating the two would turn missing
    evidence into a safety pass. ``clusters.py`` relies on that distinction.
    """

    insider_pct: float | None = None
    sniper_pct: float | None = None
    bundler_pct: float | None = None
    new_wallet_pct: float | None = None
    top10_pct: float | None = None
    dev_pct: float | None = None
    dev_sell_pct: float | None = None
    dev_address: str | None = None
    dev_migrate_count: int | None = None
    kol_pct: float | None = None
    pro_pct: float | None = None
    dev_wash_trading: bool | None = None
    insider_wash_trading: bool | None = None

    @property
    def reported_fields(self) -> tuple[str, ...]:
        return tuple(
            name for name, value in self.__dict__.items() if value is not None
        )

    @property
    def missing_fields(self) -> tuple[str, ...]:
        return tuple(name for name, value in self.__dict__.items() if value is None)


@dataclass(frozen=True)
class MemeRushRow:
    """One discovery-feed row, kept alongside the untouched vendor payload."""

    chain_id: str
    contract_address: str
    symbol: str
    name: str
    created_at: datetime | None
    migrated_at: datetime | None
    price_usd: float | None
    market_cap_usd: float | None
    liquidity_usd: float | None
    volume_usd: float | None
    holders: int | None
    buy_count: int | None
    sell_count: int | None
    progress_pct: float | None
    migrated: bool
    labels: VendorClusterLabels = field(default_factory=VendorClusterLabels)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def age_minutes(self) -> float | None:
        if self.created_at is None:
            return None
        return (datetime.now(UTC) - self.created_at).total_seconds() / 60

    def to_snapshot(self, observed_at: datetime | None = None) -> TokenSnapshot:
        """Map the row onto the project's snapshot model.

        Fields this feed cannot establish — mint/freeze authority, route
        existence, simulation result, price impact — are left as None on
        purpose. ``SafetyEngine`` is fail-closed and treats missing critical
        evidence as a rejection, so leaving them unset keeps the gates honest
        rather than smuggling in a vendor opinion as a verified fact.
        """
        now = observed_at or datetime.now(UTC)
        universe = (
            Universe.SOLANA_EMERGING if self.migrated else Universe.SOLANA_LAUNCH
        )
        return TokenSnapshot(
            identity=TokenIdentity(
                chain="solana" if self.chain_id == CHAIN_SOLANA else self.chain_id,
                address=self.contract_address,
                symbol=self.symbol,
                name=self.name,
                source_ids={"binance_web3": self.contract_address},
                verified=False,
            ),
            universe=universe,
            observed_at=now,
            provider_observed_at=self.created_at,
            age_minutes=self.age_minutes,
            price_usd=self.price_usd,
            liquidity_usd=self.liquidity_usd,
            holder_count=self.holders,
            top10_private_holder_pct=self.labels.top10_pct,
            # Not `dev_sell_pct > 0`. See developer_is_distributing: a developer
            # who has fully exited holds no supply left to dump and measured as
            # the safest state in the recorded data, while dust-level values are
            # floating-point noise rather than distribution.
            developer_selling=developer_is_distributing(self.labels.dev_sell_pct),
            bonding_curve_progress_pct=self.progress_pct,
            graduated=self.migrated,
            raw_evidence={"binance_web3": self.raw},
        )


def _timestamp(value: Any) -> datetime | None:
    milliseconds = _as_int(value)
    if not milliseconds:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def _parse_row(row: dict[str, Any]) -> MemeRushRow:
    return MemeRushRow(
        chain_id=str(row.get("chainId", "")),
        contract_address=str(row.get("contractAddress", "")),
        symbol=str(row.get("symbol", "")),
        name=str(row.get("name", "")),
        created_at=_timestamp(row.get("createTime")),
        migrated_at=_timestamp(row.get("migrateTime")),
        price_usd=_as_float(row.get("price")),
        market_cap_usd=_as_float(row.get("marketCap")),
        liquidity_usd=_as_float(row.get("liquidity")),
        volume_usd=_as_float(row.get("volume")),
        holders=_as_int(row.get("holders")),
        buy_count=_as_int(row.get("countBuy")),
        sell_count=_as_int(row.get("countSell")),
        progress_pct=_as_float(row.get("progress")),
        migrated=_as_int(row.get("migrateStatus")) == 1,
        labels=VendorClusterLabels(
            insider_pct=_as_float(row.get("holdersInsiderPercent")),
            sniper_pct=_as_float(row.get("holdersSniperPercent")),
            bundler_pct=_as_float(row.get("bundlerHoldingPercent")),
            new_wallet_pct=_as_float(row.get("newWalletHoldingPercent")),
            top10_pct=_as_float(row.get("holdersTop10Percent")),
            dev_pct=_as_float(row.get("holdersDevPercent")),
            dev_sell_pct=_as_float(row.get("devSellPercent")),
            dev_address=row.get("devAddress") or None,
            dev_migrate_count=_as_int(row.get("devMigrateCount")),
            kol_pct=_as_float(row.get("kolHoldingPercent")),
            pro_pct=_as_float(row.get("proHoldingPercent")),
            dev_wash_trading=_as_bool(row.get("tagDevWashTrading")),
            insider_wash_trading=_as_bool(row.get("tagInsiderWashTrading")),
        ),
        raw=row,
    )


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    return None


class BinanceWeb3Provider:
    """Discovery and audit adapter. It cannot sign or broadcast anything."""

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def meme_rush(
        self,
        chain_id: str = CHAIN_SOLANA,
        rank_type: int = RANK_MIGRATED,
        limit: int = 20,
        **filters: Any,
    ) -> list[MemeRushRow]:
        """Return launchpad-lifecycle candidates for one chain and stage."""
        if chain_id not in MEME_RUSH_CHAINS:
            raise ValueError(
                f"meme_rush: unsupported chain_id {chain_id!r}; "
                f"supported: {sorted(MEME_RUSH_CHAINS)}"
            )
        payload: dict[str, Any] = {
            "chainId": chain_id,
            "rankType": rank_type,
            "limit": max(1, min(limit, 100)),
            **filters,
        }
        data = post_json(
            f"{_BASE}/v1/public/wallet-direct/buw/wallet/market/token/pulse/rank/list/ai",
            payload,
            headers={"user-agent": _USER_AGENT},
            timeout=self.timeout,
        )
        return [_parse_row(row) for row in (data.get("data") or [])]

    def topic_rush(
        self,
        chain_id: str = CHAIN_SOLANA,
        rank_type: int = 10,
        sort: int = 20,
        asc: bool = False,
    ) -> list[dict[str, Any]]:
        """Return AI-detected narrative topics with their associated tokens.

        Useful as a narrative-discovery input for source scoring. It is not a
        trade signal: a topic being hot says nothing about whether any token
        under it is sellable.
        """
        if chain_id not in TOPIC_RUSH_CHAINS:
            raise ValueError(
                f"topic_rush: unsupported chain_id {chain_id!r}; "
                f"supported: {sorted(TOPIC_RUSH_CHAINS)}"
            )
        data = get_json(
            f"{_BASE}/v2/public/wallet-direct/buw/wallet/market/token",
            "/social-rush/rank/list/ai",
            params={
                "chainId": chain_id,
                "rankType": rank_type,
                "sort": sort,
                "asc": str(asc).lower(),
            },
            headers={"user-agent": _USER_AGENT},
            timeout=self.timeout,
        )
        return list(data.get("data") or [])

    def token_audit(
        self, contract_address: str, chain_id: str = CHAIN_SOLANA
    ) -> dict[str, Any]:
        """Return the vendor's contract-security audit for one token.

        Treated as one corroborating opinion among several. A LOW result is
        explicitly not a safety pass, and the audit is a point-in-time snapshot
        that a mutable contract can invalidate immediately afterwards.
        """
        if not contract_address.strip():
            raise ValueError("contract_address is required")
        data = post_json(
            f"{_BASE}/v1/public/wallet-direct/security/token/audit",
            {
                "binanceChainId": chain_id,
                "contractAddress": contract_address,
                "requestId": str(uuid.uuid4()),
            },
            headers={"user-agent": _USER_AGENT},
            timeout=self.timeout,
        )
        return dict(data.get("data") or {})
