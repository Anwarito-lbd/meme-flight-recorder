# Third-party notices

This project vendors no third-party source. The entries below record work that
was **read for architectural ideas** and services that are **called over their
public HTTP APIs**, so the provenance of both is auditable.

## Reference material read, not imported

### Jackhuang166/ai-memecoin-trading-bot

- Licence: MIT
- Repository: <https://github.com/Jackhuang166/ai-memecoin-trading-bot>
- Language: Go. This project is Python; **no code was copied, adapted or
  translated.** The influence is architectural only.

Copyright (c) Jackhuang166 and contributors. Permission is hereby granted, free
of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and
to permit persons to whom the Software is furnished to do so, subject to the
condition that the above copyright notice and this permission notice be included
in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED
"AS IS", WITHOUT WARRANTY OF ANY KIND.

**What was taken.** The decomposition of a launch pipeline into named,
separately testable stages — scanning, pre-filtering, on-chain safety, off-chain
enrichment, strategy evaluation, candidate listing, execution, risk management
and telemetry. That decomposition largely matches modules this project already
had (`discovery.py`, `risk.py`, `safety.py`, `enrichment.py`, `scoring.py`,
`monitor.py`, `paper.py`, `journal.py`), and reading it mainly confirmed the
existing seams rather than changing them.

**What was deliberately not taken, and why.**

- **Anything that signs or broadcasts.** It executes through a wallet SDK or a
  raw private key under `AUTO_EXECUTE=true`. This project is paper-only by hard
  invariant, holds no private key, and `tests/test_no_live_execution.py` must
  stay green and unmodified.
- **Its thresholds.** The headline entry rule is a minimum 80% "win
  probability". No study establishes that number. Every gate here ships with the
  study that decides whether it may be trusted, and a threshold without one is
  treated as a future defect.
- **Its placeholder logic.** The scanner, safety, off-chain and execution paths
  contain stubs that return success. Importing assumed-success logic into a
  fail-closed system would silently convert "we could not tell" into "safe",
  which is the exact failure mode this project's gates exist to prevent.

## Services called over public HTTP APIs

No code from these is included. They are called as documented public endpoints,
read-only, and each answer is wrapped with its source, timestamp, TTL,
confidence and failure state (`providers/envelope.py`).

| Service | Auth | Use |
|---|---|---|
| DEX Screener | keyless | Primary pair and liquidity evidence |
| GeckoTerminal | keyless | Independent pool-keyed candle history |
| Birdeye | API key, free tier | Mint-keyed candles, holders, price |
| Jupiter | API key, free plan | Executable route and price-impact quotes |
| GoPlus | keyless | Token security and mint-state flags |
| Helius | API key | Solana RPC, WebSocket log streams, wallet history |
| Binance Web3 (`meme_rush`) | keyless | Launchpad discovery feed |
| CoinGecko | API key, demo plan | Market context |

Terms of service are respected as a hard rule: **only public endpoints, never
authenticated or private ones, and no scraping of logged-in surfaces.** Photon,
BullX, Trojan, Axiom and GMGN publish no developer API this project depends on;
they are integrated as outbound dashboard deep links only (`deeplinks.py`), which
navigate a human to a page and transmit nothing.
