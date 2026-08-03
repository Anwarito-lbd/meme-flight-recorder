"""Decide whether a token's own program can block a sale.

This is the honeypot question, and on Solana it has a deterministic answer for
most tokens rather than a probabilistic one.

The classic SPL Token program has no hook for custom transfer logic. A mint it
owns cannot refuse a transfer, tax it, or redirect it. The only lever the issuer
retains is the freeze authority, and whether that is revoked is already known.
So for a classic mint with freeze authority revoked, a contract-level honeypot
is not possible, and that can be asserted from account data alone.

Token-2022 is different: extensions can attach a transfer hook running arbitrary
program code, impose transfer fees, install a permanent delegate able to move or
burn balances without consent, or mark the mint non-transferable outright. Each
is a legitimate feature and each is also a honeypot primitive, so their presence
means the sell side cannot be cleared without deeper inspection.

Choosing static analysis over transaction simulation is deliberate. Simulating a
sale requires a funded holder to impersonate and returns errors that are
frequently ambiguous — insufficient balance, stale blockhash and compute
exhaustion all look like failure, and reading any of them as "honeypot" would
produce confident false accusations. This check answers a narrower question
exactly, and returns UNKNOWN whenever it cannot.

What it does not cover: a token can still be impossible to exit because no
liquidity exists or because the pool is manipulated. Those are route, impact and
cluster questions, gated elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Program ids that own SPL mints.
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Token-2022 extensions that can restrict, tax, or seize a transfer.
RESTRICTIVE_EXTENSIONS: frozenset[str] = frozenset(
    {
        "transferHook",
        "transferFeeConfig",
        "permanentDelegate",
        "nonTransferable",
        "nonTransferableAccount",
        "defaultAccountState",
        "confidentialTransferMint",
        "pausable",
    }
)


class Transferability(StrEnum):
    FREE = "free"
    RESTRICTED = "restricted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TransferabilityReport:
    verdict: Transferability
    reasons: tuple[str, ...] = ()
    program_id: str = ""
    restrictive_extensions: tuple[str, ...] = ()

    @property
    def sellable(self) -> bool | None:
        """Tri-state for the safety gate: True, False, or None for unknown.

        None is not a soft pass. The gate fails closed on it, which is correct:
        an unverified sell side is exactly the risk this check exists to catch.
        """
        if self.verdict is Transferability.FREE:
            return True
        if self.verdict is Transferability.RESTRICTED:
            return False
        return None


def assess_transferability(
    program_id: str,
    extensions: tuple[str, ...] = (),
    freeze_authority_disabled: bool | None = None,
) -> TransferabilityReport:
    """Classify whether the token program itself can prevent a sale."""
    if not program_id:
        return TransferabilityReport(
            Transferability.UNKNOWN, ("token_program_unknown",), program_id
        )

    # A live freeze authority can freeze the buyer's token account, which blocks
    # the sale as effectively as any custom logic, on either program.
    if freeze_authority_disabled is False:
        return TransferabilityReport(
            Transferability.RESTRICTED, ("freeze_authority_active",), program_id
        )

    if program_id == SPL_TOKEN_PROGRAM:
        if freeze_authority_disabled is None:
            return TransferabilityReport(
                Transferability.UNKNOWN, ("freeze_authority_unknown",), program_id
            )
        # Classic SPL with no freeze authority: the program offers no mechanism
        # to block, tax, or reverse a transfer.
        return TransferabilityReport(Transferability.FREE, (), program_id)

    if program_id == TOKEN_2022_PROGRAM:
        found = tuple(sorted(set(extensions) & RESTRICTIVE_EXTENSIONS))
        if found:
            return TransferabilityReport(
                Transferability.RESTRICTED,
                tuple(f"token2022_{name}" for name in found),
                program_id,
                found,
            )
        if freeze_authority_disabled is None:
            return TransferabilityReport(
                Transferability.UNKNOWN, ("freeze_authority_unknown",), program_id
            )
        return TransferabilityReport(Transferability.FREE, (), program_id)

    # An unrecognised owner program may implement anything at all.
    return TransferabilityReport(
        Transferability.UNKNOWN, ("token_program_unrecognised",), program_id
    )
