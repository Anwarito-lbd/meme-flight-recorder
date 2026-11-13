# Does virality predict death? — study design

**Written 2026-08-19, before any of these numbers were computed.** The commit
that adds this file precedes the commit that adds the script, which precedes any
run. That ordering is the point: this project's rule is that the decision rule is
fixed before the numbers are seen, and two of three Stage 3/6/8 gates returned
UNPROVEN and stayed out of the entry path because of it.

## The question, and why it has never been asked here

The operator's thesis: **meme coins are driven by virality, not by on-chain
structure, and a well-prompted model can detect it.**

Everything this project has measured and rejected tests a *different* hypothesis.
Holder concentration, liquidity depth, route and impact, developer behaviour,
token age, maturity bands, nine exit policies, position sizing, and one
pre-written breakout strategy on CEX candles — every one of them measures
on-chain structure or price shape.

**No study here has ever joined a social signal to an outcome.**
`study_narrative_momentum.py` fetches live topics and prints distributions; it is
not among the eighteen scripts that resolve forward returns.
`Signal.NARRATIVE_ACCELERATING` has never resolved on a single candidate. Until
2026-08-19 the social fields were not journalled at all.

So "no edge found" is a true statement about a hypothesis that is not the
operator's. This study exists to test theirs.

## What makes it testable now

Since 2026-08-19 the collector journals, from the launchpad feed at zero cost:

- `narrative_text_en` — the vendor's account of why the token exists, on **98.3%**
  of rows ("Glitter TikTok Meme, Viral Fragrance")
- `twitter_handle` and `twitter_followers` — on **70.0%**
- `socials` — on 72.5%

Accumulated in the first day: **2,119 distinct mints**, 2,020 carrying narrative
text, 1,825 observations carrying a follower count.

## Decision rule — fixed here, before the data is looked at

**Verdict is on death rate, not on return.** This is a risk question, and this
project's rule is that a risk gate is judged on death rate; the developer gate
failed once precisely because return and risk were confused.

1. **Split by hash of the mint address**, not at random. `sha256(mint)` low bit
   selects development or held-out. Deterministic, so a re-run cannot reshuffle
   until it passes.
2. **On the development half only**, choose the follower threshold that best
   separates death. Record it in the output.
3. **Apply that threshold once to the held-out half.**
4. **PASS** requires, on the held-out half:
   - the high-virality cohort's death rate is **at least 10 percentage points
     below** the base rate, and
   - the cohort holds at least **100 mints**, and
   - the direction is the same on both halves.
5. Anything else is **UNPROVEN**, reported as such, and nothing is wired into the
   entry path. A single-half result is not a finding.

Ten points and n=100 are chosen now, in advance, and are not to be moved after
seeing the numbers. If they are ever changed, the change and its reason go in
`STATUS.md` and the study is re-run from scratch on fresh data.

## Arms

| arm | signal | reproducible | may it ever gate? |
|---|---|---|---|
| A | `twitter_followers` above the fitted threshold | yes, it is arithmetic | yes, if it passes |
| B | a `twitter_handle` exists at all | yes | yes, if it passes |
| C | the local model's reading of `narrative_text_en` | **no** | **never** |
| — | base rate, all mints | — | the control |

**Arm C is the operator's thesis stated precisely.** It is journalled as
`model_says_accelerating` beside the computed fields, and the study asks whether
it beats arm A. That is a measurable answer to "a well-prompted AI can do this",
rather than an opinion about it.

Arm C can never gate whatever it measures, because a model asked the same
question twice may answer differently, and there would be no way to tell whether
a verdict changed because the market moved or because the model did. If arm C
wins decisively, the honest response is to find the *computable* feature the
model is reacting to and gate on that instead.

## Controls, each one paid for by a past defect here

- **Vanished mints count as dead.** 84.2% of the observed population no longer
  quotes anywhere. A study restricted to what still prices measures the survivors
  and calls it the population.
- **Every mint lands in exactly one bucket, and the buckets reconcile to the
  total.** Printed. A study that reports only its measured subset turns missing
  data into a finding.
- **Absent is not zero.** A mint with no `twitter_followers` field is *unknown*
  and forms its own bucket; it is never merged with a measured zero. A brand-new
  account with genuinely 0 followers is a measurement and stays in arm A.
- **The base rate is printed beside every arm**, so "better than nothing" has to
  be shown rather than assumed.
- **Coverage is printed**: what share of mints could be scored at all. A signal
  present on 70% of candidates cannot help the other 30%, and that ceiling
  belongs in the result.

## Outcome resolution

Reuse, do not reinvent. `study_age_distribution.py` already resolves outcomes by
asking DexScreener what a mint quotes today against its first journalled price,
and `backfill_pool_history.py` caches minute candles for pools that have since
died — which is what allows the 84% to be priced at all. This study calls the
same path.

Death is defined as it is elsewhere in this project: quoted below **0.10x** of
the first observed price, or no pair quoted anywhere.

## Horizon, and the honest limitation

The social fields began accruing on 2026-08-19, so at first run every mint is
hours old and the horizon is short. The script therefore:

- takes `--minimum-age-hours` and reports the horizon it actually used,
- prints its own n per bucket, and
- prints **UNPROVEN** below the thresholds above rather than a verdict.

It is expected to return UNPROVEN on its first runs. That is the study working.
Re-run it as the journal fills.

## What this does not test

- Whether virality predicts **return**. That is a separate and harder question
  needing the tail, and the tail needs far more outcomes.
- Whether a *better* social source — X's own API, LunarCrush — would do better
  than the vendor's fields. This tests the free signal that exists today.
- Whether the operator could act on it in time. `twitter_followers` measures the
  reach of the source post's account, not the token's community, and the topic
  feed was measured as structurally late: zero overlap with launchpad mints and
  271x larger by median market cap.

## What a PASS would and would not license

A PASS licenses **one** thing: wiring the computed arm into the confluence count
as a resolvable signal, with the threshold this study fixed. It does not license
increasing position size, widening `readiness_policy`, or going live. The
evidence gate is unchanged and still reads 4 of 30.
