from datetime import UTC, datetime, timedelta

import pytest

from meme_flight_recorder.evidence import DerivedFact, EvidenceStore, SourceObservation
from meme_flight_recorder.journal import FlightRecorder


def source(**overrides) -> SourceObservation:
    event_time = datetime(2026, 1, 1, tzinfo=UTC)
    values = {
        "source": "x_official_api",
        "source_event_id": "post-1",
        "entity_id": "solana:mint",
        "source_event_time": event_time,
        "source_received_at": event_time + timedelta(seconds=2),
        "request_parameters": {"rule": "official-account"},
        "raw_payload": {"id": "post-1", "text": "mint"},
        "collector_version": "test@abc123",
    }
    values.update(overrides)
    return SourceObservation(**values)


def test_source_retry_is_idempotent_and_raw_blob_is_content_addressed(tmp_path) -> None:
    recorder = FlightRecorder(tmp_path / "evidence.db")
    store = EvidenceStore(recorder, tmp_path / "raw")
    first = store.record_source(source())
    second = store.record_source(source())
    assert first == second
    assert len(recorder.list_events()) == 1
    event = recorder.list_events()[0]
    assert (tmp_path / "raw" / event["payload"]["raw_payload_sha256"][:2]).is_dir()
    assert recorder.verify_chain()


def test_derived_fact_cites_point_in_time_sources(tmp_path) -> None:
    recorder = FlightRecorder(tmp_path / "evidence.db")
    store = EvidenceStore(recorder, tmp_path / "raw")
    source_hash = store.record_source(source())
    now = datetime(2026, 1, 2, tzinfo=UTC)
    store.record_fact(
        DerivedFact(
            fact_id="mint-identity-1",
            entity_id="solana:mint",
            fact_type="exact_mint_verified",
            value=True,
            derived_at=now,
            causal_cutoff=now - timedelta(seconds=1),
            source_event_hashes=(source_hash,),
            derivation_version="identity-v1",
        )
    )
    assert len(recorder.list_events()) == 2


def test_pre_event_receipt_requires_clock_skew_estimate() -> None:
    event_time = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="clock_skew"):
        source(source_received_at=event_time - timedelta(seconds=1)).validate()
