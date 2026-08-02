# Meme Flight Recorder 0.4.0

A safe, paper-only foundation for meme-coin research. It records what the system knew at each point in time, rejects candidates with missing critical evidence, classifies meme lifecycle state, ranks survivors, and supports paper/shadow positions. It cannot sign or broadcast transactions.

## What this version contains

- Established CEX paper engine plus a Freqtrade 2026.7 dry-run adapter.
- Emerging Solana meme and brand-new launch domains with separate rules.
- Fail-closed identity, authority, holder, liquidity, route, simulation, impact, and staleness checks.
- Lifecycle states from launch through graduation, momentum, distribution, and collapse.
- Confidence-aware candidate scoring; missing data lowers confidence.
- Risk engine: 0.5% CEX chart risk, 0.25% full-capital-risk Solana shadow size, 1% daily and aggregate limits.
- One stop and one 2R target for CEX paper positions, with 0.5% equity risk sizing.
- Completed-candle 1h trend and 15m consolidation-breakout-retest logic with no future range access.
- Hash-chained SQLite event journal to reveal historical tampering.
- Conservative Solana shadow fills with modeled latency, fees, entry/exit slippage, and full-position-loss risk sizing.
- Restart recovery for open paper and shadow positions.
- Read-only CoinGecko Onchain and Jupiter quote adapters.
- Read-only Helius authority, supply, largest-account, health, paginated wallet-history, and unsigned-simulation adapter.
- Bounded provider retries for rate limits and transient server errors.
- Historical paper-test harness with fees, slippage, spot caps, time exits, and held-out splits.
- FastAPI control surface and standard-library test suite.

## Deliberate exclusions

- No private keys, seed phrases, signing, swaps, transfers, or transaction broadcasting.
- No automatic BUY decision from a provider score, social signal, or smart wallet.
- No X/Reddit/TikTok scraping in V1.
- No launch sniping or leverage.
- No claim of profitability. Backtests and paper results must earn promotion.

## Quick start

Requires Python 3.12+.

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e '.[dev]'
make test
make init
make run
```

Open `http://127.0.0.1:8000/docs` for the API console. Docker is also supported:

```bash
cp .env.example .env
docker compose up --build
```

## Candidate evaluation

The request body in `examples/candidate.json` shows the complete input. Replace the placeholder with an exact mint and current timestamps, then submit:

```bash
curl -X POST http://127.0.0.1:8000/candidates/evaluate \
  -H 'content-type: application/json' \
  --data @examples/candidate.json
```

The output status is one of:

- `reject`: a hard gate failed or critical evidence is unknown.
- `monitor`: a brand-new launch passed observable checks but remains ineligible to trade.
- `eligible_for_strategy_review`: safety gates passed; this is not trade authorization.

Every snapshot and evaluation is written to the audit journal, including rejections.

## CEX paper strategy

`POST /strategy/cex/observe` accepts completed 1-hour and 15-minute candles. It records each transition, waits for a breakout retest, passes the proposed entry through the portfolio risk engine, and opens an internal paper position. The exact asset must first have a recorded `eligible_for_strategy_review` safety decision.

The optional Freqtrade adapter is in `integrations/freqtrade/`. Install and validate it with:

```bash
python3 -m pip install -e '.[dev,freqtrade]'
python3 -m freqtrade list-strategies \
  --userdir integrations/freqtrade \
  --strategy-path integrations/freqtrade \
  --config integrations/freqtrade/config.paper.json
```

The supplied configuration hard-codes `dry_run: true`, spot mode, no leverage, two open positions, an explicit meme allowlist, and blank exchange credentials. Confirm that each pair is actually available on your exchange before downloading data or starting dry run.

The committed paper wallet is $10, as requested. `scripts/select_kraken_pairs.py`
queries Kraken's current public market catalog, records pair minimums, removes unavailable
markets, and creates a credential-free USD configuration. The manual GitHub Actions workflow
`.github/workflows/kraken-research.yml` runs this resolver, downloads genuine Kraken trades,
converts them into 15-minute and 1-hour candles, runs the strict backtest and look-ahead
analysis, and preserves the evidence as a workflow artifact. A completed workflow is evidence
from historical replay; it is not a live-data dry run and does not count toward the 100
forward paper-trade promotion gate.

