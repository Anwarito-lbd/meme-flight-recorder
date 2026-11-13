"""The provider envelope and mesh, and the trap Birdeye carries.

Two things are pinned here. First, that a failed fetch and an empty result never
collapse into the same fact -- the confusion behind "a rate limit recorded as a
token that stopped trading". Second, that a carried-forward candle with no
volume is not treated as a price, which is the same error in the shape Birdeye
happens to serve it.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from meme_flight_recorder.providers.birdeye import Candle, CandleSeries, _parse_candles
from meme_flight_recorder.providers.envelope import (
    FailureKind,
    ProviderResult,
    call,
    first_usable,
)


class Boom(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"http {code}")
        self.code = code


def test_a_value_is_wrapped_with_its_provenance() -> None:
    result = call("dexscreener", lambda: 42, ttl_seconds=15.0)
    assert result.ok
    assert result.value == 42
    assert result.source == "dexscreener"
    assert result.failure is None
    assert result.latency_ms >= 0.0


def test_none_is_empty_not_a_crash() -> None:
    result = call("dexscreener", lambda: None)
    assert not result.ok
    assert result.failure is FailureKind.EMPTY
    # An empty answer is a measurement: the provider looked and found nothing.
    assert result.is_evidence


def test_a_rate_limit_is_not_evidence_about_the_token() -> None:
    """The distinction the fail-closed gates depend on."""
    result = call("jupiter", lambda: (_ for _ in ()).throw(Boom(429)))
    assert result.failure is FailureKind.RATE_LIMITED
    assert not result.is_evidence


def test_a_transport_error_is_not_evidence_either() -> None:
    result = call("jupiter", lambda: (_ for _ in ()).throw(Boom(500)))
    assert result.failure is FailureKind.TRANSPORT
    assert not result.is_evidence


def test_ttl_expiry_is_computed_from_the_fetch_time() -> None:
    result = ProviderResult("x", value=1, ttl_seconds=10.0)
    assert not result.expired()
    assert result.expired(datetime.now(UTC) + timedelta(seconds=11))


def test_the_mesh_prefers_the_first_listed_provider_that_answers() -> None:
    result, others = first_usable(
        [("primary", lambda: "a"), ("secondary", lambda: "b")], deadline_seconds=2.0
    )
    assert result.source == "primary"
    assert result.value == "a"
    assert len(others) == 1


def test_the_mesh_falls_through_to_a_working_provider() -> None:
    result, _ = first_usable(
        [
            ("primary", lambda: (_ for _ in ()).throw(Boom(429))),
            ("secondary", lambda: "b"),
        ],
        deadline_seconds=2.0,
    )
    assert result.source == "secondary"
    assert result.value == "b"


def test_a_slow_provider_cannot_hold_up_the_hot_path() -> None:
    """Never wait for every provider: the deadline binds, not the slowest one."""

    def slow() -> str:
        time.sleep(5.0)
        return "late"

    started = time.monotonic()
    result, others = first_usable(
        [("fast", lambda: "quick"), ("slow", slow)], deadline_seconds=0.5
    )
    assert time.monotonic() - started < 3.0
    assert result.value == "quick"
    assert any(other.failure is FailureKind.TIMEOUT for other in others) or result.ok


def test_no_providers_is_not_configured_rather_than_empty() -> None:
    result, _ = first_usable([], deadline_seconds=1.0)
    assert result.failure is FailureKind.NOT_CONFIGURED


# ------------------------------------------------------ the carried-price trap


def series(*volumes: float) -> CandleSeries:
    return CandleSeries(
        mint="M",
        candles=tuple(
            Candle(
                unix_time=1_000 + index * 60,
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=volume,
            )
            for index, volume in enumerate(volumes)
        ),
    )


def test_a_zero_volume_candle_is_not_a_price() -> None:
    """Verified against a real mint: 963 of 1,000 candles had no volume, and the
    token still reported a price sixteen hours after its last trade."""
    live = series(5.0, 0.0, 0.0)
    assert len(live.candles) == 3
    assert len(live.trades_only) == 1
    assert live.last_trade_at == 1_000
    assert live.silent_minutes == 2


def test_a_token_that_never_traded_has_no_last_trade() -> None:
    quiet = series(0.0, 0.0)
    assert quiet.last_trade_at is None
    assert quiet.trades_only == ()


def test_never_listed_and_stopped_trading_are_different_answers() -> None:
    """One returns no items at all; the other returns items with no volume."""
    never_listed = _parse_candles("M", {"data": {"items": []}})
    assert never_listed is not None
    assert never_listed.candles == ()

    unreadable = _parse_candles("M", {"data": {}})
    assert unreadable is None


def test_missing_volume_does_not_become_zero() -> None:
    parsed = _parse_candles("M", {"data": {"items": [{"unixTime": 10, "c": 1.0}]}})
    assert parsed is not None
    assert parsed.candles[0].volume is None
    assert not parsed.candles[0].traded
