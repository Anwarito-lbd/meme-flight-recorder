# Status — 2026-08-03

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
