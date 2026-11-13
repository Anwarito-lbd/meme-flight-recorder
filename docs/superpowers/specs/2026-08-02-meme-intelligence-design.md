# Meme-Coin Intelligence System — Design

**Date:** 2026-08-02
**Status:** Approved
**Base:** `meme-flight-recorder` 0.4.0

## Problem

The repository's first 90-day backtest completed zero trades, and the cause was
unknown. A live survey of 50 Solana candidates run during this design session
found the answer, and it was two separate problems wearing one coat:

```
gate outcomes:    50 reject,  0 eligible,  0 monitor
cluster verdicts: 40 disqualified,  5 clear,  3 suspect,  2 insufficient

  50  identity_unverified              <- evidence never fetched
  50  mint_authority_unknown           <- evidence never fetched
  50  freeze_authority_unknown         <- evidence never fetched
  50  entry_route_unknown              <- evidence never fetched
  50  exit_route_unknown               <- evidence never fetched
  50  transaction_simulation_unknown   <- evidence never fetched
  50  entry_price_impact_unknown       <- evidence never fetched
  50  exit_price_impact_unknown        <- evidence never fetched
  49  token_too_young_for_universe     <- threshold wrong for this account
  47  liquidity_below_minimum          <- threshold wrong for this account
  34  developer_selling                <- genuinely bad candidate
  23  holder_concentration_excessive   <- genuinely bad candidate
```

**Problem 1 — pipeline starvation.** Eight failures appear on *every* candidate,
and all eight are evidence the pipeline could fetch but never did. The
fail-closed gates were rejecting for missing data before their real thresholds
ever ran. No amount of threshold tuning would have revealed this.

**Problem 2 — thresholds written for a different account.** The `$50,000`
liquidity floor and `60`-minute age floor were calibrated for a ~$10,000
account. This account is $10–40.

Underneath both, the candidate flow is genuinely poor: 80% cluster-disqualified,
68% with the developer already selling. That number will not improve with better
plumbing, and the design must not pretend otherwise.

## Capital constraint

$10–40. This is load-bearing, not a footnote.

A $5–10 position pays roughly 3–6% in round-trip cost (DEX fee, priority fee,
slippage), so it needs ~+6% just to break even. Strategies requiring private RPC
and Jito tips are unreachable — a single attempt costs more than the account.

The system is therefore a **live-fire training rig**: real fills, real slippage,
real psychology, with total downside capped at $40 of tuition. Its deliverable is
a validated process with measured expectancy that scales when capital does.

## What this system is for

One pipeline, four entry points, four read-outs.

```
ENTRY                    PIPELINE                              READ-OUT
─────                    ────────                              ────────
meme-rush feed  ─┐
paste a contract ├─> normalize ─> enrich ─> gates ─> cluster ─> score ─> journal
source calls    ─┤   (snapshot)   (Helius   (safety) (insider)         (hash-chained)
Padre/Fomo CSV  ─┘                +Jupiter                                  │
                                  +DexScreener)                             │
                                                                            ├─> A funnel report
                                                                            ├─> B verdict on one token
                                                                            ├─> C source expectancy
                                                                            └─> D (locked)
```

- **A — funnel.** Surface the rare survivor from the discovery feed.
- **B — filter.** Grade a contract the user brings in from X, Padre, or Fomo.
- **C — measurement.** Timestamp source calls, score real post-call expectancy
  after costs, rank sources as originator / amplifier / distributor.
- **D — execution.** Locked. Read-out C is what earns the right to unlock it.

A, B and C share the entire pipeline; they are three entry points and three
reports, not three systems.

## Operating decisions

| Decision | Choice | Reason |
|---|---|---|
| Execution | Paper only; `tests/test_no_live_execution.py` stays green | Speed is not the proven bottleneck; candidate quality is |
| Interaction | Background collector, terminal read-outs ("B now, C later") | Read-out C needs continuous timestamped capture; push alerts risk tuning thresholds down to make the phone buzz |
| Social capture | Free APIs (Reddit, YouTube, Binance topic-rush) + manual X capture | X API free tier is discontinued; pay-per-use at $0.005/read costs $25–288/month against $40 of capital |
| Padre / Fomo | Manual CSV export | No public API; scraping them means defeating an access control |
| Scraping | Not adopted | Every source has a free API or is manual. Scrapling stays in reserve for a public page that turns out to have none. No bot-detection or CAPTCHA bypass under any circumstance |
| Liquidity gate | Ratio to order size, with absolute backstop | An absolute floor encodes an account size we do not have |
| Age gate | Absolute, but routes to MONITOR not REJECT | Age gates a different risk than size. Journaling under-age candidates is what creates the read-out C dataset |

## Boundary

