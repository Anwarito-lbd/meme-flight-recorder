# Concentration Gate Validation

Upward link: [[Home]] · Code: [`safety.py`](../../src/meme_flight_recorder/safety.py), [`clusters.py`](../../src/meme_flight_recorder/clusters.py)

## Gate Under Test
`SafetyLimits.maximum_top10_private_holder_pct = 30.0%` for `solana_emerging` (excluding bonding-curve/AMM pool vaults and burn addresses from the numerator).

## Measured Results (`STATUS.md`, 2026-08-22)
- **Sample Size:** `n = 774` priced outcomes (expanded from `n = 6` initial check).
- **Death Rate Above `30%` Threshold:** **55%**
- **Death Rate Below `30%` Threshold:** **38%**
- **Verdict:** **VALIDATED on death rate.** The concentration gate buys survival and remains unchanged.
- **Secondary Observation (Unchanged Config):** Bands are non-monotonic (`30–50%` measured `26%` death rate at `n = 65`), logged purely as a future study hypothesis rather than a post-hoc threshold tweak.

## Winner-Recall Context
Post-liquidity-fix recall check:
- Acceptance among big winners rose `0% -> 7%` (`n = 97`).
- Acceptance among deaths remains higher at `15%` (`n = 749`).
- `holder_concentration_excessive` is the single largest rejector of winners (`100` rejections), showing that existing gates perform **survival-selection, not return-selection**.

## Related Notes
- [[Adversarial On-Chain Investigator]]
- [[Study L - Late Emergence Survival]]
- [[Fail-Closed Engineering Rules]]
