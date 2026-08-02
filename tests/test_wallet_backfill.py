from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "backfill_wallet_history.py"
SPEC = importlib.util.spec_from_file_location("backfill_wallet_history", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_registry_wallets_deduplicates_and_rejects_other_chains() -> None:
    registry = {
        "wallet_candidates": [
            {"chain": "solana", "address": "A"},
            {"chain": "ethereum", "address": "B"},
            {"chain": "solana", "address": "A"},
            {"chain": "solana", "address": "C"},
        ]
    }
    assert MODULE.registry_wallets(registry) == ("A", "C")


def test_evidence_pages_are_content_addressed_and_do_not_contain_secrets(tmp_path) -> None:
    raw = {"data": [{"signature": "sig"}], "pagination": {"hasMore": False}}
    path = MODULE.write_page(tmp_path, "wallet", 1, None, raw)
    payload = json.loads(path.read_text())
    assert path.name.startswith("page-00001-")
    assert len(payload["payload_sha256"]) == 64
    assert payload["raw"] == raw
    assert "api_key" not in path.read_text().lower()
