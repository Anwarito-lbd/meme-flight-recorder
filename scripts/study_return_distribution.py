#!/usr/bin/env python3
"""What is the actual shape of returns in this market, and can it be traded?

Every other study here asks whether some filter improves on the base rate. This
one asks the prior question: what *is* the base rate, and does any long strategy
survive it at this account's size?

The answer changes what is worth building. If returns are a trend, an entry
signal can capture it. If they are a lottery -- most candidates near zero, a
handful very large -- then the edge lives in a tail, capturing a tail needs many
attempts, and the binding constraint is bankroll rather than signal quality. No
amount of filtering fixes a bankroll.

Three corrections make the numbers readable rather than flattering:

**Untradeable candidates are excluded.** The raw distribution is dominated by
tokens with no liquidity, where a price exists but no order could ever have been
filled. One observed candidate had $0.00 in its pool and a nominal 2,176,870x,
which is a bonding-curve artifact rather than a return. Restricting to the risk
engine's own liquidity floor is what makes the figure describe an executable
strategy.

**Outlier dependence is measured, not mentioned.** This system already refuses to
call its own strategy validated when one trade carries more than a third of the
profit. The same rule is applied here, and the EV is recomputed with the largest
winners removed one at a time. A positive expectancy that evaporates when three
of a hundred and fifty tokens are dropped is a property of those three tokens.

**Costs are charged.** Both legs, always.

Survivorship warning: outcomes come from tokens still quoted by the price feed
today. Tokens that died so completely that no pair remains cannot be resolved and
are counted separately rather than silently dropped, but this still biases the
result upward. Treat every figure as an upper bound.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import statistics
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider


def percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        return 0.0
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[index]


def describe(label: str, multiples: list[float], cost_pct: float) -> None:
    """Print the distribution and its dependence on a handful of winners."""
    count = len(multiples)
    print(f"--- {label}: n={count} ---")
    if count < 5:
        print("  too few outcomes to describe\n")
        return

    ordered = sorted(multiples)
    leg = cost_pct / 100.0
    # Profit per $1 staked, paying costs entering and leaving.
    pnl = sorted((value * (1 - leg) - (1 + leg) for value in multiples), reverse=True)

    print(f"  mean   {statistics.mean(ordered):>10.3f}")
    print(f"  median {statistics.median(ordered):>10.3f}")
    for fraction in (0.10, 0.25, 0.75, 0.90):
        print(f"    p{int(100 * fraction):<3}{percentile(ordered, fraction):>11.3f}")
    print(f"  max    {max(ordered):>10.2f}")

    for threshold in (1.0, 2.0, 5.0, 10.0):
        share = 100.0 * sum(1 for value in ordered if value >= threshold) / count
        print(f"    >= {threshold:>4.0f}x  {share:>5.1f}%")

    gross_profit = sum(value for value in pnl if value > 0)
    if gross_profit > 0:
        best_share = 100.0 * pnl[0] / gross_profit
        top3_share = 100.0 * sum(pnl[:3]) / gross_profit
        print(f"\n  best token   {best_share:>5.0f}% of gross profit   (rule: <= 33%)")
        print(f"  top 3        {top3_share:>5.0f}% of gross profit")
        if best_share > 33.0:
            print("  -> fails this project's own outlier rule. Not a validated edge.")

    print(f"\n  expectancy per $1 staked, {cost_pct:.0f}% per leg:")
    for dropped in (0, 1, 2, 3, 5):
        if len(pnl) - dropped < 5:
            break
        kept = pnl[dropped:]
        marker = "" if dropped else "   <- as measured"
        print(f"    dropping top {dropped}: {statistics.mean(kept):+7.3f}  (n={len(kept)}){marker}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cost-pct", type=float, default=6.0, help="Cost per leg, percent.")
    arguments = parser.parse_args()

    settings = load_settings()
    floor = settings.micro.minimum_pool_liquidity_usd
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled yet.")
        return 1

    first: dict[str, dict[str, Any]] = {}
    for event in events:
        first.setdefault(event["entity_id"], event["payload"])

    priced = {
        mint: payload
        for mint, payload in first.items()
        if payload.get("price_usd") and float(payload["price_usd"]) > 0
    }
    prices = DexScreenerProvider().prices_for_tokens(list(priced))
    unresolved = len(priced) - sum(1 for mint in priced if mint in prices)

    print(f"{len(first)} distinct mints journalled")
    print(f"  {len(first) - len(priced)} with no usable observation price")
    print(f"  {unresolved} whose current price could not be resolved")
    print(f"  {len(priced) - unresolved} with a measurable outcome\n")

    tiers: tuple[tuple[str, float], ...] = (
        ("every priced candidate", 0.0),
        (f"pool >= ${floor:,.0f} (risk-engine floor)", floor),
        ("pool >= $50,000", 50_000.0),
    )
    for label, minimum in tiers:
        multiples = [
            prices[mint] / float(payload["price_usd"])
            for mint, payload in priced.items()
            if mint in prices and float(payload.get("liquidity_usd") or 0.0) >= minimum
        ]
        describe(label, multiples, arguments.cost_pct)

    print("Read the first tier as a warning, not a result: it is dominated by tokens")
    print("with no liquidity, where a price moved but no order could have filled.")
    print()
    print("If the median is far below the mean, this market is a lottery rather than")
    print("a trend. Capturing a tail of probability p needs roughly 3/p attempts to be")
    print("more likely than not to hit one. Compare that against equity divided by")
    print("position size before concluding that a better entry signal is what is")
    print("missing -- the binding constraint may be the number of shots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
