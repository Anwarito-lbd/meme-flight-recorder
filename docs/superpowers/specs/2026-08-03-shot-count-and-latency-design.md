# Can this account trade at all? Sizing and latency, measured

**Date:** 2026-08-03
**Status:** approved for implementation
**Decision deferred by this spec:** whether to add Grok / Stage 7 social

## Context

The stated goal is a profitable memecoin sniper that catches most or all early
winners. Three measured facts from this project stand between that goal and
reality, and none of them is an entry-signal problem.

**The market is a lottery, not a trend.** Over candidates the risk engine would
have approved (pool >= $5,000, n=155): median multiple 0.255, mean 2.48. Dropping
the top 3 tokens takes expectancy from +1.27 to +0.01 per dollar staked; dropping
5 makes it negative. At the $50,000 tier one token carries 52% of gross profit,
failing this project's own outlier rule outright.

**Positive expectancy is not positive growth.** At the configured 10% of equity
per position, a typical trade (0.255x) costs 7.5% of the account. Over 30 trades,
roughly 20 losers compound to 0.925^20 ~ 0.21, and roughly 5 winners at 2x add
1.1^5 ~ 1.61, netting ~0.34. The account falls to a third *while arithmetic
expectancy is positive*. This is the standard over-Kelly failure, and it is the
real content of what was earlier miscalled a "shot count" problem.

**Shots were never scarce; variance was.** An earlier framing said $40 buys 10
shots. That was concurrent capacity. With a one-day maximum hold, 10 slots
recycle to roughly 300 shots a month. Correcting this matters: the fix is not
more shots, it is a position size whose *growth rate* is positive.

Two questions follow, and both are measurable rather than arguable. This spec
covers only those two. Grok is deliberately deferred: if no viable position size
exists, a better signal cannot be acted on regardless of its quality.

## Revision, 2026-08-03: costs measured, and the order changed

Building the cost model first overturned two configured numbers, both in the
pessimistic direction:

| position | real round-trip cost | assumed |
|---:|---:|---:|
| $1.00 | 4.65% | 6% |
| **$4.00** | **2.29%** | **6%** |
| $40.00 | 1.58% | 6% |

Every backtest charged 6% per round trip. At $4 the real figure is 2.29%, so
roughly $0.15 of the measured -$0.63 per trade was fictional cost, and the
strategy was being judged against a breakeven of 1.06x when the true one is
1.023x. Separately, `minimum_viable_position_usd = 3.0` was a choice: at a 10%
cost ceiling the derived floor is **$0.37**. Sub-dollar positions are viable,
which means the small Kelly fraction this spec was worried about is reachable.
The concern that $10-40 might be structurally untradeable rested on the assumed
numbers, and those numbers were wrong.

This reorders the work by dependency. Exit policy changes the return
distribution, and position sizing must be computed on the distribution that the
chosen policy actually produces. So:

1. Real costs into `backtest_strategy.py` (was: flat `--cost-pct`)
2. Exit-policy comparison -- new Deliverable 0 below
3. Position sizing, on the surviving policy's distribution
4. Entry latency

## Deliverable 0: `scripts/compare_exit_policies.py`

**Question:** take the double and leave, or hold for the tail?

68% of all gross profit in the measured sample came from 3 tokens out of 155,
with winners at 10x and 87x. Any policy that caps gains removes exactly that.
But uncapped exposure is also what produced negative geometric growth at the
configured position size. Lower expectancy with lower ruin risk may well beat
higher expectancy that bankrupts the account first, and which one wins is
arithmetic rather than judgement.

**This cannot be answered from endpoint multiples.** Knowing a token ended at
0.3x does not say whether it touched 2x on the way. Exit policy needs price
*paths*, so this runs through the backtest harness on candles, not over the
distribution study's endpoints.

Policies compared on identical entry signals, so only the exit differs:

- `hierarchy` -- the current engine, scaling at 1R and 2R and trailing the rest
- `take_2x` -- all out at twice entry, the "double then leave" rule
- `scale_runner` -- half out at 2x, remainder rides the existing hierarchy
- `hold_to_end` -- no discretionary exit, the tail-capture baseline

Report per policy: trades, win rate, expectancy, profit factor, largest win,
**share of gross profit from the single best trade**, and the geometric growth
contribution. A policy that wins on expectancy while failing the one-third
outlier rule is reported as failing it.

One correction worth recording, because it caused confusion: the existing engine
scales at 1R and 2R where R is *risk*, not price. With a 15% stop, "2R" is a 30%
move, not a double. The current engine therefore exits far earlier than the
"double then leave" idea it superficially resembles.

## Deliverable 1: `scripts/study_position_sizing.py`

**Question:** is there a position size at which this account grows, after real
costs, on the measured return distribution?

Optimising arithmetic expectancy is what produced the current 10% figure and the
ruin path above. This optimises the **geometric growth rate** --
`mean(log(1 + f * (R - 1)))` over the measured multiples `R` net of costs --
which is the quantity that decides whether an account survives a fat tail.

Sweep the equity fraction `f` from 0.5% to 25% and report, for each: growth rate
per trade, expected equity after 30 and 300 trades, maximum drawdown over the
measured sequence, and probability of falling below the `UNFUNDED` threshold of
$30 (the level below which `risk.py` already refuses to size a position, so
crossing it is functional ruin for this system regardless of the remaining
balance).

