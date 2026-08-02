"""Point-in-time source evidence and derived facts with causal cutoffs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .journal import FlightRecorder


@dataclass(frozen=True)
class SourceObservation:
    source: str
    source_event_id: str
    entity_id: str
    source_event_time: datetime
    source_received_at: datetime
    request_parameters: dict[str, Any]
    raw_payload: dict[str, Any]
    collector_version: str
    schema_version: str = "1"
    chain_slot: int | None = None
    cursor: str | None = None
    response_status: int | None = None
    clock_skew_ms: int | None = None

    @property
    def idempotency_key(self) -> str:
        material = f"{self.source}|{self.source_event_id}|{self.entity_id}"
        return hashlib.sha256(material.encode()).hexdigest()

    def validate(self) -> None:
        _aware(self.source_event_time)
        _aware(self.source_received_at)
        # Provider clocks can disagree, but the discrepancy must be explicit.
        if self.source_received_at < self.source_event_time and self.clock_skew_ms is None:
            raise ValueError("pre-event receipt requires a clock_skew_ms estimate")
        if not self.source.strip() or not self.source_event_id.strip():
            raise ValueError("source and source_event_id are required")
        if not self.collector_version.strip():
            raise ValueError("collector_version is required")


@dataclass(frozen=True)
class DerivedFact:
    fact_id: str
    entity_id: str
    fact_type: str
    value: Any
    derived_at: datetime
    causal_cutoff: datetime
    source_event_hashes: tuple[str, ...]
    derivation_version: str

    def validate(self) -> None:
        _aware(self.derived_at)
        _aware(self.causal_cutoff)
        if self.derived_at < self.causal_cutoff:
            raise ValueError("derived_at cannot precede the causal cutoff")
        if not self.source_event_hashes:
            raise ValueError("derived facts must cite source event hashes")
        if not self.derivation_version.strip():
            raise ValueError("derivation_version is required")


class EvidenceStore:
    def __init__(self, recorder: FlightRecorder, raw_root: str | Path) -> None:
        self.recorder = recorder
        self.raw_root = Path(raw_root)

    def record_source(self, observation: SourceObservation) -> str:
        observation.validate()
        encoded = json.dumps(
            observation.raw_payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        raw_hash = hashlib.sha256(encoded).hexdigest()
        raw_path = self.raw_root / raw_hash[:2] / f"{raw_hash}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists():
            raw_path.write_bytes(encoded)
        payload = asdict(observation)
        payload.pop("raw_payload")
        payload.update(
            {
                "raw_payload_sha256": raw_hash,
                "raw_payload_path": str(raw_path),
                "ingested_at": datetime.now(UTC).isoformat(),
            }
        )
        return self.recorder.append_once(
            "source_observation",
            observation.entity_id,
            payload,
            observation.idempotency_key,
            observed_at=observation.source_received_at,
        )

    def record_fact(self, fact: DerivedFact) -> str:
        fact.validate()
        payload = asdict(fact)
        return self.recorder.append_once(
            "derived_fact",
            fact.entity_id,
            payload,
            idempotency_key=f"derived:{fact.fact_id}:{fact.derivation_version}",
            observed_at=fact.derived_at,
        )


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
