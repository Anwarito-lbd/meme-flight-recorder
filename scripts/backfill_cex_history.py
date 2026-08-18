#!/usr/bin/env python3
"""Cache deep candle history for established meme pairs on a public CEX.

Why this exists. Every measurement in this project so far was taken on newborn
Solana launchpad tokens, and STATUS.md records what that population did: 84.2%
of 18,942 mints no longer quote at all, recall against winners is zero, and
selection, exits, waiting and sizing were each measured and each came back
negative. The conclusion recorded there is that what remains is "a different
population or more capital -- not a different rule". This script supplies the
first half of that: the price history needed to test whether an *established*
meme population pays, using the same studies and the same cost discipline.

The population is deliberately the opposite of the one already measured. These
are assets months to years old, quoted continuously, on books deep enough that
a sub-dollar position is invisible. Nothing here is a claim that they pay. It
is the data required to find out.

**Kraken cannot supply this, measured rather than assumed.** The obvious source
was Kraken, because `scripts/select_kraken_pairs.py` already resolves a research
universe there and `config/default.toml` already carries a `[safety.cex]`
profile. Asked for a year of 15m candles, Kraken **ignores `since`** and returns
only the most recent 720 rows -- 7.5 days at 15m, 30 days at 1h. That cannot
support the 30-trade bar the evidence gate requires, so the measurement is taken
on an exchange that paginates and the execution-venue question is kept separate.
Coinbase behaves the same way; binance, bybit and kucoin honour `since`.

Three rules this cache exists to protect, inherited unchanged from
`backfill_pool_history.py` because each was paid for with a real defect:

  * **A rate limit is not an absence of trading.** Every fetch records its own
    status. A transport failure is stored as `error`, never as "no candles",
    because the latter would enter a study as evidence about the market.
  * **Absent is not zero.** A symbol that returns nothing is stored as
    `no_candles` with a count of zero -- a measurement, and distinguishable from
    a symbol that was never requested.
  * **A candle with no volume is not a price.** Birdeye returned 963 zero-volume
    candles for a rugged token, carrying the last close forward sixteen hours
    past its final trade. Volume is stored on every row and counted per series
    so a consumer can drop them; this script never invents a print.

The journal is not touched. It is append-only and hash-chained and holds
decisions; this is externally fetched market data, so it lives in its own
database and can be deleted and rebuilt at will.
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import ccxt

# The established meme universe, matching `scripts/select_kraken_pairs.py` so
# that the population measured here is the population that script would resolve
# for execution. The quote currency is substituted per exchange.
DEFAULT_BASES = (
    "DOGE",
    "SHIB",
    "PEPE",
    "BONK",
    "WIF",
    "TRUMP",
    "PENGU",
    "FLOKI",
    "FARTCOIN",
)

# Measured, not documented: `since` is honoured by binance/bybit/kucoin and
# ignored by kraken/coinbase, which clamp to their most recent window whatever
# is asked. Only an exchange that walks forward from `since` can supply a year.
PAGINATING_EXCHANGES = ("binance", "bybit", "kucoin")

SCHEMA = """
create table if not exists fetches (
    exchange text not null,
    symbol text not null,
    timeframe text not null,
    status text not null,
    detail text,
    candle_count integer not null default 0,
    zero_volume_count integer not null default 0,
    first_candle_ts integer,
    last_candle_ts integer,
    requested_since_ts integer,
    fetched_at text not null,
    primary key (exchange, symbol, timeframe)
);
create table if not exists candles (
    exchange text not null,
    symbol text not null,
    timeframe text not null,
    ts integer not null,
    open real, high real, low real, close real, volume real,
    primary key (exchange, symbol, timeframe, ts)
);
"""


@dataclass(frozen=True)
class FetchOutcome:
    status: str
    detail: str | None
    rows: list[list[float]]


def open_cache(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    return connection


def fetch_series(
    exchange: Any, symbol: str, timeframe: str, since_ms: int, page_limit: int
) -> FetchOutcome:
    """Walk forward from `since_ms` to now, one page at a time.

    Pagination advances by the last timestamp seen plus one millisecond. Two
    guards matter. A page that returns nothing ends the walk, and a page whose
    last timestamp fails to advance also ends it -- without the second, an
    exchange that clamps `since` to a fixed recent window returns the same page
    for ever and the loop never terminates, which is precisely what Kraken does.

    A transport failure after some data has already arrived is `partial`, not
    `error` and not `ok`: the candles that did arrive are real and worth keeping,
    but a consumer must be able to tell that the series is short because the
    fetch stopped rather than because the market did.
    """
    collected: list[list[float]] = []
    cursor = since_ms
    seen_last = -1
    while True:
        try:
            page = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=page_limit)
        except ccxt.BadSymbol as error:
            return FetchOutcome("not_listed", str(error)[:200], [])
        except Exception as error:  # noqa: BLE001 - any transport failure is 'error', never 'empty'
            detail = f"{type(error).__name__}: {error}"[:200]
            if collected:
                return FetchOutcome("partial", detail, collected)
            return FetchOutcome("error", detail, [])
        if not page:
            break
        last_ts = int(page[-1][0])
        if last_ts <= seen_last:
            break
        seen_last = last_ts
        collected.extend(page)
        if len(page) < page_limit:
            break
        cursor = last_ts + 1
    if not collected:
        return FetchOutcome("no_candles", None, [])
    return FetchOutcome("ok", None, collected)


def store(
    cache: sqlite3.Connection,
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    outcome: FetchOutcome,
) -> dict[str, Any]:
    deduped: dict[int, list[float]] = {int(row[0]): row for row in outcome.rows}
    rows = [deduped[key] for key in sorted(deduped)]
    zero_volume = sum(1 for row in rows if not row[5])
    cache.executemany(
        "insert or replace into candles"
        " (exchange, symbol, timeframe, ts, open, high, low, close, volume)"
        " values (?,?,?,?,?,?,?,?,?)",
        [
            (exchange_id, symbol, timeframe, int(r[0]), r[1], r[2], r[3], r[4], r[5])
            for r in rows
        ],
    )
    cache.execute(
        "insert or replace into fetches"
        " (exchange, symbol, timeframe, status, detail, candle_count, zero_volume_count,"
        "  first_candle_ts, last_candle_ts, requested_since_ts, fetched_at)"
        " values (?,?,?,?,?,?,?,?,?,?,?)",
        (
            exchange_id,
            symbol,
            timeframe,
            outcome.status,
            outcome.detail,
            len(rows),
            zero_volume,
            int(rows[0][0]) if rows else None,
            int(rows[-1][0]) if rows else None,
            since_ms,
            datetime.now(UTC).isoformat(),
        ),
    )
    cache.commit()
    span_days = round((rows[-1][0] - rows[0][0]) / 86_400_000, 1) if len(rows) > 1 else 0.0
    return {
        "symbol": symbol,
        "status": outcome.status,
        "detail": outcome.detail,
        "candles": len(rows),
        "zero_volume": zero_volume,
        "span_days": span_days,
    }


def resolve_symbols(exchange: Any, bases: tuple[str, ...], quote: str) -> list[str]:
    markets = exchange.load_markets()
    resolved = []
    for base in bases:
        symbol = f"{base}/{quote}"
        market = markets.get(symbol)
        if market and market.get("active") is not False and market.get("spot", True):
            resolved.append(symbol)
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/cex-history.db")
    parser.add_argument("--exchange", default="binance", choices=PAGINATING_EXCHANGES)
    parser.add_argument("--quote", default="USDT")
    parser.add_argument("--bases", nargs="*", default=list(DEFAULT_BASES))
    parser.add_argument("--timeframes", nargs="*", default=["15m", "1h"])
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--delay", type=float, default=0.25)
    arguments = parser.parse_args()

    exchange = getattr(ccxt, arguments.exchange)({"enableRateLimit": True})
    symbols = resolve_symbols(exchange, tuple(arguments.bases), arguments.quote)
    missing = [b for b in arguments.bases if f"{b}/{arguments.quote}" not in symbols]
    print(f"exchange={arguments.exchange} quote={arguments.quote}")
    print(f"resolved {len(symbols)} of {len(arguments.bases)} bases")
    print(f"not listed: {missing or 'none'}")

    since_ms = int(time.time() * 1000) - arguments.days * 86_400_000
    cache = open_cache(arguments.cache)
    tallies: dict[str, int] = {}
    for timeframe in arguments.timeframes:
        print(f"\n=== {timeframe} ===")
        header = f"{'symbol':<16}{'status':>10}{'candles':>9}{'zero_vol':>10}{'span_d':>9}"
        print(header)
        print("-" * len(header))
        for symbol in symbols:
            outcome = fetch_series(exchange, symbol, timeframe, since_ms, arguments.page_limit)
            summary = store(cache, arguments.exchange, symbol, timeframe, since_ms, outcome)
            tallies[summary["status"]] = tallies.get(summary["status"], 0) + 1
            print(
                f"{symbol:<16}{summary['status']:>10}{summary['candles']:>9}"
                f"{summary['zero_volume']:>10}{summary['span_days']:>9}"
            )
            if summary["detail"]:
                print(f"    detail: {summary['detail']}")
            time.sleep(arguments.delay)
    cache.close()

    # Reconciling denominator: every requested series lands in exactly one
    # bucket, and the buckets must sum to the total requested.
    requested = len(symbols) * len(arguments.timeframes)
    print("\n=== fetch outcomes ===")
    for status, count in sorted(tallies.items()):
        print(f"{status:<12}{count:>5}")
    print(f"{'total':<12}{sum(tallies.values()):>5} of {requested} requested")
    if sum(tallies.values()) != requested:
        print("MISMATCH: some requested series were not accounted for")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
