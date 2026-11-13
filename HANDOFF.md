# Handoff — 2026-08-19

**Read `CLAUDE.md` and `STATUS.md` first. This file is the session summary; those
two are authoritative and win on any disagreement.**

Written for an agent arriving with no memory of this session.

---

## Goal

A Solana meme-coin research system that observes candidates, grades them through
fail-closed gates, journals every decision including rejections, manages **paper**
positions, and measures whether its own judgement was any good.

The operator's stated goal is to **find coins before they go viral**. Their
working thesis, stated this session: *meme coins are driven by virality, not
on-chain maths, and a well-prompted AI can detect it.*

**Paper only. Nothing may sign or broadcast. `tests/test_no_live_execution.py`
stays green and unmodified.** Capital is $50 real at $10 per position.

---

## Current state, in one paragraph

The measuring instrument is good and got materially better today. The strategy
still has no demonstrated edge, and the operator's virality thesis was tested on
a well-powered sample and **failed**. The evidence gate reads **4 of 30** trades
with negative expectancy. 634 tests green, ruff clean, working tree clean apart
from an untracked `scratch/`.

---

## What was done this session

Twelve commits, `b5a476a` through `c0828d6`. In dependency order:

**1. Exposure caps that could never fire (`47af4f4`).** `PositionMonitor.consider`
built `PortfolioState(equity_usd=...)` and nothing else, so `open_positions` and
`aggregate_open_risk_usd` were always 0 and both checks in `risk.approve` were
unreachable. At $10 sizing the book could have reached **$100 against $50 of
equity**. Also `--position-usd` bypassed approval, so the pool-share check tested
a position a tenth the real size. Six regression tests, each confirmed failing
first.

**2. `liquidity_unknown` eliminated (`47af4f4`, `747ca70`).** The largest
rejection reason in project history — 28,678 observations, 5,329 mints rejected
on it *and nothing else* — was caused by `_apply_pair_evidence` writing
DexScreener's `None` over the feed's real liquidity. Measured before: `None` on
98.5% of `finalizing` and 72.4% of `new`. After: **0.0% at every stage.**
Candidates clearing every gate went 5 → 12 per cycle.

**3. Free social data was being discarded (`b5a476a`).** The launchpad feed
already carries `narrativeText` on **98.3%** of rows, `twitterHandle` and
`twitterInfo.followersCnt` on **70.0%**, `socials` on 72.5% — all parsed into
`raw` and dropped. Now typed and journalled.

**4. Scout confluence doubled (`657c4cd`).** Resolvable signals went 3 → 6 by
supplying evidence already fetched. **Levels 1 and 2 became reachable for the
first time** (a candidate hit 5 of 3 required). Found a defect while doing it:
the developer signal's comment described a *band* over an implementation that was
a plain ceiling, which would have marked fully-exited developers ABSENT — the
safest state at 7% death vs 28%.

**5. The virality thesis, built and answered (`a859fbb` → `c0828d6`).** Two
studies, each with its decision rule **committed before its script**. Both
UNPROVEN. Details below.

**6. Five third-party repos audited (`33b2645`).** One is dangerous — see below.

---

## What worked

- **Committing the decision rule before the script.** The commit order is the
  evidence that the rule preceded the numbers. Do this for every future study.
- **Hash-split validation caught a real overfit**, not a bug: the follower
  threshold showed a 12-point edge on the development half and **inverted** on
  the held-out half. Fitted and read on one half, a signal that does nothing
  would have shipped. First time this project caught an overfit rather than a
  defect.
- **Disbelieving good-looking numbers.** All three pricing defects were found
  this way, not by a failing test.
- **Measuring the free surface before paying.** The plan of record was to pay X
  $0.005/read. Measuring first found the same class of data already arriving free
  on 98% of candidates.
- **Running the local model offline over stored text.** 2,047 verdicts for zero
  API cost, ~5.6s/mint.

## What did not work

- **The virality thesis itself.** Model says "spreading": **36.5% death (n=554)**
  against 36.8% for "flat" (n=307) on the held-out half. A 0.3 point difference
  on 861 coins is a measurement of no effect, not an underpowered sample. On
  return the "spreading" cohort did **worse** (EV −0.6423 vs −0.6032).
- **Follower count.** Overfit on development, inverted out-of-sample. On return,
  worse than base rate.
- **Having a Twitter account at all.** Slightly *more* death (38.3% vs 35.3%).
- **The topic/narrative feed for early detection.** Measured **zero overlap** with
  launchpad mints out of 40, and topic tokens are **271x larger** by median
  market cap. Narrative detection is structurally late.

---

## The three pricing defects — read this before trusting any outcome number

1. `_apply_pair_evidence` erased feed liquidity with the pool's `None`.
2. Outcome resolution took the deepest quoting pair with **no liquidity floor** —
   one mint's deepest pair held **$0.04** and produced a 27,183x. EV read
   **+9,241/dollar**.
