# Status — 2026-08-12

Read this first. It is the handoff for anyone, or any session, picking the
project up cold.

## What this is

A Solana meme-coin research system extending `Anwarito-lbd/meme-flight-recorder`,
on branch `stage-3-6-8-gates`. It observes candidates, grades them through
fail-closed safety gates, journals every decision including rejections, opens and
manages **paper** positions, and measures whether its own judgement was any good.

**Paper only.** Nothing in it can sign or broadcast a transaction, and
`tests/test_no_live_execution.py` must stay green and unmodified.

## Where it stands in one paragraph

The measurement apparatus works and is trustworthy. The strategy does not make
money, and the bot has **never opened a position**. Every gate, filter and
provider has now been run against real data at least once, and doing that found
nine defects — four of them in code written the same day and already believed
working. What exists is an honest instrument that keeps catching its own errors.
What does not exist is a profitable bot.

## Recall is zero. This is the most important finding in the project.

`scripts/study_winner_recall.py` asks the question every other study inverts:
**given a winner, what verdict did we give it?** Restricted to candidates with a
tradeable pool (>=$5,000), over 492 measurable outcomes:

| outcome band | n | eligible | monitor | reject |
|---|---:|---:|---:|---:|
| big winner (>=5x) | 34 | 0 (0%) | 0 (0%) | **34 (100%)** |
| winner (>=2x) | 22 | 0 (0%) | 0 (0%) | 22 (100%) |
| middling | 113 | 0 (0%) | 3 (3%) | 110 (97%) |
| dead (<10%) | 323 | 0 (0%) | 7 (2%) | 316 (98%) |

**The system has never accepted a single winner, and it accepts deaths at a
higher rate (2%) than winners (0%).** Recall against the observed universe is
exactly zero. This does not make the gates wrong — they were built to measure
risk — but "catch the early winners" cannot happen through them as configured.

Why the 56 winners (>=2x, real liquidity) were rejected:

| cause | n | nature |
|---|---:|---|
| `holder_concentration_excessive` | 36 | **a real gate, and the dominant killer** |
| `exit/entry_price_impact_excessive` | 15 / 14 | real, but check the threshold at $4 size |
| rejected *only* on `*_unknown` | 13 | **missing data, not a decision** |
| vendor sniper / fresh-wallet / dev | 6 / 5 / 3 | real |

Two distinct problems, and they need different fixes:

1. **13 of 56 winners were rejected purely because data was missing** — route and
   impact unknown, i.e. the Jupiter quota. Those are provider outages recorded as
   risk decisions. Fixing the quota alone recovers a quarter of the winners.
2. **Holder concentration rejects 36 of 56.** The vendor supplies `top10_pct`
   for **0 of 18,717** mints, so this figure comes entirely from enrichment's
   `adjusted_top_holder_pct`, tested against a 30% threshold. For a young
   launchpad token high concentration is *normal*, not pathological, so the
   threshold may be measuring the population rather than the risk.

**Do not relax that threshold on this evidence alone.** The disciplined next step
is the same one that fixed the deployer gate: measure death rate by concentration
band and find out where the risk actually sits.

`scripts/study_concentration_value.py` now does exactly that, and reports
**UNPROVEN at n=6** — the concentration *value* was never journalled, only the
failure reason, so the study cannot be backfilled and its sample starts from
2026-08-10 forward. That gap is itself the same defect class as the discarded
flow inputs: the journal recorded the verdict and threw away the evidence for it.

What the distribution already shows, over the 45 values recorded so far:

| percentile | top-10 concentration |
|---|---:|
| p25 | 3.2% |
| **p50** | **45.2%** |
| p75 | 94.8% |

It is **bimodal**, and the 30% threshold sits almost exactly at the median,
splitting the population roughly in half (49% at or below, 51% above). So the
gate is discriminating rather than rejecting everything — an earlier reading of
"every candidate is above 30%" came from a 6-outcome sample and does not survive
the larger one. Whether 30% is in the *right* place still needs outcomes, and
that needs the collector to run.

## Do this first

1. **Check the blocker.** `.venv\Scripts\python.exe scripts\preflight.py`
   Jupiter's free-tier quota was exhausted on 2026-08-03 and a *single* request
   still returned 429 after an hour. Without route and impact evidence the
   fail-closed gates reject everything, so nothing can open. Either it resets, or
   add a Jupiter API key. Nothing else is worth running until this passes.
2. **Then run the paper book.**
   `.venv\Scripts\python.exe -m meme_flight_recorder.cli collect --paper-trade`
   Default pacing is now correct (60 req/min); do not lower `--delay`.
3. **Then read the record.**
   `.venv\Scripts\python.exe scripts\report_track_record.py`
   It scores the journal against the evidence gate directly.

## The evidence gate — the only definition of "ready"

Live execution unlocks only when **all** hold, computed not judged:

