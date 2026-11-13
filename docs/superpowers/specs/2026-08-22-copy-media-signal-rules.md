# Copy & Media Confluence Resolvers — Decision Rules

Written 2026-08-22, before any outcome data for either signal exists.
Implements the two mandate pillars still resolving UNKNOWN: whale accumulation
(copy) and narrative acceleration via media (media). Momentum and math pillars
already resolve (`buy_transaction_share_pct`, `volume_ratio_to_baseline`,
concentration, developer band).

## Signal W1 — qualified wallet entry ("copy", smart money)

**Evidence:** a mint appears in a swap by a tracked wallet that itself clears
the 30-trade/coverage bar in `tracked_traders.py`; captured from existing
wallet-history backfills. Field: `ConfluenceEvidence.qualified_whale_holders`.

**Study (pre-registered):** death rate and 2x-rate within 48h for candidates
with ≥1 qualified-wallet entry vs matched candidates without, minimum n=100 per
arm, held-out split, decision rule written before first run:
- COUNTS toward confluence only if the arm difference in **death rate** is
  ≥10 points in the safer direction AND survives deleting the best trade.
- If wallets clear the risk test but show no return edge, the signal may rank,
  never gate — same split as the cluster gate.

## Signal M1 — media mention velocity ("media")

**Evidence:** `media_mention` journal events now accumulating (Reddit rotating,
Google News, curated t.me channels). Velocity = distinct credible mentions of
the exact mint in the trailing 60 minutes.

**Study (pre-registered):** same shape as the virality study that returned NO —
but measured on *discovery lead*, not sentiment: does a first mention precede
first `candidate_observed` for the same mint (lead time distribution), and do
mentioned mints die less or more over 48h at n≥100/arm?
- COUNTS toward `NARRATIVE_ACCELERATING` only if median discovery lead >0 AND
  48h death rate is not worse than baseline.
- The virality prior is negative; these rules exist so media can re-enter only
  through evidence, not enthusiasm.

## Wiring order (fixed now)

1. Both signals journal as raw evidence immediately (free, no behavior change).
2. Studies run when arms reach n≥100.
3. Only on a PASS does the resolver move into the confluence count.
4. On a FAIL the signal records its verdict and stays out — permanently unless
   a new pre-registered rule supersedes it.

No thresholds above are tunable after seeing outcomes. Read-only until then.
