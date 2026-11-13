# Established Population Negative Expectancy

Upward link: [[Home]] · Scripts: [`study_established_population.py`](../../scripts/study_established_population.py), [`backfill_cex_history.py`](../../scripts/backfill_cex_history.py) · Spec: [`2026-08-22-established-widening-decision-rule.md`](../../docs/superpowers/specs/2026-08-22-established-widening-decision-rule.md)

## Research Question
Does the existing `CexPaperEngine` 1h-trend + 15m-breakout-retest rule (`established_meme_trend_ok` / `detect_breakout` / `confirm_retest`) carry positive gross or net expectancy when widened across both uptrend and downtrend regimes?

## Measured Results (`STATUS.md`)

### 1. Initial 8-Base 1-Year Study (2026-08-15)
- Universe: `DOGE, SHIB, PEPE, BONK, WIF, TRUMP, PENGU, FLOKI` on Binance (`35,040` 15m bars each, `279,359` bars walked, `16/16` series reconciled).
- Held-out (`n = 33`): expectancy `-$0.0071/trade` on `$0.80` position, PF `0.286`, still negative after deleting top trade.
- **Cost Decomposition (`n = 54`):**
  - Strategy `0.40%` taker: PF `0.251`, net `-$0.0075/trade`
  - Strategy `0.10%` taker: PF `0.579`, net `-$0.0027/trade`
  - **Strategy zero-cost:** PF `0.929` (held-out `1.017`), net `-$0.0003/trade`
  - **Random-entry control zero-cost:** PF `1.471`, net `+$0.0010/trade`
  - **Buy and hold (`n = 8`):** median multiple `0.185` (`-81%` sector drawdown); trend filter correctly kept bot out of `76%` of bars (`212,075 / 279,359`).

### 2. Widened 17-Base 700-Day Study (2026-08-22)
- Universe: 17 spot-listed meme bases × 700 days on Binance (`34/34` series cached, `2024-09-21..2026-08-22`, spanning both sector uptrend and downtrend; `1,096,664` bars walked).
- **Held-out (`n = 79`):** net expectancy `-$0.0080/trade`, PF **0.233**.
- **Zero-cost held-out PF:** **0.842** (below the pre-committed `[0.9, 1.1]` no-information band).
- **Verdict: LINE CLOSED.** The breakout signal carries no gross edge on established CEX meme coins; the deep-pool Solana book remains the only active forward path.

## Related Notes
- [[Study L - Late Emergence Survival]]
- [[Promotion Policy and Evidence Gate]]
- [[Fail-Closed Engineering Rules]]
