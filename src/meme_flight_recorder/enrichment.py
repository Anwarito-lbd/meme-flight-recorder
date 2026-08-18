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

from .clusters import adjusted_top_holder_pct
from .models import TokenSnapshot
from .transferability import assess_transferability

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
    security_provider: Any | None = None,
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
                (
                    "identity_verified",
                    "mint_authority_disabled",
                    "freeze_authority_disabled",
                    "transaction_simulation_ok",
                )
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

            # Whether the token's own program can block a sale is decidable from
            # account data for classic SPL and Token-2022 mints. See
            # transferability.py for why this is preferred over simulating a
            # sale, which returns ambiguous errors and would produce confident
            # false accusations.
            transfer = assess_transferability(
                evidence.program_id,
                evidence.extensions,
                evidence.freeze_authority_disabled,
            )
            updates["transaction_simulation_ok"] = transfer.sellable
            if transfer.sellable is None:
                unresolved.append("transaction_simulation_ok")
            else:
                resolved.append("transaction_simulation_ok")
            transfer_evidence = {
                "transferability": transfer.verdict.value,
                "transferability_reasons": list(transfer.reasons),
                "token_program_id": transfer.program_id,
            }
            # getTokenLargestAccounts returns a *gross* figure that counts the
            # AMM pool's own token account. For a freshly migrated token the
            # pool holds most of the supply, so gross top-10 approaches 100%
            # for perfectly ordinary tokens. Writing it into
            # top10_private_holder_pct -- which means concentration *excluding*
            # infrastructure -- would reject every migrated token on a number
            # that describes the pool rather than any holder.
            #
            # It is still useful evidence, so it is recorded under its own name.
            # The genuine private figure is computed just below by resolving
            # account owners and subtracting infrastructure.
            if evidence.gross_top10_account_pct is not None:
                transfer_evidence["gross_top10_account_pct"] = evidence.gross_top10_account_pct
                transfer_evidence["largest_accounts_observed"] = (
                    evidence.largest_accounts_observed
                )
                resolved.append("gross_top10_account_pct")
            else:
                unresolved.append("gross_top10_account_pct")
                if evidence.largest_accounts_error:
                    errors["largest_accounts"] = evidence.largest_accounts_error
            # The private concentration figure the gate has always meant. Owners

            # are resolved so the AMM vault can be subtracted; see
            # clusters.adjusted_top_holder_pct for why an incomplete exclusion
            # list is safe here and the gross number never was.
            adjusted = None
            if hasattr(mint_provider, "largest_holder_owners"):
                try:
                    holdings = mint_provider.largest_holder_owners(mint)
                except Exception as error:  # noqa: BLE001
                    errors["largest_holder_owners"] = f"{type(error).__name__}: {error}"
                else:
                    adjusted = adjusted_top_holder_pct(holdings, float(evidence.supply_raw))
            if adjusted is None:
                unresolved.append("top10_private_holder_pct")
            else:
                updates["top10_private_holder_pct"] = adjusted
                transfer_evidence["adjusted_top10_private_pct"] = adjusted
                resolved.append("top10_private_holder_pct")

            updates["raw_evidence"] = {**snapshot.raw_evidence, **transfer_evidence}
    else:
        unresolved.extend(
            (
                "identity_verified",
                "mint_authority_disabled",
                "freeze_authority_disabled",
                "transaction_simulation_ok",
            )
        )

    if security_provider is not None and mint:
        try:
            sec = security_provider.token_security(mint)
        except Exception as error:  # noqa: BLE001
            errors["security_evidence"] = f"{type(error).__name__}: {error}"
        else:
            if sec is not None:
                updates["raw_evidence"] = {
                    **updates.get("raw_evidence", snapshot.raw_evidence),
                    "goplus_security": sec.raw,
                }
                if sec.sellable is not None and updates.get("transaction_simulation_ok") is None:
                    updates["transaction_simulation_ok"] = sec.sellable
                    if "transaction_simulation_ok" in unresolved:
                        unresolved.remove("transaction_simulation_ok")
                    resolved.append("transaction_simulation_ok")
                if sec.freezable is not None and updates.get("freeze_authority_disabled") is None:
                    updates["freeze_authority_disabled"] = not sec.freezable
                    if "freeze_authority_disabled" in unresolved:
                        unresolved.remove("freeze_authority_disabled")
                    resolved.append("freeze_authority_disabled")
                if sec.mintable is not None and updates.get("mint_authority_disabled") is None:
                    updates["mint_authority_disabled"] = not sec.mintable
                    if "mint_authority_disabled" in unresolved:
                        unresolved.remove("mint_authority_disabled")
                    resolved.append("mint_authority_disabled")

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