| Requirement | Status 2026-08-03 |
|---|---|
| >= 30 complete forward trades | **0** |
| positive expectancy after costs | no — negative at every timeframe |
| profit factor > 1.2 | no — best measured 0.794 |
| no single trade > 1/3 of profit | no — 79-100% |
| drawdown within limit | not reached |

Failing this is not a reason to look for a more aggressive strategy. It is a
reason to keep collecting until the answer is unambiguous.

## Constraints that shape every decision

- **Capital is $10-40 real.** Positions are ~10% of equity (~$4). Below $30 the
  account reports `UNFUNDED`. Do not propose strategies needing private RPC or
  Jito tips; they are unreachable at this size.
- **Public information only.** No leaked paid-group calls, hacked accounts or
  pre-announcement listings. Padre and Fomo sit behind logins, so their calls
  arrive by manual CSV. X's API costs more per month than the account holds.
- **Sub-second sniping is not available at this capital**, and the project's own
  research agrees: launch feeds are for observation and data collection, not
  immediate execution. The measured winners were not launch snipes — the CATE
  that reached $65M was eight days old.

## Measured expectancy

Re-measured after correcting the cost model. Costs charged both legs at the
**measured** 1.14%/leg, not the 3% previously assumed:

| timeframe | trades | win rate | expectancy | profit factor | too few candles |
|---|---:|---:|---:|---:|---:|
| 15m | 15 | 0% | -$0.63 | 0.000 | 0 of 12 |
| 1h | 7 | 0% | -$0.61 | 0.000 | 1 of 12 |
| 4h | 4 | 25% | -$0.23 | 0.394 | 3 of 12 |
| 1d | 1 | 0% | -$0.24 | 0.000 | 9 of 12 |

Every timeframe negative. The apparent improvement with holding period cannot be
confirmed here: trending Solana pools are too young to have daily candles, and
nine of twelve could not supply forty daily bars. Sampled from a *trending* feed,
so these are an upper bound.

Exit policy was then eliminated as the cause. Four policies on identical entries:

| policy | n | win | expectancy | factor | top trade |
|---|---:|---:|---:|---:|---:|
| hierarchy (production) | 21 | 9.5% | -0.3221 | 0.016 | 79% |
| take_2x | 20 | 5.0% | **-0.0811** | 0.794 | 100% |
| scale_runner | 19 | 0% | -0.4149 | 0.000 | 0% |
| hold_to_end | 19 | 0% | -0.4149 | 0.000 | 0% |

"Take the double and leave" measured best by a wide margin and is still negative,
with one trade carrying 100% of its gross profit. **Every policy loses money, so
the exit rule is not what is wrong.** Note `scale_runner` and `hold_to_end` are
identical because the half-at-2x fired once; keeping a runner turned that single
winner into a loss.

## The binding constraint is bankroll, not signal

Over candidates the risk engine would have approved (pool >= $5,000, n=155):

| | pool >= $5k | pool >= $50k (n=38) |
|---|---:|---:|
| median multiple | 0.255 | 1.231 |
| mean multiple | 2.48 | 5.13 |
| best token, share of gross profit | 28% | **52%** |
| EV per $1 as measured | +1.27 | +3.76 |
| EV dropping top 3 | **+0.01** | +0.15 |
| EV dropping top 5 | **-0.15** | -0.16 |

Mean far above median means this is a **lottery, not a trend**. Drop three tokens
out of 155 and the edge is gone. Capturing a tail of probability `p` needs about
`3/p` attempts; at `p = 5.8%` that is ~50 shots, and $40 at $4 positions buys 10
concurrent. **Ruin before the tail arrives is the base case, not the tail risk.**

This reframes every negative backtest: the entry rule was not badly tuned, it was
sampling the median — where the losses are — over holding periods too short to
reach the tail.

## The paper book was mis-wired, and the population it would trade is worse than the base rate

**The wiring bug.** `PositionMonitor.consider()` required status `ELIGIBLE`. But
`SafetyEngine` assigns `MONITOR` **only when there are no failures at all** --
it is a maturity label for young or launchpad-universe tokens, not a safety
verdict. Across 18,846 mints, 52 cleared every hard gate and **49 landed on
MONITOR and were silently discarded**. That is the entire reason the book stayed
empty. Fixed as wiring: `accepted_statuses` is explicit, `REJECT` still cannot
open, a recorded failure now vetoes regardless of status, and narrowing back to
ELIGIBLE-only remains possible. No threshold moved.

**The funnel** (`scripts/build_paper_funnel.py`, artifacts written):

| stage | surviving | % of previous |
|---|---:|---:|
| discovered | 18,846 | 100% |
| enriched (pool evidence) | 8,099 | **43%** |
| liquidity accepted | 2,020 | **29%** |
| buy route known | 625 | **31%** |
| impact accepted | 222 | 36% |
| concentration accepted | 91 | 41% |
| cleared every gate | **52** | 57% |

