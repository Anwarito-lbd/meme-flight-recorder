"""Structured, append-only research notes and experiment results.

The notebook turns interesting observations into timestamped evidence.  Notes are
never order instructions and are stored in the same tamper-evident journal as the
rest of the flight recorder.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum

from .journal import FlightRecorder


class EvidenceConfidence(StrEnum):
    UNVERIFIED = "unverified"
    CORROBORATED = "corroborated"
    VERIFIED = "verified"


class HypothesisStatus(StrEnum):
    LEAD = "lead"
    PREREGISTERED = "preregistered"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ResearchFinding:
    finding_id: str
    actor: str
    claim: str
    repeatable_mechanism: str
    hypothesis: str
    observed_at: datetime
    source_published_at: datetime
    source_urls: tuple[str, ...]
    confidence: EvidenceConfidence = EvidenceConfidence.UNVERIFIED
    status: HypothesisStatus = HypothesisStatus.LEAD
    exact_mint: str | None = None
    wallet_address: str | None = None
    invalidation_condition: str = ""

    def validate(self) -> None:
        if not self.finding_id.strip() or not self.claim.strip():
            raise ValueError("finding_id and claim are required")
        if not self.repeatable_mechanism.strip() or not self.hypothesis.strip():
            raise ValueError("repeatable_mechanism and hypothesis are required")
        if not self.source_urls or any(not item.startswith("https://") for item in self.source_urls):
            raise ValueError("at least one HTTPS source URL is required")
        if self.observed_at < self.source_published_at:
            raise ValueError("observation cannot precede source publication")
        if self.status == HypothesisStatus.PREREGISTERED and not self.invalidation_condition:
            raise ValueError("preregistered hypotheses need an invalidation condition")


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    hypothesis_finding_id: str
    preregistered_at: datetime
    completed_at: datetime
    sample_size: int
    metrics: dict[str, float | int | str | None]
    status: HypothesisStatus
    data_quality: str
    notes: str = ""

    def validate(self) -> None:
        if self.completed_at < self.preregistered_at:
            raise ValueError("experiment cannot complete before preregistration")
        if self.sample_size < 0:
            raise ValueError("sample_size cannot be negative")
        if self.status not in {
            HypothesisStatus.SUPPORTED,
            HypothesisStatus.REJECTED,
            HypothesisStatus.INCONCLUSIVE,
        }:
            raise ValueError("experiment result status must be terminal")
        if not self.data_quality.strip():
            raise ValueError("data_quality is required")


class ResearchNotebook:
    def __init__(self, recorder: FlightRecorder) -> None:
        self.recorder = recorder

    def record_finding(self, finding: ResearchFinding) -> str:
        finding.validate()
        payload = asdict(finding)
        payload["confidence"] = finding.confidence.value
        payload["status"] = finding.status.value
        return self.recorder.append(
            "research_finding",
            finding.finding_id,
            payload,
            observed_at=_utc(finding.observed_at),
        )

    def record_experiment(self, result: ExperimentResult) -> str:
        result.validate()
        payload = asdict(result)
        payload["status"] = result.status.value
        return self.recorder.append(
            "research_experiment_result",
            result.experiment_id,
            payload,
            observed_at=_utc(result.completed_at),
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)
