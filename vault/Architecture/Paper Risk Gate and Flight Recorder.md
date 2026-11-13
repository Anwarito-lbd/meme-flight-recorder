# Paper Risk Gate and Flight Recorder

Upward link: [[Home]] · Architecture: [[Multi-Agent Research Team]]

Implemented across `PaperRiskGate` and `ResearchTeamCoordinator` in [`research_team.py`](../../src/meme_flight_recorder/research_team.py), backed by [`risk.py`](../../src/meme_flight_recorder/risk.py), [`costs.py`](../../src/meme_flight_recorder/costs.py), [`paper.py`](../../src/meme_flight_recorder/paper.py), and [`journal.py`](../../src/meme_flight_recorder/journal.py).

## Paper Risk Gate Controls
- **Safety Prerequisite**: Blocks immediately (`safety_gate_reject` or `safety_gate_monitor`) unless Agent 2 returned `eligible_for_strategy_review`.
- **Capital-Tier Sizing**: Automatically switches to catastrophic-loss micro sizing (`equity < $100`), capping positions at `min(max_position_usd, equity * pct)` while verifying `position >= minimum_viable_position_usd`. See [[Micro-Capital Cost Curve and Sizing]].
- **Portfolio Limits**: Enforces `maximum_open_positions`, `maximum_aggregate_open_position_pct`, `maximum_daily_loss_pct`, `consecutive_loss_limit`, `maximum_pool_share_pct`, and `maximum_round_trip_cost_pct`.

## Cryptographic Hash-Chained Journal
`FlightRecorder` stores every observation, evaluation, multi-agent packet (`research_desk_packet`), and paper position open/close in append-only SQLite with:

```text
event_hash = SHA256(previous_hash | observed_at | event_type | entity_id | canonical_payload_json)
```

`PaperBroker` never mutates rows in place; open positions are reconstructed deterministically on restart via `journal.position_events()`, and `journal.verify_chain()` recomputes the full chain from `GENESIS`.

## Related Notes
- [[Micro-Capital Cost Curve and Sizing]]
- [[Promotion Policy and Evidence Gate]]
- [[Multi-Agent Research Team]]
