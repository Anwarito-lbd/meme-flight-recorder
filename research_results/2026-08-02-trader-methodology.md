# Trader and public-figure signal research — 2026-08-02

## Decision

Do not copy a named personality. Reconstruct and separately test the mechanisms that
can be observed before a trade: wallet-cluster accumulation, public launch announcements,
liquidity/buyer confirmation, and liquid CEX momentum. Screenshots, lifestyle content,
interviews, and after-the-fact wallet reveals are not signals.

## Kimchi

The repeated story is that `@kimchi1x` made about $40 million in one TRUMP trade. The
available reporting also says no wallet has been shown that independently corroborates the
trade. A pre-TRUMP interview reportedly showed a roughly $150,000 Solana balance and
described 15-hour trading days, wallet tracking, a private group, and six-figure monthly
results. Those details make Kimchi a methodology lead, not a copy-trading source.

Research treatment: reject every Kimchi-derived trade signal until an address is linked by
strong evidence and its *complete pre-reveal history* is reconstructed. Even then, test it as
one member of a multi-wallet cluster so one lucky trade cannot dominate selection.

Sources:

- https://x.com/kimchi1x
- https://www.kucoin.com/news/flash/kimchi1x-claims-40m-profit-from-trump-coin-sparks-debate-on-meme-coin-speculation

## TJR

TJR's public material is primarily trading education, price action, motivation, and media.
The search found public claims about meme traders and interviews, but no stable public wallet
with complete transaction history attributable to TJR. His material can generate hypotheses
about structure and risk; it cannot establish a profitable meme strategy.

Research treatment: extract explicit rules only when they can be coded without discretion,
then test them on held-out data. Do not use follower count, claimed income, or course content
as reliability weights.

Source: https://x.com/_TJRTrades

Searches also surfaced retrospective claims that TJR streams or calls helped move specific
tokens, including several different tokens using the same DIDDY symbol. No source found a
complete, attributable wallet history joined to exact post/stream timestamps and exact mints.
That ambiguity is itself a result: a ticker or personality name is unusable without a mint,
timestamp, pool, and pre-call price. Any future TJR study must compare the return before the
call, immediately after it, and after realistic follower delay to detect whether the audience
is becoming exit liquidity.

## More defensible smart-wallet candidates

Nansen published exact Solana addresses for several wallets with notable historical meme
activity. This is better evidence than an anonymous screenshot, but the list is still selected
after strong past performance and therefore carries survivorship bias. The addresses are
stored in `research/trader_signal_registry.json` only for forward observation.

Source: https://nansen.ai/post/top-10-memecoin-wallets-to-track-for-2025

Additional cases refine the methodology:

- **Naseem:** public analysis attributes a first-block TRUMP purchase to a wallet associated
  with Naseem. A first-block entry before the public announcement is not reproducible from an
  official-post feed; use it to model sniper/privileged-information risk, not expected returns.
- **Ansem:** a circulated public Solana address is
  `AVAZvHLR2PcWpDf8BXY4rVxNHYRBytycHkcB5z5QNXYm`, but analyses note incoming transfers from
  other wallets and the feedback effect of his own public influence. The visible address is an
  incomplete cluster and cannot support naive copy PnL.
- **Murad:** ZachXBT described 11 high-confidence wallets linked through common funding and
  holdings similar to public posts. This is a probabilistic cluster attribution, not proof that
  every address or transaction belongs to one person.

Sources:

- https://coincept.substack.com/p/milliseconds-to-millions-snipers
- https://m.theblockbeats.info/en/news/54727
- https://x.com/zachxbt/status/1843940648430493906

Admission rule for a paper observation:

1. At least three previously registered wallets buy the same exact mint.
2. Each wallet's eligibility was established from history available before the observation.
3. Identity, authorities, holder structure, liquidity, and a reverse sell route pass.
4. Liquidity and unique buyers accelerate after the wallet buys.
5. Developer and connected wallets are not net sellers.
6. The hypothetical entry includes observed detection delay, route impact, priority fees,
   adverse movement, and failed transactions.

### Live wallet-history smoke test

On 2026-08-02 the recorder fetched the newest 100 balance-changing Helius records for each of
the six registered Nansen wallet candidates. Every address returned `has_more=true`, so no
100-record page represents complete history. Only 4 to 23 records per page named the watched
address as fee payer. A token balance increase can therefore be an unsolicited transfer,
routed activity, or activity funded elsewhere; it is not automatically a trader buy.

Before a record can count as a wallet signal, the next parser must retrieve the raw transaction
and prove signer/authority involvement, identify the swap program and pool, reconstruct both
sides of the swap, and measure pool state at the confirmed slot. Fee-payer matching is useful
evidence but is not sufficient because sponsored or routed transactions can use another payer.
This finding invalidates naive “positive token balance change = copy buy” logic.

## Politician and celebrity launches

TRUMP, MELANIA, CAR, and LIBRA show why an official post is not enough. Copycats make exact
mint verification mandatory, and public buyers may arrive after privileged wallets. For
LIBRA, reporting found liquidity activity before the president's post and an extreme collapse;
Nansen reporting said 86% of traders lost money. The registry therefore keeps these as
historical event studies, not templates for automatic buying.

Public-launch forward rule:

