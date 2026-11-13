"""Study L - late emergence, per the 2026-08-22 pre-registered rule.

Do tokens our outcome studies scored dead actually come back? Population:
distinct mints from candidate_observed with an observed price > 0. Two arms by
age of FIRST observation: >= 7 days and <= 48 hours. Current prices resolved
today via DexScreener; a venue counts only at liquidity >= $5,000, and where
several venues quote one mint the median price is taken (both rules inherited
from recorded defects). Read-only; no gates touched.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from meme_flight_recorder.providers.http import get_json

MIN_VENUE_LIQUIDITY_USD = 5_000.0
BATCH = 30
DELAY_S = 0.35

con = sqlite3.connect("data/flight_recorder.db")
cur = con.cursor()
cur.execute(
    "SELECT entity_id, observed_at, payload_json FROM events WHERE event_type='candidate_observed'"
)

first_seen: dict[str, tuple[datetime, float, str]] = {}
malformed = 0
for entity_id, observed_at, payload_json in cur:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        malformed += 1
        continue
    if not isinstance(payload, dict):
        malformed += 1
        continue
    price = payload.get("price_usd")
    symbol = payload.get("symbol") or ""
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        continue
    if price_f <= 0:
        continue
    try:
        obs_dt = datetime.fromisoformat(str(observed_at))
    except ValueError:
        continue
    if obs_dt.tzinfo is None:
        obs_dt = obs_dt.replace(tzinfo=UTC)
    prior = first_seen.get(entity_id)
    if prior is None or obs_dt < prior[0]:
        first_seen[entity_id] = (obs_dt, price_f, symbol)
con.close()

now = datetime.now(UTC)
old_arm = {m: v for m, v in first_seen.items() if (now - v[0]).total_seconds() >= 7 * 86400}
new_arm = {
    m: v for m, v in first_seen.items() if (now - v[0]).total_seconds() <= 48 * 3600
}
print(f"distinct priced mints: {len(first_seen)} (malformed payloads skipped: {malformed})")
print(f"arm >=7d: {len(old_arm)} | arm <=48h: {len(new_arm)}")

mints = sorted(set(old_arm) | set(new_arm))
print(f"fetching current quotes for {len(mints)} mints in batches of {BATCH}...")
prices: dict[str, float] = {}
venue_counts: dict[str, int] = {}
consecutive_errors = 0
for i in range(0, len(mints), BATCH):
    chunk = mints[i : i + BATCH]
    try:
        data = get_json(
            "https://api.dexscreener.com", f"/tokens/v1/solana/{','.join(chunk)}"
        )
        consecutive_errors = 0
    except OSError as error:
        consecutive_errors += 1
        if consecutive_errors <= 2 or consecutive_errors % 50 == 0:
            print(f"  batch {i // BATCH}: {type(error).__name__}: {error}")
        time.sleep(1.0)
        continue
    by_mint: dict[str, list[float]] = {}
    for pair in data if isinstance(data, list) else []:
        mint = pair.get("baseToken", {}).get("address")
        liq = (pair.get("liquidity") or {}).get("usd")
        price_s = pair.get("priceUsd")
        if not mint or liq is None or price_s is None:
            continue
        try:
            liq_f, price_f = float(liq), float(price_s)
        except (TypeError, ValueError):
            continue
        if liq_f < MIN_VENUE_LIQUIDITY_USD or price_f <= 0:
            continue
        by_mint.setdefault(mint, []).append(price_f)
    for mint, values in by_mint.items():
        prices[mint] = statistics.median(values)
        venue_counts[mint] = len(values)
    time.sleep(DELAY_S)

priced = sum(1 for m in old_arm if m in prices)
print(f"old-arm mints still quoted today (>= ${MIN_VENUE_LIQUIDITY_USD:,.0f} venue): {priced}")


def arm_stats(arm: dict[str, tuple], label: str) -> None:
    ratios = []
    n_priced = 0
    for mint, (_dt, obs_price, _sym) in arm.items():
        cur_price = prices.get(mint)
        if cur_price is None:
            continue
        n_priced += 1
        ratios.append(cur_price / obs_price)
    if not ratios:
        print(f"{label}: no priced members")
        return
    two_x = sum(1 for r in ratios if r >= 2.0)
    five_x = sum(1 for r in ratios if r >= 5.0)
    alive = sum(1 for r in ratios if r >= 0.1)
    buckets = [r for r in ratios]
    buckets.sort()
    median_r = statistics.median(buckets)
    print(
        f"{label}: priced {n_priced}/{len(arm)} | >=2x {two_x} ({100.0 * two_x / n_priced:.1f}%) "
        f"| >=5x {five_x} ({100.0 * five_x / n_priced:.1f}%) | >=0.1x(alive) {alive} "
        f"({100.0 * alive / n_priced:.1f}%) | median ratio {median_r:.4f}"
    )
    return


print("--- decision metric ---")
arm_stats(old_arm, "L7 (>=7d)")
arm_stats(new_arm, "L2 (<=48h)")

l7_two_x = [
    1 for m, v in old_arm.items() if m in prices and prices[m] / v[1] >= 2.0
]
l2_two_x = [
    1 for m, v in new_arm.items() if m in prices and prices[m] / v[1] >= 2.0
]
n7 = sum(1 for m in old_arm if m in prices)
n2 = sum(1 for m in new_arm if m in prices)
if n7 >= 150 and n2 >= 150:
    rate7 = len(l7_two_x) / n7
    rate2 = len(l2_two_x) / n2
    print(f"L7 2x-rate {rate7:.3f} vs L2 2x-rate {rate2:.3f}")
    if rate7 >= 2.0 * rate2:
        print("VERDICT: late emergence CONFIRMED - long-horizon pass becomes mandatory before further strategy verdicts")
    else:
        print("VERDICT: hours-scale outcomes stand; no mandatory re-run")
else:
    print(f"thin arms (n7={n7}, n2={n2}); descriptive only per the rule")

print("--- median ratio by days-since-first-observation bucket ---")
buckets: dict[str, list[float]] = {}
for mint, (first_dt, obs_price, _sym) in first_seen.items():
    cur_price = prices.get(mint)
    if cur_price is None:
        continue
    days = (now - first_dt).total_seconds() / 86400
    key = f"{min(int(days), 21):02d}d"
    buckets.setdefault(key, []).append(cur_price / obs_price)
for key in sorted(buckets):
    vals = buckets[key]
    print(f"  {key}: n={len(vals):>5} median {statistics.median(vals):.4f}")
