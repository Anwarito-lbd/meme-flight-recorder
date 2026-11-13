# Third-party audit record

`CLAUDE.md` requires that third-party trading bots, MCP servers and repos are
untrusted until inspected, and that nothing handling a private key is imported.
It used to say those rejections were recorded in `STATUS.md`. **They were not** —
no such section ever existed, so every candidate was re-litigated from scratch and
the reasoning behind each rejection was lost. That is the same defect class as the
journal recording a verdict and discarding the evidence for it. This file is that
record.

## Inspection protocol

Applied in this order. Steps 1–3 are cheap and reject most candidates before
anything is downloaded.

1. **Read, do not clone.** Inspect the source through the web. A repo that must be
   cloned to be understood is not yet trusted enough to be on disk.
2. **Key and signing exposure.** Does it hold, read, derive or transmit a private
   key? Does it sign or broadcast a transaction? Either is an immediate reject,
   whatever the custody story. Better key handling is still key handling, and the
   invariant is about the capability existing at all.
3. **Maintenance signal.** Commit count, last activity, and the star-to-fork ratio.
   Hackathon repos are routinely fork-farmed, so many forks against no stars is a
   reason for more scrutiny, not less.
4. **Licence**, and whether the licence permits the intended use.
5. **What it would actually add**, against what the repo already has. A capability
   already covered is a dependency and an attack surface for no gain.
6. **Public information only.** Anything reselling paid-group calls, private
   alpha, or pre-announcement listings breaches the project's own constraint.
7. **If admitted**, pin it in `skills-lock.json` by source and content hash. That
   is the only mechanism by which external code enters this project.

Decisions are one of `ADOPT`, `FORK`, `SELECTIVE REUSE`, `REFERENCE`, `REJECT`.

## Currently admitted

Pinned in `skills-lock.json` by source and content hash:

| Skill | Source | Provides |
|---|---|---|
| `meme-rush` | `binance/binance-skills-hub` | Launchpad lifecycle feed and AI hot topics |
| `emblem-memecoin-scout` | `emblemcompany/agent-skills` | Pump.fun/LaunchLab alerts, rug detection, holder analysis, smart-money tracking |
| `backtesting-frameworks` | `wshobson/agents` | Backtest construction guidance |

## Audited 2026-08-15

### `SoulPass-AI/soulpass-cli-skill` — REJECT

Hardware-secured Solana wallet and trading terminal for AI agents: Jupiter swaps,
DeFi yield, whale copy-trading, agent payments, a `sign` command for messages and
raw hashes, and a `serve` JSON-RPC daemon.

Rejected on three independent grounds, any one sufficient:

- **It signs and broadcasts transactions.** That is the project's first hard
  invariant, protected by `tests/test_no_live_execution.py`.
- **It handles private keys.** Secure-Enclave custody is a *better* key-handling
  story, not an absence of one.
- **It cannot run here.** Secure Enclave requires Apple Silicon; this project runs
  on Windows 11.

Worth stating because it generalises: **the execution layer is not this project's
missing piece.** A paper broker, exit engine, position store and risk engine all
exist. Execution is withheld by evidence, not by missing tooling, so a better
execution tool solves a problem the project does not have.

### `digitalarchivo/meme-agent` — REJECT

Solana AI Hackathon 2024 entry. Autonomous social posting plus trading via Jupiter,
Groq/Mixtral for content, Twitter and Discord automation.

- **Requires `SOLANA_PRIVATE_KEY` in a plaintext `.env` and executes real trades.**
  Same invariant breach as above, with none of SoulPass's custody care.
- **Unmaintained:** 3 commits on main, ISC licence, self-described "experimental
  software. Use at your own risk."
- **0 stars against 26 forks.** Not organic interest.
- **No measurement apparatus** — no journal, no cost model, no study. It is the
  half this project already has, minus the half that makes it trustworthy.

### `lyc0603/copytrading` — REFERENCE ONLY

Artifacts for an ACM Web Conference 2026 paper on detecting manipulative bots in
meme-coin trading, via a multi-agent explainable-LLM architecture. MIT licence, no
private keys, no signing. Data is Pumpfun and Raydium across pre- and post-Trump
windows, stored in Snowflake.

The closest fit of the three, and still not an adoption candidate:

- **Its population is the one already measured to exhaustion here** — newborn
  Pumpfun tokens, the cohort where 84.2% of 18,942 mints stopped quoting entirely.
- **The finding it would feed is already measured, and points the other way.**
  Across 402 outcomes, cluster-*clear* candidates died **more** often than
  cluster-*disqualified* (21% vs 9%) and hit 2x **less** (5% vs 16%). Manipulated
  tokens pump. Better manipulation detection sharpens a risk gate already measured
  to cost return; it does not produce an edge.
