# Historical paper-test report — 2026-08-02

## Outcome

The strict strategy produced **zero completed trades** for DOGE, SHIB, PEPE, BONK, and WIF. This result does not establish profitability or failure; it shows that the current signal was too selective for this dataset and that the dataset cannot support threshold tuning.

## Test design

- Data window: 2026-05-04 22:00 UTC through 2026-08-02 21:00 UTC.
- Development split: approximately the first 60 days.
- Held-out split: approximately the final 30 days, with 220 prior hours used only as indicator warm-up.
- Assets: DOGE, SHIB, PEPE, BONK, WIF.
- Starting paper equity: $10,000 per asset test.
- Risk per trade: 0.5% of current equity.
- Maximum spot notional: 100% of equity; no leverage.
- Entry/exit slippage: 0.10% each side.
- Modeled round-trip fees: 0.40%.
- Maximum holding time: 48 base bars.
- Same-candle stop/target ambiguity: stop wins (pessimistic).
- Signal: completed-bar trend, consolidation breakout, and retest; no future bars used.

## Data limitation

The VM could reach CoinGecko but could not reach Kraken's public API. CoinGecko's public 90-day series consists of hourly point samples and rolling market-volume observations, not Kraken 15-minute OHLCV or order-book fills. Proxy bars were therefore labeled `coingecko_hourly_point_proxy_not_exchange_ohlcv` throughout the test.

The strict consolidation gate produced no breakout candidates. A sensitivity probe confirmed that loosening the range and volume thresholds can create isolated development-period candidates, but none were robust in the held-out DOGE period. Production thresholds were not changed because doing so from proxy data would be overfitting.

## Improvements made from the run

1. CEX risk sizing is now capped at account equity for spot trading; tight stops cannot imply leveraged notional.
2. Zero-trade metrics no longer divide by zero.
3. Open positions are force-closed at the end of a historical test, preventing survivorship bias.
4. HTTP 429 and transient 5xx responses use bounded retries.
5. Same-candle stop/target ambiguity resolves pessimistically to the stop.
6. Direct API paper entries now use the recorded candidate's chain/universe and preserve risk amount for R-multiple accounting.

## Promotion decision

**Do not promote or loosen the strategy.** The next evidence gate is a genuine Kraken backtest on 15-minute and 1-hour exchange candles, followed by look-ahead analysis and a live-data dry run. At least 100 completed paper trades are still required before any live consideration.
