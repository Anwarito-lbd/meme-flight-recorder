#!/usr/bin/env python3
"""Fetch the forward candle history of every journalled pool into a local cache.

Every expectancy number in this project so far compares the price at the first
observation with the price *today*. That answers "what happened eventually", not
"what could have been traded", and it cannot answer the question that matters
now: does waiting until a token has survived to some age improve the trade?

Answering that needs the price path between the observation and the outcome, at
a resolution finer than the holding period. GeckoTerminal serves minute candles
retroactively for Solana pools, including pools that have since died -- verified
against real pairs from this journal before this script was written.

Three rules this cache exists to protect:

  * **A rate limit is not a token death.** Every fetch records its own status.
    An HTTP failure is stored as `error`, never as "no candles", because the
    latter would enter the study as evidence that a pool stopped trading.
  * **Absent is not zero.** A pool with no candles in the window is stored as
    `no_candles` with a count of zero, which is a *measurement*, and is
    distinguishable from a pool that was never requested.
  * **The journal is not touched.** It is append-only and hash-chained and holds
    decisions. This is externally fetched market data, so it lives in its own
    database and can be deleted and rebuilt at will.

Pacing is derived from what the provider actually grants rather than what it
publishes -- measured, because the documented 30/min returns 429 at well under
that rate. Do not lower it.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.env import load_env
from meme_flight_recorder.providers.http import get_json

GECKO_BASE = "https://api.geckoterminal.com"
CANDLES_PER_REQUEST = 1000
# The free tier publishes 30 requests/minute, but measured against the live
# endpoint it rejects at well under that: 11 of 12 requests at ~1/s returned
# 429. Pacing is therefore set from what the provider actually grants, not from
# what it documents, and the same rule as the collector applies -- do not lower
# it. A 429 recorded as data becomes a token that stopped trading.
DEFAULT_DELAY_SECONDS = 2.5

# One request per mint, anchored so that it provably covers the window the study
# needs. The endpoint only walks *backwards* from `before_timestamp`, so asking
# for the 1,000 minutes ending at observation + 1,000 minutes returns:
#
#   * a pool that traded every minute -> exactly [observation, +16.7h];
#   * a sparse pool -> its entire life, since it has fewer than 1,000 candles.
#
# Paging further back was the original design and cost 23s/mint against a
# 68-hour total. This is bounded at one call and loses nothing before +16.7h,
# which contains every survival band under test and a full hold after each.
WINDOW_MINUTES = 1_000
WINDOW_HOURS = WINDOW_MINUTES / 60.0


@dataclass(frozen=True)
class Target:
    mint: str
    pair: str
    observed_at: datetime
    entry_price: float
    liquidity_usd: float | None
    clean: bool


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists candles (
            pair text not null,
            ts integer not null,
            open real, high real, low real, close real, volume real,
            primary key (pair, ts)
        );
        create table if not exists fetches (
            mint text primary key,
            pair text not null,
            series_key text,
            source text,
            window_from integer not null,
            window_to integer not null,
            status text not null,
            candle_count integer not null,
            first_candle_ts integer,
            last_candle_ts integer,
            detail text,
            fetched_at text not null
        );
        create index if not exists candles_pair_ts on candles (pair, ts);
        """
    )
    # Older caches predate provenance. Adding the columns rather than rebuilding
    # keeps the hours of history already fetched usable.
    existing = {row[1] for row in connection.execute("pragma table_info(fetches)")}
    for column in ("series_key", "source"):
        if column not in existing:
            connection.execute(f"alter table fetches add column {column} text")
    connection.execute(
        "update fetches set series_key = pair, source = 'geckoterminal' where series_key is null"
    )
    connection.commit()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def targets(database_path: str, source: str = "birdeye") -> list[Target]:
    """One row per mint, taken from its first observation carrying a pool.

    The first observation is the only moment a decision could have been made
    without hindsight, so it is the only valid anchor for a forward study.
    """
    connection = sqlite3.connect(database_path)
    query = (
        "select entity_id, observed_at, payload_json from events "
        "where event_type = 'candidate_observed' order by id"
    )
    seen: dict[str, Target] = {}
    for mint, observed_at, payload in connection.execute(query):
        if mint in seen:
            continue
        record = json.loads(payload)
        pair = record.get("pair_address")
        price = record.get("price_usd")
        if not price or price <= 0:
            continue
        # Birdeye is keyed by mint, so a missing pool address is not a reason to
        # skip a token -- that is the whole coverage argument for it.
        if source == "geckoterminal" and not pair:
            continue
        seen[mint] = Target(
            mint=mint,
            pair=str(pair or mint),
            observed_at=parse_time(observed_at),
            entry_price=float(price),
            liquidity_usd=record.get("liquidity_usd"),
            clean=not record.get("failures"),
        )
    connection.close()
    return list(seen.values())


def priority(target: Target) -> tuple[int, float]:
    """Order the queue so a partial run still answers the important questions.

    Safety-clean mints first because they are the cohort under test and there
    are only tens of them; then by pool depth, because a shallow pool could
    never have been traded at any age and its history is the least informative.
    """
    return (0 if target.clean else 1, -(target.liquidity_usd or 0.0))


