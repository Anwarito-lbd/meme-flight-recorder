"""Tests for Stage 6 organic-flow assessment.

The most important tests here are not the happy paths. They are the ones that
pin down what the module refuses to claim: that a transaction count never
becomes a buyer, and that absent evidence never becomes a pass.
"""

from __future__ import annotations

from meme_flight_recorder.config import FlowLimits
from meme_flight_recorder.flow import (
    FlowObservation,
    FlowVerdict,
    assess_flow,
    observations_from_events,
)


def observation(**overrides: object) -> FlowObservation:
    """A healthy baseline reading, so each test varies one thing."""
    defaults: dict[str, object] = {
        "observed_at": "2026-08-03T00:00:00+00:00",
        "price_usd": 1.0,
        "liquidity_usd": 50_000.0,
        "holders": 1_000,
        "volume_5m_usd": 5_000.0,
        "volume_to_liquidity_5m": 0.1,
        "buy_txns_5m": 60,
        "sell_txns_5m": 40,
    }
    defaults.update(overrides)
    return FlowObservation(**defaults)  # type: ignore[arg-type]


def test_growing_holders_and_stable_liquidity_is_organic() -> None:
    result = assess_flow([observation(), observation(holders=1_100)])
    assert result.verdict is FlowVerdict.ORGANIC
    assert result.holder_growth_pct == 10.0


def test_unique_buyer_acceleration_is_never_populated() -> None:
    """The framework's primary metric is unavailable and must stay unknown.

    A transaction count is not a buyer. If this ever returns a number, some
    caller will read it as unique addresses and the gate will report passes it
    never earned.
    """
    for history in (
        [observation(), observation(holders=1_100)],
        [observation(), observation(holders=1_000, volume_5m_usd=100_000.0)],
        [observation()],
    ):
        assert assess_flow(history).unique_buyer_acceleration is None


def test_unique_buyers_always_listed_as_missing() -> None:
    result = assess_flow([observation(), observation(holders=1_100)])
    assert "unique_buyers" in result.missing


def test_missing_holder_data_cannot_pass() -> None:
    result = assess_flow([observation(holders=None), observation(holders=None)])
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE
    assert "holder_growth" in result.missing


def test_missing_liquidity_data_cannot_pass() -> None:
    result = assess_flow(
        [observation(liquidity_usd=None), observation(holders=1_100, liquidity_usd=None)]
    )
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE
    assert "liquidity_growth" in result.missing


def test_single_observation_is_insufficient() -> None:
    result = assess_flow([observation()])
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE
    assert result.observations == 1


def test_empty_history_is_insufficient_not_an_error() -> None:
    assert assess_flow([]).verdict is FlowVerdict.INSUFFICIENT_EVIDENCE


def test_volume_spike_without_holder_growth_is_suspicious() -> None:
    result = assess_flow([observation(), observation(volume_5m_usd=50_000.0, holders=1_000)])
    assert result.verdict is FlowVerdict.SUSPICIOUS
    assert "volume_spike_without_holders" in result.wash_flags


def test_volume_spike_without_price_response_is_suspicious() -> None:
    result = assess_flow(
        [observation(), observation(volume_5m_usd=50_000.0, holders=1_500, price_usd=1.01)]
    )
    assert result.verdict is FlowVerdict.SUSPICIOUS
    assert "volume_spike_without_price_response" in result.wash_flags


def test_extreme_turnover_is_suspicious() -> None:
    result = assess_flow(
        [observation(), observation(holders=1_100, volume_to_liquidity_5m=8.0)]
    )
    assert result.verdict is FlowVerdict.SUSPICIOUS
    assert "extreme_volume_to_liquidity" in result.wash_flags


def test_manipulation_evidence_beats_healthy_growth() -> None:
    """A token can be both growing and manufactured; the flag still fires."""
    result = assess_flow(
        [observation(), observation(holders=2_000, price_usd=3.0, volume_to_liquidity_5m=9.0)]
    )
    assert result.verdict is FlowVerdict.SUSPICIOUS
    assert result.holder_growth_pct == 100.0


def test_draining_liquidity_blocks_organic() -> None:
    result = assess_flow([observation(), observation(holders=1_100, liquidity_usd=40_000.0)])
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE


def test_seller_dominated_transactions_block_organic() -> None:
    result = assess_flow(
        [observation(), observation(holders=1_100, buy_txns_5m=20, sell_txns_5m=80)]
    )
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE


def test_unknown_buy_share_does_not_block_when_required_signals_present() -> None:
    result = assess_flow(
        [
            observation(buy_txns_5m=None, sell_txns_5m=None),
            observation(holders=1_100, buy_txns_5m=None, sell_txns_5m=None),
        ]
    )
    assert result.verdict is FlowVerdict.ORGANIC
    assert result.buy_share_pct is None


def test_flat_holder_count_blocks_organic() -> None:
    result = assess_flow([observation(), observation(holders=1_000)])
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE


def test_zero_start_holders_does_not_report_growth() -> None:
    """Zero to something has no percentage change; inventing one would lie."""
    result = assess_flow([observation(holders=0), observation(holders=500)])
    assert result.holder_growth_pct is None
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE


def test_legacy_payload_without_flow_fields_is_insufficient_not_an_error() -> None:
    """Rows journalled before Stage 6 existed must still decode."""
    legacy = {
        "observed_at": "2026-08-03T02:55:00+00:00",
        "price_usd": 0.001,
        "liquidity_usd": 26.0,
        "holders": 12,
    }
    history = [FlowObservation.from_payload(legacy), FlowObservation.from_payload(legacy)]
    result = assess_flow(history)
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE
    assert result.buy_share_pct is None


def test_buy_share_falls_back_to_totals_when_window_absent() -> None:
    reading = FlowObservation.from_payload(
        {"observed_at": "x", "buy_txns_total": 75, "sell_txns_total": 25}
    )
    assert reading.buy_share_pct == 75.0


def test_buy_share_prefers_the_five_minute_window() -> None:
    reading = FlowObservation.from_payload(
        {
            "observed_at": "x",
            "buy_txns_5m": 10,
            "sell_txns_5m": 90,
            "buy_txns_total": 900,
            "sell_txns_total": 100,
        }
    )
    assert reading.buy_share_pct == 10.0


def test_observations_group_by_mint_preserving_order() -> None:
    events = [
        {"entity_id": "MINT_A", "payload": {"observed_at": "1", "holders": 10}},
        {"entity_id": "MINT_B", "payload": {"observed_at": "1", "holders": 99}},
        {"entity_id": "MINT_A", "payload": {"observed_at": "2", "holders": 20}},
    ]
    histories = observations_from_events(events)
    assert [reading.holders for reading in histories["MINT_A"]] == [10, 20]
    assert len(histories["MINT_B"]) == 1


def test_limits_are_configurable() -> None:
    strict = FlowLimits(minimum_holder_growth_pct=50.0)
    result = assess_flow([observation(), observation(holders=1_100)], strict)
    assert result.verdict is FlowVerdict.INSUFFICIENT_EVIDENCE
