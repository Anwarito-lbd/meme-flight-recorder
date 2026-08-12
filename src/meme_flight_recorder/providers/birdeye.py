"""Birdeye: price, holders and candle history, keyed by mint rather than pool.

Added because it answers the two things that were blocking the maturity study.
It serves OHLCV **by mint address**, so it covers the 8,324 journalled mints that
never carried a pool address, and it answers in about a second where the
keyless alternative was taking sixteen.

**The trap this provider carries, verified against a real journalled mint.**
Birdeye returns a candle for every minute in the requested window whether or not
anyone traded, carrying the previous close forward. One rugged token returned
1,000 candles of which **963 had zero volume**: sixteen hours after its last
trade it still reported a price of 0.0001042. Read naively that is a live token
at a stable price. It is a corpse with a stale tag on it.

So the rule here is absolute and is enforced in the parser rather than left to
callers: **a candle with no volume is not a price.** `trades_only` drops them,
and `last_trade_at` reports when the token was last actually traded, which is
the only honest input to a survival question. Zero volume is a *measurement* of
no trading, not missing data, and the two are kept separate.

I/O only. Parsing is pure and the caller decides what the evidence means.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .http import get_json

BASE_URL = "https://public-api.birdeye.so"

# Measured, not documented: eight consecutive OHLCV calls at 1.05s spacing
# returned 8/8 with no 429, while a burst of unspaced calls to token_overview
# was refused. Treated the same way as collector pacing -- derived from what the
# provider grants, and not to be lowered.
MINIMUM_REQUEST_SPACING_SECONDS = 1.05

# The endpoint truncates at 1,000 items regardless of the window asked for,
# which at one-minute resolution is 16.6 hours.
MAX_ITEMS_PER_REQUEST = 1_000


def _as_float(value: Any) -> float | None:
    """None stays None. A missing number is never zero."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Candle:
    unix_time: int
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None

    @property
    def traded(self) -> bool:
        """Whether anyone actually traded in this minute.

        The distinction the whole provider turns on. A carried-forward close
        with no volume is a quote nobody took.
        """
        return bool(self.volume and self.volume > 0)


@dataclass(frozen=True)
class CandleSeries:
    mint: str
    candles: tuple[Candle, ...]

    @property
    def trades_only(self) -> tuple[Candle, ...]:
        return tuple(candle for candle in self.candles if candle.traded)

    @property
    def last_trade_at(self) -> int | None:
        traded = self.trades_only
        return traded[-1].unix_time if traded else None

    @property
    def silent_minutes(self) -> int:
        """Minutes between the last real trade and the end of the window."""
        if not self.candles:
            return 0
        last = self.last_trade_at
        if last is None:
            return len(self.candles)
        return max(0, (self.candles[-1].unix_time - last) // 60)


def _parse_candles(mint: str, payload: Any) -> CandleSeries | None:
    items = ((payload or {}).get("data") or {}).get("items")
    if items is None:
        return None
    candles = tuple(
        Candle(
            unix_time=int(item.get("unixTime") or 0),
            open=_as_float(item.get("o")),
            high=_as_float(item.get("h")),
            low=_as_float(item.get("l")),
            close=_as_float(item.get("c")),
            volume=_as_float(item.get("v")),
        )
        for item in items
        if item.get("unixTime")
    )
    return CandleSeries(mint=mint, candles=candles)


@dataclass(frozen=True)
class HolderStake:
    owner: str
    amount: float | None


class BirdeyeProvider:
    """Read-only. Never signs, never broadcasts, never holds a key beyond the API one."""

    def __init__(self, api_key: str | None = None, timeout: int = 15) -> None:
        self.api_key = api_key or os.getenv("BIRDEYE_API_KEY") or ""
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self.api_key, "x-chain": "solana"}

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.configured:
            # An unconfigured provider must not look like an empty one. The
            # caller has to be able to tell "no key" from "no data".
            raise RuntimeError("BIRDEYE_API_KEY is not set")
        return get_json(BASE_URL, path, params=params, headers=self._headers(),
                        timeout=self.timeout)

    def price(self, mint: str) -> float | None:
        payload = self._get("/defi/price", {"address": mint})
        return _as_float(((payload or {}).get("data") or {}).get("value"))

    def candles(
        self, mint: str, time_from: int, time_to: int, interval: str = "1m"
    ) -> CandleSeries | None:
        """Raw series, carried-forward candles included.

        They are kept rather than filtered here so that a caller can tell a
        token that was never listed from one that stopped trading -- the first
        returns no items at all, the second returns items with zero volume.
        """
        payload = self._get(
            "/defi/ohlcv",
            {"address": mint, "type": interval, "time_from": time_from, "time_to": time_to},
        )
        return _parse_candles(mint, payload)

    def top_holders(self, mint: str, limit: int = 20) -> list[HolderStake] | None:
        payload = self._get(
            "/defi/v3/token/holder", {"address": mint, "limit": min(max(limit, 1), 100)}
        )
        items = ((payload or {}).get("data") or {}).get("items")
        if items is None:
            return None
        return [
            HolderStake(owner=str(item.get("owner") or ""), amount=_as_float(item.get("amount")))
            for item in items
            if item.get("owner")
        ]


def observed_at_from(series: CandleSeries | None) -> datetime | None:
    if series is None:
        return None
    last = series.last_trade_at
    return datetime.fromtimestamp(last, tz=UTC) if last else None