More than half the population is rejected for **absent data** -- 10,747 never got
pool evidence, 1,395 had no route -- not for anything measured about the token.
That is a collection problem, and the fix is to collect it.

**Then the clean cohort was measured, and this is the finding that matters.**
`scripts/analyse_clean_candidates.py`:

| group | n | median | dead | win | >=2x | max |
|---|---:|---:|---:|---:|---:|---:|
| **safety clean** | 8 | **0.021** | **75%** | **0%** | **0%** | **0.2** |
| everything else | 329 | 0.052 | 61% | 15% | 9.7% | 18,996 |

**Every measurable safety-clean candidate lost money.** The best one returned
0.2x. A further 41 of 49 could not be priced at all, which for a token means the
pair is gone.

**Mechanism, and it is structural rather than bad luck.** The clean cohort is
*younger* than the rest -- median 4.7 minutes against 1.8 for everything else --
and 47 of 51 are freshly `migrated` launchpad graduations, every one carrying
`token_too_young_for_universe`. A token accumulates red flags by existing, so
"passes every gate" selects for tokens too new to have shown one yet. **The gates
select for youth, and youth is what dies.**

This is the same shape as recall being zero, arriving from the other direction.
It does not mean the gates are wrong -- they measure risk, and they do reject the
things that look dangerous. It means clearing them carries no positive return
information on this population, so a paper book fed by them would trade the worst
subset available.

Marked UNPROVEN by the script itself at n=8, which is correct. The direction is
consistent across every measurement in this project and should not be dismissed
for sample size, but it should not be sized on either.

## Sizing corrected to 2%, and GoPlus added

**Sizing.** `position_pct_of_equity` moved 10% → **2%** ($0.80 at $40), and
`minimum_viable_position_usd` 3.00 → 0.50 (derived: the cost floor is $0.37).
The old 10% was chosen by reasoning about ruin from an observed loss rate; the
new figure is computed from geometric growth over the measured distribution,
where 10% is −0.0274/trade (25% of equity left after 50 trades) and 2% is the
first positive band. It also buys ~25 concurrent positions instead of ~5, which
is the regime a 4–6% tail needs. **This is the single most consequential change
in the project and it applies whatever strategy runs.**

**GoPlus** (`providers/goplus.py`), free, no key, **14/14 coverage** on
journalled deep-pool mints. It strengthens the weakest hard gate: sellability
was static analysis only, and GoPlus reads actual mint state — `non_transferable`,
`freezable`, `transfer_hook`, `transfer_fee`, `mintable`, `closable`.

**What it does not do, tested rather than assumed.** The hope was an independent
adjusted concentration figure to check the gate rejecting 36 of 56 winners.
Measured against 14 real mints, GoPlus returns an **empty `tag` on every holder
and no `lp_holders`** — nothing is labelled, so nothing can be excluded.
`adjusted_top10_holder_pct` therefore returns `None` on this population rather
than serving the gross number under an adjusted name.

**And the check settled the underlying question anyway.** Those 14 mints show
~100% top-10 concentration with 9–260 total holders. The concentration gate is
**not producing false positives** — these tokens genuinely are that concentrated.
The winners it rejected were concentrated tokens that happened to moon, which is
the same shape as the cluster-gate finding: in this market the risky ones are
the ones that run. That closes the "maybe the threshold is misplaced" hypothesis
without needing to loosen anything.

## Exit speed was the last untested lever, and it does not rescue this either

`scripts/study_exit_on_hold.py`. Entry at each mint's **first journalled
observation** — a moment chosen before the outcome existed — then held under nine
exit rules. Position at the growth-optimal 2%, real costs, n=31.

| exit policy | median | dead | growth/trade | −top1 | equity x@50 |
|---|---:|---:|---:|---:|---:|
| hold | 0.686 | 29% | −0.0079 | −0.0089 | 0.67 |
| trail −30% | 0.911 | **16%** | −0.0037 | −0.0048 | 0.83 |
| time 24 bars | 0.976 | 16% | −0.0026 | −0.0041 | 0.88 |
| **take 2x, stop −50%** | 0.884 | **13%** | **−0.0018** | −0.0025 | **0.91** |

**No policy produces positive growth.** The best loses 9% of equity over 50
trades instead of 33%.

The shape is worth keeping, because it is a real trade-off rather than a null
result: exits **do** work as protection — death rate falls from 29% to 13–16%,
and the median rises from 0.686 to 0.98. They just cannot make it profitable,
because capping the upside removes the tail that was paying for the losses. You
can make this population safer or you can keep its upside; you cannot do both,
and neither is positive.

That closes the last untested lever. Selection is dead (recall zero, the one
filter was one token), sizing is corrected but only reduces the bleed, and exits
buy survival at the cost of the thing that pays. What remains is a different
population or more capital — not a different rule.