- **Operationally** it needs Snowflake and OpenAI credentials, and an
  agent-per-token LLM cost model is unreachable at $10–50 capital.

Legitimate value: a **methodology reference** for `clusters.py` and the unproven
`study_flow_value.py`. Cite it; do not import it.

### Two YouTube videos — NOT ASSESSABLE

"Turning 1 Solana into $20,000 Scalping Solana Meme coins" and "How I Built a
Profitable Crypto AI Trading Bot". Only titles were retrievable — no description,
transcript, channel or date. **Content that has not been seen is not audited**, and
both titles are outcome claims of the shape this project exists to test: a 20,000x
result is a tail draw, not a method, until the denominator of attempts is shown.
The operator confirmed neither contained a specific mechanism to measure.

The workflow screenshot from one of them *was* assessable and is recorded in
`STATUS.md` — node by node, against measurements already in this repo.

### X/Twitter data skills — REJECT (cost, not capability)

Searched the registry 2026-08-15 for a free X feed. **There is no free route.**
The free API tier was discontinued in February 2026; scraping breaches both X's
Terms of Service and this project's public-information-only rule.

| Skill | Installs | Cost | Credentials |
|---|---:|---|---|
| `fetcher-sh/fetcher-skills@twitter-api` | 2.4K | pay-per-call, **pricing undisclosed** | none (they bill directly) |
| `xquik-dev/x-twitter-scraper@xquik-social-research` | 46 | unspecified | `XQUIK_API_KEY` |

`fetcher-sh` is the more credible of the two — no OAuth dance, meaningful install
count — but **undisclosed per-call pricing cannot be budgeted against a $50
account**, which is disqualifying on its own terms rather than on quality.

X's own API is now pay-per-use with no monthly minimum at $0.005 per post read,
which makes it reachable at post-gate volume (~$6/month) and ruinous at feed
volume (10,000 reads = $50 = the whole account). **The operator has declined to
pay for it.** Recorded as a decision, not an open question.

### Solana data and execution candidates, 2026-08-19 — one adopt, four reject

Five candidates supplied by the operator, all inspected read-only against the
protocol above. Nothing was cloned, installed or pinned.

| Candidate | Verdict | Decisive reason |
|---|---|---|
| **Solana Tracker Data API** | **ADOPT, scout only** | Official hosted API. No code enters the tree; it is an HTTP call. Free tier **10,000 req/month at 3 req/s**, covering risk scores, holder analytics and wallet analytics — the evidence the movers feed lacks. |
| `paoloanzn/free-solscan-api` | REJECT | Reverse-engineered client for Solscan's *internal website* API, which the README concedes is "not officially supported for external use". **Archived 2026-08-03** — Solscan now serves Cloudflare challenges and it no longer functions. Fails step 3 on maintenance, and works by circumventing a provider's protections. |
| `jup-ag/jupiter-swap-api-client` | REJECT | Clean on safety: official, and it never touches keys — it returns unsigned transaction bytes. Rejected on **step 5** instead. It is Rust against a Python 3.12 codebase; its quote capability is already covered by `JupiterQuoteProvider`; and `/swap` plus `/swap-instructions` add transaction *building*, the one capability the paper-only invariant exists to exclude. A capability already covered is a dependency and an attack surface for no gain. |
| `solanatracker/raptor-binary` | REJECT | "This is a closed-source project. The repository hosts binary releases only." Step 1 is *read, do not clone*; a binary cannot be read at all. It **submits** transactions via Yellowstone Jet TPU and accepts `--jet-identity <PATH>`, a keypair path. 13 stars, 2 forks, no licence. Running an unauditable binary that can submit transactions, on the machine holding trading capital, is what the protocol exists to prevent. |
| `kukapay/crypto-feargreed-mcp` | REJECT the wrapper | Clean on every safety step — MIT, no credentials, no key handling, no execution. But it is an **MCP server**, and the collector is a headless Python loop that cannot call one. Underneath it is `alternative.me`'s Fear & Greed API: free, keyless, one GET. Taking a dependency to wrap a single GET fails step 5; use the source directly if the signal is ever wanted. |
| Instrumetriq / browser extensions | REJECT, structurally | A browser extension runs inside a browser. The collector is unattended and has none. No integration path exists, whatever the extension's quality. |

**Where Solana Tracker may be used, and why not in the collector.** At ~144
cycles/day x 73 candidates the collector grades roughly **10,500 candidates per
day**, against a monthly allowance of 10,000 — less than one day of collection.
Even restricted to candidates clearing every gate (~21/cycle) it needs ~3,000/day,
still 9x over. So it is a **scout-only** provider, with a hard daily budget as a
no-default config field, a counter on the provider instance checked *before* the
call, and pacing derived from the 3 req/s limit. `SOLANATRACKER_API_KEY` goes in
`.env` by the operator's own hand. The account's private endpoint hostname is
account-identifying and must not be committed.

