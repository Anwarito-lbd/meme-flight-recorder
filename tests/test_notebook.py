from datetime import UTC, datetime, timedelta

import pytest

from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.notebook import (
    EvidenceConfidence,
    ExperimentResult,
    HypothesisStatus,
    ResearchFinding,
    ResearchNotebook,
)


def finding(**overrides) -> ResearchFinding:
    published = datetime(2026, 1, 1, tzinfo=UTC)
    values = {
        "finding_id": "kimchi-trump-claim",
        "actor": "Kimchi",
        "claim": "A large TRUMP profit was claimed publicly.",
        "repeatable_mechanism": "Test wallet-cluster activity before public announcements.",
        "hypothesis": "A qualified cluster predicts positive delayed net returns.",
        "observed_at": published + timedelta(minutes=1),
        "source_published_at": published,
        "source_urls": ("https://example.test/source",),
        "confidence": EvidenceConfidence.UNVERIFIED,
        "status": HypothesisStatus.PREREGISTERED,
        "invalidation_condition": "Net expectancy is non-positive after modeled costs.",
    }
    values.update(overrides)
    return ResearchFinding(**values)


def test_notebook_records_hash_chained_finding_and_result(tmp_path) -> None:
    recorder = FlightRecorder(tmp_path / "notes.db")
    notebook = ResearchNotebook(recorder)
    item = finding()
    notebook.record_finding(item)
    notebook.record_experiment(
        ExperimentResult(
            experiment_id="delay-study-001",
            hypothesis_finding_id=item.finding_id,
            preregistered_at=item.observed_at,
            completed_at=item.observed_at + timedelta(days=30),
            sample_size=100,
            metrics={"expectancy_r": -0.1},
            status=HypothesisStatus.REJECTED,
            data_quality="point_in_time_wallet_and_pool_snapshots",
        )
    )
    assert recorder.verify_chain()
    assert {event["event_type"] for event in recorder.list_events()} == {
        "research_finding",
        "research_experiment_result",
    }


def test_preregistered_finding_requires_invalidation_condition() -> None:
    with pytest.raises(ValueError, match="invalidation"):
        finding(invalidation_condition="").validate()


def test_finding_cannot_claim_knowledge_before_publication() -> None:
    published = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="precede"):
        finding(observed_at=published - timedelta(seconds=1)).validate()
