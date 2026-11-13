"""What every provider must say about its own answer.

A provider that returns a bare value forces every caller to guess three things:
how old it is, where it came from, and whether the absence of a field means
"zero" or "we could not tell". This project has paid for that guess repeatedly --
a 429 recorded as a token that stopped trading, a missing liquidity figure read
as an empty pool, an EV of +130,273/dollar computed off a $0.00 pool.

So a provider answer is an envelope, never a value:

  * ``value``      -- the payload, or None. None is *always* "not known".
  * ``source``     -- which provider said it, so two answers can be compared.
  * ``observed_at``-- when the provider observed it, not when we asked.
  * ``fetched_at`` -- when we asked, so staleness is computable either way.
  * ``ttl_seconds``-- how long this may be reused before it must be refetched.
  * ``confidence`` -- 0..1, how much the provider's own evidence supports it.
  * ``failure``    -- why there is no value. A failed fetch and an empty result
    are different facts and must never collapse into the same one.
  * ``latency_ms`` -- measured on a monotonic clock, for the hot-path budget.

The rule the mesh enforces on top: **never wait for every provider in the
trading hot path.** A slow optional provider must not be able to delay a
decision, so the mesh takes the first usable answer within a deadline and
records the rest as failures rather than blocking on them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class FailureKind(StrEnum):
    """Why a provider produced no value. Each demands a different response."""

    # The provider answered, and the answer was "there is nothing here". This is
    # a measurement and may be trusted as one.
    EMPTY = "empty"
    # The provider refused because we asked too often. This is damage to us, not
    # information about the token, and must never enter a study as evidence.
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    # The provider answered with something we could not parse. Distinct from
    # EMPTY because it points at our decoder rather than at the token.
    UNREADABLE = "unreadable"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class ProviderResult[T]:
    source: str
    value: T | None = None
    observed_at: datetime | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: float = 30.0
    confidence: float = 0.0
    failure: FailureKind | None = None
    detail: str = ""
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.value is not None and self.failure is None

    @property
    def is_evidence(self) -> bool:
        """Whether this may be used as evidence about the token.

        A rate limit, timeout or transport error says nothing about the token,
        so a fail-closed gate must treat it as absent rather than as negative.
        An EMPTY answer *is* evidence: the provider looked and found nothing.
        """
        return self.ok or self.failure is FailureKind.EMPTY

    def expired(self, now: datetime | None = None) -> bool:
        reference = now or datetime.now(UTC)
        return (reference - self.fetched_at).total_seconds() > self.ttl_seconds


def call[T](
    source: str,
    function: Callable[[], T | None],
    *,
    ttl_seconds: float = 30.0,
    confidence: float = 1.0,
    observed_at: datetime | None = None,
) -> ProviderResult[T]:
    """Run one provider call and wrap whatever happens in an envelope.

    Classification is by exception *type and code*, never by a string match on
    the message, so a provider changing its wording cannot silently reclassify a
    rate limit as a transport error.
    """
    started = time.monotonic()

    def elapsed() -> float:
        return (time.monotonic() - started) * 1000.0

    try:
        value = function()
    except TimeoutError as error:
        return ProviderResult(
            source, failure=FailureKind.TIMEOUT, detail=str(error), latency_ms=elapsed()
        )
    except Exception as error:  # noqa: BLE001 - every failure must be classified, not raised
        code = getattr(error, "code", None)
        kind = FailureKind.RATE_LIMITED if code == 429 else FailureKind.TRANSPORT
        return ProviderResult(
            source,
            failure=kind,
            detail=f"{type(error).__name__}: {error}",
            latency_ms=elapsed(),
        )
    if value is None:
        return ProviderResult(source, failure=FailureKind.EMPTY, latency_ms=elapsed())
    return ProviderResult(
        source,
        value=value,
        observed_at=observed_at,
        ttl_seconds=ttl_seconds,
        confidence=confidence,
        latency_ms=elapsed(),
    )


def first_usable(
    providers: list[tuple[str, Callable[[], Any]]],
    deadline_seconds: float,
) -> tuple[ProviderResult[Any], list[ProviderResult[Any]]]:
    """Return the first usable answer within the deadline, plus the others.

    The providers run concurrently and the deadline is absolute. A provider that
    has not answered by then is recorded as TIMEOUT and abandoned -- it is not
    waited on, and it cannot delay the decision. That is the whole point: the
    hot path must be bounded by the deadline, not by the slowest dependency.

    Order matters for ties. `providers` should be listed best-first, so a
    keyless primary is preferred over an optional keyed one.
    """
    if not providers:
        return ProviderResult("none", failure=FailureKind.NOT_CONFIGURED), []

    results: list[ProviderResult[Any]] = []
    # Deliberately not a `with` block. ThreadPoolExecutor.__exit__ joins every
    # worker, so a `with` here would wait for the slowest provider *after* the
    # deadline had already passed -- exactly the behaviour the deadline exists
    # to prevent. A test pins this: a 5s provider must not delay a 0.5s
    # deadline. Instead the pool is shut down without waiting, and the abandoned
    # thread finishes into a result nobody reads.
    pool = ThreadPoolExecutor(max_workers=len(providers))
    try:
        futures = {
            pool.submit(call, source, function): source for source, function in providers
        }
        started = time.monotonic()
        for future, source in futures.items():
            remaining = deadline_seconds - (time.monotonic() - started)
            try:
                results.append(future.result(timeout=max(remaining, 0.0)))
            except FutureTimeout:
                results.append(
                    ProviderResult(
                        source,
                        failure=FailureKind.TIMEOUT,
                        detail=f"exceeded {deadline_seconds}s deadline",
                        latency_ms=deadline_seconds * 1000.0,
                    )
                )
            except Exception as error:  # noqa: BLE001
                results.append(
                    ProviderResult(source, failure=FailureKind.TRANSPORT, detail=str(error))
                )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    ranked = {result.source: result for result in results}
    for source, _function in providers:
        result = ranked.get(source)
        if result is not None and result.ok:
            return result, [other for other in results if other is not result]
    return results[0], results[1:]
