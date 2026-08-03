"""The rate-limit circuit breaker.

Without it, a rate-limited cycle still issues two calls per candidate -- roughly
120 wasted requests that all 429 and keep the limit pinned, so it never recovers
between cycles. Observed directly: Jupiter stayed limited for forty minutes
across repeated runs.
"""

from __future__ import annotations

import pytest

from meme_flight_recorder.providers.jupiter import JupiterQuoteProvider, RateLimited

SOL = "So11111111111111111111111111111111111111112"
TOKEN = "MINT_A"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def limited_provider(clock: Clock, failures: int = 3, cooldown: float = 300.0):
    provider = JupiterQuoteProvider(
        failures_before_open=failures, cooldown_seconds=cooldown, clock=clock
    )
    provider.calls = 0  # type: ignore[attr-defined]

    def always_429(*_args, **_kwargs):
        provider.calls += 1  # type: ignore[attr-defined]
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    import meme_flight_recorder.providers.jupiter as module

    module.get_json = always_429
    return provider


def restore() -> None:
    import meme_flight_recorder.providers.jupiter as module
    from meme_flight_recorder.providers import http

    module.get_json = http.get_json


def test_breaker_opens_after_repeated_rate_limits() -> None:
    clock = Clock()
    provider = limited_provider(clock, failures=3)
    try:
        for _ in range(3):
            with pytest.raises(RateLimited):
                provider.quote(SOL, TOKEN, 1000)
        assert provider.rate_limited is True
    finally:
        restore()


def test_open_breaker_stops_spending_the_allowance() -> None:
    """The whole point: skipped requests are what let the limit recover."""
    clock = Clock()
    provider = limited_provider(clock, failures=3)
    try:
        for _ in range(3):
            with pytest.raises(RateLimited):
                provider.quote(SOL, TOKEN, 1000)
        spent = provider.calls  # type: ignore[attr-defined]
        for _ in range(50):
            with pytest.raises(RateLimited):
                provider.quote(SOL, TOKEN, 1000)
        assert provider.calls == spent  # type: ignore[attr-defined]
    finally:
        restore()


def test_breaker_closes_after_the_cooldown() -> None:
    clock = Clock()
    provider = limited_provider(clock, failures=3, cooldown=300.0)
    try:
        for _ in range(3):
            with pytest.raises(RateLimited):
                provider.quote(SOL, TOKEN, 1000)
        assert provider.rate_limited is True
        clock.now += 301.0
        assert provider.rate_limited is False
    finally:
        restore()


def test_a_single_rate_limit_does_not_open_the_breaker() -> None:
    """One 429 is noise; the breaker is for a sustained cap."""
    clock = Clock()
    provider = limited_provider(clock, failures=3)
    try:
        with pytest.raises(RateLimited):
            provider.quote(SOL, TOKEN, 1000)
        assert provider.rate_limited is False
    finally:
        restore()


def test_non_rate_limit_errors_are_not_swallowed() -> None:
    """A genuine failure must not be misreported as a rate limit."""
    provider = JupiterQuoteProvider()
    import meme_flight_recorder.providers.jupiter as module

    def boom(*_args, **_kwargs):
        raise RuntimeError("HTTP Error 500: Server Error")

    module.get_json = boom
    try:
        with pytest.raises(RuntimeError) as caught:
            provider.quote(SOL, TOKEN, 1000)
        assert not isinstance(caught.value, RateLimited)
        assert provider.rate_limited is False
    finally:
        restore()


def test_amount_validation_still_applies() -> None:
    with pytest.raises(ValueError):
        JupiterQuoteProvider().quote(SOL, TOKEN, 0)
