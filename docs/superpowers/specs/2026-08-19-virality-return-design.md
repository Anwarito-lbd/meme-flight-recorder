# Does virality make money? — study design

**Written 2026-08-19, before any of these numbers were computed.** Committed
before the script, as with the death arm. That ordering is the evidence that the
rule preceded the result.

Companion to `2026-08-19-virality-value-design.md`. Same population, same
cohorts, same hash-split. That one asks whether virality reduces **death**; this
one asks whether it makes **money**. They are different questions and this
project has already been burned by treating them as one.

## Why the death arm is not enough

A signal can cut death and destroy profit at the same time, and it has happened
here. The maturity study waited for tokens to survive before entering:

| band | dead | 2x rate |
|---|---:|---:|
| enter on sight | 44.0% | 7.8% |
| survive 6h | **11.4%** | **0.0%** |

Death fell by three quarters and the tail went to nothing. Every band was still
negative after costs. The exit study found the same shape from the other side:
exits cut death 29% → 13% by capping the upside that was paying for the losses.

**You can make this population safer or keep its upside. Neither has been
positive.** So an arm passing on death rate licenses nothing on its own, and this
study exists so that a death-rate pass cannot be mistaken for an edge.

## Decision rule — fixed here, before the data is looked at

The high-virality cohort **PASSES** only if, on the **held-out half**:

1. its **EV per dollar exceeds the base rate's**, and
2. it **still exceeds the base rate after deleting the cohort's top three
   winners**, and
3. the cohort holds at least **100** priced mints.

Anything else is **UNPROVEN**.

**Condition 2 is the one that matters.** It is the control that killed the only
filter this project ever thought it had: `deep >=$50k` beat the base rate at
n=63 and still beat it after deleting its top three, then died at n=166 when a
single 209.7x was removed — "removing one token flips growth negative at every
position size". A mean far above a median means a lottery, and a lottery does not
survive its own tail being trimmed.

These thresholds are not to be moved after seeing the numbers. If they are ever
changed, the change and its reason go in `STATUS.md` and the study is re-run from
scratch on fresh data.

## What is reported

Per arm, per half, always beside the base rate:

- **the full distribution** — p25 / p50 / p75 / p90 of the outcome multiple. A
  lottery hides behind a median, and this project's median of 0.001 with a mean
  of 2.02 is exactly that shape.
- **2x and 5x rates**
- **EV per dollar**, then EV after deleting the top **1, 3 and 5** winners
- **geometric growth per trade** at $10 with real round-trip costs from
  `costs.round_trip_cost` — the number that says whether an account compounds,
  which an EV figure does not
- **coverage**: what share of mints the arm could score at all

## Arms

Identical to the death study so the two are directly comparable:

| arm | signal | may it ever gate? |
|---|---|---|
| A | `twitter_followers` above the fitted threshold | yes, if it passes |
| B | a `twitter_handle` exists at all | yes, if it passes |
| C1 | the model calls the story spreading | **never** |
| C2 | the model reads the text as positive | **never** |
| — | base rate, all priced mints | the control |

Arm C is the operator's thesis. It is reported and compared against arm A, and it
can never gate: a model asked the same question twice may answer differently, and
there would be no way to tell whether a verdict changed because the market moved
or because the model did. If C beats A, the honest response is to find the
computable feature the model is reacting to and gate on that.

## Controls

- **Vanished mints are outcomes, not omissions.** A mint with no pair quoted
  today is scored at multiple `0.0`, not dropped. They are 84.2% of this
  population; dropping them would measure the survivors and call it the market.
- **Costs on both legs, from `costs.round_trip_cost` at $10.** Never a flat
  percentage: fixed network fees do not shrink with the order, and that term is
  what decides whether a position is viable at all.
- **Every mint in exactly one bucket**, buckets printed and reconciled to the
  total.
- **Absent is not zero.** A mint with no follower count is *unknown* and forms
  its own bucket; a measured zero stays in arm A.

## Horizon, and the honest limitation

The social fields began accruing 2026-08-19, so the first runs measure hours, not
days. A 2x that happens on day three is invisible to a four-hour horizon, and
this study will therefore **understate** the tail at first. It prints the horizon
it used and its n per bucket, and reports UNPROVEN below the thresholds rather
than a verdict.

It is expected to return UNPROVEN on its first runs. Re-run it as the journal
fills.

## What a PASS would license

Wiring the **computed** arm into the confluence count at the fitted threshold.
Nothing else. Not a larger position, not a wider `readiness_policy`, not live
execution. The evidence gate is unchanged and still reads 4 of 30 complete
forward trades.

And one thing a PASS would explicitly **not** settle: whether the signal is
actionable in time. Virality detection was measured as structurally late — zero
overlap between the launchpad feed and the AI-narrative feed, with narrative
tokens 271x larger by median market cap. A signal that predicts return but only
becomes visible after the move is a description, not a strategy.
