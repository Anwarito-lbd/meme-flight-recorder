# CLAUDE.md

Solana meme-coin research system: observes candidates, grades them through
fail-closed safety gates, journals every decision including rejections, manages
paper positions, and measures whether its own judgement was any good.

**`STATUS.md` is the authoritative handoff. Read it before doing anything.** It
carries current state, measured numbers, the findings that must not be
rediscovered, and open work in priority order. This file holds only what must be
true in every session.

## Hard invariants — do not violate without an explicit instruction

- **Paper only.** Nothing may sign or broadcast a transaction.
  `tests/test_no_live_execution.py` stays green and unmodified.
- **Live execution is gated on evidence**, computed not judged: ≥30 complete
  forward trades, positive expectancy after costs, profit factor >1.2, no single
  trade >⅓ of profit, drawdown within limit. Failing it is never a reason to seek
  a more aggressive strategy.
- **Capital is $10–40 real.** Positions ~10% of equity (~$4). Do not propose
  strategies needing private RPC or Jito tips; they are unreachable at this size.
- **Public information only.** No leaked paid-group calls, hacked accounts, or
  pre-announcement listings.
- **Never commit secrets.** `.env` and `data/` are gitignored. Keys go in `.env`.

## The working standard

This project's value is that its numbers can be trusted. These rules are what
protect that, and each was paid for with a real defect.

**Run every component against real data before reporting it working.** Nine
defects were found this way in one day, four in code written that same day and
already believed correct. Passing tests prove the fixture matches the code, not
that either matches reality.

**Absent is not zero.** A missing value serialises as `None`/`null`, never `0`.
"No data" has been misread as "no effect" three times here — a timeframe sweep
reporting missing candles as "no qualifying setup", a flow study showing zero
suspicious tokens when the inputs were never journalled, and an entry study
reporting EV of +130,273/dollar off a $0.00-liquidity token.

**Every study prints a reconciling denominator.** Every input must land in
exactly one bucket, and the buckets must sum to the total. A study that reports
only its measured subset can turn missing data into a finding.

**Do not generalise from a single case.** Encode a rule only when the pattern
repeats across measured data or a structural mechanism explains it. Several
claims have been withdrawn this way.

**Write the decision rule before seeing the numbers.** Every gate ships with the
study that decides whether it may be trusted, and the threshold is fixed in
advance. Two of three gates built this way returned "unproven" and stayed out of
the entry path. That is the system working.

**Judge a risk gate on risk and a return gate on return** — but "on risk" means
*on death rate*. The cluster gate passed that test; the developer-selling gate
failed it on its own terms and was inverted for weeks. Distinguish the two cases;
do not quote the first as a blanket defence.

**Name a field for what it contains.** Transaction counts are not unique buyers —
one wallet makes a hundred. `unique_buyer_acceleration` is permanently `None`
because the data cannot supply it, and approximating it from counts would be a
weak number wearing a strong name.

**Identity is the mint address, never the ticker.** Five CATE mints and four SAOF
mints appeared in one night. Every join is on mint address.

**Market capitalisation is not exit liquidity.** Size against pool depth and
quoted impact.

**Costs are charged on both legs, always**, from `costs.round_trip_cost`. Never a
flat percentage: fixed network fees do not shrink with the order, and that term
decides whether a sub-dollar position is viable.

**Gates fail closed.** Missing evidence rejects. Do not relax a gate to make
candidates pass — supply the missing evidence from chain instead. Relaxing
produces trades immediately and makes every number after that worthless.

## Commands

```bash
.venv\Scripts\python.exe -m pytest -q                    # 487 tests
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe scripts\preflight.py            # provider health — run first
.venv\Scripts\python.exe -m meme_flight_recorder.cli collect --paper-trade
.venv\Scripts\python.exe scripts\report_track_record.py  # record vs the evidence gate
```

Do not lower `--delay`. Pacing is derived from the provider's published rate
limit; halving it exhausted Jupiter's quota and every candidate then failed
closed on unknown route data.

`scripts/study_*.py` each answer one question and are listed in STATUS.md.

## Conventions

- Python 3.12, ruff, line length 100. Match surrounding style.
- `src/meme_flight_recorder/` is the package; `scripts/` holds read-only studies
  and operational entry points. Studies never mutate state.
- Analysis modules are **pure** — they assess evidence a caller fetched, so they
  test without a network and cannot spend API budget. I/O lives in `providers/`
  and the `*_history` / `*_store` modules.
- The journal is append-only and hash-chained. Positions are reconstructed by
  replay, never mutated. Verify with `journal.verify_chain()`.
- New event types must not reuse an existing name with a different payload shape:
  `paper_position_*` and `cohort_position_*` are deliberately separate ledgers
  because `PaperBroker` replays the former into a different dataclass.
- Comments explain *why*, especially where a threshold came from. A number
  without a justification is a future defect.

## Scope

Foundation, measurement and analysis. Do not add a gate, filter or signal without
the study that measures it. Do not wire an unproven filter into sizing or entry.

Third-party trading bots, MCP servers and repos are untrusted until inspected;
several were evaluated and rejected this way (see STATUS.md). Nothing that
handles a private key gets imported.
