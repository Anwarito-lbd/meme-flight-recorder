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
