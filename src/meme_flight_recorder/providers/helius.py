from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .http import get_json, post_json


@dataclass(frozen=True)
class MintEvidence:
    mint: str
    slot: int
    decimals: int
    supply_raw: int
    mint_authority_disabled: bool
    freeze_authority_disabled: bool
    gross_top10_account_pct: float | None
    largest_accounts_observed: int
    largest_accounts_error: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class WalletHistoryPage:
    address: str
    transactions: tuple[dict[str, Any], ...]
    next_cursor: str | None
    has_more: bool
    raw: dict[str, Any]


class HeliusProvider:
    """Read-only Solana RPC evidence. It cannot construct or broadcast transactions."""

    def __init__(
        self,
        api_key: str | None = None,
        rpc_url: str | None = None,
        transport: Callable[..., Any] = post_json,
        get_transport: Callable[..., Any] = get_json,
        wallet_api_base: str = "https://api.helius.xyz",
    ) -> None:
        key = api_key if api_key is not None else os.getenv("HELIUS_API_KEY", "")
        configured_url = rpc_url if rpc_url is not None else os.getenv("SOLANA_RPC_URL", "")
        if configured_url:
            self.rpc_url = configured_url
        elif key:
            self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={key}"
        else:
            raise ValueError("Set HELIUS_API_KEY or SOLANA_RPC_URL")
        self._api_key = key
        self._transport = transport
        self._get_transport = get_transport
        self._wallet_api_base = wallet_api_base
        self._request_id = 0

    def rpc(self, method: str, params: list[Any] | None = None) -> Any:
        self._request_id += 1
        response = self._transport(
            self.rpc_url,
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params or [],
            },
        )
        if not isinstance(response, dict):
            raise TypeError("Invalid Helius response")
        if response.get("error"):
            error = response["error"]
            raise RuntimeError(f"Helius RPC error {error.get('code')}: {error.get('message')}")
        if "result" not in response:
            raise RuntimeError("Helius response has no result")
        return response["result"]

    def health(self) -> bool:
        return self.rpc("getHealth") == "ok"

    def wallet_history(
        self,
        address: str,
        before: str | None = None,
        token_accounts: str = "balanceChanged",
    ) -> WalletHistoryPage:
        """Fetch one newest-first page of parsed history without discarding failures."""
        if not self._api_key:
            raise ValueError("HELIUS_API_KEY is required for Wallet API history")
        if not address:
            raise ValueError("Wallet address is required")
        if token_accounts not in {"balanceChanged", "none", "all"}:
            raise ValueError("token_accounts must be balanceChanged, none, or all")
        params: dict[str, Any] = {"tokenAccounts": token_accounts}
        if before:
            params["before"] = before
        response = self._get_transport(
            self._wallet_api_base,
            f"v1/wallet/{address}/history",
            params=params,
            headers={"X-Api-Key": self._api_key},
        )
        if not isinstance(response, dict) or not isinstance(response.get("data"), list):
            raise TypeError("Invalid Helius Wallet API response")
        pagination = response.get("pagination") or {}
        has_more = bool(pagination.get("hasMore"))
        next_cursor = pagination.get("nextCursor") if has_more else None
        if has_more and not next_cursor:
            raise RuntimeError("Helius Wallet API says more data exists without a cursor")
        return WalletHistoryPage(
            address=address,
            transactions=tuple(response["data"]),
            next_cursor=next_cursor,
            has_more=has_more,
            raw=response,
        )

    def mint_evidence(self, mint: str) -> MintEvidence:
        account = self.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        value = account.get("value")
        if not value:
            raise ValueError("Mint account was not found")
        parsed = value.get("data", {}).get("parsed", {})
        if parsed.get("type") != "mint":
            raise ValueError("Address is not an SPL token mint")
        info = parsed.get("info", {})
        supply = self.rpc("getTokenSupply", [mint])
        supply_raw = int(supply["value"]["amount"])
        largest: dict[str, Any] | None = None
        largest_error: str | None = None
        try:
            largest = self.rpc("getTokenLargestAccounts", [mint])
        except RuntimeError as error:
            # Some providers refuse this method for tokens with huge account counts.
            # Authority evidence remains useful, but concentration must stay unknown.
            largest_error = str(error)
        accounts = largest.get("value", []) if largest else []
        top10_raw = sum(int(item["amount"]) for item in accounts[:10])
        concentration = (100 * top10_raw / supply_raw) if supply_raw and largest else None
        return MintEvidence(
            mint=mint,
            slot=max(
                int(account.get("context", {}).get("slot", 0)),
                int(supply.get("context", {}).get("slot", 0)),
                int(largest.get("context", {}).get("slot", 0)) if largest else 0,
            ),
            decimals=int(info["decimals"]),
            supply_raw=supply_raw,
            mint_authority_disabled=info.get("mintAuthority") is None,
            freeze_authority_disabled=info.get("freezeAuthority") is None,
            gross_top10_account_pct=concentration,
            largest_accounts_observed=len(accounts),
            largest_accounts_error=largest_error,
            raw={"account": account, "supply": supply, "largest_accounts": largest},
        )

    def simulate_unsigned_transaction(
        self, transaction_base64: str, replace_recent_blockhash: bool = True
    ) -> dict[str, Any]:
        if not transaction_base64:
            raise ValueError("transaction_base64 is required")
        result = self.rpc(
            "simulateTransaction",
            [
                transaction_base64,
                {
                    "encoding": "base64",
                    "sigVerify": False,
                    "replaceRecentBlockhash": replace_recent_blockhash,
                    "commitment": "confirmed",
                },
            ],
        )
        value = result.get("value", {})
        return {
            "ok": value.get("err") is None,
            "error": value.get("err"),
            "units_consumed": value.get("unitsConsumed"),
            "logs": value.get("logs") or [],
            "raw": result,
        }
