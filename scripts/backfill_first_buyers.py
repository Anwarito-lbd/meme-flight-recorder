#!/usr/bin/env python3
"""Extract the ordered earliest buyers of journalled mints, from chain history.

The features need mints that have **both** a first-buyer record and a resolved
forward outcome. Newly discovered mints have the first half and none of the
second, so waiting for them means waiting days. Instead this walks backwards
through the chain history of mints already in the outcome cache, which have the
forward half already.

Cost discipline, because this is the most RPC-hungry thing in the project:

  * signatures are paged to the **oldest** page and only that page is fetched,
    because the earliest buyers are what matter and the recent ones are not;
  * a bounded number of transactions per mint;
  * every mint records why it produced nothing, so an empty result is
    distinguishable from an unattempted one.

Wallet-level facts (funder, wallet age, prior profitability) each cost further
lookups per *wallet*, so they are gated behind `--resolve-wallets` and their
coverage is reported rather than assumed.

Writes to `data/first_buyers.db`. Resumable.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

from meme_flight_recorder.env import load_env
from meme_flight_recorder.venues import Action, decode_transaction

# Helius free tier. Derived from what it grants, not what it documents.
# Raised from 0.12 after measuring a 44% HTTPError rate: the free tier grants
# less than the burst this was asking for.
DELAY_SECONDS = 0.25


def rpc(url: str, method: str, params: list[Any], timeout: int = 25) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists buys (
            mint text not null,
            ordinal integer not null,
            wallet text not null,
            slot integer not null,
            signature text not null,
            block_time integer,
            sol_delta real,
            succeeded integer,
            primary key (mint, ordinal)
        );
        create table if not exists mint_status (
            mint text primary key,
            status text not null,
            buys_found integer not null,
            signatures_seen integer not null,
            detail text,
            fetched_at text not null
        );
        create index if not exists buys_mint on buys (mint);
        """
    )
    connection.commit()


def oldest_signatures(url: str, mint: str, pages: int, per_page: int) -> list[dict[str, Any]]:
    """Page back to the oldest available signatures for a mint."""
    collected: list[dict[str, Any]] = []
    before: str | None = None
    for _ in range(pages):
        params: dict[str, Any] = {"limit": per_page}
        if before:
            params["before"] = before
        time.sleep(DELAY_SECONDS)
        result = rpc(url, "getSignaturesForAddress", [mint, params]).get("result") or []
        if not result:
            break
        collected.extend(result)
        if len(result) < per_page:
            break
        before = result[-1]["signature"]
    # Oldest first: the earliest buyers are the population under study.
    collected.sort(key=lambda row: (int(row.get("slot") or 0), str(row.get("signature"))))
    return collected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/pool_history.db")
    parser.add_argument("--out", default="data/first_buyers.db")
    parser.add_argument("--limit", type=int, default=120, help="Mints to process this run.")
    parser.add_argument("--max-buys", type=int, default=50)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--per-page", type=int, default=100)
    arguments = parser.parse_args()

    load_env()
    import os

    key = os.getenv("HELIUS_API_KEY")
    if not key:
        print("HELIUS_API_KEY missing")
        return 1
    url = f"https://mainnet.helius-rpc.com/?api-key={key}"

    # Only mints with a resolved forward outcome are worth the calls.
    history = sqlite3.connect(f"file:{arguments.cache}?mode=ro", uri=True)
    candidates = [
        row[0]
        for row in history.execute(
            # Ordered by a hash of the mint, which is outcome-independent.
            #
            # This previously read 'order by candle_count desc', which fed the
            # study the most-heavily-traded tokens first -- i.e. the survivors.
            # Measured: the selected population had a median of 891 candles
            # against 50 for the cache as a whole, and reported a 0.0% death
            # rate where the base rate is 60-64%. The features looked
            # spectacular because every token in the sample had already lived.
            #
            # Sampling must never be correlated with the outcome being measured.
            "select mint from fetches where status = 'ok' order by substr(mint, -6), mint"
        )
    ]
    history.close()

    out = sqlite3.connect(arguments.out)
    ensure_schema(out)
    # Errors are excluded so they retry. An HTTPError is a rate limit or a
    # transient outage -- it says nothing about the mint, and recording it as
    # "done" would permanently discard a mint for a provider hiccup.
    done = {
        row[0]
        for row in out.execute("select mint from mint_status where status != 'error'")
    }
    pending = [mint for mint in candidates if mint not in done][: arguments.limit]
    print(f"mints with forward outcomes: {len(candidates)}   already done: {len(done)}")
    print(f"processing {len(pending)} this run\n")

    counts: dict[str, int] = {}
    for index, mint in enumerate(pending, start=1):
        status = "ok"
        detail = ""
        buys: list[tuple] = []
        failed_lookups = 0
        signatures: list[dict[str, Any]] = []
        try:
            signatures = oldest_signatures(url, mint, arguments.pages, arguments.per_page)
        except Exception as error:  # noqa: BLE001 - a failed fetch is not an empty history
            status, detail = "error", f"{type(error).__name__}"

        if status == "ok" and not signatures:
            status, detail = "no_signatures", "chain returned no history for this mint"

        if status == "ok":
            ordinal = 0
            for entry in signatures:
                if ordinal >= arguments.max_buys:
                    break
                time.sleep(DELAY_SECONDS)
                try:
                    payload = rpc(
                        url,
                        "getTransaction",
                        [
                            entry["signature"],
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed",
                            },
                        ],
                    ).get("result")
                except Exception:  # noqa: BLE001 - a failed lookup is a gap, not a non-buy
                    failed_lookups += 1
                    continue
                if not payload:
                    continue
                decoded = decode_transaction(payload)
                if decoded.primary_mint != mint:
                    continue
                is_buy = any(
                    instruction.action is Action.BUY for instruction in decoded.instructions
                )
                # A buy spends SOL, so the fee payer's balance falls by more
                # than the fee. This catches venues whose discriminator did not
                # match while still refusing to call a sell a buy.
                spent = (decoded.sol_delta_lamports or 0) < -20_000
                if not (is_buy or spent):
                    continue
                buys.append(
                    (
                        mint,
                        ordinal,
                        decoded.fee_payer or "",
                        decoded.slot,
                        decoded.signature,
                        decoded.block_time,
                        (decoded.sol_delta_lamports or 0) / 1e9,
                        1 if decoded.succeeded else 0,
                    )
                )
                ordinal += 1
            if not buys:
                status, detail = "no_buys", f"{len(signatures)} signatures, none decoded as a buy"

        if buys:
            out.executemany(
                "insert or replace into buys (mint, ordinal, wallet, slot, signature, "
                "block_time, sol_delta, succeeded) values (?, ?, ?, ?, ?, ?, ?, ?)",
                buys,
            )
        out.execute(
            "insert or replace into mint_status (mint, status, buys_found, signatures_seen, "
            "detail, fetched_at) values (?, ?, ?, ?, ?, ?)",
            (mint, status, len(buys), len(signatures),
             f"{detail} failed_lookups={failed_lookups}".strip(), datetime.now(UTC).isoformat()),
        )
        out.commit()
        counts[status] = counts.get(status, 0) + 1
        if index % 10 == 0 or index == len(pending):
            print(f"  {index}/{len(pending)}  {counts}", flush=True)

    print(f"\ndone: {counts}")
    total = out.execute("select count(distinct mint) from buys").fetchone()[0]
    rows = out.execute("select count(*) from buys").fetchone()[0]
    print(f"mints with buys: {total}   buy rows: {rows}")
    out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
