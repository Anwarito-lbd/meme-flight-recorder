# Status — 2026-08-03

Read this first. It is the handoff for anyone (or any session) picking the
project up cold.

## What this is

A Solana meme-coin research system on branch `intelligence-layer`, extending
`Anwarito-lbd/meme-flight-recorder`. It observes candidates, grades them through
fail-closed safety gates, simulates entries and exits, and measures whether its
own judgement was any good.

**Paper only.** Nothing in it can sign or broadcast a transaction, and
`tests/test_no_live_execution.py` must stay green and unmodified.

## The headline finding

The system completes trades now, and the first measured expectancy is
**negative**. Real candles, costs charged on both legs, $4 positions:

| timeframe | trades | win rate | expectancy | profit factor |
|---|---:|---:|---:|---:|
| 15m | 21 | 0% | -$0.41 | 0.000 |
| 1h | 12 | 17% | -$0.27 | 0.153 |
| 4h | 11 | 18% | -$0.23 | 0.396 |
| 1d | 2 | 50% | +$0.08 | 2.823 |

Expectancy improves monotonically with holding period, which fits costs being
large relative to short-timeframe moves. Every sample big enough to read is
negative and the only positive cell has two trades. **Do not trade this live.**

These tokens came from a *trending* feed, which by construction contains things
that already worked, so even these numbers are an upper bound.

## Constraints that shape every decision

- **Capital is $10-40 real.** Positions are 10% of equity (~$4), sized so the
  measured total-loss rate does not lead to ruin. Below $30 the account reports
  `UNFUNDED`. Do not propose strategies needing private RPC or Jito tips.
- **Live execution is gated on evidence**: >=30 complete trades, positive
  expectancy after costs, profit factor >1.2, no single trade >1/3 of profit,
  drawdown within limit. The gate is computed, not judged.
- **Public information only.** No leaked group calls, hacked accounts, or
  pre-announcement listings. Padre and Fomo are behind logins, so their calls
  arrive by manual CSV. X's API is pay-per-read and costs more per month than
  the account holds, so X calls are manual too.

## Findings that overturned an assumption

Each of these cost real effort to learn and should not be rediscovered.

**Manipulated tokens pump.** Across 402 measured outcomes, cluster-*clear*
candidates died more often than cluster-*disqualified* ones (21% vs 9%) and hit
2x less often (5% vs 16%). The biggest single winner was rejected for
`developer_selling`. The cluster gate measures **risk** and must not be judged
on return; do not loosen it because "the data says they pump".

**Stops do not work here.** A replayed collapse fell from $0.0522 to $0.0000303
inside one five-minute candle, ~1700x below its stop, filling at -99.9%. No exit
rule survives a gap. Only position sizing protects the account.

**Ticker collision is routine.** Five distinct CATE mints and four SAOF mints
appeared in one night. Every join is on mint address, never symbol. The CATE
that reached $65M was a *different* token from the one the system flagged, and
was eight days old — never a launchpad candidate at all.

**Age does not predict return.** Tested across 350 mints: older pools die less
(18% to 3%) but stop moving entirely (>2x rate 9% to 0%). The population where
it might matter was absent from the sample, so no predictive claim is attached
to the age threshold. It stands as a budget and gap-risk filter only.

## Defects fixed, with how they were found

- `journal.py` leaked a connection per call: `with sqlite3.connect(...)` manages
  the transaction, not the handle.
- `.env` was never read, so a correctly filled key silently did nothing.
- Enrichment wrote a *gross* top-10 figure (which counts the AMM pool) into a
  field meaning concentration *excluding* infrastructure, rejecting 25/25
  candidates on a number describing the pool.
- The inherited breakout detector required a 16-candle range under 2.5 ATR,
  admitting 1.3% of 4,717 measured windows. Now 3.75, from the random-walk
  baseline (ATR*sqrt(16) ~ 4.0) and the tightest observed quartile.
- Profit-trail exits fired above *entry* rather than above cost-adjusted
  breakeven, producing four "profit" exits that lost money.
- The thesis exit fired at any price below the breakout level while entry
  accepted a retest within 0.25 ATR of it, so positions were opened at the level
  and closed by the first tick of noise. The two now share one tolerance.

## Running it

```powershell
cd "C:\Users\issao\OneDrive\Desktop\meme"
.venv\Scripts\python.exe scripts\preflight.py                      # check first
.venv\Scripts\python.exe -m meme_flight_recorder.cli collect --interval 300 --limit 20
```

`run_collector.bat` does the same with logging and auto-restart. The collector
suppresses system sleep while running — the first unattended night produced 9
cycles instead of 96 because the host slept.

| command | what it answers |
|---|---|
| `cli calibrate` | Did the gates and confidence score predict anything? |
| `cli score-sources <csv>` | Which callers are worth following, after costs? |
| `scripts/backtest_strategy.py` | What is the strategy's expectancy? |
| `scripts/replay_collapse.py` | What happens on the worst real case? |
| `scripts/study_age_survival.py` | Does a proposed pattern actually repeat? |
| `scripts/survey_live_candidates.py` | Why is everything being rejected? |

## Open work, in the order I would do it

1. **Test the daily-timeframe gradient on a non-survivorship sample.** The only
   positive expectancy cell has n=2 and the token sample is biased upward.
2. **Wire the collector to open and monitor paper positions**, so forward trades
   accumulate alongside the backtest.
3. Source scoring needs the user's CSV of real calls; the collector supplies the
   other half automatically.
4. Deferred: latency harness, wallet cohort tracking, the TJR/Unipcs/Murad
   playbook studies, session reporting.

## Working standard

Do not generalise from a single case. Encode a rule only when the pattern
repeats across measured data or a structural mechanism explains it. Two claims
were withdrawn this way already; the recorded rejections are what made that
possible, and keeping them is the closest thing this project has to an edge.
