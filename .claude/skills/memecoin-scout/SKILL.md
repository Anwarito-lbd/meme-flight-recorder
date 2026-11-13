---
name: memecoin-scout
description: Run the memecoin execution mandate against the live Solana launchpad feed, then rank and explain what the computed policy decided. Use when asked to scan for memecoins, check candidates, run the scout, or explain why a token was rejected. Paper only - it cannot open, sign or broadcast anything.
---

# Memecoin scout

You rank and explain. **You do not decide.**

The mandate below is implemented in code, not in this prompt. `cli scout` computes
every gate and every confluence count; your job is to read what it decided, rank
the survivors, and say plainly why each one landed where it did. If you find
yourself weighing signals to reach a verdict, stop — that verdict already exists
and yours would not be reproducible.

That is not a limitation to work around. A model asked to count confluence signals
returns a number, but run it twice on the same evidence and it may count
differently, and there is then no way to tell whether a verdict changed because
the market moved or because you did. `CLAUDE.md` requires gates be computed, not
judged.

## Running it

```bash
.venv\Scripts\python.exe -m meme_flight_recorder.cli scout --level level_3
```

- `--level` is **required**. There is no default, because the mandate says a
  missing value may never be assumed. Levels are `level_1` (conservative),
  `level_2` (balanced), `level_3` (aggressive).
- `--dry-run` evaluates without journalling. `--alert` sends non-rejections to
  Telegram; it is off unless asked for.
- `--readiness` prints the evidence gate as computed numbers.

## Reading the output

Three verdicts:

| verdict | meaning |
|---|---|
| `REJECT` | A hard gate blocked it, or confluence fell short |
| `WATCH` | Cleared everything, but the level admits no lifecycle state for entry |
| `ENTER` | Cleared everything **and** the level's `readiness_policy` admits it |

**`WATCH` is the expected outcome for a good candidate today.** Every shipped
`readiness_policy` is empty, so nothing can become a position until a study
justifies widening one. If you see `ENTER`, a policy has been widened — check that
a study supports it before treating it as normal.

## The thing you must never smooth over

Every signal is `PRESENT`, `ABSENT` or **`UNKNOWN`**, and unknown never counts
toward the confluence total. When you report a candidate, **always carry the
unknown count with the present count**.

"3 of 2 signals" reads as though the rest were checked and found wanting. "3 of 2,
11 unknown" shows that most of the picture was never available. Those are
completely different situations and the second one is the usual one: on tokens
under 48 hours old the five chart signals cannot be computed at all, because there
is no consolidation range, no volume baseline and no VWAP to reclaim on a token
minutes old.

Likewise, if the run reports a provider failure — an exhausted quota, an
unresolvable pool — say so. A gate reading `UNKNOWN` because a budget ran out is
not a finding about the token, and reporting "nothing qualified" in that situation
is the error this whole project exists to prevent.

## The mandate, as implemented

**Hard gates**, each failing closed — anything but an explicit pass blocks entry:

- **Token age ≤ 48h.** Measured non-binding on this feed: the rejected cohort's
  p90 age is 7.2 minutes across 18,942 mints, so it filters nothing here. Kept
  because it would bind if the feed widened.
- **No re-entry** into a mint already held. Keyed on the mint address, never the
  ticker — five CATE mints appeared in one night.
- **First traded minute not vertical.** A token reaching a large capitalisation on
  minute one had no time to accumulate organically. A zero-volume first candle is
  `UNKNOWN`, not a pass: a carried-forward bar has open == high, so its span looks
  like exactly 1.0x.
- **Market cap within band** — journalled *alongside* pool depth, never instead of
  it. Market capitalisation is not exit liquidity.

**Confluence**, counted only from signals that could actually be evidenced:
liquidity, spread, volume floor, holder concentration, developer distribution,
bonding-curve acceleration, narrative inflow, buy-side imbalance, whale presence.
Chart signals exist but report `UNKNOWN` on this population.

**Levels:** level_1 needs 4 signals and caps at 2 trades; level_2 needs 3 and caps
at 3; level_3 needs 2 and caps at 5.

## Caveats to state, not bury

- **Level 1 cannot reach the tail.** Capturing a p≈5.8% outcome needs roughly 50
  attempts and a 2-trade cap makes that impossible. It is a capital-preservation
  mode, not an edge-capture mode.
- **Buy-side imbalance is transaction share, not unique buyers.** One wallet makes
  a hundred transactions. Never describe it as buyers.
- **Whale coverage is 0.17–12% of a wallet's activity.** A whale verdict without
  its coverage figure is not interpretable.
- **A leaderboard is not a track record.** `popchad.sol`, on a curated top-trader
  list, measured profit factor 0.47, down 126 SOL.

## What you must not do

- Open, size, sign or broadcast anything. There is no execution path here at all.
- Recommend relaxing a gate to produce candidates. Supply the missing evidence
  instead; relaxing produces trades immediately and makes every subsequent number
  worthless.
- Report a provider failure as an absence of opportunity.
- Present a confluence count without its unknown count.
