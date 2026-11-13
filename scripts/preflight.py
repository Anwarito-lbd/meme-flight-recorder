#!/usr/bin/env python3
"""Check that everything the collector depends on is actually working.

Run before leaving the collector unattended, and again afterwards to confirm
nothing degraded overnight. Every check is read-only.
"""

from __future__ import annotations

import os

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.binance_web3 import BinanceWeb3Provider
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider
from meme_flight_recorder.providers.helius import HeliusProvider
from meme_flight_recorder.providers.jupiter import JupiterQuoteProvider

WSOL = "So11111111111111111111111111111111111111112"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

failures: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {label:<28} {detail}")
    if not passed:
        failures.append(label)


def note(label: str, detail: str) -> None:
    """Informational, never a failure.

    Alerts are the operator's eyes on an unattended book, but a run without
    them is still a valid run -- failing preflight here would block collection
    over something only the operator can supply. The point is that the gap is
    printed every morning instead of discovered by silence.
    """
    print(f"  [NOTE] {label:<28} {detail}")


def main() -> int:
    print("\nconfiguration")
    settings = load_settings()
    check("execution mode is paper", settings.execution_mode == "paper", settings.execution_mode)
    check("equity configured", settings.starting_equity_usd > 0, f"${settings.starting_equity_usd}")
    check("HELIUS_API_KEY loaded", bool(os.getenv("HELIUS_API_KEY")))
    check("COINGECKO_API_KEY loaded", bool(os.getenv("COINGECKO_API_KEY")))

    from meme_flight_recorder.notify import credentials

    token, chat_id = credentials()
    if token and chat_id:
        note("telegram alerts", "configured; open/close/scout alerts will send")
    else:
        missing = "TELEGRAM_BOT_TOKEN" if not token else "TELEGRAM_CHAT_ID"
        note(
            "telegram alerts",
            f"NOT CONFIGURED - {missing} absent from .env; the book trades "
            f"silently until it is filled by hand",
        )

    print("\njournal")
    recorder = FlightRecorder(settings.database_path)
    check("hash chain intact", recorder.verify_chain(), str(settings.database_path))
    observations = recorder.events_by_type("candidate_observed")
    check("observations readable", True, f"{len(observations)} stored")
    cycles = recorder.events_by_type("collector_cycle_completed")
    check("cycles recorded", True, f"{len(cycles)} cycles")

    print("\nproviders")
    try:
        check("helius rpc", HeliusProvider().health())
    except Exception as error:  # noqa: BLE001
        check("helius rpc", False, f"{type(error).__name__}: {error}")

    try:
        quote = JupiterQuoteProvider().quote(WSOL, BONK, 50_000_000)
        check("jupiter quote", quote.found, f"impact {quote.price_impact_pct:.4f}%")
    except Exception as error:  # noqa: BLE001
        check("jupiter quote", False, f"{type(error).__name__}: {error}")

    try:
        pair = DexScreenerProvider().deepest_pair(BONK)
        check("dexscreener", pair is not None, f"${pair.liquidity_usd:,.0f} pool" if pair else "")
    except Exception as error:  # noqa: BLE001
        check("dexscreener", False, f"{type(error).__name__}: {error}")

    try:
        rows = BinanceWeb3Provider().meme_rush(limit=3)
        check("binance discovery feed", len(rows) > 0, f"{len(rows)} candidates")
    except Exception as error:  # noqa: BLE001
        check("binance discovery feed", False, f"{type(error).__name__}: {error}")

    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        print("The collector degrades rather than guessing, so it will still run,")
        print("but any unavailable provider leaves its evidence unknown and those")
        print("candidates keep failing closed.\n")
        return 1

    print("\nAll checks passed. Safe to run the collector unattended.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
