# Established-Population Widening — Decision Rule

Written 2026-08-22, **before** any number from the widened run was seen.
Implements open work item 0 in STATUS.md ("Widen the established-population test
before abandoning it"). Commits the rule first so the result cannot be argued
into something more comfortable after the fact.

## What is being tested

The one strategy already written (`CexPaperEngine` breakout/consolidation-retest,
unmodified) over a wider slice of the established meme population:

- **Window:** 700 days (--days 700), which contains the late-2024 sector uptrend
  as well as the 2025 downtrend the original 365-day run measured. The original
  run tested one strategy over one regime; this tests the same strategy over both.
- **Universe:** the shipped default bases plus additional spot-listed meme bases
  resolved against Binance's live symbol catalog; anything not listed is reported
  and skipped by the script itself.
- **Arms:** identical to the 2026-08-15 run — strategy at measured costs,
  strategy at zero cost, random-entry control at zero cost, buy-and-hold —
  with the same seed, warm-up, and 50/50 development/held-out split.

## Decision rule (fixed now)

**PROVEN — the hypothesis earns continued research** only if ALL hold on the
held-out half:

1. Strategy expectancy after real costs > $0 per trade,
2. Held-out profit factor > 1.0,
3. Still positive after deleting the single best held-out trade.

**CLOSED — otherwise.** Two recorded sub-cases so the failure mode is legible:

- Zero-cost held-out PF within [0.9, 1.1] means the signal carries no gross
  information; fees are then irrelevant and this line closes regardless of fee
  assumptions.
- If the zero-cost random control matches or beats the zero-cost strategy, the
  entry signal has no information even where absolute numbers look acceptable.

**Regime note fixed in advance:** if the strategy pays in the uptrend window and
loses it back in the downtrend, that is regime dependence, not edge. The rule
above is evaluated over the whole held-out period, not on the flattering subset.

No threshold is tuned, no arm is added, and no exit policy changes as a result
of what this run shows. A CLOSED verdict ends this line of work and leaves the
deep-pool Solana book as the only remaining forward path.

Read-only. Nothing is traded.
