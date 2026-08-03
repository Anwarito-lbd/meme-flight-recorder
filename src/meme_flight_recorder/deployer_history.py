"""Fetching and caching deployer wallet history.

Kept separate from ``deployer.py`` so that the assessment stays pure and
testable without a network, while the parts that spend API budget and touch the
filesystem live in one place.

**Caching is not an optimisation here, it is a correctness requirement.** A
wallet's history at the moment a token was observed cannot be re-fetched later:
the endpoint returns the wallet as it is now. Once a deployer has been seen, the
pages are evidence and are written under content-derived names so a later run
cannot silently overwrite them -- the same discipline
``scripts/backfill_wallet_history.py`` already applies.

Deployers repeat across launches, which is the whole reason Stage 3 exists, so
the cache hit rate rises quickly and the marginal cost per candidate falls
toward zero.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .deployer import DeployerEvidence, extract_evidence

DEFAULT_CACHE_DIR = Path("data/deployer-history")


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def cached_pages(address: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> list[dict[str, Any]]:
    """Return every cached page for a wallet, oldest fetch first."""
    folder = cache_dir / address
    if not folder.is_dir():
        return []
    pages: list[dict[str, Any]] = []
    for path in sorted(folder.glob("page-*.json")):
        try:
            pages.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            # A corrupt page is missing evidence, not empty evidence. Skipping
            # it leaves the history truncated, which downgrades the verdict to
            # UNRESOLVED rather than clearing the wallet on a partial read.
            continue
    return pages


def write_page(
    address: str,
    page_number: int,
    raw: dict[str, Any],
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Path:
    """Persist one fetched page under a content-derived name."""
    digest = _digest(raw)
    folder = cache_dir / address
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"page-{page_number:05d}-{digest[:12]}.json"
    envelope = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "address": address,
        "page_number": page_number,
        "payload_sha256": digest,
        "raw": raw,
    }
    path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    return path


def evidence_from_cache(
    address: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    *,
    vendor_labels: Any | None = None,
) -> DeployerEvidence | None:
    """Rebuild evidence from cached pages without touching the network."""
    pages = cached_pages(address, cache_dir)
    if not pages:
        return None
    transactions: list[dict[str, Any]] = []
    truncated = False
    for page in pages:
        raw = page.get("raw") or {}
        data = raw.get("data")
        if isinstance(data, list):
            transactions.extend(item for item in data if isinstance(item, dict))
        # Any page reporting more available data means the record is partial.
        if (raw.get("pagination") or {}).get("hasMore"):
            truncated = True
    return extract_evidence(
        address,
        transactions,
        history_truncated=truncated,
        vendor_labels=vendor_labels,
    )


def fetch_evidence(
    address: str,
    provider: Any,
    *,
    max_pages: int = 3,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    vendor_labels: Any | None = None,
    refresh: bool = False,
) -> DeployerEvidence | None:
    """Return deployer evidence, fetching only what the cache lacks.

    ``provider`` needs a ``wallet_history(address, before=...)`` method; the
    Helius adapter supplies one. A provider failure returns whatever the cache
    holds rather than raising, because a deployer whose history cannot be
    fetched must still produce a verdict -- UNRESOLVED -- instead of taking the
    whole collector cycle down with it.

    ``max_pages`` is deliberately small. This runs per candidate on a free API
    tier, and an unbounded walk through a busy wallet would exhaust the budget
    on one address.
    """
    if not refresh:
        cached = evidence_from_cache(address, cache_dir, vendor_labels=vendor_labels)
        if cached is not None:
            return cached

    transactions: list[dict[str, Any]] = []
    truncated = True
    cursor: str | None = None
    for page_number in range(max_pages):
        try:
            page = provider.wallet_history(address, before=cursor)
        except Exception:  # noqa: BLE001 - budget limits and dead keys are routine
            break
        write_page(address, page_number, page.raw, cache_dir)
        transactions.extend(dict(item) for item in page.transactions)
        if not page.has_more:
            truncated = False
            break
        cursor = page.next_cursor
        if not cursor:
            break

    if not transactions:
        return None
    return extract_evidence(
        address,
        transactions,
        history_truncated=truncated,
        vendor_labels=vendor_labels,
    )
