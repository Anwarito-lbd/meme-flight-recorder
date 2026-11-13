#!/usr/bin/env python3
"""Which Helius streaming path is actually available on this key, and how fast?

Track D says: probe `transactionSubscribe` first, and fall back to standard
WebSocket `logsSubscribe` on four program logs plus slot monitoring if it is not
available. That is a question about this account, not about the documentation,
so it is measured rather than assumed -- `transactionSubscribe` is an Enhanced
WebSocket feature and is not on every plan.

Latency is measured on a **monotonic** clock throughout. Wall-clock timestamps
drift and go backwards over NTP corrections, and a hot-path budget measured on
one is not a measurement. Stage boundaries recorded here are:

    connect -> subscribe -> first event -> decode

No HTTP polling anywhere, and nothing here trades. It opens a socket, listens,
prints percentiles and exits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

from meme_flight_recorder.env import load_env

# The four venues a Solana launch actually crosses. Narrow filters matter: an
# unfiltered subscription on mainnet delivers far more than can be decoded, and
# the queue backs up until the stream is meaningless.
PROGRAMS: dict[str, str] = {
    "pump_fun": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "pump_swap": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "raydium_amm_v4": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "meteora_dlmm": "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
}


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]

    return {
        "n": float(len(ordered)),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
    }


async def probe(url: str, seconds: float) -> dict[str, object]:
    import websockets

    report: dict[str, object] = {}
    started = time.monotonic()
    async with websockets.connect(url, ping_interval=20, max_size=None) as socket:
        report["connect_ms"] = (time.monotonic() - started) * 1000.0

        # 1. Does this key have Enhanced WebSockets?
        await socket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "transactionSubscribe",
                    "params": [
                        {"failed": False, "accountInclude": [PROGRAMS["pump_fun"]]},
                        {
                            "commitment": "processed",
                            "encoding": "jsonParsed",
                            "transactionDetails": "full",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                }
            )
        )
        raw = await asyncio.wait_for(socket.recv(), timeout=15)
        first = json.loads(raw)
        enhanced = "result" in first and "error" not in first
        report["transaction_subscribe"] = "available" if enhanced else "unavailable"
        report["transaction_subscribe_detail"] = (
            "" if enhanced else str(first.get("error", ""))[:200]
        )

        if not enhanced:
            # 2. Fall back: four program log subscriptions plus slot monitoring.
            for index, (name, program) in enumerate(PROGRAMS.items(), start=10):
                await socket.send(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": index,
                            "method": "logsSubscribe",
                            "params": [
                                {"mentions": [program]},
                                {"commitment": "processed"},
                            ],
                        }
                    )
                )
                await asyncio.wait_for(socket.recv(), timeout=15)
                report[f"subscribed_{name}"] = True
            await socket.send(
                json.dumps({"jsonrpc": "2.0", "id": 99, "method": "slotSubscribe"})
            )
            await asyncio.wait_for(socket.recv(), timeout=15)
            report["slot_monitoring"] = True

        report["subscribe_ms"] = (time.monotonic() - started) * 1000.0

        # 3. Listen, and measure the gap between consecutive events and the cost
        #    of decoding each one. Both are hot-path stages.
        gaps: list[float] = []
        decode: list[float] = []
        events = 0
        slots = 0
        deadline = time.monotonic() + seconds
        last = time.monotonic()
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                break
            now = time.monotonic()
            gaps.append((now - last) * 1000.0)
            last = now
            decode_started = time.monotonic()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            decode.append((time.monotonic() - decode_started) * 1000.0)
            method = message.get("method") or ""
            if method == "slotNotification":
                slots += 1
            else:
                events += 1

        report["events"] = events
        report["slot_notifications"] = slots
        report["listen_seconds"] = seconds
        report["inter_event_ms"] = percentiles(gaps)
        report["decode_ms"] = percentiles(decode)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=20.0)
    arguments = parser.parse_args()

    load_env()
    key = os.getenv("HELIUS_API_KEY")
    if not key:
        print("HELIUS_API_KEY is not set -- cannot probe. This is not a measurement.")
        return 1

    try:
        report = asyncio.run(probe(f"wss://mainnet.helius-rpc.com/?api-key={key}", arguments.seconds))
    except Exception as error:  # noqa: BLE001 - the probe reports failures, it does not raise
        print(f"probe failed: {type(error).__name__}: {error}")
        return 1

    for name, value in report.items():
        if isinstance(value, dict):
            if not value:
                print(f"{name:<28} (no samples)")
                continue
            rendered = "  ".join(f"{k}={v:.2f}" for k, v in value.items())
            print(f"{name:<28} {rendered}")
        else:
            print(f"{name:<28} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
