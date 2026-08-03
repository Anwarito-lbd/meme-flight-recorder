#!/usr/bin/env python3
"""Does accumulation by qualified wallets predict anything?

Stage 8 is a *return* gate, so unlike the deployer study this one is judged on
forward return. The question is narrow: when two or more independently funded
wallets that qualify as repeatable traders buy the same mint, does that mint
outperform the base rate of everything else the collector saw?

**The same withdrawal rule applies as in the flow study, and it is fixed here
before any number is seen:** if the cohort signal does not beat the base rate,
it is recorded as unproven, stays journalled, and is not wired into entry.
Setup 3 (public wallet-cohort accumulation) does not become available on the
strength of a plausible mechanism alone.

**Independence is required, not assumed.** Two wallets sharing a funding source
are one economic actor with two addresses, and counting them as two independent
confirmations is precisely the manipulation this signal is supposed to survive.
Wallets whose earliest funder matches are collapsed to one vote.

Requires wallet history under ``data/wallet-history`` -- run
``scripts/backfill_wallet_history.py`` first. With no history cached this script
reports "no data", which is the correct answer and not a finding.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from meme_flight_recorder.config import CohortLimits, load_settings
from meme_flight_recorder.deployer_history import cached_pages
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider
from meme_flight_recorder.wallets import WalletClass, classify_wallet, profile_wallet


def load_wallet_transactions(folder: Path) -> dict[str, list[dict[str, Any]]]:
    """Read every cached wallet's transactions, keyed by address."""
    histories: dict[str, list[dict[str, Any]]] = {}
    if not folder.is_dir():
        return histories
    for wallet_dir in sorted(folder.iterdir()):
        if not wallet_dir.is_dir():
            continue
        transactions: list[dict[str, Any]] = []
        for page in cached_pages(wallet_dir.name, folder):
            data = (page.get("raw") or {}).get("data")
            if isinstance(data, list):
                transactions.extend(item for item in data if isinstance(item, dict))
        if transactions:
            histories[wallet_dir.name] = transactions
    return histories


def earliest_funder(transactions: list[dict[str, Any]], address: str) -> str | None:
    """The first address to send this wallet SOL, used to collapse duplicates."""
    inbound: list[tuple[float, str]] = []
    for transaction in transactions:
        try:
            moment = float(transaction.get("timestamp") or 0)
        except (TypeError, ValueError):
            continue
        for transfer in transaction.get("nativeTransfers") or []:
            source = str(transfer.get("fromUserAccount") or "")
            if transfer.get("toUserAccount") == address and source:
                inbound.append((moment, source))
    return min(inbound)[1] if inbound else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet-history", type=Path, default=Path("data/wallet-history"))
    parser.add_argument("--minimum-cohort", type=int, default=2)
    parser.add_argument("--dead-below", type=float, default=0.10)
    arguments = parser.parse_args()

    settings = load_settings()
    limits: CohortLimits = settings.cohort

    histories = load_wallet_transactions(arguments.wallet_history)
    if not histories:
        print(f"No wallet history under {arguments.wallet_history}.")
        print("This is 'no data', not 'no effect'. Run:")
        print("  .venv\\Scripts\\python.exe scripts\\backfill_wallet_history.py")
        print("then re-run this study. Stage 8 stays unproven until then.")
        return 0

    qualified: dict[str, str | None] = {}
    class_counts: dict[str, int] = defaultdict(int)
    for address, transactions in histories.items():
        profile = profile_wallet(address, transactions)
        verdict, _notes = classify_wallet(profile, limits)
        class_counts[verdict.value] += 1
        if verdict is WalletClass.REPEATABLE_TRADER:
            qualified[address] = earliest_funder(transactions, address)

    print(f"{len(histories)} wallets profiled")
    for label, count in sorted(class_counts.items(), key=lambda item: -item[1]):
        print(f"  {label:<24} {count:>4}")
    print(f"\n{len(qualified)} qualified as repeatable traders\n")

    if not qualified:
        print("No wallet cleared the bar. That is a finding about the wallet sample,")
        print("not about the signal: with no qualified traders there is nothing to test.")
        return 0

    # Which qualified wallets bought which mints, collapsed by funding source so
    # one actor running several addresses casts a single vote.
    votes: dict[str, set[str]] = defaultdict(set)
    for address, funder in qualified.items():
        identity = funder or address
        for transaction in histories[address]:
            for transfer in transaction.get("tokenTransfers") or []:
                if transfer.get("toUserAccount") == address and transfer.get("mint"):
                    votes[str(transfer["mint"])].add(identity)

    recorder = FlightRecorder(settings.database_path)
    first: dict[str, dict[str, Any]] = {}
    for event in recorder.events_by_type("candidate_observed"):
        first.setdefault(event["entity_id"], event["payload"])

    usable = {
        mint: float(payload["price_usd"])
        for mint, payload in first.items()
        if payload.get("price_usd") and float(payload["price_usd"]) > 0
    }
    prices = DexScreenerProvider().prices_for_tokens(list(usable))

    with_cohort: list[float] = []
    base_rate: list[float] = []
    for mint, entry in usable.items():
        now = prices.get(mint)
        if not now:
            continue
        multiple = now / entry
        if len(votes.get(mint, ())) >= arguments.minimum_cohort:
            with_cohort.append(multiple)
        else:
            base_rate.append(multiple)

    print(f"mints with >={arguments.minimum_cohort} independent qualified buyers: {len(with_cohort)}")
    print(f"base rate (everything else measured):                {len(base_rate)}\n")

    if not with_cohort:
        print("No overlap between the qualified wallets and the journalled candidates.")
        print("The cohort signal cannot be tested until the same tokens appear in both.")
        return 0

    header = f"{'group':<28} {'n':>5} {'dead':>6} {'median':>8} {'>2x':>5}"
    print(header)
    print("-" * len(header))
    for label, values in (("cohort accumulation", with_cohort), ("base rate", base_rate)):
        if not values:
            continue
        dead = sum(1 for value in values if value < arguments.dead_below)
        winners = sum(1 for value in values if value >= 2.0)
        print(
            f"{label:<28} {len(values):>5} {100 * dead / len(values):>5.0f}% "
            f"{statistics.median(values):>8.2f} {100 * winners / len(values):>4.0f}%"
        )

    if len(with_cohort) < 20:
        print("\nVERDICT: UNPROVEN -- too few cohort observations to separate the groups.")
        print("Stage 8 stays journalled and is not wired into entry.")
        return 0

    if statistics.median(with_cohort) > statistics.median(base_rate or [0.0]):
        print("\nVERDICT: cohort accumulation outperformed the base rate. Worth enabling")
        print("Setup 3 as a research alert -- never as automatic copy trading.")
    else:
        print("\nVERDICT: UNPROVEN -- cohort accumulation did not beat the base rate.")
        print("Per the rule fixed before this ran, Stage 8 stays journalled and is NOT")
        print("wired into entry. Record the negative result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
