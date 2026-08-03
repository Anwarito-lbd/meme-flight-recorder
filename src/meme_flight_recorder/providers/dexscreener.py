"""Read-only DexScreener adapter for pool liquidity, pair age and flow.

DexScreener publishes a free, keyless API covering pair-level liquidity,
creation time, volume and transaction counts across chains. That makes it the
cheapest way to resolve two of the gate inputs the discovery feed cannot
establish reliably: how deep the pool actually is, and how old the *pair* is
rather than how old the token claims to be.

A token can be re-listed into a new pool, so pair age and token age are
different facts. The safety gates care about the pool a trade would actually
route through, which is the pair.

One deliberate omission: this API reports transaction *counts*, not unique
addresses. A single wallet can generate a hundred buys. Transaction counts are
therefore never mapped onto the snapshot's ``unique_buyers_5m`` field, which
would silently convert a weak signal into a strong one. They are exposed under
their own name so the distinction survives into the journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .http import get_json

BASE_URL = "https://api.dexscreener.com"


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PairEvidence:
    """One liquidity pool for a token, as reported by DexScreener."""

    chain_id: str
    dex_id: str
    pair_address: str
    base_token: str
    quote_token: str
    price_usd: float | None
    liquidity_usd: float | None
    fdv_usd: float | None
    market_cap_usd: float | None
    volume_5m_usd: float | None
    volume_1h_usd: float | None
    volume_24h_usd: float | None
    buy_txns_5m: int | None
    sell_txns_5m: int | None
    pair_created_at: datetime | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def pair_age_minutes(self) -> float | None:
        if self.pair_created_at is None:
            return None
        return (datetime.now(UTC) - self.pair_created_at).total_seconds() / 60

    @property
    def volume_to_liquidity_5m(self) -> float | None:
        """Turnover ratio. Extreme values are a wash-trading tell, not momentum."""
        if not self.liquidity_usd or self.volume_5m_usd is None:
            return None
        return round(self.volume_5m_usd / self.liquidity_usd, 6)


def _parse_pair(pair: dict[str, Any]) -> PairEvidence:
    liquidity = pair.get("liquidity") or {}
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    txns_5m = txns.get("m5") or {}
    created = pair.get("pairCreatedAt")
    return PairEvidence(
        chain_id=str(pair.get("chainId", "")),
        dex_id=str(pair.get("dexId", "")),
        pair_address=str(pair.get("pairAddress", "")),
        base_token=str((pair.get("baseToken") or {}).get("address", "")),
        quote_token=str((pair.get("quoteToken") or {}).get("address", "")),
        price_usd=_as_float(pair.get("priceUsd")),
        liquidity_usd=_as_float(liquidity.get("usd")),
        fdv_usd=_as_float(pair.get("fdv")),
        market_cap_usd=_as_float(pair.get("marketCap")),
        volume_5m_usd=_as_float(volume.get("m5")),
        volume_1h_usd=_as_float(volume.get("h1")),
        volume_24h_usd=_as_float(volume.get("h24")),
        buy_txns_5m=int(txns_5m["buys"]) if txns_5m.get("buys") is not None else None,
        sell_txns_5m=int(txns_5m["sells"]) if txns_5m.get("sells") is not None else None,
        pair_created_at=(
            datetime.fromtimestamp(int(created) / 1000, tz=UTC) if created else None
        ),
        raw=pair,
    )


class DexScreenerProvider:
    """Pool-level evidence. Keyless, read-only, and unable to place an order."""

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def pairs_for_token(self, token_address: str) -> list[PairEvidence]:
        """Return every known pair for a token, deepest liquidity first."""
        if not token_address.strip():
            raise ValueError("token_address is required")
        data = get_json(
            BASE_URL, f"/latest/dex/tokens/{token_address}", timeout=self.timeout
        )
        pairs = data.get("pairs") or []
        parsed = [_parse_pair(pair) for pair in pairs]
        parsed.sort(key=lambda pair: pair.liquidity_usd or 0.0, reverse=True)
        return parsed

    def deepest_pair(self, token_address: str) -> PairEvidence | None:
        """Return the pool a trade would most plausibly route through.

        Headline liquidity for a token is often the sum across many pools, which
        overstates what any single order can access. Sizing against the deepest
        individual pool is the conservative reading, and the one the price-impact
        quote will actually reflect.
        """
        pairs = self.pairs_for_token(token_address)
        return pairs[0] if pairs else None

    def total_liquidity_usd(self, token_address: str) -> float | None:
        pairs = self.pairs_for_token(token_address)
        values = [pair.liquidity_usd for pair in pairs if pair.liquidity_usd is not None]
        return sum(values) if values else None
