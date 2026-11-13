"""Decode real Solana launch-venue transactions into exact facts.

The rule this module exists to enforce: **identity is the mint address, decoded
from the instruction, never inferred from a ticker or a name.** Five CATE mints
and four SAOF mints appeared in this journal in one night, so any pipeline that
resolves a token by symbol is resolving the wrong token some of the time.

Instruction classification is by **Anchor discriminator** -- the first eight
bytes of `sha256("global:<name>")` -- not by account count, data length or
position in the transaction. Those heuristics drift silently when a program is
upgraded; a discriminator either matches or it does not.

A failed transaction is decoded exactly like a successful one and is then marked
failed. It is not skipped. A transaction that reverted still tells us somebody
tried to buy, and dropping it would bias every flow measurement toward the
trades that happened to work.

Pure. No network, no I/O. The caller fetches the transaction and this reads it,
which is what lets the decoders be tested against saved real fixtures.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_INDEX = {character: index for index, character in enumerate(BASE58_ALPHABET)}


def base58_decode(value: str) -> bytes:
    """Decode base58 as Solana encodes instruction data.

    Leading '1' characters encode leading zero bytes and must be preserved; a
    decoder that drops them corrupts any discriminator beginning with 0x00.
    """
    if not value:
        return b""
    number = 0
    for character in value:
        index = _BASE58_INDEX.get(character)
        if index is None:
            raise ValueError(f"invalid base58 character: {character!r}")
        number = number * 58 + index
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    padding = len(value) - len(value.lstrip("1"))
    return b"\x00" * padding + body


def discriminator(name: str, namespace: str = "global") -> bytes:
    return hashlib.sha256(f"{namespace}:{name}".encode()).digest()[:8]


class Venue(StrEnum):
    PUMP_FUN = "pump_fun"
    PUMP_SWAP = "pump_swap"
    RAYDIUM_LAUNCHLAB = "raydium_launchlab"
    RAYDIUM_AMM_V4 = "raydium_amm_v4"
    METEORA_DBC = "meteora_dbc"
    METEORA_DLMM = "meteora_dlmm"
    UNKNOWN = "unknown"


PROGRAM_IDS: dict[str, Venue] = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": Venue.PUMP_FUN,
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": Venue.PUMP_SWAP,
    "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj": Venue.RAYDIUM_LAUNCHLAB,
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": Venue.RAYDIUM_AMM_V4,
    "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN": Venue.METEORA_DBC,
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": Venue.METEORA_DLMM,
}

VENUE_PROGRAM_IDS: dict[Venue, str] = {venue: program for program, venue in PROGRAM_IDS.items()}


class Action(StrEnum):
    CREATE = "create"
    BUY = "buy"
    SELL = "sell"
    MIGRATE = "migrate"
    POOL_CREATE = "pool_create"
    UNKNOWN = "unknown"


# Discriminators per venue. Each is derived rather than pasted, so the derivation
# is visible and a wrong instruction name fails loudly instead of matching
# nothing forever.
def _table(venue: Venue, names: dict[str, Action]) -> dict[bytes, tuple[Venue, Action]]:
    return {discriminator(name): (venue, action) for name, action in names.items()}


DISCRIMINATORS: dict[bytes, tuple[Venue, Action]] = {
    **_table(
        Venue.PUMP_FUN,
        {
            "create": Action.CREATE,
            "buy": Action.BUY,
            "sell": Action.SELL,
            "migrate": Action.MIGRATE,
        },
    ),
    **_table(
        Venue.PUMP_SWAP,
        {
            "create_pool": Action.POOL_CREATE,
            "buy": Action.BUY,
            "sell": Action.SELL,
        },
    ),
    **_table(
        Venue.RAYDIUM_LAUNCHLAB,
        {
            "initialize": Action.CREATE,
            "buy_exact_in": Action.BUY,
            "sell_exact_in": Action.SELL,
            "migrate_to_amm": Action.MIGRATE,
        },
    ),
    **_table(
        Venue.METEORA_DBC,
        {
            "initialize_virtual_pool_with_spl_token": Action.CREATE,
            "swap": Action.BUY,
            "migration_damm_v2": Action.MIGRATE,
        },
    ),
}

WSOL_MINT = "So11111111111111111111111111111111111111112"


@dataclass(frozen=True)
class DecodedInstruction:
    venue: Venue
    action: Action
    program_id: str
    mint: str | None
    accounts: tuple[str, ...]
    discriminator_hex: str
    inner: bool


@dataclass(frozen=True)
class DecodedTransaction:
    """One transaction, decoded. Identity is `(slot, transaction_index, signature)`."""

    signature: str
    slot: int
    transaction_index: int | None
    succeeded: bool
    error: str | None
    fee_payer: str | None
    block_time: int | None
    instructions: tuple[DecodedInstruction, ...] = ()
    mints: tuple[str, ...] = ()
    sol_delta_lamports: int | None = None

    @property
    def identity(self) -> tuple[int, int | None, str]:
        return (self.slot, self.transaction_index, self.signature)

    @property
    def venues(self) -> tuple[Venue, ...]:
        return tuple({instruction.venue for instruction in self.instructions})

    @property
    def primary_mint(self) -> str | None:
        """The non-wrapped-SOL mint this transaction is about.

        Wrapped SOL appears in almost every swap as the quote leg, so returning
        it as "the mint" would label every trade as a trade in SOL.
        """
        for instruction in self.instructions:
            if instruction.mint and instruction.mint != WSOL_MINT:
                return instruction.mint
        for mint in self.mints:
            if mint != WSOL_MINT:
                return mint
        return None


def _token_mints(meta: dict[str, Any]) -> tuple[str, ...]:
    """Mints touched by the transaction, from balance records.

    These are authoritative -- the runtime wrote them -- which is why they are
    preferred over guessing a mint from an account index that shifts between
    program versions.
    """
    seen: list[str] = []
    for key in ("preTokenBalances", "postTokenBalances"):
        for balance in meta.get(key) or []:
            mint = balance.get("mint")
            if mint and mint not in seen:
                seen.append(str(mint))
    return tuple(seen)


def _sol_delta(meta: dict[str, Any], accounts: list[str], fee_payer: str | None) -> int | None:
    pre = meta.get("preBalances")
    post = meta.get("postBalances")
    if not pre or not post or not fee_payer or fee_payer not in accounts:
        return None
    index = accounts.index(fee_payer)
    if index >= len(pre) or index >= len(post):
        return None
    return int(post[index]) - int(pre[index])


def _account_keys(message: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for entry in message.get("accountKeys") or []:
        if isinstance(entry, dict):
            key = entry.get("pubkey")
            if key:
                keys.append(str(key))
        else:
            keys.append(str(entry))
    return keys


def _resolve_accounts(instruction: dict[str, Any], account_keys: list[str]) -> tuple[str, ...]:
    raw = instruction.get("accounts") or []
    resolved: list[str] = []
    for entry in raw:
        if isinstance(entry, int):
            if 0 <= entry < len(account_keys):
                resolved.append(account_keys[entry])
        else:
            resolved.append(str(entry))
    return tuple(resolved)


def _mint_from_accounts(
    venue: Venue, accounts: tuple[str, ...], candidates: tuple[str, ...]
) -> str | None:
    """Pick the instruction's mint, cross-checked against the balance records.

    An account index alone is fragile across program upgrades, and a balance
    record alone cannot say *which* instruction touched it. Intersecting the two
    is stronger than either: the mint must both appear in this instruction's
    account list and be a token the runtime recorded a balance for.
    """
    known = {mint for mint in candidates if mint != WSOL_MINT}
    for account in accounts:
        if account in known:
            return account
    return None


def decode_transaction(payload: dict[str, Any]) -> DecodedTransaction:
    """Decode one `getTransaction` result (jsonParsed, maxSupportedTransactionVersion=0)."""
    transaction = payload.get("transaction") or {}
    message = transaction.get("message") or {}
    meta = payload.get("meta") or {}
    signatures = transaction.get("signatures") or []
    account_keys = _account_keys(message)
    fee_payer = account_keys[0] if account_keys else None
    error = meta.get("err")

    mints = _token_mints(meta)

    raw_instructions: list[tuple[dict[str, Any], bool]] = [
        (instruction, False) for instruction in message.get("instructions") or []
    ]
    for group in meta.get("innerInstructions") or []:
        for instruction in group.get("instructions") or []:
            raw_instructions.append((instruction, True))

    decoded: list[DecodedInstruction] = []
    for instruction, is_inner in raw_instructions:
        program_id = str(instruction.get("programId") or "")
        venue = PROGRAM_IDS.get(program_id)
        if venue is None:
            continue
        data = instruction.get("data")
        action = Action.UNKNOWN
        prefix = b""
        if isinstance(data, str) and data:
            try:
                prefix = base58_decode(data)[:8]
            except ValueError:
                prefix = b""
            match = DISCRIMINATORS.get(prefix)
            if match is not None:
                action = match[1]
        accounts = _resolve_accounts(instruction, account_keys)
        decoded.append(
            DecodedInstruction(
                venue=venue,
                action=action,
                program_id=program_id,
                mint=_mint_from_accounts(venue, accounts, mints),
                accounts=accounts,
                discriminator_hex=prefix.hex(),
                inner=is_inner,
            )
        )

    return DecodedTransaction(
        signature=str(signatures[0]) if signatures else "",
        slot=int(payload.get("slot") or 0),
        transaction_index=payload.get("transactionIndex"),
        succeeded=error is None,
        error=str(error) if error else None,
        fee_payer=fee_payer,
        block_time=payload.get("blockTime"),
        instructions=tuple(decoded),
        mints=mints,
        sol_delta_lamports=_sol_delta(meta, account_keys, fee_payer),
    )


@dataclass
class DecodeStats:
    """A reconciling denominator for the decoder itself."""

    seen: int = 0
    venue_matched: int = 0
    action_matched: int = 0
    mint_resolved: int = 0
    failed_transactions: int = 0
    by_action: dict[str, int] = field(default_factory=dict)
    by_venue: dict[str, int] = field(default_factory=dict)

    def observe(self, decoded: DecodedTransaction) -> None:
        self.seen += 1
        if not decoded.succeeded:
            self.failed_transactions += 1
        if not decoded.instructions:
            return
        self.venue_matched += 1
        for instruction in decoded.instructions:
            self.by_venue[instruction.venue.value] = (
                self.by_venue.get(instruction.venue.value, 0) + 1
            )
            self.by_action[instruction.action.value] = (
                self.by_action.get(instruction.action.value, 0) + 1
            )
        if any(item.action is not Action.UNKNOWN for item in decoded.instructions):
            self.action_matched += 1
        if decoded.primary_mint:
            self.mint_resolved += 1
