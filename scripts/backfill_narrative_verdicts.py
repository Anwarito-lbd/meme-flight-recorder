#!/usr/bin/env python3
"""Run the local model over every journalled narrative text, once, into a cache.

`study_virality_value.py` has three arms. Two are arithmetic -- follower count
and whether a handle exists -- and both returned UNPROVEN on the first run, with
the follower threshold inverting between the development and held-out halves.

The third arm is the operator's actual thesis: that a well-prompted model can
read virality out of the text where a plain number cannot. It could not be
measured, because `model_says_accelerating` is journalled on
`scout_candidate_evaluated` while the social fields are journalled on
`candidate_observed`, so the two never meet on the same mint. Only 28 scout
decisions exist against 2,150 mints carrying narrative text.

This script closes that gap offline. It reads the stored text, asks the local
model the same two questions the scout asks live, and caches the answers so the
study can join them by mint.

Three rules, the same ones the other backfills in this directory protect:

  * **The journal is not touched.** It is append-only, hash-chained, and holds
    decisions. These are derived readings of data already in it, so they live in
    their own database and can be deleted and rebuilt at will.
  * **Absent is not zero.** A model that would not answer is stored as `null`,
    distinguishable from a model that answered "not spreading". "The model
    declined" and "the model said no" are different facts and must not merge.
  * **The model identity is stored on every row.** A verdict from `qwen2.5:3b` is
    not a verdict from something else, and a study that mixes them silently is
    comparing two different instruments. Changing the model means the affected
    rows are stale, and this records which they are.

Costs no API budget: the model runs on this host. Resumable -- rows already
present are skipped, so it can be interrupted and restarted freely.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.providers.news import (
    DEFAULT_OLLAMA_MODEL,
    available_backends,
    classify,
    judge_acceleration,
)

CACHE_PATH = Path("data/narrative_verdicts.db")


def open_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        create table if not exists verdicts (
            mint text primary key,
            symbol text,
            narrative_text text not null,
            sentiment text not null,
            -- 1, 0, or NULL. NULL means the model would not answer, which is
            -- not the same as answering "flat" and must never collapse to it.
            accelerating integer,
            model text not null,
            computed_at text not null
        );
        """
    )
    connection.commit()
    return connection


def pending_mints(database_path: str, cache: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(mint, symbol, text) for every journalled mint not yet read by the model.

    First observation per mint only. A mint appears in the journal many times and
    its narrative text does not change, so re-reading it would multiply cost for
    an identical answer.
    """
    done = {row[0] for row in cache.execute("select mint from verdicts")}
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    seen: dict[str, tuple[str, str, str]] = {}
    try:
        rows = connection.execute(
            "SELECT entity_id, payload_json FROM events "
            "WHERE event_type = 'candidate_observed' ORDER BY id"
        )
        for mint, payload_json in rows:
            if mint in seen or mint in done:
                continue
            payload = json.loads(payload_json)
            text = payload.get("narrative_text_en")
            if not text:
                continue
            seen[mint] = (mint, str(payload.get("symbol") or ""), str(text))
    finally:
        connection.close()
    return list(seen.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="stop after N mints (0 = all)")
    parser.add_argument("--model", default=None, help="override OLLAMA_MODEL")
    parser.add_argument(
        "--cache", default=str(CACHE_PATH), help="cache database path"
    )
    args = parser.parse_args()

    # The backend is forced on rather than read from the environment. This script
    # exists to run the model; silently doing nothing because
    # SCOUT_SENTIMENT_BACKEND is unset would produce an empty cache that looks
    # like "the model had no opinion about anything".
    os.environ["SCOUT_SENTIMENT_BACKEND"] = "ollama"
    model = args.model or os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL

    backends = available_backends()
    if not backends.get("ollama"):
        print("Ollama is not answering, or has no models pulled.")
        print("  Start it and run:  ollama pull " + DEFAULT_OLLAMA_MODEL)
        print("An unreachable model is UNKNOWN, not a neutral reading, so this")
        print("script stops rather than writing 2,000 rows of nothing.")
        return 1

    settings = load_settings()
    cache = open_cache(Path(args.cache))
    pending = pending_mints(str(settings.database_path), cache)
    if args.limit:
        pending = pending[: args.limit]

    already = cache.execute("select count(*) from verdicts").fetchone()[0]
    print(f"\nmodel:   {model}")
    print(f"cache:   {args.cache} ({already:,} already read)")
    print(f"pending: {len(pending):,} mints\n")
    if not pending:
        print("Nothing to do.\n")
        return 0

    started = time.monotonic()
    written = 0
    for index, (mint, symbol, text) in enumerate(pending, start=1):
        reading = classify(symbol, [text], model=model)
        accelerating = judge_acceleration(symbol, text, model=model)
        cache.execute(
            "insert or replace into verdicts"
            " (mint, symbol, narrative_text, sentiment, accelerating, model, computed_at)"
            " values (?, ?, ?, ?, ?, ?, ?)",
            (
                mint,
                symbol,
                text,
                reading.sentiment.value,
                None if accelerating is None else int(accelerating),
                model,
                datetime.now(UTC).isoformat(),
            ),
        )
        written += 1
        if index % 25 == 0:
            cache.commit()
            elapsed = time.monotonic() - started
            rate = elapsed / index
            remaining = (len(pending) - index) * rate
            print(
                f"  {index:>6,}/{len(pending):,}  {rate:.1f}s/mint"
                f"  ~{remaining / 60:.0f} min left"
            )
    cache.commit()

    counts = dict(
        cache.execute("select sentiment, count(*) from verdicts group by sentiment")
    )
    accel = dict(
        cache.execute(
            "select case when accelerating is null then 'declined'"
            " when accelerating = 1 then 'spreading' else 'flat' end,"
            " count(*) from verdicts group by 1"
        )
    )
    total = cache.execute("select count(*) from verdicts").fetchone()[0]
    print(f"\nwrote {written:,} verdicts; cache now holds {total:,}\n")
    print(f"  sentiment:    {counts}")
    print(f"  acceleration: {accel}")
    print(
        f"  buckets sum:  {sum(accel.values()):,} of {total:,}"
        f"  {'OK' if sum(accel.values()) == total else 'DOES NOT RECONCILE'}\n"
    )
    cache.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
