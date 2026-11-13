# Micro-Capital Cost Curve and Sizing

Upward link: [[Home]] · Code: [`costs.py`](../../src/meme_flight_recorder/costs.py), [`risk.py`](../../src/meme_flight_recorder/risk.py) · Config: [`default.toml`](../../config/default.toml)

## The Cost-Floor vs Kelly-Growth Tension
At micro bankrolls (`equity < $100`), percentage-of-equity risk sizing collides with fixed Solana network fees (`2 * fixed_cost_per_leg_usd`):
- Under [`costs.py`](../../src/meme_flight_recorder/costs.py), `minimum_viable_position_usd` puts the `10%`-cost floor at `$0.37` (with `$0.50` configured minimum).
- **Measured Cost Curve (`CLAUDE.md`):**
  - At `$0.80` position size: round-trip cost is **5.44%** -> PF `0.138`.
  - At `$10.00` position size (`20%` of `$50` account, `5` concurrent slots): round-trip cost drops to **1.81%** -> PF rises `0.138 -> 0.476` on identical entries.
  - **Accepted Trade-Off:** Higher per-trade cost efficiency reduces total shot count (`5` slots vs `25`), whereas capturing a `p ~ 5.8%` fat tail requires roughly `50` attempts.

## Geometric Growth vs Arithmetic Expectancy
On the measured deep-pool distribution (`n = 166`, median outcome `0.001`), arithmetic-optimal `10%` sizing produces **negative geometric growth** (`-0.0274/trade`, decaying the account to `25%` of starting equity in `50` trades):
- `f = 2%`: `+0.0029 / trade`
- `f = 3%`: `+0.0012 / trade`
- `f = 5%`: `-0.0048 / trade`
- `f = 10%`: `-0.0274 / trade`

## Why Sizing Is the Stop on Thin Pools
In replay, a collapsed token fell **1,700× past its stop inside a single candle** (`docs/MANDATE_MAPPING.md`). Hard stop-losses cannot execute in illiquid gaps; catastrophic-loss sizing (`risk = position_value_usd`) caps ruin at the stake.

## Related Notes
- [[Paper Risk Gate and Flight Recorder]]
- [[Promotion Policy and Evidence Gate]]
- [[Study L - Late Emergence Survival]]
