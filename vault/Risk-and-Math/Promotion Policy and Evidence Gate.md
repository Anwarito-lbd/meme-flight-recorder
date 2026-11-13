# Promotion Policy and Evidence Gate

Upward link: [[Home]] · Contract: [`2026-08-22-promotion-policy.md`](../../docs/superpowers/specs/2026-08-22-promotion-policy.md) · Script: [`report_track_record.py`](../../scripts/report_track_record.py), [`weekly_review.py`](../../scripts/weekly_review.py)

## Binding Contract (2026-08-22)
No manual override path exists. Capital cap is `$50` real maximum until **all** conditions hold simultaneously:

1. **Evidence Gate** (computed directly from `FlightRecorder` by `report_track_record.py`):
   - `>= 30` completed forward paper trades
   - Positive expectancy per trade after all measured costs
   - Profit factor `> 1.2`
   - No single trade accounts for `> 1/3` of total profit
   - Drawdown within `[risk]` limits
2. **At least one new-signal study passes its pre-registered bar** on held-out data:
   - `M1` (media-velocity study) or `W1` (whale-entry study) per `2026-08-22-copy-media-signal-rules.md`.
3. **Explicit Autonomy Transition**: Even under the [[One Front Door and Autonomy Levels]] architecture (`L0` / `L1` / `L2`), live signing remains physically absent from `src/` and enforced by `tests/test_no_live_execution.py`.

## Checkpoints
- **At `n = 30`:** Evaluate expectancy and profit factor against the gate; if failing, continue forward collection to `n = 50`.
- **At `n = 50`:** If still negative with no passing signal, record `"no edge found under current population and costs"` and choose new population, new capital regime, or stop — **never** post-hoc threshold tuning.

## Related Notes
- [[One Front Door and Autonomy Levels]]
- [[Micro-Capital Cost Curve and Sizing]]
- [[Established Population Negative Expectancy]]
