# Study C — Discovery Coverage

Upward link: [[Home]] · Spec: [`2026-08-22-late-emergence-and-coverage-rules.md`](../../docs/superpowers/specs/2026-08-22-late-emergence-and-coverage-rules.md) · Script: [`study_discovery_coverage.py`](../../scripts/study_discovery_coverage.py)

## Research Question
Of real Solana trending movers meeting the deep-pool rule (`liquidity >= $50,000`, `pair age >= 2 days`), what fraction does `FlightRecorder` observe at all?

## Pre-Registered Decision Bands
- `>= 70%`: discovery adequate; selection/gates are the frontier.
- `< 50%`: discovery is the binding constraint; expand feeds before further gate work.
- `50% – 70%`: ambiguous band; report both numbers and pair with [[Study L - Late Emergence Survival]].

## Measured Results (`STATUS.md`, 2026-08-22)
- **Qualified Trending Movers:** `41`
- **Present in Journal:** `26`
- **Measured Coverage:** **63.4%** (inside the 50–70% ambiguous band).
- **Engineering Follow-Through:** Widened the CoinGecko movers poll from 1 page (~20 pools) to **3 trending pages**, deduplicated by pool address, with graceful per-page degradation (`src/meme_flight_recorder/providers/coingecko.py`). Meanwhile, [[Virality and Narrative Analyst]] polls free public Reddit Atom feeds, public Telegram previews, and Google News RSS to test whether social discovery arrives earlier (`M1` study).

## Related Notes
- [[Study L - Late Emergence Survival]]
- [[Virality and Narrative Analyst]]
- [[Promotion Policy and Evidence Gate]]
