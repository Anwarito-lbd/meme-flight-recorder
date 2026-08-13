"""Reconcile what the live stream saw against what the chain actually contains.

A WebSocket feed is not a record. It drops events under load, it disconnects,
and it resumes wherever it happens to resume -- and none of that is visible from
inside the stream. A collector that trusts it will report a quiet market when
what actually happened was a reconnect, which is the same class of error as a
rate limit recorded as a token death.

So every event carries an identity the chain also knows:

    (slot, transaction_index, signature)

The signature alone would be enough to deduplicate, but it cannot answer "what
did we miss", because a signature we never saw is a signature we cannot look up.
The slot can: canonical history is fetched *by slot range*, so the gap between
the highest slot seen before a disconnect and the first slot seen after it is
exactly the region that must be re-fetched.

Reconciliation is reported as a set of counts that reconcile to a total, never
as a single percentage. "97% reconciled" hides whether the missing 3% was never
delivered, delivered twice, or never looked for.

Pure. The caller supplies live and canonical observations; this only compares.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class EventIdentity:
    """Chain-native identity. Ordered so slot gaps are computable."""

    slot: int
    transaction_index: int | None
    signature: str

    @property
    def key(self) -> str:
        """Deduplication key.

        The signature is the deduplicating field because it is unique chain-wide
        and stable across delivery paths: the same transaction seen live and
        again in canonical history must collapse to one event, and its slot is
        identical anyway.
        """
        return self.signature


@dataclass
class ReconciliationReport:
    live_observed: int = 0
    canonical_observed: int = 0
    duplicates: int = 0
    matched: int = 0
    missed_live: int = 0
    recovered: int = 0
    unresolved: int = 0
    reconnects: int = 0
    largest_gap_slots: int = 0
    slot_range: tuple[int, int] | None = None

    @property
    def total_unique(self) -> int:
        return self.matched + self.missed_live + self.unresolved

    @property
    def reconciliation_pct(self) -> float | None:
        """Share of canonical events we ended up holding, live or recovered.

        None when nothing canonical was fetched -- an unmeasured ratio is not
        100%, and reporting it as such is how a broken feed looks healthy.
        """
        if self.canonical_observed == 0:
            return None
        return round(100.0 * (self.matched + self.recovered) / self.canonical_observed, 4)

    def as_dict(self) -> dict[str, object]:
        return {
            "live_observed": self.live_observed,
            "canonical_observed": self.canonical_observed,
            "duplicates": self.duplicates,
            "matched": self.matched,
            "missed_live": self.missed_live,
            "recovered": self.recovered,
            "unresolved": self.unresolved,
            "reconnects": self.reconnects,
            "largest_gap_slots": self.largest_gap_slots,
            "slot_range": list(self.slot_range) if self.slot_range else None,
            "reconciliation_pct": self.reconciliation_pct,
        }


@dataclass
class SlotReconciler:
    """Tracks live events, detects slot gaps, and folds canonical history in."""

    live: dict[str, EventIdentity] = field(default_factory=dict)
    canonical: dict[str, EventIdentity] = field(default_factory=dict)
    recovered_keys: set[str] = field(default_factory=set)
    duplicates: int = 0
    reconnects: int = 0
    _last_slot: int | None = None
    _gaps: list[tuple[int, int]] = field(default_factory=list)

    def observe_live(self, identity: EventIdentity) -> bool:
        """Record a streamed event. Returns False when it is a duplicate.

        Deduplication happens here, before any enrichment, because enriching the
        same transaction twice costs provider budget and inflates every count
        downstream.
        """
        if identity.key in self.live:
            self.duplicates += 1
            return False
        self.live[identity.key] = identity
        # Slots advance monotonically. A backwards jump means the stream
        # reconnected and is replaying, not that the chain reorganised at this
        # scale, so it is counted as a reconnect rather than a gap.
        if self._last_slot is not None and identity.slot < self._last_slot:
            self.reconnects += 1
        self._last_slot = max(self._last_slot or identity.slot, identity.slot)
        return True

    def mark_disconnect(self, last_slot_before: int, first_slot_after: int) -> None:
        """Record a known outage window so its slots can be re-fetched.

        This is the honest part. The stream cannot tell us what it missed, so
        the collector must record *when it was not listening* and hand that
        range to canonical history.
        """
        self.reconnects += 1
        if first_slot_after > last_slot_before + 1:
            self._gaps.append((last_slot_before + 1, first_slot_after - 1))

    @property
    def gaps(self) -> list[tuple[int, int]]:
        return list(self._gaps)

    @property
    def largest_gap_slots(self) -> int:
        return max((high - low + 1 for low, high in self._gaps), default=0)

    def observe_canonical(self, identity: EventIdentity) -> None:
        self.canonical[identity.key] = identity

    def report(self) -> ReconciliationReport:
        live_keys = set(self.live)
        canonical_keys = set(self.canonical)
        matched = live_keys & canonical_keys
        missed = canonical_keys - live_keys
        recovered = missed & self.recovered_keys

        slots = [item.slot for item in (*self.live.values(), *self.canonical.values())]
        return ReconciliationReport(
            live_observed=len(self.live),
            canonical_observed=len(self.canonical),
            duplicates=self.duplicates,
            matched=len(matched),
            missed_live=len(missed),
            recovered=len(recovered),
            # Missed and not recovered. This is the number that must be zero
            # before any claim about feed reliability is made.
            unresolved=len(missed - recovered),
            reconnects=self.reconnects,
            largest_gap_slots=self.largest_gap_slots,
            slot_range=(min(slots), max(slots)) if slots else None,
        )

    def recover(self, identities: list[EventIdentity]) -> int:
        """Fold canonical events fetched for a gap back into the record."""
        added = 0
        for identity in identities:
            self.observe_canonical(identity)
            if identity.key not in self.live:
                self.recovered_keys.add(identity.key)
                added += 1
        return added