Not yet built — recorded here so the reasoning is not re-derived.

### Execution bots and SDKs, 2026-08-19 — four rejects and one open question

Five repos supplied by the operator. All inspected read-only; nothing cloned,
installed or run.

#### `solana-snipe-bot/Free-SolanaSniperBot-2026-MemecoinAutoTrader` — REJECT, DO NOT FUND

The most important entry in this file. Its own README says:

> "We generate a wallet for you. Send SOL and you're ready to go."

**The private key is theirs.** Sending SOL to that wallet is sending it to an
address someone else controls, which is a deposit, not a trade. There is no
custody story to evaluate because there is no custody — the funds are simply
somewhere else.

The repo contains **no source code**: a README and one JavaScript file pointing at
`meme-snipe.com`. Three commits. No licence. 131 stars against 5 forks, which is
the inverse of the usual ratio for software people actually run. The marketing
claims **"Audited & transparent"** while there is nothing published to audit —
those two statements cannot both be true, and that contradiction is the tell.

Rejected on step 2 (key custody), step 1 (nothing readable), step 3 (three
commits) and step 4 (no licence) independently. Any one would be sufficient.

#### `YZYLAB/solana-trade-bot` — REJECT

`PRIVATE_KEY=your_wallet_private_key` in `.env`, executes buys and sells, routes
through a paid Solana Tracker plan. MIT, 161 stars, 8 commits. Step 2 immediate
reject: the invariant is about the capability existing at all, and better key
handling is still key handling.

#### `piotrostr/listen` — REJECT for import, credible project

A real one: AGPL-3.0, Rust, 1.1k stars, 837 commits, actively maintained. It
loads keypairs in `solana-keygen` format and signs and sends transactions, which
is step 2. Beyond that it is Rust against a Python codebase, and its data layer —
transaction monitoring, price tracking — duplicates what `providers/` already
does. A capability already covered is a dependency and an attack surface for no
gain.

#### `santoshlite/memecoooins` — REJECT

Not a research tool. It is a $50 "crypto roulette": the service generates a
wallet per user, funds it, and allocates to **random** meme coins with hourly
rebalancing. 4 stars, 0 forks, no licence, and **no documented selection
methodology** — the randomness is the product. Nothing here to learn from and
custody sits with the operator of the service.

#### `nirholas/pump-fun-sdk` — EVALUATED, not adopted, and worth revisiting

The only one of the five that clears the safety steps. Apache-2.0, 115 stars, 36
forks, 3,707 commits, actively maintained. Third-party rather than official
Pump.fun software. Its own claim, which the structure supports:

> "Safe by construction. Reads never load a keypair. Every trade is simulated
> before anything is sent."

The SDK returns `TransactionInstruction[]` and never sends; the caller signs. The
read-only surface — `pump quote buy|sell`, `pump curve`, `pump price`,
`pump pool` — loads no key at all.

**Why it is interesting here.** Jupiter cannot quote a token still on its bonding
curve: there is no AMM pool to route through. That is structural, not a quota
problem, and it is why pre-bond candidates carry `entry_route_unknown` and
`entry_price_impact_unknown`. A curve quote would supply impact evidence exactly
where Jupiter cannot.

**Why it is not a straightforward yes.** This file's own prior decision reads:
*"Deliberately not built: estimating price impact from constant-product maths
against pool depth. It would make candidates pass, and would substitute an
estimate for a quote inside a gate whose purpose is verifying a real executable
route."* A `pump quote` reads actual on-chain curve state and applies the
protocol's own pricing function, so it is closer to a real quote than to an
estimate from depth — but it is still not a filled order, and adopting it would
make candidates pass a gate they currently fail.

**Decision: not adopted now.** Revisit only if a study gives a reason to care
about pre-bond candidates. Wiring a TypeScript CLI into a Python codebase to make
more candidates pass a gate, before any signal is shown to predict anything, is
effort in the wrong order.

## Pending

### MobyAgent — NOT YET AUDITED

Proposed for whale and KOL intelligence. Two specific questions before any access,
rather than general caution:

1. **Does it resell paid-group or private calls?** That breaches the
   public-information-only constraint regardless of how good the data is.
2. **Is its whale/KOL ranking a leaderboard?** Measured here: `popchad.sol`, on a
   curated top-trader list, showed profit factor **0.47**, down 126 SOL. A
   leaderboard would have said the opposite.

Also check redundancy first: `emblem-memecoin-scout` is already pinned and already
provides smart-money tracking and rug detection. A second dependency covering the
same ground is pure attack surface.
