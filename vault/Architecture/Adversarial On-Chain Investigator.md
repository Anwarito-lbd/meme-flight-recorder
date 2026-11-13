# Adversarial On-Chain Investigator

Upward link: [[Home]] · Architecture: [[Multi-Agent Research Team]]

Implemented as `AdversarialOnChainInvestigator` in [`research_team.py`](../../src/meme_flight_recorder/research_team.py), unifying [`safety.py`](../../src/meme_flight_recorder/safety.py), [`vendor_labels.py`](../../src/meme_flight_recorder/vendor_labels.py), [`clusters.py`](../../src/meme_flight_recorder/clusters.py), [`deployer.py`](../../src/meme_flight_recorder/deployer.py), [`lifecycle.py`](../../src/meme_flight_recorder/lifecycle.py), and [`readiness.py`](../../src/meme_flight_recorder/readiness.py).

## Fail-Closed Audit Dimensions

Every candidate is graded across four adversarial dimensions; missing evidence produces `UNKNOWN` (reject) rather than passing:

1. **Identity & Freshness**: verified mint address, `age <= stale_after_seconds` (120s default), and `age_minutes >= minimum_age_minutes` (60m Solana emerging; under-age clean tokens become `MONITOR`, not `REJECT`).
2. **Authority & Rug Flags**: `mint_authority_disabled`, `freeze_authority_disabled`, `connected_wallet_risk`, and `developer_selling` (assessed through `developer_is_distributing`, distinguishing active distribution `[1%, 50%)` from fully exited `>= 50%`, which measured as the safest state at 7% death vs 28% for 0% sold across 871 mints).
3. **Concentration & Clusters**: `top10_private_holder_pct <= 30.0%` (Solana emerging) — validated in [[Concentration Gate Validation]] — plus optional funding-graph cluster assessment.
4. **Liquidity & Exit Route**: `liquidity_usd >= $5,000` backstop, verified `entry_route_found` & `exit_route_found` (Jupiter quote), `transaction_simulation_ok`, and bounded entry (`<=2.0%`) / exit (`<=3.0%`) price impact.

## Opportunistic Vendor Overlay Rule
`VendorLabelIndex` caches Binance Web3 `meme_rush` lifecycle ranks (`RANK_NEW=10`, `RANK_FINALIZING=20`, `RANK_MIGRATED=30`) with a 180s TTL. `apply_vendor_labels` fills **only** fields that remain `None` after direct RPC checks and never overwrites on-chain truth.

## Related Notes
- [[Concentration Gate Validation]]
- [[Fail-Closed Engineering Rules]]
- [[Paper Risk Gate and Flight Recorder]]
