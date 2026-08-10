"""Read-only token-security evidence from GoPlus.

Added because the sell-side gate was the weakest hard gate in the system.
`transferability.py` reasons statically about whether a mint's program *could*
block a sale -- classic SPL cannot, Token-2022 can through extensions -- which is
sound but conservative, and it cannot see an extension that is present and
configured to bite. GoPlus reads the mint's actual state.

It also supplies something unexpected and more immediately useful: **top-10
holders with percentages and tags**. Holder concentration is the single largest
cause of this system rejecting winners (36 of 56), and it currently comes from
one source. A second, independent source makes that number checkable instead of
merely believed.

The tags matter. GoPlus labels holder accounts, so a pool or a locked position
can be excluded from concentration rather than counted as a private holder --
which is the exact defect that once rejected 25 of 25 candidates on a number
describing the AMM pool.

No API key is required for the documented endpoint, verified against it directly.
The adapter is read-only by construction: it has no code path that builds, signs
or broadcasts a transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .http import get_json

BASE_URL = "https://api.gopluslabs.io"

# Holder tags that denote infrastructure rather than a private owner. Excluding
# them is what separates "someone controls 90% of supply" from "the bonding
# curve holds 90% because the token has barely traded".
INFRASTRUCTURE_TAGS: frozenset[str] = frozenset(
    {"pool", "liquidity pool", "lp", "burn", "burned", "dex", "raydium", "pump.fun"}
)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(value: Any) -> bool | None:
    """Read a GoPlus status field, preserving unknown as None.

    GoPlus encodes these either as a bare "0"/"1" or as an object carrying a
    `status`. Absent means the provider did not report it, which is not the same
    as reporting safety, and the gates depend on that distinction.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        status = value.get("status")
        return None if status is None else str(status) == "1"
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, str) and value != "":
        return value == "1"
    return None


@dataclass(frozen=True)
class TokenSecurity:
    """What GoPlus reports about one mint. Every field may be None."""

    mint: str
    mintable: bool | None = None
    freezable: bool | None = None
    closable: bool | None = None
    balance_mutable: bool | None = None
    non_transferable: bool | None = None
    has_transfer_hook: bool | None = None
    transfer_fee_pct: float | None = None
    metadata_mutable: bool | None = None
    trusted_token: bool | None = None
    holder_count: int | None = None
    holders: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def sellable(self) -> bool | None:
        """Whether a holder can currently sell, as far as this source can tell.

        None when the provider reported none of the relevant fields. False when
        any one of them says transfers can be blocked. This never returns True
        on missing evidence -- the gates fail closed and depend on it.
        """
        signals = (self.non_transferable, self.freezable, self.has_transfer_hook)
        if all(signal is None for signal in signals):
            return None
        return not any(signal is True for signal in signals)

    @property
    def top10_holder_pct(self) -> float | None:
        """Raw top-10 concentration, infrastructure included."""
        if not self.holders:
            return None
        total = sum(_as_float(holder.get("percent")) or 0.0 for holder in self.holders[:10])
        return round(100.0 * total, 4)

    @property
    def adjusted_top10_holder_pct(self) -> float | None:
        """Top-10 concentration excluding tagged infrastructure and locked supply.

        **Returns None on this population, and that is the correct answer.**
        Measured against 14 journalled Solana meme mints, GoPlus returned an
        empty `tag` on every holder and no `lp_holders` at all. With nothing
        labelled, there is nothing to exclude, and returning the gross figure
        under the name "adjusted" would be a number wearing a stronger name than
        it has earned -- the same defect as reporting transaction counts as
        unique buyers.

        So this reports None unless at least one holder is actually labelled.
        Callers wanting the unadjusted figure should ask for
        ``top10_holder_pct`` and know what they are getting.
        """
        if not self.holders:
            return None
        labelled = any(
            str(holder.get("tag") or "").strip() or str(holder.get("is_locked") or "0") == "1"
            for holder in self.holders[:10]
        )
        if not labelled:
            return None
        total = 0.0
        for holder in self.holders[:10]:
            tag = str(holder.get("tag") or "").strip().lower()
            if tag in INFRASTRUCTURE_TAGS:
                continue
            if str(holder.get("is_locked") or "0") == "1":
                continue
            total += _as_float(holder.get("percent")) or 0.0
        return round(100.0 * total, 4)


class GoPlusProvider:
    """Token-security evidence. It cannot build, sign, or broadcast anything."""

    def __init__(self, timeout: int = 20, api_key: str | None = None) -> None:
        self.timeout = timeout
        self.api_key = api_key

    def token_security(self, mint: str) -> TokenSecurity | None:
        """Fetch security evidence for one Solana mint.

        Returns None when the provider has nothing for this mint, which is
        distinct from returning a report full of Nones: the first means "not
        covered", the second means "covered, and these specific facts are
        unknown". Both keep the gates closed, and conflating them would lose the
        ability to tell coverage gaps from data gaps.
        """
        if not mint.strip():
            raise ValueError("mint is required")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        payload = get_json(
            BASE_URL,
            "/api/v1/solana/token_security",
            params={"contract_addresses": mint},
            headers=headers,
            timeout=self.timeout,
        )
        if str(payload.get("code")) != "1":
            return None
        result = payload.get("result") or {}
        row = result.get(mint)
        if row is None:
            # The API has been observed to key the result case-sensitively; fall
            # back to the single entry rather than reporting no coverage.
            values = [value for value in result.values() if isinstance(value, dict)]
            if len(values) != 1:
                return None
            row = values[0]

        fee = row.get("transfer_fee")
        fee_pct = None
        if isinstance(fee, dict) and fee:
            fee_pct = _as_float(fee.get("current_fee_rate") or fee.get("fee_rate"))

        return TokenSecurity(
            mint=mint,
            mintable=_flag(row.get("mintable")),
            freezable=_flag(row.get("freezable")),
            closable=_flag(row.get("closable")),
            balance_mutable=_flag(row.get("balance_mutable_authority")),
            non_transferable=_flag(row.get("non_transferable")),
            has_transfer_hook=_flag(row.get("transfer_hook")),
            transfer_fee_pct=fee_pct,
            metadata_mutable=_flag(row.get("metadata_mutable")),
            trusted_token=_flag(row.get("trusted_token")),
            holder_count=(
                int(value) if (value := _as_float(row.get("holder_count"))) is not None else None
            ),
            holders=tuple(row.get("holders") or ()),
            raw=row,
        )
