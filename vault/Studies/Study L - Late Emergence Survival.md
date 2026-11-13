# Study L — Late Emergence Survival

Upward link: [[Home]] · Spec: [`2026-08-22-late-emergence-and-coverage-rules.md`](../../docs/superpowers/specs/2026-08-22-late-emergence-and-coverage-rules.md) · Script: [`study_late_emergence.py`](../../scripts/study_late_emergence.py)

## Hypothesis & Pre-Registered Rule
Prior outcome studies measured survival over hours, raising the question whether winners emerge days later. The decision rule was committed **before** fetching quotes:
- Population: distinct priced mints observed `>= 7 days` ago, re-priced via DexScreener batch quotes (requiring venue liquidity `>= $5,000` and taking median across pairs).
- Comparison arm: mints observed within the last 48 hours.
- Trigger: `L7 >= 2 * L2` with `n >= 150` in both arms.

## Measured Results (`STATUS.md`, 2026-08-22)
- **Total >=7d cohort:** `18,878` distinct priced mints (out of `27,367` total priced mints).
- **7-Day Survival (`>= $5k` venue liquidity):** `110 / 18,878` (**0.58%**). Long-horizon death rate exceeds the earlier short-horizon `84.2%` figure.
- **Survivor Tail Distribution (`n = 110`):**
  - `25.5%` are `>= 2x` their initial observation price
  - `12.7%` are `>= 5x` their initial observation price
- **48h Comparison Arm:** `0%` at `>= 2x` (`n = 32` priced).
- **Pre-Registered Verdict:** Arms were thin (`110 < 150`), so the study is descriptive only and triggers **no mandatory re-run**. However, the shape independently supports the book's **hold-to-7-days** exit policy: the rare survivors are precisely where the convex tail lives.

## Related Notes
- [[Study C - Discovery Coverage]]
- [[Concentration Gate Validation]]
- [[Micro-Capital Cost Curve and Sizing]]
