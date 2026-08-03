"""Discovery of established movers: tokens already alive and starting to run.

The collector's original discovery source was a launchpad feed, which shows
tokens in their first minutes. Measured over one night, that population had a
median pool liquidity of $26, and 61% of its candidates were disqualified for
coordinated-wallet concentration. It is structurally the worst-quality candidate
pool in the market.

It also structurally cannot contain the trades worth taking. The token that
prompted this module reached $65.8M market cap on a $1.2M pool, and it was eight
days old when it ran. It was never rejected by the gates; it was never a
candidate, because an eight-day-old token does not appear on a launchpad feed.

Trending pools close that gap. The same endpoint returned that exact token at
position two with `+259%` over 24 hours. Candidates from here flow through the
identical pipeline -- enrich, gate, cluster, journal -- because the safety
argument does not change with the discovery source. Only the population does.

One deliberate choice: pool age is read from pool creation, not token creation.
A trade routes through a pool, and a long-established token can have a pool
minutes old. Using token age there would wave through exactly the thin, new
liquidity the age gate exists to catch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .models import TokenIdentity, TokenSnapshot, Universe


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        # Python 3.11+ parses a trailing "Z" directly.
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class TrendingPool:
    """One trending pool, normalised from the provider's payload."""

    pool_address: str
    mint: str
    name: str
    symbol: str
    price_usd: float | None
    liquidity_usd: float | None
    market_cap_usd: float | None
    pool_created_at: datetime | None
    volume_5m_usd: float | None
    volume_1h_usd: float | None
    volume_24h_usd: float | None
    buys_5m: int | None
    sells_5m: int | None
    price_change_5m_pct: float | None
    price_change_1h_pct: float | None
    price_change_24h_pct: float | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def pool_age_minutes(self) -> float | None:
        if self.pool_created_at is None:
            return None
        return (datetime.now(UTC) - self.pool_created_at).total_seconds() / 60

    @property
    def buy_share_5m_pct(self) -> float | None:
        """Share of recent trades that were buys.

        Trade counts, not unique addresses. One wallet can produce many trades,
        so this measures pressure rather than participation.
        """
        if self.buys_5m is None or self.sells_5m is None:
            return None
        total = self.buys_5m + self.sells_5m
        return None if total == 0 else round(100.0 * self.buys_5m / total, 2)

    def to_snapshot(self, observed_at: datetime | None = None) -> TokenSnapshot:
        """Map onto the shared snapshot model.

        Authority, route, impact and simulation fields are left unset. This
        source cannot establish them, and the fail-closed gates must keep
        rejecting until enrichment resolves them from the chain itself.
        """
        now = observed_at or datetime.now(UTC)
        return TokenSnapshot(
            identity=TokenIdentity(
                chain="solana",
                address=self.mint,
                symbol=self.symbol,
                name=self.name,
                source_ids={"pool": self.pool_address},
                verified=False,
            ),
            universe=Universe.SOLANA_EMERGING,
            observed_at=now,
            provider_observed_at=self.pool_created_at,
            age_minutes=self.pool_age_minutes,
            price_usd=self.price_usd,
            liquidity_usd=self.liquidity_usd,
            volume_5m_usd=self.volume_5m_usd,
            price_momentum=self.price_change_1h_pct,
            graduated=True,
            raw_evidence={"trending_pool": self.raw},
        )


def parse_trending_pools(payload: dict[str, Any]) -> list[TrendingPool]:
    """Normalise a trending-pools response into typed rows.

    Rows without a resolvable mint are dropped rather than guessed at. Every
    join downstream is on mint address, because ticker collision is routine:
    five distinct tokens shared one ticker on a single night in this project's
    own data.
    """
    pools: list[TrendingPool] = []
    for item in payload.get("data") or []:
        attributes = item.get("attributes") or {}
        relationships = item.get("relationships") or {}

        base = ((relationships.get("base_token") or {}).get("data") or {}).get("id") or ""
        # Provider ids are namespaced as "<network>_<address>".
        mint = base.split("_", 1)[1] if "_" in base else ""
        if not mint:
            continue

        name = str(attributes.get("name") or "")
        symbol = name.split("/")[0].strip() if "/" in name else name.strip()
        volume = attributes.get("volume_usd") or {}
        transactions = attributes.get("transactions") or {}
        window_5m = transactions.get("m5") or {}
        change = attributes.get("price_change_percentage") or {}

        pools.append(
            TrendingPool(
                pool_address=str(attributes.get("address") or ""),
                mint=mint,
                name=name,
                symbol=symbol,
                price_usd=_as_float(attributes.get("base_token_price_usd")),
                liquidity_usd=_as_float(attributes.get("reserve_in_usd")),
                market_cap_usd=_as_float(attributes.get("market_cap_usd"))
                or _as_float(attributes.get("fdv_usd")),
                pool_created_at=_parse_time(attributes.get("pool_created_at")),
                volume_5m_usd=_as_float(volume.get("m5")),
                volume_1h_usd=_as_float(volume.get("h1")),
                volume_24h_usd=_as_float(volume.get("h24")),
                buys_5m=_as_int(window_5m.get("buys")),
                sells_5m=_as_int(window_5m.get("sells")),
                price_change_5m_pct=_as_float(change.get("m5")),
                price_change_1h_pct=_as_float(change.get("h1")),
                price_change_24h_pct=_as_float(change.get("h24")),
                raw=item,
            )
        )
    return pools


@dataclass(frozen=True)
class MoverFilter:
    """Pre-filter applied before spending enrichment calls on a candidate.

    This is a budget filter, not a safety gate. Nothing here can approve a
    token; it only decides which candidates are worth the six network calls
    enrichment costs.
    """

    minimum_liquidity_usd: float = 50_000.0
    minimum_volume_24h_usd: float = 100_000.0
    minimum_pool_age_minutes: float = 60.0
    maximum_price_change_5m_pct: float = 25.0

    def accepts(self, pool: TrendingPool) -> tuple[bool, str]:
        if pool.liquidity_usd is None or pool.liquidity_usd < self.minimum_liquidity_usd:
            return False, "liquidity_too_thin"
        if (
            pool.volume_24h_usd is None
            or pool.volume_24h_usd < self.minimum_volume_24h_usd
        ):
            return False, "volume_too_low"
        age = pool.pool_age_minutes
        if age is None or age < self.minimum_pool_age_minutes:
            return False, "pool_too_new"
        # No-chase, applied before any work is done. A token already vertical on
        # a five-minute basis is one where the move has been made and the
        # remaining buyers are the exit.
        if (
            pool.price_change_5m_pct is not None
            and pool.price_change_5m_pct > self.maximum_price_change_5m_pct
        ):
            return False, "already_vertical"
        return True, "accepted"


def select_movers(
    payload: dict[str, Any], filters: MoverFilter | None = None
) -> tuple[list[TrendingPool], dict[str, int]]:
    """Return pools worth enriching, plus a count of why the rest were skipped."""
    filters = filters or MoverFilter()
    accepted: list[TrendingPool] = []
    skipped: dict[str, int] = {}
    for pool in parse_trending_pools(payload):
        ok, reason = filters.accepts(pool)
        if ok:
            accepted.append(pool)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    accepted.sort(key=lambda pool: pool.volume_24h_usd or 0.0, reverse=True)
    return accepted, skipped
