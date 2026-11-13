# One Front Door and Autonomy Levels

Upward link: [[Home]] · Architecture: [[Multi-Agent Research Team]]

Following the operator control pattern ("one front door, an agent decision layer, a read-only bridge, and explicit autonomy levels so nothing moves money without human control"), the research desk enforces four structural boundaries in [`research_team.py`](../../src/meme_flight_recorder/research_team.py):

## 1. One Front Door
- **Web Console & API:** `/desk`, `/api/desk/state`, `/api/desk/evaluate`, `/api/desk/diary` ([`api.py`](../../src/meme_flight_recorder/api.py), [`desk_ui.py`](../../src/meme_flight_recorder/desk_ui.py)).
- **CLI Gateway:** `meme-recorder desk-diary`, `meme-recorder collect`, `meme-recorder scout`, `meme-recorder media-scan` ([`cli.py`](../../src/meme_flight_recorder/cli.py)).

## 2. Decision Layer
Composes [[Virality and Narrative Analyst]] + [[Adversarial On-Chain Investigator]] + [[Paper Risk Gate and Flight Recorder]]. Every decision is recorded in `FlightRecorder` regardless of outcome.

## 3. Read-Only Bridge (`ReadOnlyBridge`)
Separates market/on-chain telemetry from the decision layer with `write_capable=False` and `signing_capable=False`:
- **Internal Read-Only Adapters:** CoinGecko Onchain, DexScreener, Jupiter quotes, Helius RPC, Binance Web3 ranked labels, GoPlus security, and public Reddit/Telegram/GNews feeds.
- **Documented Solana Ecosystem References (Read-Only Patterns Only):**
  - `GMGNAI/gmgn-skills` — Smart-money & holder concentration queries
  - `pump-fun/pump-public-docs` — Pump.fun bonding curve & PumpSwap AMM specs
  - `chainstacklabs/pumpfun-cli` & `chainstacklabs/pumpfun-bonkfun-bot` — Bonding-curve CLI/stream telemetry patterns (execution excluded)
  - `sendaifun/solana-agent-kit` — Solana token-analytics & read-only agent tool patterns

## 4. Explicit Autonomy Levels
- **`L0_observe_only`**: Evaluates and journals research packets; zero paper orders opened.
- **`L1_paper_replay_and_journal`**: Opens and closes deterministic paper positions in `PaperBroker`.
- **`L2_human_approval_draft`**: Emits an unsigned `HumanApprovalDraft` (`requires_human_confirmation=True`, `execution_blocked=True`, `signing_capable=False`).

## Related Notes
- [[Multi-Agent Research Team]]
- [[Promotion Policy and Evidence Gate]]
- [[Fail-Closed Engineering Rules]]
