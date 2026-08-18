# Memecoin scout — design

Date: 2026-08-15. Status: approved, implementing.

## Why

The operator supplied a memecoin execution mandate (three strictness levels, a
parameter questionnaire, a 48-hour age gate, a minimum-volume filter, a
no-second-entry rule and a first-minute scam-pump filter) and an n8n workflow
screenshot showing a Pump.fun scanner feeding Birdeye candles, NewsAPI sentiment
and a Helius whale tracker into a Claude agent that emits entry/SL/TP, then fires
a Telegram alert and a Jupiter auto-swap.

Auditing that workflow against this repo found almost every node already built,
and its headline strategy already measured: `2x TP / −50% SL` was the best of nine
exit policies in `study_exit_on_hold.py` and still returned −0.0018 growth/trade.

This spec builds the mandate anyway, as the operator's decision, with the eight
defects the audit found corrected and with each new gate shipped behind the study
that decides whether it may be trusted.

## Decisions taken

**Population: young tokens, non-chart signals only.** Discovery stays at ≤48h.
Chart-based confluence signals (volume expansion, consolidation breakout,
breakout+pullback, higher low, VWAP reclaim) require candle history that this
population does not have — measured: 13 of 21 tokens had fewer than 30 candles.
Those signals therefore report `UNKNOWN` and never contribute. Confluence is
scored from evidence that exists at that age: bonding-curve acceleration,
narrative inflow, holder concentration, liquidity, spread/impact, dev
distribution, buy-side imbalance, minimum volume.

**Win rate is not the objective.** Measured on this project's own data, higher win
rate came from smaller targets and smaller targets sell the tail: `take_2x` won
5.0% of trades and was the *best* policy at −0.0811, while `hierarchy` won 9.5%
and returned −0.3221; a 1.3x take-profit won 33.3% and still lost at PF 0.575.
The scout optimises expectancy and profit factor, reports win rate, and never
steers by it.

**Sizing: $10 per trade at $50 equity**, as set by the operator and recorded in
`.env`. Justification is cost, not preference: the round trip is 1.81% of a $10
position against 5.44% at $0.80, and on identical entries that moved profit factor
0.138 → 0.476. The accepted cost is shot count — $10 buys 5 concurrent positions
where $1.00 buys 50, and capturing a p≈5.8% tail needs roughly 50 attempts.

**Execution stays paper.** No signing, no key handling, one seam with a single
implementation. `tests/test_no_live_execution.py` stays green and unmodified.
`cli scout --readiness` prints the evidence gate as computed numbers so the system
states when it is satisfied rather than anyone judging.

## The largest identified leak

In the momentum-scalp run, **45 of 53 exits (85%) were `stagnation_fast_exit`** and
only 7 were stops. The strategy is not losing to bad entries or bad stops; it is
paying ~1.81% round-trip 45 times for a position that never developed a thesis.
The improvement is to exit on **thesis invalidation** — the evidence that justified
entry ceasing to hold — rather than on a 3-candle timer, and to measure the new
rule against the old on identical entries.

## Architecture

Small units, each independently testable, following the repo's existing split
between pure analysis and I/O.

| Unit | Purpose | Depends on |
|---|---|---|
| `confluence.py` | Score mandate signals as `PRESENT`/`ABSENT`/`UNKNOWN` and count only the present ones | pure; nothing |
| `[strictness.level_1..3]` | Confluence minimums, volume multiples, trade caps, loss stops, lifecycle tuple | config |
| `[scout.filters]` | The 12-item questionnaire as computed thresholds | config |
| `no_reentry` gate | Reject a mint the journal shows was held | journal replay |
| `first_candle_vertical` gate | Reject a token whose first traded minute is vertical | `trades_only` candles |
| `cli scout` | Assemble scanner → gates → confluence → policy → paper book | providers, journal |
| `notify.py` | Telegram alert | `TELEGRAM_BOT_TOKEN` |
| `providers/news.py` | Article count and sentiment | news API key |
| `memecoin-scout` skill | Rank and explain; cannot open a position | read-only |

**`UNKNOWN` never counts toward confluence.** A signal that cannot be evidenced is
missing data, not a passed check. This is what keeps the gate fail-closed and is
asserted by test rather than trusted to review.

**Every parameter must be supplied.** `cli scout` refuses to run when any
questionnaire value is missing — the mandate's "you are not permitted to assume
missing values", implemented rather than instructed. The resolved parameter set is
journalled with each decision so any verdict is reproducible from the record.

## Data flow

```
meme_rush + topic_rush  (paced from the provider limit, not 500ms)
  -> age gate (<=48h)  -> minimum volume -> liquidity + quoted route impact
  -> no_reentry -> first_candle_vertical
  -> confluence scoring (non-chart signals; chart signals UNKNOWN)
  -> strictness level policy (computed)  -> PaperBroker
  -> journal (decision + rejection + parameter set)  -> optional Telegram
```

Market cap is journalled beside pool depth, never instead of it. Birdeye runs in
`trades_only` mode: a zero-volume candle is not a price. Zero news articles is
`UNKNOWN`, never neutral. Whale verdicts carry `WalletProfile.coverage_pct`,
because measured coverage is 0.17–12% of a wallet's transactions.

## Error handling

Every provider failure is a named bucket that reconciles against the request
count. This is the check that exposed a mint-as-pool-address 404 affecting 12 of
22 tokens today; without it a rate limit silently shrinks the sample. Transport
failure, empty result and "looked and found nothing" stay distinguishable via
`providers/envelope.py`'s `FailureKind`.

## Testing

- `UNKNOWN` signals do not count toward confluence.
- `scout` refuses to run with any questionnaire parameter missing.
- A zero-article sentiment result is `UNKNOWN`, not neutral.
- A zero-volume candle is not treated as a price.
- A previously-held mint is rejected by `no_reentry`.
- Missing first-candle data yields `UNKNOWN`, not a pass.
- `tests/test_no_live_execution.py` green and unmodified.

## Studies, decision rules fixed in advance

Return-facing, bar read from `CohortLimits` (30 trades, PF > 1.2, no trade > ⅓ of
profit, positive after deleting the best trade):

- `study_bonding_acceleration.py` — does pre-bond curve acceleration predict return?
- `study_narrative_acceleration.py` — does 1h topic inflow separate winners from deaths?
- `study_confluence_value.py` — does outcome improve **monotonically** with confluence count? A confluence gate not monotone in signal count is counting noise.

Risk-facing, judged on **death rate**, each also reporting the 2x rate it costs:

- `study_first_candle_pump.py` — do vertical first candles die more often?
- `study_reentry_value.py` — do already-held mints die more often on re-entry?

Arm A (mandate: levels, 2x TP, −50% SL, trade caps) and Arm B (barbell: 50% at 2x,
25% at 10x, remainder uncapped) run on identical entries, costs and journal.

**A gate or level reaches entry only if its study passes.** Anything UNPROVEN ships
as scan-and-journal only.

## Out of scope

Signing or auto-execution. Private-key handling. Promoting any gate to entry ahead
of its study. MobyAgent until audited, and it is likely redundant with the already
pinned `emblem-memecoin-scout`.
