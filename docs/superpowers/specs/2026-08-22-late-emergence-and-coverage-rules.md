# Research Roadmap — Three Studies, Rules Fixed Before Numbers

Written 2026-08-22. Every rule below was committed before its data was fetched.
This file exists so the results cannot be argued into something comfortable.

## Why these three

Every negative verdict so far resolved outcomes over hours. The project's own
STATUS.md records the caveat: "these outcomes are measured over hours, so a 2x
on day three is invisible." Meanwhile the forward book accumulates ~2 trades
against a 30-trade gate. Three questions dominate everything else:

---

## Study L — Late emergence (do the dead come back?)

**Question:** Among candidates observed ≥7 days ago, how many are ≥2x their
observed price TODAY? Existing studies would have scored those "dead" or
"vanished."

**Population:** distinct mints from `candidate_observed` with an observed price
> 0, earliest observation ≥7 days old, excluding stablecoin-looking symbols.
Priced today via DexScreener batch quotes; a quote counts only if its venue
liquidity ≥ $5,000 (below that a price is not a price — recorded defect).
Where several pairs quote one mint, take the median price (recorded defect #3).

**Comparison arm:** same computation restricted to mints first observed in the
last 48 hours (the near-term window every earlier study effectively used).

**Decision rule (fixed now):**
- Let L7 = share of ≥7-day mints at ≥2x today; L2 = the same share in the 48h arm.
- If **L7 ≥ 2 × L2 with n ≥ 150 in both arms**: outcome horizons materially
  undercount winners. Every death-rate table in this project is then suspect,
  and outcome resolution gains a mandatory long-horizon pass before another
  strategy verdict is trusted.
- If L7 < 2 × L2 or n is thin: hours-scale outcomes stand; no re-run.
- Secondary, descriptive only: median current/observed ratio by
  days-since-first-observation bucket. No threshold attached.

## Study C — Discovery coverage (are we even looking where winners happen?)

**Question:** Of today's real Solana movers (top gainers, liquidity ≥$50k,
pair age ≥2 days — the population the deep-pool rule trades), what fraction
does our journal contain AT ALL?

**Decision rule (fixed now):**
- Coverage ≥ 70%: discovery is adequate; selection/gates remain the frontier.
- Coverage < 50%: discovery is the binding constraint; feed expansion outranks
  every remaining gate study.
- 50–70%: ambiguous, report both numbers, decide after Study L.

## Action A — Forward-record acceleration (paper only)

The evidence gate reads 4 of 30. At `[risk] maximum_open_positions = 2` and a
50% aggregate cap ($25 = two $10 positions), the book cannot turn over fast
enough to answer within weeks. Paper equity is free; per-trade economics do not
change with concurrency, and fewer `book_full` skips means LESS selection bias,
not more.

**Change:** `[risk] maximum_open_positions` 2 → 4;
`[risk.micro].maximum_aggregate_open_position_pct` 50 → 80.
**Reverts** the moment `ReadinessPolicy.trading_policy()` goes live — the live
configuration is decided by the promotion review, not inherited from this.
Recorded here so the acceleration is visible, not silent.

---

Nothing here relaxes a safety gate, tunes a threshold post hoc, or adds an
entry rule. Read-only studies plus one documented paper-book capacity change.
