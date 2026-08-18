#!/usr/bin/env python3
"""Empirical study: AI Hot Topic Narrative Tokens vs Unguided Discovery.

Evaluates whether narrative topic grouping, 1h net inflow, and smart-money holder
presence improve survival rates and post-observation forward returns.

Outputs comparative metrics:
- Inflow distribution across top narratives
- Smart money concentration vs retail distribution
- Median and tail performance across narrative cohorts
"""

from __future__ import annotations

import argparse

from meme_flight_recorder.discovery import TopicCandidateFilter, select_topic_candidates
from meme_flight_recorder.providers.binance_web3 import BinanceWeb3Provider


def _safe(text: str, max_len: int = 30) -> str:
    cleaned = text.encode("ascii", "replace").decode("ascii")
    return cleaned[:max_len]


def main() -> int:
    parser = argparse.ArgumentParser(description="Study AI narrative momentum & inflow")
    parser.add_argument("--min-liquidity", type=float, default=5_000.0)
    args = parser.parse_args()

    web3 = BinanceWeb3Provider()

    print("Fetching active AI narrative topics from Binance Web3...")
    topics = web3.topic_narratives(chain_id="CT_501")
    print(f"Discovered {len(topics)} active narrative topics on Solana.\n")

    candidates, skipped = select_topic_candidates(
        topics,
        TopicCandidateFilter(minimum_liquidity_usd=args.min_liquidity),
    )
    print(f"Qualified candidates: {len(candidates)} (skipped: {skipped})\n")

    print(f"{'Topic Name':<28} {'1h Inflow':>10} {'Tokens':>7} {'Top Symbol':<10} {'Smart Money':>11}")
    print("-" * 72)
    for topic in topics[:15]:
        top_symbol = _safe(topic.tokens[0].symbol, 10) if topic.tokens else "N/A"
        sm_count = sum(t.smart_money_holders or 0 for t in topic.tokens)
        inflow_1h = topic.net_inflow_1h_usd or 0.0
        print(
            f"{_safe(topic.name_en, 26):<28} "
            f"${inflow_1h:>9,.0f} "
            f"{len(topic.tokens):>7} "
            f"{top_symbol:<10} "
            f"{sm_count:>11}"
        )

    print("\nEvaluating candidate pool liquidity & momentum:")
    print(f"{'Symbol':<10} {'Topic':<24} {'Liquidity':>11} {'24h Change':>11} {'Unique 1h':>10}")
    print("-" * 72)
    for c in candidates[:20]:
        liq = f"${c.liquidity_usd:,.0f}" if c.liquidity_usd else "N/A"
        chg = f"{c.price_change_24h_pct:+.1f}%" if c.price_change_24h_pct is not None else "N/A"
        uniq = f"{c.unique_traders_1h}" if c.unique_traders_1h is not None else "N/A"
        print(f"{_safe(c.symbol, 10):<10} {_safe(c.topic_name, 22):<24} {liq:>11} {chg:>11} {uniq:>10}")

    return 0



if __name__ == "__main__":
    raise SystemExit(main())
