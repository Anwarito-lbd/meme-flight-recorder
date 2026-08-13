#!/usr/bin/env python3
"""Run stream-driven discovery live and measure what it actually finds.

Replaces a twenty-item vendor list with the chain. The question it has to answer
is not "does it connect" but "how many candidates per minute does it surface,
and what did it cost to surface them" -- because the whole reason discovery was
a vendor list is that the firehose looked too expensive to touch.

So every stage prints a count and the counts reconcile:

    messages -> parsed -> creation / trade / other -> fetched -> unique mints

`fetched` is the number that matters. If it tracks `creations` rather than
`messages`, the filter is doing its job and the firehose is affordable.

Writes `artifacts/stream_discovery.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meme_flight_recorder.env import load_env
from meme_flight_recorder.stream_discovery import (
    DiscoveryStats,
    MintRegistry,
    parse_notification,
)
from meme_flight_recorder.venues import VENUE_PROGRAM_IDS, Venue, decode_transaction

ARTIFACTS = Path("artifacts")
WATCHED = (Venue.PUMP_FUN, Venue.PUMP_SWAP, Venue.RAYDIUM_LAUNCHLAB, Venue.METEORA_DBC)


def rpc(url: str, method: str, params: list[Any], timeout: int = 20) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


async def run(ws_url: str, rpc_url: str, seconds: float, fetch_budget: int) -> dict:
    import websockets

    stats = DiscoveryStats()
    registry = MintRegistry()
    discovered: list[dict[str, Any]] = []
    latencies: list[float] = []
    fetch_failures: list[str] = []
    not_yet_available = 0
    no_mint_resolved = 0

    subscriptions: dict[int, Venue] = {}
    async with websockets.connect(ws_url, ping_interval=20, max_size=None) as socket:
        for index, venue in enumerate(WATCHED, start=1):
            await socket.send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": index,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [VENUE_PROGRAM_IDS[venue]]},
                            # `confirmed`, not `processed`. A processed
                            # transaction is not yet queryable by
                            # getTransaction, so the first version fetched 40
                            # signatures and resolved zero mints -- every
                            # lookup returned null because the transaction had
                            # not reached a queryable commitment.
                            {"commitment": "confirmed"},
                        ],
                    }
                )
            )
            reply = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            if isinstance(reply.get("result"), int):
                subscriptions[reply["result"]] = venue
        print(
            f"subscribed to {len(WATCHED)} venues "
            f"({len(subscriptions)} ids mapped), listening {seconds:.0f}s ..."
        )

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(
                    socket.recv(), timeout=max(0.1, deadline - time.monotonic())
                )
            except TimeoutError:
                break
            stats.messages += 1
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            event = parse_notification(message, subscriptions)
            if event is None:
                continue
            stats.observe(event)

            # The cost gate. Only creations justify an RPC call; trades are
            # counted and dropped, which is what makes the firehose affordable.
            if not event.worth_fetching or stats.fetched >= fetch_budget:
                continue
            started = time.perf_counter_ns()
            try:
                payload = rpc(
                    rpc_url,
                    "getTransaction",
                    [
                        event.signature,
                        {
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "confirmed",
                        },
                    ],
                ).get("result")
            except Exception as error:  # noqa: BLE001 - a failed fetch is not a discovery
                # Recorded, not silent: a fetch that fails is a gap in
                # discovery, which is different from a transaction that
                # contained nothing worth discovering.
                fetch_failures.append(type(error).__name__)
                continue
            stats.fetched += 1
            latencies.append((time.perf_counter_ns() - started) / 1e6)
            if not payload:
                # Not yet queryable even at confirmed. Counted so that "found
                # nothing" is distinguishable from "asked too early".
                not_yet_available += 1
                continue
            decoded = decode_transaction(payload)
            mint = decoded.primary_mint
            if not mint:
                no_mint_resolved += 1
                continue
            if registry.add(mint):
                discovered.append(
                    {
                        "mint": mint,
                        "venue": event.venue.value,
                        "slot": decoded.slot,
                        "signature": decoded.signature,
                        "succeeded": decoded.succeeded,
                        "instructions": list(event.instructions)[:6],
                        "first_seen": datetime.now(UTC).isoformat(),
                    }
                )
                print(f"  + {mint}  {event.venue.value}  slot={decoded.slot}")

    stats.unique_mints = len(registry)
    stats.duplicates = registry.duplicates

    def percentiles(values: list[float]) -> dict[str, float]:
        if not values:
            return {}
        ordered = sorted(values)
        return {
            "p50": ordered[len(ordered) // 2],
            "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            "max": ordered[-1],
        }

    return {
        "seconds": seconds,
        "messages": stats.messages,
        "parsed": stats.parsed,
        "creations": stats.creations,
        "trades": stats.trades,
        "other": stats.other,
        "fetched": stats.fetched,
        "unique_mints": stats.unique_mints,
        "duplicates": stats.duplicates,
        "reconciles": stats.reconciles(),
        "by_venue": stats.by_venue,
        "top_instructions": dict(
            sorted(stats.by_instruction.items(), key=lambda item: -item[1])[:12]
        ),
        "fetch_latency_ms": percentiles(latencies),
        "fetch_failures": len(fetch_failures),
        "not_yet_available": not_yet_available,
        "no_mint_resolved": no_mint_resolved,
        "discovered": discovered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--fetch-budget", type=int, default=60)
    arguments = parser.parse_args()

    load_env()
    key = os.getenv("HELIUS_API_KEY")
    if not key:
        print("HELIUS_API_KEY missing")
        return 1

    result = asyncio.run(
        run(
            f"wss://mainnet.helius-rpc.com/?api-key={key}",
            f"https://mainnet.helius-rpc.com/?api-key={key}",
            arguments.seconds,
            arguments.fetch_budget,
        )
    )

    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "stream_discovery.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )

    seconds = max(result["seconds"], 1e-9)
    print("\n=== stream discovery ===")
    print(f"  messages          {result['messages']:>8}  ({result['messages'] / seconds:.0f}/s)")
    print(f"  parsed            {result['parsed']:>8}")
    print(f"  creations         {result['creations']:>8}")
    print(f"  trades            {result['trades']:>8}")
    print(f"  other             {result['other']:>8}")
    print(f"  buckets reconcile {result['reconciles']!s:>8}")
    print(f"  FETCHED           {result['fetched']:>8}  <- the cost gate")
    print(f"  not yet queryable {result['not_yet_available']:>8}")
    print(f"  no mint resolved  {result['no_mint_resolved']:>8}")
    print(f"  fetch failures    {result['fetch_failures']:>8}")
    print(f"  unique mints      {result['unique_mints']:>8}")
    print(f"  duplicates        {result['duplicates']:>8}")
    if result["fetch_latency_ms"]:
        detail = "  ".join(f"{k}={v:.1f}ms" for k, v in result["fetch_latency_ms"].items())
        print(f"  fetch latency     {detail}")
    print(f"  by venue          {result['by_venue']}")
    rate = result["unique_mints"] / seconds * 3600
    print(f"\n  discovery rate    {rate:.0f} unique mints/hour")
    print("  vendor baseline         20 pools/query (CoinGecko trending, entire universe)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
