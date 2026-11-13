# Promotion Policy — Binding

Agreed 2026-08-22 by the operator, superseding any earlier reading.
This document is the contract between present-us and future-us.

## Capital commitment

$50 real maximum until promotion fires. No top-ups mid-evaluation; changing the
amount resets nothing but must be recorded before the fact.

## Live execution unlocks ONLY when ALL of the following hold:

1. **Evidence gate** (computed by `report_track_record.py`, never hand-maintained):
   - ≥ 30 complete forward paper trades
   - positive expectancy per trade after all measured costs
   - profit factor > 1.2
   - no single trade > 1/3 of total profit
   - drawdown within `[risk]` limits
2. **At least one new-signal study passes its pre-registered bar**, i.e. the
   whale-entry study (`2026-08-22-copy-media-signal-rules.md`, W1) or the
   media-velocity study (M1) — or any later study governed by the same rules —
   demonstrates its edge on held-out data at its stated minimum sample.
3. `ReadinessPolicy.trading_policy()` is flipped only after both, with the flip,
   the numbers, and the study reference recorded in STATUS.md the same day.

## If the gate fails at n=30

Keep collecting to n=50 while the new-signal studies mature. If the book is
still negative at n=50 with no passing signal, the verdict is recorded as
"no edge found under current population and costs" and the operator chooses:
new population, new cost regime (more capital), or stop. Tuning thresholds
after seeing outcomes is not one of the choices.

## What does NOT unlock live

- Manual vibes, a hot week, a big win, social-media conviction, or fatigue
  with paper trading.
- Any strategy variant introduced after the record started counting.

## Standing automation meanwhile

Collector + scout + media loop runs unattended (Startup link). Telegram alerts
activate when the operator fills the two `.env` keys. Weekly:
`report_track_record.py` against this document.