**A survivorship bug caught in this study, worth recording.** Its first version
sampled currently-trending pools and entered ~1,000 bars back, reporting a median
of 3.24 and 0% dead. That is "buy a token that is trending today, ten days ago".
It was caught only because the numbers disagreed violently with the 0.001 median
measured elsewhere — the disagreement was the signal, not the review.

## WITHDRAWN 2026-08-10: the deep-pool filter does not survive a larger sample

The finding below was measured at n=63 and reported as the first filter to beat
the base rate after deleting its top three winners. **At n=166 it does not hold.**

Deep (>=$50k) outcomes, n=166: median **0.001**, mean 2.02, 8% reach 2x, 4% reach
5x. The typical deep-pool token loses essentially everything; the mean is carried
entirely by the tail.

Geometric growth per trade, on the measured distribution with real costs:

| drop top | n | f=2% | f=3% | equity x after 50 trades (f=2%) |
|---:|---:|---:|---:|---:|
| 0 | 166 | **+0.0029** | +0.0012 | 1.16 |
| **1** | 165 | **-0.0069** | -0.0107 | **0.71** |
| 2 | 164 | -0.0088 | -0.0134 | 0.65 |
| 5 | 161 | -0.0132 | -0.0197 | 0.52 |

**Removing one token — a single 209.7x out of 166 — flips growth negative at
every position size.** There is no fraction at which this strategy compounds.

Two things follow, and both matter more than the withdrawn finding.

**The current 10% sizing is catastrophically over-Kelly.** At f=10% growth is
-0.0274 per trade: the account falls to **25% of starting equity over 50 trades**
even on the tail-inclusive distribution. The growth-optimal fraction is ~2%
(~$0.80 at $40), which the $0.37 cost floor permits. That is a real and separate
finding: sizing is wrong by roughly 5x regardless of which strategy is run.

**Selection is not where the edge is.** Recall is zero, and the one filter that
looked like an edge was one token. Median 0.001 says the tokens that pass go to
approximately zero, which points at **exit speed** as the only untested lever:
the exit engine was measured against the breakout strategy, never against
filter-and-hold. That is the next thing worth measuring, and it is cheap.

## The one filter that survived its own test (SUPERSEDED — see above)

`scripts/study_structural_entry.py`, over 1,694 mints with resolved outcomes,
entering at first observation and holding:

| arm | n | median | dead | >=2x | EV/$1 | **EV without top 3** |
|---|---:|---:|---:|---:|---:|---:|
| tradeable >=$5k (baseline) | 257 | 0.164 | 43% | 14.8% | +0.836 | +0.072 |
| **deep >=$50k** | 63 | 0.208 | 49% | 23.8% | +2.968 | **+0.264** |
| deep + dev exhausted | 4 | — | — | — | too few | too few |

`deep >=$50k` is the **only** candidate all session that beat the base rate and
still beat it after its three largest winners were deleted. Note the shape: deep
pools die *more* (49% vs 43%) and pay far more when they run. This is a return
filter, which is what the safety gates were structurally never going to supply.

Not wired into sizing. n=63, one collection window, outcomes only from tokens
still quoted today.


## 2026-08-12: three corrections that change how every earlier number reads

### 1. The safety-clean cohort is OLDER, not younger. The youth mechanism is withdrawn.

The previous entry read "the clean cohort is *younger* than the rest -- median
4.7 minutes against 1.8 for everything else" and concluded "the gates select for
youth, and youth is what dies." **4.7 is larger than 1.8.** The numbers were
right and the word was wrong, and the mechanism built on it does not survive.

`scripts/study_age_distribution.py`, full population, ages in minutes:

| cohort | n | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| safety clean | 51 | 2.97 | **4.69** | 7.40 | 15.40 |
| safety rejected | 18,891 | 0.73 | **1.77** | 3.62 | 7.24 |

The gates already select the *older* end of what they see -- 2.6x the median --
and it still dies. The corrected statement is narrower and worse: **the entire
observed universe is newborn.** The rejected cohort's p90 is 7.2 minutes. There
is no mature population in this journal, so nothing here has ever tested whether
maturity helps.

Age at observation does not separate winners from deaths either, in either
direction:

| outcome | p25 | p50 | p75 | p90 |
|---|---:|---:|---:|---:|
| winner_10x | 1.16 | **2.61** | 7.83 | 40.35 |
| winner_2x | 2.08 | 4.96 | 6.26 | 17.71 |
| dead | 1.79 | **4.06** | 6.92 | 11.03 |
| vanished | 0.81 | 1.88 | 3.71 | 7.16 |

The 10x winners are *younger* at observation than the deaths. This confirms the
already-withdrawn "age does not predict return" finding on 18,942 mints instead
of 350. **Observation age is dead as a filter.** Entry age -- observe now, enter
later -- is a different question and is measured below.

