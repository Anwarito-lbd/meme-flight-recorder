#!/usr/bin/env python3
"""Disconnect the live stream on purpose, and prove the missed events come back.

Claiming a feed is reliable because it has not visibly broken is not evidence.
This breaks it deliberately: listen, kill the socket mid-flight, stay down long
enough for the chain to move on, reconnect, then re-fetch the slots we were not
listening to and show that the hole closes.

    listen -> record (slot, transaction_index, signature)
    -> DISCONNECT for N seconds
    -> reconnect, record the first slot seen after
    -> fetch canonical history for the gap
    -> report matched / missed / recovered / unresolved

PASS requires `unresolved == 0` **and** `recovered > 0`. Recovering nothing is
not a pass: it usually means the gap was empty, and a test that cannot fail
proves nothing.

Writes `artifacts/reconciliation_proof.json`.
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
from meme_flight_recorder.reconciliation import EventIdentity, SlotReconciler
from meme_flight_recorder.venues import VENUE_PROGRAM_IDS, Venue

ARTIFACTS = Path("artifacts")
# One venue, so the canonical re-fetch is a bounded query. Pump.fun is the
# busiest launch program, which makes the gap non-empty in a few seconds.
PROGRAM = VENUE_PROGRAM_IDS[Venue.PUMP_FUN]


def rpc(url: str, method: str, params: list[Any], timeout: int = 30) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


async def listen(url: str, seconds: float, reconciler: SlotReconciler) -> tuple[int, int]:
    """Subscribe to program logs and record identities. Returns (first, last) slot."""
    import websockets

    first_slot = 0
    last_slot = 0
    async with websockets.connect(url, ping_interval=20, max_size=None) as socket:
        await socket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "logsSubscribe",
                    "params": [{"mentions": [PROGRAM]}, {"commitment": "confirmed"}],
                }
            )
        )
        await asyncio.wait_for(socket.recv(), timeout=15)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(
                    socket.recv(), timeout=max(0.1, deadline - time.monotonic())
                )
            except TimeoutError:
                break
            message = json.loads(raw)
            value = (
                ((message.get("params") or {}).get("result") or {}).get("value") or {}
            )
            slot = ((message.get("params") or {}).get("result") or {}).get("context", {}).get(
                "slot"
            )
            signature = value.get("signature")
            if not signature or slot is None:
                continue
            # logsSubscribe does not carry a transaction index. It is None here
            # rather than 0: a fabricated index would make two different
            # transactions in one slot look like the same event.
            reconciler.observe_live(EventIdentity(int(slot), None, str(signature)))
            first_slot = first_slot or int(slot)
            last_slot = max(last_slot, int(slot))
    return first_slot, last_slot


def canonical_for_slots(rpc_url: str, low: int, high: int, cap: int = 400) -> list[EventIdentity]:
    """Signatures the chain holds for the program across a slot range.

    Walked backwards from the newest signature because that is the only
    direction the endpoint supports, and stopped once below the gap.
    """
    found: list[EventIdentity] = []
    before: str | None = None
    for _page in range(6):
        params: dict[str, Any] = {"limit": 1000}
        if before:
            params["before"] = before
        result = rpc(rpc_url, "getSignaturesForAddress", [PROGRAM, params]).get("result") or []
        if not result:
            break
        for entry in result:
            slot = int(entry.get("slot") or 0)
            if low <= slot <= high:
                found.append(EventIdentity(slot, None, str(entry["signature"])))
        oldest = min(int(entry.get("slot") or 0) for entry in result)
        before = result[-1]["signature"]
        if oldest < low or len(found) >= cap:
            break
    return found


async def run(rpc_url: str, ws_url: str, listen_seconds: float, outage_seconds: float) -> dict:
    reconciler = SlotReconciler()

    print(f"listening {listen_seconds:.0f}s ...")
    _first, last_before = await listen(ws_url, listen_seconds, reconciler)
    before_count = len(reconciler.live)
    print(f"  {before_count} events, last slot {last_before}")

    print(f"DISCONNECTED for {outage_seconds:.0f}s (socket closed, nothing listening)")
    await asyncio.sleep(outage_seconds)

    print(f"reconnecting, listening {listen_seconds:.0f}s ...")
    first_after, _last = await listen(ws_url, listen_seconds, reconciler)
    print(f"  {len(reconciler.live) - before_count} new events, first slot {first_after}")

    reconciler.mark_disconnect(last_before, first_after)
    gaps = reconciler.gaps
    print(f"outage slot gap: {gaps}")

    # Everything we DID see is canonical too; fold it in so matched is real.
    for identity in list(reconciler.live.values()):
        reconciler.observe_canonical(identity)

    total_recovered = 0
    for low, high in gaps:
        found = canonical_for_slots(rpc_url, low, high)
        total_recovered += reconciler.recover(found)
        print(f"  canonical fetch for slots {low}-{high}: {len(found)} signatures")

    report = reconciler.report()
    print(f"recovered {total_recovered} events that the stream never delivered")
    return {
        "listen_seconds": listen_seconds,
        "outage_seconds": outage_seconds,
        "last_slot_before_outage": last_before,
        "first_slot_after_outage": first_after,
        "gaps": [list(gap) for gap in gaps],
        **report.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-seconds", type=float, default=12.0)
    parser.add_argument("--outage-seconds", type=float, default=20.0)
    arguments = parser.parse_args()

    load_env()
    key = os.getenv("HELIUS_API_KEY")
    if not key:
        print("HELIUS_API_KEY missing -- cannot prove anything")
        return 1

    result = asyncio.run(
        run(
            f"https://mainnet.helius-rpc.com/?api-key={key}",
            f"wss://mainnet.helius-rpc.com/?api-key={key}",
            arguments.listen_seconds,
            arguments.outage_seconds,
        )
    )

    # Recovering nothing is not a pass: it means the gap was empty and the test
    # could not have failed.
    result["result"] = (
        "PASS" if result["unresolved"] == 0 and result["recovered"] > 0 else "FAIL"
    )
    result["generated_at"] = datetime.now(UTC).isoformat()

    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "reconciliation_proof.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )

    print("\n=== reconciliation ===")
    for name in (
        "live_observed",
        "canonical_observed",
        "duplicates",
        "matched",
        "missed_live",
        "recovered",
        "unresolved",
        "reconnects",
        "largest_gap_slots",
        "reconciliation_pct",
    ):
        print(f"  {name:<22}{result[name]}")
    print(f"\nresult: {result['result']}")
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