3. With a floor, depth was still the wrong tie-break: three venues quoted
   ~$0.00105 and orca quoted **$5.27** on the deepest pool. That produced 29,359x.

`present_prices` in `scripts/study_age_distribution.py` now takes
`minimum_liquidity_usd` and `use_median`, **both defaulting to the old
behaviour** so nothing changed silently.

> **Open consequence, not yet worked through:** every other study resolving
> outcomes through that helper still runs at the old defaults. The bias has a
> direction — an unsellable coin reads as alive — so those studies **understate
> death and overstate return**. The recorded 84.2%-vanished figure is a floor,
> not a ceiling. Every outcome-derived number in `STATUS.md` predates this fix.

---

## Operator decisions — do not re-litigate

- **$50 equity, $10 per position.** Chosen on cost: 1.81% round trip at $10
  against 5.44% at $0.80. The $0.80 positions still in the book are legacy from
  2026-08-15.
- **Real ceiling is 2 concurrent positions**, not 5: `[risk]
  maximum_open_positions = 2` and a 50% aggregate cap. Offered a raise to 100%
  on 2026-08-19 and **declined it**.
- **Exit policy stays take-profit 2x with no stop** — matches what the deep-pool
  entry rule was measured under.
- **Narrative acceleration is computed, the model only explains.** The model's own
  verdict is journalled beside the computed one so it can be measured, which is
  how it was measured and rejected.
- **Never accept a credential in chat.** Keys go in `.env` by the operator's hand.

---

## Third-party audits, 2026-08-19 (`docs/third-party-audit.md`)

**`solana-snipe-bot/Free-SolanaSniperBot-2026-MemecoinAutoTrader` — DO NOT FUND.**
Its README says *"We generate a wallet for you. Send SOL and you're ready to go."*
The key is theirs. No source code — a README and one JS file pointing at a
website. 3 commits, no licence, and it advertises "Audited & transparent" with
nothing published to audit. If the operator raises it again, repeat this.

Also rejected: `YZYLAB/solana-trade-bot` (takes `PRIVATE_KEY`),
`piotrostr/listen` (signs; Rust; duplicates the data layer),
`santoshlite/memecoooins` ($50 random-allocation roulette).

**`nirholas/pump-fun-sdk` — evaluated, not adopted.** Clears every safety step;
"reads never load a keypair". Its curve quote could supply impact evidence where
Jupiter structurally cannot (pre-bond tokens have no AMM route). Not integrated
because it would make candidates pass a gate before any signal is shown to
predict. Revisit only if a study gives a reason to care about pre-bond coins.

**Solana Tracker — adopt for the scout only.** Free tier 10,000 req/month at
3 req/s. The collector grades ~10,500 candidates/day, so the entire monthly
allowance is under one day of collection. Scout-only, hard daily budget, key in
`.env` as `SOLANATRACKER_API_KEY`.

---

## Next steps, in the order I would do them

1. **Decide whether to continue.** The operator asked this directly and chose
   "finish the open questions, then decide". Those questions are now finished and
   the answer was negative. This decision is theirs and is genuinely open — do
   not assume continuation.
2. **Re-run outcome-derived studies with the pricing fixes.**
   `study_age_distribution.py`, `study_structural_entry.py`,
   `study_maturity_bands.py` and anything else calling `present_prices` still use
   the old defaults. Their numbers are optimistic by an unknown amount. This is
   the highest-value work that does not need new data.
3. **The collector keeps running** and the forward record accrues at ~1 trade/day
   against 2 slots. 30 trades is 3–4 weeks. Current expectancy is
   **−$0.6275/trade**, so the likely outcome is confirmation that it loses.
4. **Only if the operator wants to spend money**: Solana Tracker for the movers
   feed's `developer_activity_unknown` and `cluster_evidence_insufficient` gaps,
   then a longer-horizon return study. Both unproven.
5. **Do not** build more plumbing to make candidates pass gates. Nothing is shown
   to predict yet, so that is effort in the wrong order.

---

## Known weaknesses in this session's own work

- **The hash-split is weaker than walk-forward.** Splitting by `sha256(mint)`
  still lets a single coordinated campaign land in both halves. A time-based
  walk-forward split would be stronger.
- **Prompt injection is unmitigated.** `narrative_text_en` is written by the coin's
  creator and fed to the local model. Blast radius is small because the model
  cannot gate — that is architecture saving us, not design.
- **Outcome horizon is hours**, so a 2x on day three is invisible. Both virality
  studies therefore understate the tail.
- **No HHI on buyer concentration** — transaction counts are not unique buyers.

---

## Running it

```bash
.venv\Scripts\python.exe -m pytest -q
```

```bash
.venv\Scripts\python.exe scripts\preflight.py
```

```bash
run_collector.bat
```

```bash
.venv\Scripts\python.exe scripts\report_track_record.py
```

Background processes at handoff: the collector is running in a minimised console
titled "meme collector". The narrative backfill has **completed** (2,047
verdicts in `data/narrative_verdicts.db`).