### 2. Every expectancy figure above this line has a survivorship denominator.

Same script, every mint in exactly one bucket, summing to 18,942:

| bucket | n | share |
|---|---:|---:|
| **vanished** (no pair quoted anywhere today) | **15,940** | **84.2%** |
| no entry price | 2,060 | 10.9% |
| middling | 569 | 3.0% |
| dead (<0.10x) | 308 | 1.6% |
| >=2x / >=5x / >=10x | 23 / 14 / 28 | 0.34% |

**84% of the population no longer quotes at all.** The 492 measurable outcomes,
the 166 deep pools, the exit-policy comparison -- all were computed on the ~5%
that still quote. Counting vanished as the death it is:

| cohort | n | priced | dead (of priced) | >=2x (of priced) | dead incl. vanished |
|---|---:|---:|---:|---:|---:|
| safety clean | 51 | 10 | 60.0% | **0.0%** | **92.2%** |
| safety rejected | 18,891 | 932 | 32.4% | 7.0% | 96.3% |

Do not quote a rate from this project without stating whether vanished mints are
in the denominator.

### 3. Safety, maturity and readiness are now three fields, not one

`src/meme_flight_recorder/readiness.py`, pure and tested without a network:
`SafetyVerdict` (PASS/FAIL/UNKNOWN), `LifecycleState`
(NEW/BONDING/NEAR_GRADUATION/GRADUATED/SURVIVING/MATURE/ESTABLISHED/DEAD) and
`StrategyReadiness` (WATCH/ENTRY/BLOCK). Invariants asserted by tests rather
than trusted to review: FAIL can never reach ENTRY, UNKNOWN can never reach
ENTRY, DEAD can never reach ENTRY, and **PASS + GRADUATED is WATCH**. No
threshold moved and no gate was relaxed; `CandidateStatus` and `SafetyEngine`
are untouched, so the journal payload shape is unchanged.

`MonitorConfig.readiness_policy` now gates entry, and **its default admits no
lifecycle state at all**. Widening that tuple is the single visible act that
turns research into trading, and it needs a study. MONITOR is no longer read as
a trading verdict anywhere.

## The maturity study: survival buys safety, not profit (PRELIMINARY, n small)

`scripts/backfill_pool_history.py` caches minute candles from GeckoTerminal,
which serves them retroactively **for dead pools too** -- that is what lets a
study price the 84% that DexScreener can no longer see.
`scripts/study_maturity_bands.py` then simulates the decision that was never
tested: observe at t0, wait, and enter at t0+B only if the pool is still
trading, using nothing from after t0+B.

All-mints cohort, 4h hold, costs both legs at the measured 5.44% round trip on a
$0.80 position:

| band | n | net exp $ | PF | dead% | rug% | win% | 2x% | median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| enter on sight | 48 | -0.4242 | 0.329 | 41.7 | 75.0 | 22.9 | 10.4 | -0.054 |
| survive 5m | 50 | -0.3978 | 0.363 | 42.0 | 74.0 | 24.0 | 12.0 | -0.054 |
| survive 15m | 69 | -0.3723 | 0.358 | 39.1 | 66.7 | 24.6 | 10.1 | -0.054 |
| survive 30m | 62 | -0.3313 | 0.383 | 40.3 | 61.3 | 27.4 | 11.3 | -0.054 |
| survive 1h | 49 | **-0.2962** | 0.397 | 34.7 | 57.1 | 32.7 | 12.2 | -0.052 |
| survive 2h | 34 | -0.3480 | 0.221 | 20.6 | 50.0 | 35.3 | 2.9 | 0.229 |
| survive 6h | 20 | -0.2665 | 0.089 | **15.0** | **30.0** | **45.0** | 0.0 | **0.973** |

**Every band is negative after costs, and no band has a profit factor above
0.4.** What survival *does* buy is monotone and large: death 41.7% -> 15.0%, rug
75% -> 30%, win rate 22.9% -> 45.0%, median -0.054 -> 0.973. And it costs the
tail: the 2x rate collapses from 12.2% at 1h to 0.0% by 6h.

**This is the same trade-off the exit study found, arriving from the other
direction: you can make this population safer or you can keep its upside, and
neither is positive.** Waiting is not a different answer, it is the same answer.

The safety-PASS cohort is worse and unambiguous -- **0% win rate at every band on
both the development and held-out splits**, every trade a total loss.

**Preliminary and marked so.** The candle cache covers 73 of 18,942 mints at the
time of writing; the backfill is priority-ordered (safety-clean first, then
deepest pools) and rate-limited to roughly 16s/mint by the provider, so it needs
to run for hours before these n are worth defending. Re-run both scripts as it
fills. The direction is consistent with every other measurement in this project,
but the numbers are not yet sized on.

