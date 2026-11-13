#!/usr/bin/env python3
"""The weekly ritual, in one command.

Read-only. Prints everything the promotion policy needs to be checked against,
in the order the policy names it: provider health, the evidence gate dials,
and the sample sizes of the two new-signal studies that must pass before live.
Nothing here mutates state or spends API budget beyond public reads.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from meme_flight_recorder.env import load_env


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    load_env()
    db_path = ROOT / "data" / "flight_recorder.db"

    section("provider health")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight.py")], check=False
    )

    section("evidence gate — dial 1")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report_track_record.py")], check=False
    )

    section("new-signal samples — dial 2")
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    def count(event_type: str) -> int:
        return cur.execute(
            "SELECT count(*) FROM events WHERE event_type=?", (event_type,)
        ).fetchone()[0]

    observed = count("candidate_observed")
    scout = count("scout_candidate_evaluated")
    mentions = count("media_mention")
    print(f"  candidate observations : {observed:>8}")
    print(f"  scout verdicts         : {scout:>8}")
    print(f"  media mentions         : {mentions:>8}")

    # M1 arms: distinct mints mentioned, vs distinct mints ever observed.
    mentioned = cur.execute(
        "SELECT count(DISTINCT entity_id) FROM events "
        "WHERE event_type='media_mention' AND entity_id IN "
        "(SELECT DISTINCT entity_id FROM events WHERE event_type='candidate_observed')"
    ).fetchone()[0]
    print(f"  mentioned-and-observed: {mentioned:>8}  (M1 discovery-lead pairs)")

    # W1 arms: how much wallet history exists for tracked-trader analysis.
    try:
        wallet_db = ROOT / "data" / "wallet-history"
        wallets = (
            len([d for d in wallet_db.iterdir() if d.is_dir()])
            if wallet_db.exists()
            else 0
        )
        pages = len(list(wallet_db.glob("*/*.json"))) if wallet_db.exists() else 0
        print(
            f"  wallet history         : {pages:>4} pages across {wallets} wallets "
            f"(~{pages * 100} txs; W1 needs tracked-trader swaps)"
        )
    except Exception as error:  # noqa: BLE001 - inventory is informational
        print(f"  wallet cache unreadable: {type(error).__name__}: {error}")
    con.close()

    section("checkpoints (promotion-policy.md)")
    print("  n=30 -> read expectancy/PF above against the gate lines")
    print("  n=50 -> if still negative AND no signal passed: population/capital/stop")
    print("\nWeekly ritual complete. Close the terminal; the loop keeps running.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
