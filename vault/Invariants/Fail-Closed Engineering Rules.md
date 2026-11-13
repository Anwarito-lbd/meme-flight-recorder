# Fail-Closed Engineering Rules

Upward link: [[Home]] · Rules Source: [`CLAUDE.md`](../../CLAUDE.md) · Traceability: [`MANDATE_MAPPING.md`](../../docs/MANDATE_MAPPING.md)

Every invariant below was paid for by a real defect observed in live collection or replay:

1. **Paper Only (`tests/test_no_live_execution.py`)**: Zero private keys, zero seed phrases, zero transaction signing or broadcasting anywhere in `src/`.
2. **Absent Is Not Zero**: Missing fields serialize as `None`/`null`, never `0`. Conflating missing data with zero previously produced a spurious `+$130,273/dollar` EV on a `$0.00`-liquidity token and caused `13/56` winners to look like risk rejections during provider outages.
3. **Reconciling Denominators**: Every study must assign every input to exactly one bucket summing to the total input population.
4. **Identity Is the Mint Address, Never the Ticker**: Five `CATE` mints and four `SAOF` mints appeared in one night; every social and on-chain join keys strictly on base58 mint address.
5. **Market Cap Is Not Exit Liquidity**: Position viability is gated on quoted Jupiter round-trip price impact and pool depth (`<= 0.5%` pool share), not market cap.
6. **Pre-Registered Decision Rules**: Every gate and study (`Study L`, `Study C`, `Established Widening`, `M1`/`W1`) commits its pass/fail rule in `docs/superpowers/specs/` before fetching data.
7. **Judge Risk Gates on Death Rate, Return Gates on Return**: [[Concentration Gate Validation]] passed on death rate (`55%` vs `38%`), whereas the developer-selling gate failed on its own terms until fixed to a band (`[1%, 50%)`).

## Related Notes
- [[Multi-Agent Research Team]]
- [[One Front Door and Autonomy Levels]]
- [[Concentration Gate Validation]]
