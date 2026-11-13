from __future__ import annotations

import os
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar

from .http import get_json


class CoinGeckoProvider:
    """Read-only discovery/enrichment. Never sufficient to approve a candidate."""

    def __init__(self, api_key: str | None = None, plan: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("COINGECKO_API_KEY", "")
        self.plan = (plan or os.getenv("COINGECKO_API_PLAN", "demo")).lower()
        if self.plan == "pro":
            self.base_url = "https://pro-api.coingecko.com/api/v3"
            self.headers = {"x-cg-pro-api-key": self.api_key} if self.api_key else {}
        else:
            self.base_url = "https://api.coingecko.com/api/v3"
            self.headers = {"x-cg-demo-api-key": self.api_key} if self.api_key else {}

    def meme_universe(self, limit: int = 50) -> Any:
        return self._get(
            "/coins/markets",
            {
                "vs_currency": "usd",
                "category": "meme-token",
                "order": "volume_desc",
                "per_page": max(1, min(limit, 250)),
                "sparkline": "false",
            },
        )

    def market_chart(self, coin_id: str, days: int = 90) -> Any:
        """Public historical price/market-volume points for research, not exchange fills."""
        return self._get(
            f"/coins/{coin_id}/market_chart",
            {
                "vs_currency": "usd",
                "days": max(1, min(days, 365)),
                "interval": "hourly" if days <= 90 else "daily",
            },
        )

    def new_solana_pools(self, page: int = 1) -> Any:
        return self._get(
            "/onchain/networks/solana/new_pools",
            {
                "include": "base_token,quote_token,dex",
                "page": max(1, page),
            },
        )

    def trending_solana_pools(self, page: int = 1) -> Any:
        return self._get(
            "/onchain/networks/solana/trending_pools",
            {
                "include": "base_token,quote_token,dex",
                "page": max(1, page),
            },
        )

    def token_pools(self, mint: str, page: int = 1) -> Any:
        return self._get(
            f"/onchain/networks/solana/tokens/{mint}/pools",
            {
                "include": "base_token,quote_token,dex",
                "page": max(1, page),
            },
        )

    # The provider exposes a separate path per timeframe, and each accepts only
    # its own aggregates. Requesting aggregate=60 on the minute path returns
    # nothing rather than erroring, which silently looks like "no data" and can
    # be mistaken for a real result.
    OHLCV_AGGREGATES: ClassVar[Mapping[str, frozenset[int]]] = MappingProxyType(
        {
            "minute": frozenset({1, 5, 15}),
            "hour": frozenset({1, 4, 12}),
            "day": frozenset({1}),
        }
    )

    def pool_ohlcv(
        self, pool: str, aggregate: int = 15, limit: int = 100, timeframe: str = "minute"
    ) -> Any:
        allowed = self.OHLCV_AGGREGATES.get(timeframe)
        if allowed is None:
            raise ValueError(f"timeframe must be one of {sorted(self.OHLCV_AGGREGATES)}")
        if aggregate not in allowed:
            raise ValueError(
                f"aggregate {aggregate} unsupported for {timeframe}; "
                f"allowed: {sorted(allowed)}"
            )
        return self._get(
            f"/onchain/networks/solana/pools/{pool}/ohlcv/{timeframe}",
            {
                "aggregate": aggregate,
                "limit": max(1, min(limit, 1000)),
            },
        )

    def pool_trades(self, pool: str, trade_volume_in_usd_greater_than: float = 0) -> Any:
        return self._get(
            f"/onchain/networks/solana/pools/{pool}/trades",
            {
                "trade_volume_in_usd_greater_than": max(0, trade_volume_in_usd_greater_than),
            },
        )

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        return get_json(self.base_url, path, params=params, headers=self.headers)
