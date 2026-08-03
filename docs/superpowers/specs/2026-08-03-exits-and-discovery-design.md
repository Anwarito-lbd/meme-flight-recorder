# Exits, Entry Signals, and Established-Mover Discovery — Design

**Date:** 2026-08-03
**Status:** Approved
**Base:** `meme-flight-recorder` on branch `intelligence-layer`

## Problem

Two gaps, discovered from live data rather than reasoning.

**1. The system has never completed a trade.** It decides whether a candidate is
safe and then stops. `MONITOR` and `ELIGIBLE_FOR_STRATEGY_REVIEW` are labels, not
positions. With zero closed trades there is no expectancy, and without
expectancy there is no honest basis for risking money. Exit logic is therefore
not a refinement; it is the precondition for everything else.

**2. Discovery only covers newborns.** The collector polls a launchpad feed
showing tokens in their first minutes. An overnight run measured that population
precisely:

```
427 observations, 186 distinct mints
median pool liquidity            $26
pools below the $5,000 backstop  146/195
cluster-disqualified             261/427  (61%)
developer already selling        212
```

That is structurally the worst-quality candidate pool available. Meanwhile the
token the user noticed — CATE at $65.8M market cap — was **eight days old** when
it ran. It was never rejected; it was never a candidate, because an eight-day-old
token never appears on a launchpad feed.

Confirmed during design: CoinGecko's trending-pools endpoint returns that exact
token at position two, `$1,221,685` liquidity, `+259%` over 24h. One call the
system was not making.

## Forward evidence so far

The only outcome data available, n=9, from tokens sharing two tickers:

| | multiple |
|---|---|
| Rejected by gates | 3.56x, 1.71x, 1.24x, 1.05x, 0.78x, 0.00x, 0.00x |
| Reached MONITOR | 1.01x, 0.00x |

The system's picks underperformed its rejections. The sample is far too small to
conclude anything in either direction, and that is the point: **the honest state
is "unmeasured", and this design exists to end that.**

A secondary finding worth preserving: five distinct CATE mints and four distinct
SAOF mints appeared in one night. Ticker collision is severe and routine, which
is why every join in this system is on mint address and never on symbol.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Live execution | Paper now, live behind an automatic evidence gate | Getting to live *sooner in expectation* than trading now, because $40 spent on an unvalidated system is not replaceable |
| Population | Established movers first; launchpad newborns later or never | Measured: newborns have $26 median liquidity and 61% cluster-disqualification. Also the profile the user actually wants |
| Entry | A separate decision from the safety gate | A gate says "not obviously unsafe"; it does not say "buy". Conflating them is why nothing ever opened |
| Stale open position | Exit, do not hold | Missing evidence means "don't enter" for new candidates, but "get out" for money already at risk |

## Architecture

```
DISCOVERY              PIPELINE (built)          POSITION (new)
trending pools ─┐
                ├─> enrich → gates → cluster ─> entry rule ─> paper position
launchpad feed ─┘                                                  │
                                                                   ▼
                                                            exit monitor
                                                    security │ liquidity │ flow
                                                    thesis   │ time      │ profit
                                                                   │
                                                                   ▼
                                                       closed trade → journal
                                                                   ▼
                                                       expectancy → promotion gate
```

## Components

| Module | Job | Status |
|---|---|---|
| `providers/coingecko.py` | Trending pools, OHLCV, pool trades | Exists, verified live |
| `strategy.py` | `detect_breakout`, `confirm_retest`, `established_meme_trend_ok` | Exists; built for the TJR playbook and fits established movers |
| `risk.py` | Micro-capital sizing, pool-relative liquidity | Exists |
| `journal.py` | Hash-chained event store | Exists |
| `discovery.py` | Trending pools → `TokenSnapshot` | New |
| `entry.py` | Eligible candidate → entry decision | New |
| `positions.py` | Open and close paper positions | New |
| `exits.py` | Exit hierarchy | New |
| `expectancy.py` | Closed-trade statistics and promotion gate | New |

## Entry rule

Every safety gate passes and the cluster verdict is `clear`, **and**:

- 15-minute structure confirms, via the existing `detect_breakout` and
  `confirm_retest`;
- liquidity is rising rather than draining;
- the intended order is at most 0.5% of pool depth;
- **no-chase:** reject when price has already moved sharply within the last
  15 minutes. CATE was `+259%` on the day. Buying that candle is how a trader
  becomes someone else's exit liquidity. Entry is on the retest or not at all.

## Exit hierarchy

Evaluated in priority order; the first match closes or reduces.

1. **Security** — authority change, developer dump, or liquidity withdrawal
   greater than 15% within ten minutes. Immediate, subject to executable depth.
2. **Thesis** — structure breaks below the level that justified entry.
3. **Liquidity** — pool falls below the backstop, or modelled exit impact
   exceeds its limit.
4. **Flow** — sellers dominate and holder growth turns negative.
5. **Time** — the expected follow-through has not appeared within the planned
   window.
6. **Profit** — scale out at 1R and 2R, trail the remainder.

**Stale-position exit.** When an open position cannot be verified for several
consecutive polls, it is closed. Holding an asset you can no longer observe is
not a position, it is a hope.

Trailing stops are modelled as filling at observed depth, never at the trigger
price. In thin pools the difference is the entire result.

## Sizing

Unchanged. Micro-capital catastrophic-loss sizing: position equals risk, capped
at the lesser of the dollar cap and 25% of equity, with a pool-share ceiling and
an absolute liquidity backstop. At $40 that is a $10 position.

## Promotion gate

Computed automatically from closed trades. Live execution stays disabled until
every condition holds:

- at least 30 complete trades;
- positive expectancy after all costs;
- profit factor above 1.2;
- no single trade contributing more than one third of total profit;
- maximum drawdown within the configured limit.

`tests/test_no_live_execution.py` stays green throughout and is never modified.

## Error handling

The existing rule holds for candidates: provider failure yields `None`, never a
default, and the fail-closed gate rejects.

For **open positions** the rule inverts. Repeated failure to verify a held
position triggers the stale-position exit. The asymmetry is deliberate: missing
evidence should prevent new risk and should also retire existing risk.

## Testing

- Unit tests per module, fixtures only, no network.
- A regression case replaying the real CATE and SAOF observations already in the
  journal, including the CATE that went to zero, so exit logic is exercised
  against an actual collapse rather than a hypothetical one.
- Promotion gate tested to stay closed on a profitable-but-insufficient sample,
  which is the failure mode most likely to cost real money.
- `test_no_live_execution.py` untouched.

## Expected outcome

Within one to two weeks of continuous paper operation this produces a measured
expectancy number. That number may well be negative. If it is, the system will
say so, and the correct response is to reduce scope rather than loosen
thresholds until the numbers look better.
