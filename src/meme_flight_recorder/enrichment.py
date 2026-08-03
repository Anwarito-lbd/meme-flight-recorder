"""Fill a discovery snapshot with the evidence the safety gates require.

A discovery feed can say a token exists and looks popular. It cannot say
whether the mint authority is still live, whether a sell route exists, or what
the round trip would actually cost. ``SafetyEngine`` is fail-closed, so every
one of those unknowns is a rejection — which means an unenriched pipeline
rejects everything for missing evidence and never gets to exercise its real
thresholds. Surveying the live feed showed exactly that: eight
``*_unknown`` failures on every single candidate.

This module is the missing stage. It takes a snapshot built from discovery data
and attaches:

* identity verification and mint/freeze authority state, from the chain itself;
* entry and exit routes plus quoted price impact, sized for the *actual*
  intended order rather than a headline liquidity number.

Enrichment degrades rather than guesses. When a provider is unavailable or
errors, the corresponding field stays ``None`` and the gate keeps rejecting.
That is the intended behaviour: a missing provider must never look like a pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from .models import TokenSnapshot

# Native SOL, used as the quote asset for round-trip route evidence.
WRAPPED_SOL = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000


class _MintProvider(Protocol):
    def mint_evidence(self, mint: str) -> Any: ...


class _QuoteProvider(Protocol):
    def round_trip_evidence(
        self, quote_mint: str, token_mint: str, quote_amount: int, slippage_bps: int = ...
    ) -> tuple[Any, Any]: ...


@dataclass(frozen=True)
class EnrichmentReport:
    """What was actually established, and what failed to be established."""

    resolved: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def coverage_pct(self) -> float:
        total = len(self.resolved) + len(self.unresolved)
        if total == 0:
            return 0.0
        return round(100.0 * len(self.resolved) / total, 1)


def enrich_snapshot(
    snapshot: TokenSnapshot,
    *,
    mint_provider: _MintProvider | None = None,
    quote_provider: _QuoteProvider | None = None,
    intended_order_sol: float = 0.05,
    slippage_bps: int = 100,
) -> tuple[TokenSnapshot, EnrichmentReport]:
    """Return an evidence-complete snapshot plus a report on what was resolved.

    ``intended_order_sol`` must reflect the order you would really place. Price
    impact is a function of order size, so quoting a nominal amount and then
    trading a larger one measures the wrong trade. For micro-capital accounts
    this is small, which is exactly why such accounts can pass impact gates that
    a larger account could not.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    errors: dict[str, str] = {}
    updates: dict[str, Any] = {}

    mint = snapshot.identity.address

    if mint_provider is not None and mint:
        try:
            evidence = mint_provider.mint_evidence(mint)
        except Exception as error:  # noqa: BLE001 - provider failure must not pass a gate
            errors["mint_evidence"] = f"{type(error).__name__}: {error}"
            unresolved.extend(
                ("identity_verified", "mint_authority_disabled", "freeze_authority_disabled")
            )
        else:
            # mint_evidence raises unless the address really is an SPL mint, so
            # reaching here is itself the identity verification.
            updates["identity"] = replace(snapshot.identity, verified=True)
            updates["mint_authority_disabled"] = evidence.mint_authority_disabled
            updates["freeze_authority_disabled"] = evidence.freeze_authority_disabled
            resolved.extend(
                ("identity_verified", "mint_authority_disabled", "freeze_authority_disabled")
            )
            # Only override concentration when the chain actually answered;
            # getTokenLargestAccounts is refused for some high-account mints.
            if evidence.gross_top10_account_pct is not None:
                updates["top10_private_holder_pct"] = evidence.gross_top10_account_pct
                resolved.append("top10_private_holder_pct")
            else:
                unresolved.append("top10_private_holder_pct")
                if evidence.largest_accounts_error:
                    errors["largest_accounts"] = evidence.largest_accounts_error
    else:
        unresolved.extend(
            ("identity_verified", "mint_authority_disabled", "freeze_authority_disabled")
        )

    if quote_provider is not None and mint:
        amount = int(intended_order_sol * LAMPORTS_PER_SOL)
        try:
            entry, exit_route = quote_provider.round_trip_evidence(
                WRAPPED_SOL, mint, amount, slippage_bps
            )
        except Exception as error:  # noqa: BLE001
            errors["round_trip"] = f"{type(error).__name__}: {error}"
            unresolved.extend(
                (
                    "entry_route_found",
                    "exit_route_found",
                    "entry_price_impact_pct",
                    "exit_price_impact_pct",
                )
            )
        else:
            updates["entry_route_found"] = entry.found
            resolved.append("entry_route_found")
            if entry.price_impact_pct is not None:
                updates["entry_price_impact_pct"] = entry.price_impact_pct
                resolved.append("entry_price_impact_pct")
            else:
                unresolved.append("entry_price_impact_pct")

            # No exit quote means the sell side was never demonstrated. That is
            # the honeypot shape, so it is recorded as a failed route rather
            # than an unknown one.
            if exit_route is None:
                updates["exit_route_found"] = False
                resolved.append("exit_route_found")
                unresolved.append("exit_price_impact_pct")
            else:
                updates["exit_route_found"] = exit_route.found
                resolved.append("exit_route_found")
                if exit_route.price_impact_pct is not None:
                    updates["exit_price_impact_pct"] = exit_route.price_impact_pct
                    resolved.append("exit_price_impact_pct")
                else:
                    unresolved.append("exit_price_impact_pct")
    else:
        unresolved.extend(
            (
                "entry_route_found",
                "exit_route_found",
                "entry_price_impact_pct",
                "exit_price_impact_pct",
            )
        )

    enriched = replace(snapshot, **updates) if updates else snapshot
    return enriched, EnrichmentReport(tuple(resolved), tuple(unresolved), errors)