def fetch_window(pair: str, start: int, end: int, delay: float) -> tuple[str, list[list[Any]], str]:
    """Return (status, candles, detail) for one pool over [start, end].

    A single request, because `end` is chosen so that one page of 1,000 minute
    candles provably spans the whole window. Retries only on 429, and a mint
    that never gets through is stored as `rate_limited` rather than as a pool
    with no trades.
    """
    rows: list[list[Any]] = []
    for attempt in range(4):
        time.sleep(delay if attempt == 0 else 20.0 * attempt)
        try:
            payload = get_json(
                GECKO_BASE,
                f"/api/v2/networks/solana/pools/{pair}/ohlcv/minute",
                params={
                    "aggregate": 1,
                    "limit": CANDLES_PER_REQUEST,
                    "before_timestamp": end,
                },
            )
        except HTTPError as error:
            if error.code == 429:
                # A rate limit is invisible damage: recorded as data it becomes
                # a token that stopped trading. Back off and retry; if it never
                # succeeds the mint is stored as rate_limited, which is a
                # *missing measurement* and is excluded from every denominator
                # rather than counted as a death.
                continue
            return "error", [], f"HTTP {error.code}"
        except Exception as error:  # noqa: BLE001 - network failure is not a measurement
            return "error", [], type(error).__name__
        rows = ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        break
    else:
        return "rate_limited", [], "429 after four attempts"

    inside = [row for row in sorted(rows, key=lambda r: int(r[0])) if start <= int(row[0]) <= end]
    if not rows:
        return "no_candles", [], "provider returned an empty series"
    if not inside:
        return "no_candles_in_window", [], f"{len(rows)} candles, all outside the window"
    return "ok", inside, ""


def fetch_birdeye(mint: str, start: int, end: int, delay: float) -> tuple[str, list[list[Any]], str]:
    """Birdeye keys candles by *mint*, which is why it is worth having.

    8,324 journalled mints never carried a pool address, so a pool-keyed
    provider could never see them at all. It is also roughly fifteen times
    faster in practice.

    The catch, verified against a real journalled mint before this was written:
    Birdeye returns a candle for **every** minute whether or not anyone traded,
    carrying the last close forward. One rugged token returned 1,000 candles of
    which 963 had zero volume, still quoting a price sixteen hours after its
    final trade. Only candles with volume are stored, so a cached row means the
    same thing whichever provider filled it: a minute in which somebody traded.
    """
    from meme_flight_recorder.providers.birdeye import BirdeyeProvider

    provider = BirdeyeProvider()
    if not provider.configured:
        return "not_configured", [], "BIRDEYE_API_KEY is not set"
    for attempt in range(4):
        time.sleep(delay if attempt == 0 else 15.0 * attempt)
        try:
            series = provider.candles(mint, start, end)
        except HTTPError as error:
            if error.code == 429:
                continue
            return "error", [], f"HTTP {error.code}"
        except Exception as error:  # noqa: BLE001 - a network failure is not a measurement
            return "error", [], type(error).__name__
        if series is None:
            return "no_candles", [], "provider returned no items array"
        if not series.candles:
            return "no_candles", [], "never listed"
        traded = series.trades_only
        if not traded:
            # A real measurement: the token was quoted and nobody traded it.
            return "no_candles", [], f"{len(series.candles)} candles, none with volume"
        return (
            "ok",
            [
                [c.unix_time, c.open, c.high, c.low, c.close, c.volume]
                for c in traded
                if start <= c.unix_time <= end
            ],
            f"{len(series.candles) - len(traded)} silent minutes",
        )
    return "rate_limited", [], "429 after four attempts"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Stop after this many mints.")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--cache", default="data/pool_history.db")
    parser.add_argument(
        "--source",
        choices=("birdeye", "geckoterminal"),
        default="birdeye",
        help="Birdeye is keyed by mint and covers the whole population.",
    )
    arguments = parser.parse_args()
    # Each provider has its own measured floor, and neither may be lowered.
    floor = 1.05 if arguments.source == "birdeye" else DEFAULT_DELAY_SECONDS
    delay = max(arguments.delay, floor)

    load_env()
    settings = load_settings()
    queue = sorted(targets(str(settings.database_path), arguments.source), key=priority)
    print(f"mints with a pool and an entry price: {len(queue)}")

    cache = sqlite3.connect(arguments.cache)
    ensure_schema(cache)
    done = {
        row[0]
        for row in cache.execute("select mint from fetches where status in ('ok', 'no_candles')")
    }
    pending = [target for target in queue if target.mint not in done]
    print(f"already cached: {len(done)}   pending: {len(pending)}")
    if arguments.limit:
        pending = pending[: arguments.limit]

    counts: dict[str, int] = {}
    for index, target in enumerate(pending, start=1):
        start = int(target.observed_at.timestamp())
        end = start + WINDOW_MINUTES * 60
        series_key = target.mint if arguments.source == "birdeye" else target.pair
        if arguments.source == "birdeye":
            status, rows, detail = fetch_birdeye(target.mint, start, end, delay)
        else:
            status, rows, detail = fetch_window(target.pair, start, end, delay)
        counts[status] = counts.get(status, 0) + 1
        if rows:
            cache.executemany(
                "insert or replace into candles (pair, ts, open, high, low, close, volume) "
                "values (?, ?, ?, ?, ?, ?, ?)",
                [
                    (series_key, int(r[0]), *(float(v) if v is not None else None for v in r[1:6]))
                    for r in rows
                ],
            )
        cache.execute(
            "insert or replace into fetches (mint, pair, series_key, source, window_from, "
            "window_to, status, candle_count, first_candle_ts, last_candle_ts, detail, "
            "fetched_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                target.mint,
                target.pair,
                series_key,
                arguments.source,
                start,
                end,
                status,
                len(rows),
                int(rows[0][0]) if rows else None,
                int(rows[-1][0]) if rows else None,
                detail,
                datetime.now(UTC).isoformat(),
            ),
        )
        cache.commit()
        if index % 25 == 0 or index == len(pending):
            print(f"  {index}/{len(pending)}  {counts}", flush=True)
    print(f"done: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