**A defect found by verification, worth recording.** The first version scored a
position unsellable if the pool had not printed within 30 minutes of the exit,
and reported 7 of 7 trades as total losses. One of those pools went on trading
for another 34 hours. **No trades is quiet, not empty** -- the same "absent is
not zero" error, committed in a new place. The exit now fills at the next print
at or after the exit moment, whenever it arrives, and only a pool that never
prints again is a total loss. That correction moved death rates from 62-30% to
42-15%, so it was not cosmetic.


## 2026-08-12 (later): the provider blocker is gone, and the streaming question is answered

### Jupiter is unblocked. `preflight.py` passes end to end for the first time.

A Jupiter API key moved quoting from the exhausted keyless `lite-api` host to
`api.jup.ag`. Measured on the same quote: **keyless 188ms, keyed 125ms**,
identical `outAmount` and route plan. `JupiterQuoteProvider` reads
`JUPITER_API_KEY` and falls back to the keyless host when it is absent, so a
missing optional key degrades throughput and never stops the system.

This retires the item that has been blocker #1 in this file since 2026-08-03:
route and impact evidence is available again, so the fail-closed gates stop
rejecting on `entry_route_unknown` / `*_impact_unknown`. **13 of 56 winners were
rejected purely on that missing evidence.**

### Birdeye replaces the candle backfill, and carries a trap that must not be forgotten

Birdeye serves OHLCV **keyed by mint**, not by pool. Two consequences:

- **Coverage**: 16,989 journalled mints can be backfilled against 10,618 with the
  pool-keyed provider, because 8,324 mints never carried a pool address.
- **Speed**: ~1.05s/mint against a measured ~16s/mint for GeckoTerminal, whose
  free tier throttles far below its published 30/min. Full coverage moves from
  roughly 47 hours to roughly 5.

**The trap, verified on a real journalled mint before any of it was believed.**
Birdeye returns a candle for *every* minute whether or not anyone traded,
carrying the last close forward. One rugged token returned **1,000 candles of
which 963 had zero volume**, still quoting a price sixteen hours after its final
trade. Read naively that is a live token at a stable price; it is a corpse with
a stale tag on it. `providers/birdeye.py` therefore treats **a candle with no
volume as not a price** — `trades_only` drops them and `last_trade_at` is the
only honest input to a survival question. The backfill stores traded minutes
only, so a cached row means the same thing whichever provider filled it.

### Helius `transactionSubscribe` is NOT available on this plan. Measured, not assumed.

`scripts/probe_helius_stream.py` asked directly and got:

    {"code": -32600, "message": "transactionSubscribe is not available on the free plan"}

The documented fallback works and was measured over 15 seconds:

| stage | measurement |
|---|---|
| connect | 828 ms |
| subscribe (4 program logs + slot) | 1,047 ms cumulative |
| events received | **37,625 in 15s (~2,508/sec)** |
| slot notifications | 32 |
| inter-event gap | p50 0ms, p95 0ms, p99 16ms, max 344ms |
| decode | p50 0ms, max 16ms |

Subscribed to Pump.fun, PumpSwap, Raydium AMM v4 and Meteora DLMM logs plus
`slotSubscribe`. **Caveat on the percentiles: the host clock granularity is
16ms, so every p50/p95 of "0ms" means "below measurement resolution", not zero.**
Do not quote these as sub-millisecond without a finer timer.

The operational finding is the volume. At ~2,500 events/sec the stream is
deliverable and decoding is nearly free, but almost none of it is a launch —
filtering has to happen before any per-event work, or the queue backs up until
the stream means nothing.

### Providers now answer in an envelope, and the mesh cannot be blocked

`providers/envelope.py`: every provider answer carries `source`, `observed_at`,
`fetched_at`, `ttl_seconds`, `confidence`, `failure` and a monotonic
`latency_ms`. `FailureKind` separates EMPTY (the provider looked and found
nothing — *is* evidence) from RATE_LIMITED / TIMEOUT / TRANSPORT / NOT_CONFIGURED
(says nothing about the token — *not* evidence). `is_evidence` is what a
fail-closed gate reads, so a 429 can no longer enter a study as a token that
stopped trading.

**A bug caught by its own test.** `first_usable` originally ran the providers in
a `with ThreadPoolExecutor(...)` block. `__exit__` joins every worker, so the
hot path waited for the slowest provider *after* the deadline had already
passed — precisely what the deadline exists to prevent. A test asserting that a
5s provider cannot delay a 0.5s deadline failed, which is how it was found. The
pool is now shut down with `wait=False, cancel_futures=True`.

### The maturity study at n=166: the pattern holds and is still negative

Cache coverage 253 of 18,956 mints (1.3%) and growing. All-mints cohort, 4h
hold, costs both legs:

