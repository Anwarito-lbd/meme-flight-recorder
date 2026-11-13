"""Study C - discovery coverage, per the 2026-08-22 pre-registered rule.

Of today's real Solana movers (trending pools, liquidity >= $50k, pair age
>= 2 days), what fraction does the journal contain AT ALL? Read-only.

Reuses `parse_trending_pools` so "a mover" means exactly what the collector's
own feed means, rather than a second parsing of the same payload.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from meme_flight_recorder.discovery import parse_trending_pools
from meme_flight_recorder.env import load_env
from meme_flight_recorder.providers.coingecko import CoinGeckoProvider

load_env()

MIN_LIQUIDITY_USD = 50_000.0
MIN_AGE_DAYS = 2.0
PAGES = 3

provider = CoinGeckoProvider()
pools: list = []
seen_pools: set[str] = set()
for page in range(1, PAGES + 1):
    payload = provider.trending_solana_pools(page=page)
    for pool in parse_trending_pools(payload):
        if pool.pool_address not in seen_pools:
            seen_pools.add(pool.pool_address)
            pools.append(pool)
print(f"trending pools fetched: {len(pools)} across {PAGES} pages")

qualified = {}
for pool in pools:
    liq = float(pool.liquidity_usd or 0.0)
    age_min = pool.pool_age_minutes
    if liq < MIN_LIQUIDITY_USD:
        continue
    if age_min is None or age_min < MIN_AGE_DAYS * 1440:
        continue
    qualified[pool.mint] = liq

print(
    f"qualified movers (>= ${MIN_LIQUIDITY_USD:,.0f}, >= {MIN_AGE_DAYS:.0f}d old): {len(qualified)}"
)
if not qualified:
    print("no qualified sample; nothing to compare")
    raise SystemExit(0)

con = sqlite3.connect("data/flight_recorder.db")
cur = con.cursor()
covered = 0
for mint in qualified:
    row = cur.execute(
        "SELECT 1 FROM events WHERE event_type='candidate_observed' AND entity_id=? LIMIT 1",
        (mint,),
    ).fetchone()
    if row:
        covered += 1
con.close()

n = len(qualified)
pct = 100.0 * covered / n
print(f"coverage: {covered}/{n} = {pct:.1f}%")
if pct >= 70:
    print("VERDICT: discovery adequate; selection remains the frontier")
elif pct < 50:
    print("VERDICT: discovery is the binding constraint; feed expansion outranks gate studies")
else:
    print("VERDICT: ambiguous band; decide after Study L")