**The cost model must be measured, not assumed.** The 3%-per-leg figure used
throughout the backtests was never derived from anything. Real cost at position
size `p` is a fixed component (Solana base fee, priority fee) plus a proportional
one (DEX fee, quoted price impact at that size). Fixed costs dominate at small
size and are exactly what determines whether a sub-dollar position is viable, so
they cannot be folded into a percentage. Impact comes from the Jupiter quotes
`enrichment.py` already fetches; fees are configurable with documented defaults.

Then re-derive `MicroCapitalLimits.minimum_viable_position_usd`, currently 3.0 by
choice rather than by measurement. The floor is defined as **the smallest
position whose modelled round-trip cost is at most 10% of the position value**.
Ten percent is itself a choice and is recorded as one, but it is anchored: the
existing `ExitLimits.round_trip_cost_pct` already treats 6% as the cost a trade
must clear to break even, and a floor set where costs approach a fifth of the
stake would make the break-even move larger than most of the measured
distribution's upside.

**Decision rule, fixed before any number is seen:**

- If some `f` yields positive growth *and* the resulting position clears the
  measured cost floor, that `f` becomes the recommended sizing and the reasoning
  is recorded.
- If the growth-optimal `f` sits *below* the cost floor, the conclusion is that
  $10-40 cannot trade this distribution. The script must then state the equity at
  which the optimal position clears the floor. That number is the answer to
  "what would it take", and it is more useful than a tuned parameter.
- Reporting a positive growth rate that depends on the top 3 tokens is
  prohibited: the sweep reports growth with the top 3 removed alongside the
  headline, as `study_return_distribution.py` already does for expectancy.

## Deliverable 2: `scripts/study_entry_latency.py`

**Question:** how late does this system actually see a token, and had the winners
already moved by then?

The claim being tested is mine, that sub-second sniping is unreachable at this
capital because competitors use Jito bundles and private RPC while this system
polls a public feed. That is a plausible assertion and this project's standard is
that plausible assertions get measured.

Three measurements, all from data already journalled:

1. **Age at first observation.** `candidate_observed` carries `age_minutes`.
   Distribution of how old tokens already were when first seen, split by outcome
   band (died / flat / 2x / 5x). If winners were already hours old at first
   sight, launch-moment sniping is not what would have caught them.
2. **In-cycle delay.** The collector paces at 0.6s per candidate, so a 60-token
   cycle takes ~100 seconds and candidate #50 is seen ~30s after candidate #1,
   on top of the 300s polling interval. Report the real distribution of delay by
   position in cycle -- this is self-inflicted latency and is measurable exactly.
3. **Move timing versus observation.** For tokens that reached 2x or more, was
   the observation before or after the run? Approximated from age at observation
   against pool age, acknowledged as approximate rather than presented as exact.

**What this can and cannot settle.** It measures *this system's* latency
precisely. It does not measure competitors' latency, which is not observable
from here. So the honest output is "the earliest this system could act is X", and
whether that precedes the winners' moves -- not a claim about what a Jito-bundled
sniper achieves. If winners are routinely already old at first observation, the
sniper framing is wrong for reasons that have nothing to do with competitors.

## Deliverable 3: Grok / Stage 7 -- deferred, with the decision rule written now

Not built in this spec. The gate for building it:

- Deliverable 1 finds a position size with positive growth, **and**
- Deliverable 2 shows winners are reachable at this system's achievable latency.

If both hold, Grok is added as a **narrative and catalyst evidence source on
candidates that already cleared the hard gates** -- never in a sniper entry path,
because a 1-5 second LLM call is disqualifying in a sub-second race, and never as
a signal in its own right. Every contract address it returns is verified on-chain
through two independent routes before use, because a hallucinated address is an
instant total loss. It ships with `study_social_value.py` and the same withdrawal
rule that kept Stage 6 flow out of the entry path.

Access is via the xAI API. Open Grok weights do not help here: the value is live
X data, which open weights do not provide, and the model sizes involved are not
runnable on this hardware.

## Files

| File | Change |
|---|---|
| `scripts/study_position_sizing.py` | new -- growth-rate sweep and cost floor |
| `scripts/study_entry_latency.py` | new -- observed latency and winner reachability |
| `src/meme_flight_recorder/costs.py` | new -- fixed plus proportional cost model, pure and testable |
| `src/meme_flight_recorder/config.py` | `CostModel` limits beside the existing limit classes |
| `tests/test_costs.py` | new |
| `STATUS.md` | record both results, including negative ones |

Untouched: the entry, exit, position and risk engines. Neither deliverable
changes trading behaviour; both are measurements that decide what should change.
`tests/test_no_live_execution.py` stays green and unmodified.

## Verification

1. `.venv\Scripts\python.exe -m pytest -q` -- 270 existing tests stay green, plus
   cost-model tests: fixed costs dominate below $1, a zero-size position raises
   rather than returning zero cost, and impact is read from the quote rather than
   assumed.
2. Both studies print a reconciling denominator and refuse to conclude when the
   inputs are missing, matching `study_flow_value.py` and the corrected
   `backtest_strategy.py`. "No data" must never print as "no effect".
3. `study_position_sizing.py` reproduces the known failure case: at `f = 0.10`
   the 30-trade equity path lands near a third of starting equity, matching the
   hand calculation above. A sweep that does not reproduce it is wrong.
4. `journal.verify_chain()` still returns True.

## What this spec does not fix

The sample remains one 14-hour window, and outcomes come only from tokens still
quoted today, so both studies inherit an upward bias. They are decisive about
*direction* -- whether growth is possible at all, whether latency is the binding
issue -- and not about precise thresholds. Any recommended position size from
Deliverable 1 should be re-derived once a frozen multi-day cohort exists.
