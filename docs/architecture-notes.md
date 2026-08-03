# Streaming, wallet tracking and promoter analysis: what applies here

A proposed architecture (2026-08-03): Helius/QuickNode webhooks, Birdeye/DEX
Screener WebSockets, Redis Pub/Sub as an event bus, a JWT-authenticated
WebSocket gateway in Node/Go/Bun, plus tracking large wallets, popular traders
and politicians.

Recorded here because most of it is a good design for a different problem, and
the distinction is worth keeping.

## What does not apply: the broadcast tier

Redis Pub/Sub and a WebSocket gateway exist to fan one event out to **thousands
of connected clients** without each one hitting the database. That is the
architecture of a consumer product like Fomo.

This system has one user, one process and $10-40 of capital. Putting an event
bus and a socket gateway between the collector and its only consumer adds
failure modes -- connection loss, duplicate delivery, ordering, auth -- and no
information. A function call already delivers the event, exactly once, in order.

If this ever serves other people, revisit. Until then it is infrastructure
theatre, and the project already has enough places for a silent failure to hide.

## What does apply: push instead of poll

Helius webhooks or Birdeye WebSockets are worth having, for reasons that have
nothing to do with broadcasting:

- **Latency.** The collector polls on a 300s interval and paces 2.0s per
  candidate, so a candidate late in a cycle is seen minutes after the event. A
  push feed removes both delays.
- **Rate limits.** Every 429 recorded in this project came from polling quotes.
  Push feeds do not consume that quota, so the enrichment budget goes further.
- **The measurement it enables.** `scripts/study_entry_latency.py` (specced, not
  built) is meant to quantify how late this system sees a token. Push feeds are
  the fix if that study says latency is the binding issue.

Cost check before adopting: verify current free-tier limits for webhooks against
the provider's own documentation. This account cannot carry a paid plan.

## Wallet and trader tracking: already built, needs data not code

`wallets.py` (Stage 8) already reconstructs a wallet's trades FIFO and computes
realised P&L, win rate, profit factor, median return, maximum drawdown, median
holding period, token diversity and single-outlier dependence, then classifies
the wallet.

Two rules are enforced in code rather than documented as guidance, because they
are the whole reason the module is worth anything:

- fewer than 30 observable trades cannot be classified a repeatable trader, at
  any level of performance;
- a wallet whose single best trade carries more than a third of gross profit is
  flagged outlier dependent and likewise cannot.

These are the same conditions this system applies before it will consider
trading its own strategy live. Applying a weaker standard to strangers than to
itself would be incoherent -- and "big wallet with a huge win" is precisely what
those rules exist to reject, because a leaderboard cannot distinguish a trader
from someone who bought one token that went up.

What is missing is wallet history. Run `scripts/backfill_wallet_history.py`,
then `scripts/study_cohort_value.py`, which currently reports "no data" -- the
correct answer, and not a finding.

## Politicians and promoters: measure them, do not follow them

Report (2) places politicians in a **catalyst-risk** list, never a trusted-alpha
list, and the LIBRA case is why: promoted in February 2025, then deleted,
collapsed and investigated, with researchers linking roughly US$99 million of
withdrawals to creator-related wallets. The people following the promotion were
the exit liquidity.

An authentic high-profile post can promote a structurally unsafe asset, and a
compromised account can point at an impersonating contract. Neither is analysis.

The lawful and measurable edge is the inverse of following:

1. record the call with its exact timestamp and contract;
2. check whether wallets associated with the caller bought **before** the post;
3. check whether they sold **after** followers arrived;
4. measure the return a subscriber could actually have realised from the
   post-call price, after costs;
5. classify the source as originator, amplifier or distributor on that evidence.

`cli score-sources` computes exactly this and has never run, because it needs a
CSV of real calls. That remains the cheapest untried input in the system, and it
needs a file rather than any more code.

## Boundary

Public information only. Lawfully joining a group and using its calls under its
rules is fine. Leaked paid-group calls, hacked accounts, scraped private
channels and pre-announcement listings are not, and nothing here should be built
toward them.
