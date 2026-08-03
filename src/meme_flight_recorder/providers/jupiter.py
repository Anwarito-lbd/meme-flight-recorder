"""Quote evidence from Jupiter, with a circuit breaker around the rate limit.

The free endpoint has a modest allowance, and exceeding it is not a transient
inconvenience here: without route and impact evidence the fail-closed gates
reject every candidate, and the rejection is journalled indistinguishably from a
token that genuinely failed. A rate limit becomes evidence about tokens.

Worse, the naive behaviour makes the outage permanent. Rate-limited, a cycle
still issues two calls per candidate -- roughly 120 wasted requests that all 429
and keep the limit pinned, so it never recovers between cycles. Observed
directly: Jupiter stayed rate-limited for forty minutes across repeated runs.

So once enough consecutive 429s arrive, the breaker opens and quoting is skipped
entirely for a cooldown. Candidates during that window are recorded with unknown
routes, exactly as they would have been anyway, but the allowance is left alone
so it can recover. Failing fast is strictly better than failing slowly for the
same result.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .http import get_json


class RateLimited(RuntimeError):
    """The provider refused the request because of its rate limit."""


@dataclass(frozen=True)
class RouteEvidence:
    found: bool
    input_mint: str
    output_mint: str
    input_amount: int
    output_amount: int | None
    price_impact_pct: float | None
    raw: dict[str, Any]


class JupiterQuoteProvider:
    """Quote-only adapter. It cannot build, sign, or broadcast a transaction."""

    base_url = "https://lite-api.jup.ag/swap/v1"

    def __init__(
        self,
        *,
        failures_before_open: int = 5,
        cooldown_seconds: float = 300.0,
        clock: Any = time.monotonic,
    ) -> None:
        self.failures_before_open = failures_before_open
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._consecutive_limits = 0
        self._open_until: float = 0.0

    @property
    def rate_limited(self) -> bool:
        """True while the breaker is open and quoting is being skipped."""
        return self._clock() < self._open_until

    def _record_success(self) -> None:
        self._consecutive_limits = 0

    def _record_rate_limit(self) -> None:
        self._consecutive_limits += 1
        if self._consecutive_limits >= self.failures_before_open:
            self._open_until = self._clock() + self.cooldown_seconds

    def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 100,
    ) -> RouteEvidence:
        if amount <= 0:
            raise ValueError("amount must be positive base units")
        if self.rate_limited:
            # Skipped, not attempted. The caller records unknown either way, and
            # not spending the request is what lets the allowance recover.
            raise RateLimited("quote skipped: rate-limit cooldown is active")
        try:
            data = get_json(
                self.base_url,
                "/quote",
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": amount,
                    "slippageBps": max(1, min(slippage_bps, 500)),
                    "restrictIntermediateTokens": "true",
                },
            )
        except Exception as error:
            if "429" in str(error):
                self._record_rate_limit()
                raise RateLimited(str(error)) from error
            raise
        self._record_success()
        output = int(data["outAmount"]) if data.get("outAmount") else None
        impact = float(data["priceImpactPct"]) * 100 if data.get("priceImpactPct") else None
        return RouteEvidence(
            bool(data.get("routePlan") and output),
            input_mint,
            output_mint,
            amount,
            output,
            impact,
            data,
        )

    def round_trip_evidence(
        self,
        quote_mint: str,
        token_mint: str,
        quote_amount: int,
        slippage_bps: int = 100,
    ) -> tuple[RouteEvidence, RouteEvidence | None]:
        entry = self.quote(quote_mint, token_mint, quote_amount, slippage_bps)
        exit_route = None
        if entry.output_amount:
            exit_route = self.quote(token_mint, quote_mint, entry.output_amount, slippage_bps)
        return entry, exit_route