| band | n | net exp $ | PF | dead% | rug% | win% | 2x% | median |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| enter on sight | 166 | -0.2764 | 0.593 | 44.0 | 79.5 | 13.3 | 7.8 | -0.054 |
| survive 15m | 168 | -0.3672 | 0.382 | 38.7 | 66.1 | 17.9 | 8.9 | -0.054 |
| survive 30m | 145 | -0.2861 | 0.473 | 38.6 | 57.9 | 19.3 | 10.3 | -0.054 |
| survive 1h | 109 | -0.2763 | 0.425 | 33.0 | 52.3 | 25.7 | 9.2 | -0.015 |
| survive 2h | 81 | -0.2991 | 0.259 | 16.0 | 42.0 | 30.9 | 3.7 | 0.691 |
| survive 6h | 44 | **-0.2091** | **0.155** | **11.4** | **22.7** | **38.6** | **0.0** | **0.939** |

Sample roughly tripled since the first run and **nothing reversed**. Death
44.0% -> 11.4%, rug 79.5% -> 22.7%, win 13.3% -> 38.6%, median -0.054 -> 0.939.
And the profit factor *falls* as the band lengthens, 0.593 -> 0.155, because the
2x rate goes 7.8% -> **0.0%**. Waiting removes the losers and the winners
together. **Every band is negative after costs. NO EDGE FOUND.**

## Findings that must not be rediscovered

**The deployer gate was running backwards, and is now a band.** Over 871 mints:
dev sold >=50% died **7%**; dev sold nothing died **28%**. Held in every
lifecycle stratum and coverage-matched. Mechanism: a developer who has exited has
no supply left to dump. The gate rejected the 7% band and kept the 28% band.
`maximum_dev_sell_pct` is now a band (reject only `1.0 < pct < 50.0`). Caveat
recorded at the time: this flipped 508 candidates to allowed and newly rejects
**zero**, because the middle band is empty in this population.

**The same question had three implementations.** All written `dev_sell_pct > 0`.
Fixing two left the third — in the discovery feed's snapshot mapping — rejecting
live candidates on floating-point dust (one at 1.47e-08). All now route through
`deployer.developer_is_distributing`, with a test asserting they agree.

**Manipulated tokens pump.** Across 402 outcomes, cluster-*clear* candidates died
more often than cluster-*disqualified* (21% vs 9%) and hit 2x less (5% vs 16%).
The cluster gate measures **risk**; do not judge it on return. But see the
deployer finding: "risk gates are judged on risk" means judged on *death rate*,
and the developer-selling gate failed that on its own terms. Distinguish the two
cases rather than quoting the first as a blanket defence.

**Stops do not work here.** A replayed collapse fell from $0.0522 to $0.0000303
inside one five-minute candle, ~1700x below its stop. Only position sizing
protects the account.

**Ticker collision is routine.** Five CATE mints and four SAOF mints in one
night. Every join is on mint address, never symbol.

**Age does not predict return.** Tested across 350 mints; claim withdrawn.

**Costs were overstated 2.6x.** Real round-trip at $4 is **2.29%**, not 6%, and
impact was being charged twice. The `$3.00` minimum viable position was a choice;
the derived floor at a 10% cost ceiling is **$0.37**, so sub-dollar positions are
viable and the small Kelly fraction a lottery needs is reachable.

**Collector pacing exceeded the provider's limit by 3x and always had.** The flat
0.6s delay was justified by arithmetic that did not hold (60 x 0.6 = 36s, not the
"~100s" claimed), giving 200 req/min. Every 429 traces to it. Now derived from a
target rate; 2.0s and exactly 60/min at defaults.

**A 429 is invisible damage.** Route and impact read unknown, gates fail closed,
and the candidate is journalled indistinguishably from one that genuinely failed
— a rate limit recorded as evidence about tokens. `JupiterQuoteProvider` now
opens a circuit breaker after repeated 429s so the collector stops re-pinning its
own quota.

**Check that "no result" is not "no data".** Caught three times: a timeframe
sweep reporting missing candles as "no qualifying setup"; a flow study showing
zero suspicious tokens when the wash inputs were never journalled; and an entry
study reporting EV of +130,273/dollar off a $0.00-liquidity token.

**Fixtures can encode the same bug as the code.** 326 tests passed while
`profile_wallet` returned zero trades on 1,200 real transactions, because it
parsed the Enhanced Transactions shape while the endpoint returns
`balanceChanges`. Run every component on real data before calling it working.

## The wallet layer, and how little it can see

Six real wallets cached under `data/wallet-history/`, 1,200-1,400 transactions
each. One cleared the 30-trade bar:

**popchad.sol**, on a curated "top trader" list, shows profit factor **0.47**,
down 126 SOL, classified `outlier_dependent`. A leaderboard would have said the
opposite. **But that verdict covers 12% of its activity** — see below. The honest
claim is "unprofitable across the trades this method can price".

Coverage, measured over 8,100 real transactions:

