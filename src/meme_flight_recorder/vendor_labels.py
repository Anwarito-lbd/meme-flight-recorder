"""Opportunistic vendor labels, keyed by mint.

``enrich_snapshot`` resolves everything the chain can answer on demand: mint and
freeze authority, routes, price impact, holder concentration. It cannot resolve
``developer_selling``, because no chain call answers "is the deployer currently
distributing". That fact reaches this project through one vendor field --
Binance Web3's ``devSellPercent`` -- and only through a *ranked feed*, never a
per-mint lookup. ``token_audit`` was checked and carries risk levels only.

So this is a **feed index, not a lookup**. It refreshes the three rank types
that correspond to the launchpad lifecycle and answers from what the feed
happened to contain. Measured 2026-08-25 at limit=100 per rank type:

    RANK_NEW (10)        100 rows,  83 with dev_sell_pct
    RANK_FINALIZING (20) 100 rows,  99 with dev_sell_pct
    RANK_MIGRATED (30)   100 rows,  96 with dev_sell_pct

A mint outside the feed gets ``None`` for everything and its gates keep
rejecting. That is the intended outcome, not a gap to paper over: the whole
point of the safety engine is that absent evidence never reads as a pass.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .providers.binance_web3 import (
    CHAIN_SOLANA,
    RANK_FINALIZING,
    RANK_MIGRATED,
    RANK_NEW,
    VendorClusterLabels,
)

# The launchpad lifecycle, which is also what Axiom's three Pulse columns show.
LIFECYCLE_RANKS = (RANK_NEW, RANK_FINALIZING, RANK_MIGRATED)

# A ranked feed of the top launchpad coins does not turn over every minute,
# and each refresh costs three upstream calls that block the caller for
# ~8s. At a 60s TTL that spike landed inside the agent's verdict budget
# often enough to abort it. Longer TTL, fewer spikes, same freshness in
# practice.
DEFAULT_TTL_SECONDS = 180.0
DEFAULT_LIMIT = 100


@dataclass(frozen=True)
class VendorCoverage:
    """What the last refresh actually established."""

    rows: int = 0
    with_dev_sell: int = 0
    refreshed_at: float | None = None
    errors: tuple[str, ...] = ()

    @property
    def stale(self) -> bool:
        return self.refreshed_at is None


class VendorLabelIndex:
    """TTL-cached index of vendor cluster labels, keyed by contract address.

    Refresh is lazy and shared: one call per TTL per rank type, however many
    candidates are evaluated in between. A per-candidate fetch would spend the
    vendor's rate limit on data the feed already returned in bulk.
    """

    def __init__(
        self,
        provider: Any,
        *,
        chain_id: str = CHAIN_SOLANA,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        limit: int = DEFAULT_LIMIT,
        ranks: tuple[int, ...] = LIFECYCLE_RANKS,
        clock: Any = time.monotonic,
    ) -> None:
        self._provider = provider
        self._chain_id = chain_id
        self._ttl = float(ttl_seconds)
        self._limit = int(limit)
        self._ranks = tuple(ranks)
        self._clock = clock
        self._by_mint: dict[str, VendorClusterLabels] = {}
        self._coverage = VendorCoverage()
        self._fetched_at: float | None = None

    @property
    def coverage(self) -> VendorCoverage:
        return self._coverage

    def _expired(self) -> bool:
        return self._fetched_at is None or (self._clock() - self._fetched_at) >= self._ttl

    def refresh(self, *, force: bool = False) -> VendorCoverage:
        if not force and not self._expired():
            return self._coverage

        by_mint: dict[str, VendorClusterLabels] = {}
        errors: list[str] = []
        rows = 0
        for rank in self._ranks:
            try:
                fetched = self._provider.meme_rush(
                    chain_id=self._chain_id, rank_type=rank, limit=self._limit
                )
            except Exception as error:  # noqa: BLE001 - a dead feed must not pass a gate
                errors.append(f"rank{rank}: {type(error).__name__}: {error}")
                continue
            for row in fetched:
                address = getattr(row, "contract_address", None)
                labels = getattr(row, "labels", None)
                if not address or labels is None:
                    continue
                rows += 1
                # Later rank types win only when they carry more evidence; a row
                # with nothing reported must not evict one that had a dev_sell.
                existing = by_mint.get(address)
                if existing is None or len(labels.reported_fields) > len(existing.reported_fields):
                    by_mint[address] = labels

        # A refresh that failed entirely must not silently blank an index that
        # still holds usable data - but it must also not look fresh.
        if not by_mint and errors:
            self._coverage = VendorCoverage(
                rows=self._coverage.rows,
                with_dev_sell=self._coverage.with_dev_sell,
                refreshed_at=self._coverage.refreshed_at,
                errors=tuple(errors),
            )
            return self._coverage

        self._by_mint = by_mint
        self._fetched_at = self._clock()
        self._coverage = VendorCoverage(
            rows=rows,
            with_dev_sell=sum(1 for v in by_mint.values() if v.dev_sell_pct is not None),
            refreshed_at=self._fetched_at,
            errors=tuple(errors),
        )
        return self._coverage

    def labels_for(self, mint: str) -> VendorClusterLabels | None:
        """Labels for one mint, or None when the feed did not carry it."""
        if not mint:
            return None
        self.refresh()
        return self._by_mint.get(mint)


def apply_vendor_labels(snapshot: Any, labels: VendorClusterLabels | None) -> tuple[Any, tuple[str, ...]]:
    """Overlay vendor labels onto a snapshot. Returns (snapshot, resolved names).

    Overlay, never overwrite. A value the chain already established is better
    evidence than a vendor's opinion of it, so only fields still ``None`` are
    filled. Absent vendor fields stay absent.

    ``developer_selling`` goes through ``developer_is_distributing`` rather than
    a ``> 0`` test, because that function is the single source of truth for the
    distinction between a developer mid-distribution and one who has fully
    exited -- the safest measured state, which a flat nonzero test would reject.
    """
    from dataclasses import replace

    from .deployer import developer_is_distributing

    if labels is None:
        return snapshot, ()

    resolved: list[str] = []
    updates: dict[str, Any] = {}

    if snapshot.developer_selling is None and labels.dev_sell_pct is not None:
        distributing = developer_is_distributing(labels.dev_sell_pct)
        if distributing is not None:
            updates["developer_selling"] = distributing
            resolved.append("developer_selling")

    if snapshot.top10_private_holder_pct is None and labels.top10_pct is not None:
        updates["top10_private_holder_pct"] = labels.top10_pct
        resolved.append("top10_private_holder_pct")

    if not updates:
        return snapshot, ()

    # Keep the raw labels so a later threshold change can be re-derived from
    # evidence rather than from a stored verdict.
    raw = dict(getattr(snapshot, "raw_evidence", {}) or {})
    raw["binance_web3_labels"] = {
        name: getattr(labels, name) for name in labels.reported_fields
    }
    updates["raw_evidence"] = raw

    return replace(snapshot, **updates), tuple(resolved)
