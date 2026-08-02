from __future__ import annotations

from dataclasses import asdict

from .journal import FlightRecorder
from .lifecycle import infer_lifecycle
from .models import CandidateStatus, TokenSnapshot
from .safety import SafetyEngine
from .scoring import score_candidate


class CandidateService:
    def __init__(self, recorder: FlightRecorder, safety: SafetyEngine) -> None:
        self.recorder = recorder
        self.safety = safety

    def evaluate(self, snapshot: TokenSnapshot) -> dict[str, object]:
        entity_id = f"{snapshot.identity.chain}:{snapshot.identity.address}"
        self.recorder.append(
            "snapshot_observed", entity_id, snapshot.as_dict(), snapshot.observed_at
        )
        decision = self.safety.evaluate(snapshot)
        lifecycle = infer_lifecycle(snapshot)
        score = score_candidate(snapshot)
        result: dict[str, object] = {
            "entity_id": entity_id,
            "universe": snapshot.universe.value,
            "status": decision.status.value,
            "failures": list(decision.failures),
            "warnings": list(decision.warnings),
            "lifecycle": lifecycle.value,
            "score": asdict(score),
        }
        self.recorder.append("candidate_evaluated", entity_id, result)
        if decision.status == CandidateStatus.REJECT:
            return result
        # Eligibility means only strategy review; never an automatic trade authorization.
        return result
