# Multi-Agent Research Team

Upward link: [[Home]]

The Multi-Agent Meme-Coin Research Team (`ResearchTeamCoordinator` in [`research_team.py`](../../src/meme_flight_recorder/research_team.py)) composes the repository's fail-closed primitives into four explicit, auditable desk roles.

## Pipeline Order (Fail-Closed by Construction)

1. **[[Virality and Narrative Analyst]]** (`ViralityNarrativeAnalyst`): produces an advisory `ViralityReport` from deduplicated `SocialEvent` observations. Social conviction can **never** waive a downstream safety or risk check.
2. **[[Adversarial On-Chain Investigator]]** (`AdversarialOnChainInvestigator`): overlays opportunistic vendor labels (`apply_vendor_labels`), runs `SafetyEngine.evaluate`, and emits an `InvestigationVerdict` with per-dimension gate status (`identity_and_freshness`, `authority_and_rug_flags`, `concentration_and_clusters`, `liquidity_and_exit_route`).
3. **[[Paper Risk Gate and Flight Recorder]]** (`PaperRiskGate` + `FlightRecorder`): blocks any candidate below `eligible_for_strategy_review`, models round-trip fees via `round_trip_cost`, checks `RiskEngine.approve`, and appends a SHA-256 hash-chained `research_desk_packet` event.

## Local Inspection & Export Surfaces

- **Web Desk UI** (`GET /desk`, `GET /api/desk/state`, `POST /api/desk/evaluate`): implemented in [`desk_ui.py`](../../src/meme_flight_recorder/desk_ui.py) and [`api.py`](../../src/meme_flight_recorder/api.py).
- **Markdown Desk Diary** (`GET /api/desk/diary`, `meme-recorder desk-diary`): summarizes chain verification, gate rejection Pareto, packet decisions, and paper-book closures.

## Related Notes
- [[Virality and Narrative Analyst]]
- [[Adversarial On-Chain Investigator]]
- [[Paper Risk Gate and Flight Recorder]]
- [[Fail-Closed Engineering Rules]]