| transaction shape | share | readable? |
|---|---:|---|
| single token, no SOL leg (airdrop, transfer, burn) | 88.3% | no — not a trade |
| token-to-token swap | 4.5% | **no — a trade that cannot be priced** |
| no balance changes | 4.0% | no |
| SOL-paired swap | **3.2%** | yes |

Per wallet coverage runs **0.17% to 12.07%**. `WalletProfile.coverage_pct`
reports it so it cannot be omitted. Token-to-token swaps count as
`unpriceable_swaps` rather than being dropped, because a silently skipped swap is
indistinguishable from a wallet that did not trade.

## Why the bot cannot open a position yet

Two feeds with opposite problems:

| feed | population | evidence |
|---|---|---|
| launchpad (`meme_rush`) | median pool $26; only ~3% clear $50k | vendor labels present, gates satisfiable |
| movers (trending pools, `--source movers`) | pools that actually qualify | no vendor labels; `developer_activity_unknown`, `cluster_evidence_insufficient` |

Holder concentration is now solved for both: `adjusted_top_holder_pct` resolves
token-account *owners* and excludes protocol-controlled balances by checking
whether the owner is a System Program account. An address list could not do this
— every launchpad token's supply sits in a bonding curve on no list. Measured:
OPTM gross 100% → adjusted **14.5%**; AURELIUS 100% → **99.98%** with no curve to
subtract, correctly still rejected.

**The fix for the rest is to supply evidence from chain, not to relax the gate.**
Relaxing would produce trades immediately and make every subsequent number
worthless.

**Deliberately not built:** estimating price impact from constant-product maths
against pool depth. It would make candidates pass, and would substitute an
estimate for a quote inside a gate whose purpose is verifying a real executable
route.

## Open work, in the order I would do it

1. **Unblock Jupiter** — wait for the quota or add an API key. Nothing else
   matters until route evidence exists.
2. **Resolve deployer + cluster evidence for the movers feed** so the good
   population can pass the gates. Holder concentration is done; these two remain.
3. **Accumulate 30 forward paper trades** and re-run `report_track_record.py`.
4. **Re-run `study_structural_entry.py` on a frozen multi-day cohort** before
   letting `deep >=$50k` change any behaviour.
5. Deferred: `study_position_sizing.py` and `study_entry_latency.py` (specced in
   `docs/superpowers/specs/2026-08-03-shot-count-and-latency-design.md`, not
   built); GoPlus for live sell simulation — free, covers Solana, and the
   sellability gate is currently static analysis only.
6. `cli score-sources` has never run: it needs a CSV of real calls from the user.
   Cheapest untried input in the system.

## Running it

```powershell
cd "C:\Users\issao\OneDrive\Desktop\meme"
.venv\Scripts\python.exe scripts\preflight.py
.venv\Scripts\python.exe -m meme_flight_recorder.cli collect --paper-trade
```

`run_collector.bat` adds logging and auto-restart. The collector suppresses
system sleep; the first unattended night produced 9 cycles instead of 96 because
the host slept.

| command | what it answers |
|---|---|
| `scripts/report_track_record.py` | Where is the forward record against the gate? |
| `scripts/study_age_distribution.py` | How old is each cohort, and what happened to it? |
| `scripts/backfill_pool_history.py` | Cache minute candles, dead pools included. |
| `scripts/study_maturity_bands.py` | Does waiting until survival make entry pay? |
| `scripts/replay_paper_strategy.py` | What would the book have done, chronologically? |
| `scripts/probe_helius_stream.py` | Which streaming path is available, and how fast? |
| `scripts/study_structural_entry.py` | Does a structural filter beat the base rate? |
| `scripts/study_return_distribution.py` | What is the shape of returns, and is it a tail? |
| `scripts/compare_exit_policies.py` | Which exit policy survives? |
| `scripts/backtest_strategy.py` | What is the strategy's expectancy? |
| `scripts/study_deployer_value.py` | Does the deployer verdict predict death? |
| `scripts/study_flow_value.py` | Does organic flow predict return? (unproven) |
| `scripts/study_cohort_value.py` | Do qualified wallets predict return? (no data) |
| `scripts/backfill_wallet_history.py` | Fetch wallet history (needs `--max-pages`) |
| `scripts/replay_collapse.py` | What happens on the worst real case? |
| `cli calibrate` | Did the gates and confidence score predict anything? |

## Working standard

Do not generalise from a single case. Encode a rule only when the pattern repeats
across measured data or a structural mechanism explains it. Several claims were
withdrawn this way; the recorded rejections are what made that possible and are
the closest thing this project has to an edge.

Every gate ships with the study that decides whether it may be trusted, and the
decision rule is written **before** the numbers are seen. Two of three Stage
3/6/8 studies returned "unproven" and stayed out of the entry path. That is the
system working, not stalling.

Run every component against real data before reporting it as working. Nine
defects were found this way in one day, four in code written that same day and
already believed correct.