1. Capture a post from a pre-registered official account with its platform timestamp.
2. Extract the mint and cross-check it with an independent source and direct RPC.
3. Record the first observable pool state; never backfill facts discovered later.
4. Remain monitor-only while the token is brand new.
5. Reject developer selling, dangerous authorities, concentrated private holders, failed
   reverse routes, stale feeds, or excessive entry/exit impact.
6. A later paper entry can be evaluated only after buyer diversity and liquidity expand and
   a pullback/reclaim or another fully specified forward rule occurs.

Sources:

- https://www.trmlabs.com/resources/blog/tracing-trump
- https://melaniameme.com/
- https://x.com/FA_Touadera/status/1888722674265764017
- https://www.trmlabs.com/resources/blog/the-libra-affair-tracking-the-memecoin-that-launched-a-scandal-in-argentina

## What successful-looking wallets have in common

The defensible common mechanisms are speed, information filtering, attention to wallet and
developer flows, concentration in rare high-conviction opportunities, fast exits, and tolerance
for many failures. This is a highly adversarial market: Galaxy Research describes execution
speed as dominant and hold times as shrinking, while a 2026 paper on 190 paper trades found
that removing only the top three winners made the system unprofitable. A system must therefore
report outcome concentration and rejected-token counterfactuals, not only win rate.

Sources:

- https://www.galaxy.com/insights/research/memecoins-pump-fun-solana-kols
- https://arxiv.org/abs/2606.08232
- https://arxiv.org/abs/2602.13480

## Tool verdict

- **Helius Wallet API:** selected for raw paginated history and balance changes. The local
  adapter preserves failures and cursors; `scripts/backfill_wallet_history.py` stores immutable,
  content-addressed raw pages. Helius documents a maximum of 100 transactions per page and
  manual cursor pagination.
- **Birdeye API:** potentially valuable for current wallet PnL, multi-wallet/token PnL, tagged
  top traders, first/last trade times, funding, and `dev`/`bundler`/`sniper`/`insider`/
  `smart_trader` cohorts. The public Birdeye skill found in the registry does not expose these
  newer endpoints, so it was not selected.
- **Nansen API/CLI:** strong candidate for wallet labels, historical-address transactions,
  smart-money netflows, and survivorship-aware discovery. It requires a separate paid-access
  decision; Nansen outputs must still be backed by raw chain history.
- **X Filtered Stream:** correct future source for official accounts and timestamped public
  calls. It delivers posts within seconds and supports user-based rules; it requires an X
  developer bearer token. The Nitter/Camofox `x-monitor` skill was rejected because it monitors
  replies, not authoritative launch posts, and depends on brittle unofficial infrastructure.
- **VectorBT skill:** useful later for delay, parameter-sensitivity, Monte Carlo, noise, and
  walk-forward robustness. Do not let it replace Freqtrade's Kraken look-ahead validation or
  create a second source of execution truth.

Sources:

- https://www.helius.dev/docs/wallet-api/history
- https://docs.birdeye.so/reference/get-defi-v2-tokens-top_traders
- https://docs.birdeye.so/reference/get-wallet-v2-pnl-multiple
- https://docs.nansen.ai/api/overview
- https://docs.x.com/x-api/posts/filtered-stream/introduction

## Three independent hypotheses

1. **CEX breakout/retest** — genuine Kraken 15m/1h data, strict historical and look-ahead
   validation, then forward dry run.
2. **Smart-wallet cluster** — three-wallet consensus plus independent market confirmation;
   forward shadow fills only.
3. **Verified public launch** — official announcement and exact mint, initially monitor-only;
   later paper eligibility requires safety, sellability, organic buyers, and liquidity growth.

Promotion remains blocked until a hypothesis has a point-in-time dataset, a held-out positive
result after all costs, and enough completed forward paper observations to measure fragility.

## Expanded attributable-trader audit

- **Unipcs / Bonk Guy:** the original USELESS post is a clean historical event study because
  its post ID, exact publication time (`2025-06-09 12:07:18 UTC`), and exact mint
  (`Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk`) can be joined. Test the token's recovery
  before the call separately from follower-delay returns after 30 seconds, 1, 3, 5, 15, and
  60 minutes. His reported leveraged BONK result lacks a complete exchange ledger and is only
  a hypothesis source.
- **GCR:** Arkham identifies the public `ezekielx.eth` wallet and two additional wallets
  attributed through shared exchange-deposit evidence. Study both winning and losing trades.
  The repeatable candidates are forced-liquidation intensity, relative weakness, supply
  expansion, and post-panic spot scaling—not live wallet copying.
- **Machi Big Brother:** use the public wallet as a negative control for repeated leverage,
  liquidation re-entry, and drawdown escalation. Those patterns should activate hard lockouts.
- **Waddles, RookieXBT, Cupsey, and Orangie:** excluded from automated scoring because no
  evidence-grade wallet plus complete exact-mint/timestamp trail was found. Crowd-posted wallet
  lists are not attribution.

Sources:

- https://x.com/theunipcs/status/1932046846827856078
- https://www.bybit.com/en/learn/interviews/unipcs-bonk-guy-interview
- https://info.arkm.com/research/potential-gcr-addresses-identified
- https://info.arkm.com/research/gigantic-rebirth-crypto-trader
- https://hyperdash.com/address/0x020ca66c30bec2c4fe3861a94e4db4a498a35872