## Solana shadow execution

`POST /shadow/solana/positions` creates hypothetical positions only after the exact mint passes the candidate safety gate. It requires an independently verified exit route and a successful unsigned simulation. The fill model adds configured price impact and adverse slippage; P&L deducts modeled round-trip fees. Open state is reconstructed from the append-only journal after restart.

## Provider boundaries

`CoinGeckoProvider` supplies listed meme rankings, new/trending Solana pools, token pools, pool OHLCV, and recent trades. `JupiterQuoteProvider` asks only for entry and independent reverse-route quotes. Neither provider approves or executes a trade.

The Helius adapter verifies mint/freeze authorities and supply directly. Its largest-account result is deliberately named `gross_top10_account_pct`: pool, treasury, burn, and custody accounts must be classified before it can become `top10_private_holder_pct`. If the RPC refuses that expensive query, concentration remains unknown and the safety engine continues to fail closed. Its Wallet API adapter retrieves newest-first pages with token balance changes and preserves failed transactions; callers must journal every page before following `next_cursor`. Unsigned simulation evidence must still come from a transaction-building layer that cannot broadcast.

## Historical validation performed

The included 90-day research pass used hourly CoinGecko point samples for DOGE, SHIB, PEPE, BONK, and WIF from May 4 through August 2, 2026. Approximately 60 days were used as a development split and the final 30 days as a held-out split, with 220 warm-up hours supplied before evaluation.

The strict breakout/retest configuration produced zero completed trades on all five assets in both splits. This is an insufficient-activity result, not evidence of profitability. The source is a price/rolling-volume proxy rather than exchange 15-minute OHLCV, so thresholds were not loosened to manufacture trades. The next valid experiment requires Kraken 15-minute and 1-hour candles plus Freqtrade look-ahead analysis.

See `research_results/2026-08-02-paper-test.md` for assumptions and interpretation.

Emblem Meme Coin Scout remains an optional operator-side enrichment source. Its findings should be normalized into a `TokenSnapshot`, cross-checked against CoinGecko and direct RPC, and preserved in `raw_evidence`.

## External trader and public-figure research

`research/trader_signal_registry.json` contains provenance-tagged research leads for Kimchi,
TJR, several Nansen-published Solana wallets, and historical politician launches. It is not an
allowlist. `external_signals.py` makes influencer claims permanently ineligible, keeps new
public-figure launches monitor-only, and requires at least three qualified wallets plus market,
safety, developer-flow, and exit-route confirmation before a wallet-cluster observation can
advance to forward paper study. See
`research_results/2026-08-02-trader-methodology.md` for the evidence and hypothesis design.

Run a bounded, read-only wallet-history backfill after setting a replacement Helius key:

```bash
python scripts/backfill_wallet_history.py --max-pages 5
```

Raw pages are content-addressed under the ignored `data/wallet-history/` directory. A page is
persisted before its cursor is followed, so partial history remains distinguishable from a
complete backfill.

## Build roadmap

1. Run the recorder continuously and preserve every observed/dead token.
2. Add holder-account classification, creator history, and program-aware unsigned simulation inputs.
3. Add Pump.fun/PumpSwap/Raydium/Meteora program listeners.
4. Download exchange candles and run Freqtrade look-ahead analysis and historical backtests.
5. Backtest with point-in-time data, fees, slippage, failures, and walk-forward splits.
6. Add X attention only for shortlisted exact mints; Reddit later; TikTok optional.
7. Add a dashboard and Telegram alerts after the data path proves stable.

Do not consider live execution before at least 100 completed CEX paper trades, 1,000 evaluated Solana candidates, positive walk-forward expectancy after all modeled costs, and successful restart/stale-data/kill-switch tests.