Public information only. No leaked paid-group calls, hacked accounts or DMs,
pre-announcement listings, or confidential issuer plans. "Insider groups" appear
here strictly as an **adversary to detect**: the system maps coordinated wallet
clusters so the user does not become their exit liquidity.

## Components

| Module | Job | Status |
|---|---|---|
| `providers/binance_web3.py` | Candidate discovery, narratives, vendor audit | Built, live |
| `providers/dexscreener.py` | Liquidity, pair age, volume — free, no key | To build |
| `enrichment.py` | Thin snapshot to evidence-complete snapshot | Built — resolves 6 of the 8 unknowns |
| `clusters.py` | Insider / bundle / wash grading, two tiers | Built, 19 tests |
| `safety.py` | Fail-closed gates | Extended |
| `risk.py` | Micro-capital sizing, ratio liquidity floor | To modify |
| `sources/` | Reddit, YouTube, manual CSV importer | To build |
| `source_expectancy.py` | Score sources at +30s / 5m / 60m / 24h after costs | To build |
| `collector.py` | The always-on capture loop | To build |

### Cluster detection

Two deliberately separated evidence tiers:

- **Vendor tier** — precomputed insider / sniper / bundler / fresh-wallet
  percentages from the discovery feed. Wide coverage, unverifiable, cheap.
  A pre-filter, never a clearance.
- **Funding-graph tier** — independent union-find reconstruction over on-chain
  funding ancestry, with infrastructure addresses excluded before measuring so
  a shared pool or exchange wallet cannot merge the holder base into one
  meaningless cluster.

Three invariants:

1. Absent evidence yields `INSUFFICIENT_EVIDENCE`, which rejects. Coercing a
   missing percentage to zero would convert ignorance into a safety pass.
2. Clusters are never asserted as common ownership. Shared funding is also
   consistent with airdrops, exchange withdrawals, and shared routers. Every
   verdict carries a confidence scaled by observed holder coverage.
3. Verdicts combine by worst-verdict-wins and lowest-confidence-wins. One tier
   seeing nothing does not cancel another tier's finding, because seeing nothing
   is the expected result when coverage is poor.

## Risk model

Sizing uses catastrophic-loss logic below $100 equity: the position *is* the
risk, because a meme coin can reach zero or become unsellable regardless of any
chart stop. Percentage-of-equity sizing at this scale produces meaningless
positions — the existing `0.25%` rule yields $0.10 at $40 equity.

A `minimum_viable_position_usd` gate rejects any trade whose estimated
round-trip cost consumes a material fraction of expected edge. At $40 this will
reject most candidates. That is correct and informative behaviour, not a bug.

Liquidity gate: `order_usd / pool_liquidity_usd <= 0.5%` **and**
`pool_liquidity_usd >= $5,000`. At small orders the backstop binds; as capital
grows the ratio takes over.

## Error handling

Enrichment resolves identity, mint authority, freeze authority, entry route,
exit route, and both price-impact fields. It does **not** resolve
`transaction_simulation_ok`, which needs an unsigned swap transaction to
simulate. That is in scope for the project — `HeliusProvider` already exposes
`simulate_unsigned_transaction`, and simulation never broadcasts — but is
deferred. Until it lands, on-chain candidates keep failing that one gate by
design rather than being waved through.

One rule: **provider failure produces `None`, never a default.**

`enrichment.py` catches per provider, records the error, and leaves the field
unset so the fail-closed gate rejects. `EnrichmentReport.coverage_pct` then
distinguishes "this token is bad" from "our data was bad" — precisely the
distinction the current pipeline cannot make, and the reason the original
zero-trade result was uninterpretable.

## Testing

- Unit tests per module, no network; the 19 cluster tests set the pattern
  (fixtures, not live calls).
- `tests/test_no_live_execution.py` is never modified and must stay green.
- `scripts/survey_live_candidates.py` is the live integration check. It is how
  the starvation problem was found and how the fix is verified.
- Machi negative-control replay must trigger a `RiskEngine` lockout.
- Every strategy must pass `scripts/validate_lookahead.py`.

## Expected outcome

After enrichment lands and the floors are corrected, the eligible count may
still be near zero. Today's flow was 80% cluster-disqualified and 68%
developer-selling; neither improves with better plumbing.

If that happens, the system is working correctly and reporting something true.
The response is to report it as a finding, not to loosen thresholds until
numbers appear.

## Fixes already applied to the base repository

- `journal.py` leaked a SQLite connection and file handle on every call.
  `with sqlite3.connect(...)` manages the transaction, not the handle. Replaced
  with a context manager that commits and closes. This also unblocked the test
  suite on Windows, where the open handle locked the database file.
- `ccxt` was imported by `tests/test_kraken_config.py` but undeclared; added to
  the `dev` extra.
