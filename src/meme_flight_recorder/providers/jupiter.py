from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .http import get_json


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

    def quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = 100,
    ) -> RouteEvidence:
        if amount <= 0:
            raise ValueError("amount must be positive base units")
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
