"""Resolve a research universe against Kraken's live public market catalog.

The script intentionally uses public market metadata only.  It writes a generated
paper configuration and a provenance report; it never accepts API credentials.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ccxt

DEFAULT_BASES = (
    "DOGE",
    "SHIB",
    "PEPE",
    "BONK",
    "WIF",
    "TRUMP",
    "MELANIA",
    "PENGU",
    "FARTCOIN",
)


def select_active_spot_pairs(
    markets: dict[str, dict[str, Any]], bases: tuple[str, ...], quote: str
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    selected: list[str] = []
    evidence: dict[str, dict[str, Any]] = {}
    for base in bases:
        symbol = f"{base}/{quote}"
        market = markets.get(symbol)
        if not market:
            evidence[base] = {"symbol": symbol, "available": False, "reason": "not_listed"}
            continue
        is_spot = bool(market.get("spot", market.get("type") == "spot"))
        is_active = market.get("active") is not False
        available = is_spot and is_active
        limits = market.get("limits") or {}
        evidence[base] = {
            "symbol": symbol,
            "available": available,
            "spot": is_spot,
            "active": is_active,
            "amount_min": (limits.get("amount") or {}).get("min"),
            "cost_min": (limits.get("cost") or {}).get("min"),
        }
        if available:
            selected.append(symbol)
    return selected, evidence


def build_config(template: dict[str, Any], pairs: list[str], quote: str, wallet: float) -> dict:
    if not pairs:
        raise ValueError("Kraken returned no active research pairs")
    config = json.loads(json.dumps(template))
    config["dry_run"] = True
    config["dry_run_wallet"] = wallet
    config["trading_mode"] = "spot"
    config["stake_currency"] = quote
    config["exchange"]["name"] = "kraken"
    config["exchange"]["key"] = ""
    config["exchange"]["secret"] = ""
    config["exchange"]["pair_whitelist"] = pairs
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--quote", default="USD")
    parser.add_argument("--wallet", type=float, default=10.0)
    parser.add_argument("--bases", nargs="*", default=list(DEFAULT_BASES))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.wallet <= 0:
        raise ValueError("Paper wallet must be positive")
    exchange = ccxt.kraken({"enableRateLimit": True})
    markets = exchange.load_markets()
    pairs, evidence = select_active_spot_pairs(markets, tuple(args.bases), args.quote)
    template = json.loads(args.template.read_text(encoding="utf-8"))
    config = build_config(template, pairs, args.quote, args.wallet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    report = {
        "observed_at": datetime.now(UTC).isoformat(),
        "exchange": "kraken",
        "quote": args.quote,
        "paper_wallet": args.wallet,
        "selected_pairs": pairs,
        "market_evidence": evidence,
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
