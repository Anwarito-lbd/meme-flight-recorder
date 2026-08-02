"""Backfill public Solana wallet history into immutable raw page files.

This script is read-only. It requires Helius Wallet API access and never accepts a
private key, seed phrase, transaction, or destination address.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meme_flight_recorder.providers.helius import HeliusProvider


def registry_wallets(registry: dict[str, Any]) -> tuple[str, ...]:
    addresses = []
    for candidate in registry.get("wallet_candidates", []):
        if candidate.get("chain") == "solana" and candidate.get("address"):
            addresses.append(str(candidate["address"]))
    return tuple(dict.fromkeys(addresses))


def write_page(
    output_dir: Path,
    address: str,
    page_number: int,
    cursor_used: str | None,
    raw: dict[str, Any],
) -> Path:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    envelope = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "address": address,
        "page_number": page_number,
        "cursor_used": cursor_used,
        "payload_sha256": digest,
        "raw": raw,
    }
    folder = output_dir / address
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"page-{page_number:05d}-{digest[:12]}.json"
    # Unique content-derived names prevent a later run from silently overwriting evidence.
    path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--registry", type=Path, default=Path("research/trader_signal_registry.json")
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/wallet-history"))
    parser.add_argument("--max-pages", type=int, default=5)
    parser.add_argument("--wallet", action="append", dest="wallets")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.max_pages < 1:
        raise ValueError("max-pages must be at least one")
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    wallets = tuple(args.wallets or registry_wallets(registry))
    if not wallets:
        raise ValueError("No Solana wallets selected")
    provider = HeliusProvider()
    for address in wallets:
        cursor: str | None = None
        total = 0
        for page_number in range(1, args.max_pages + 1):
            page = provider.wallet_history(address, before=cursor)
            path = write_page(args.output_dir, address, page_number, cursor, page.raw)
            total += len(page.transactions)
            print(
                json.dumps(
                    {
                        "address": address,
                        "page": page_number,
                        "transactions": len(page.transactions),
                        "total": total,
                        "has_more": page.has_more,
                        "evidence_file": str(path),
                    }
                )
            )
            if not page.has_more:
                break
            cursor = page.next_cursor


if __name__ == "__main__":
    main()
