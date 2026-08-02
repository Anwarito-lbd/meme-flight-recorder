"""Fail a research run when look-ahead analysis is missing, biased, or inconclusive."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def validate(csv_path: Path, log_path: Path, outcome: str, minimum_signals: int) -> None:
    if outcome != "success":
        raise ValueError(f"look-ahead command outcome was {outcome!r}")
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        raise ValueError("look-ahead CSV is missing")
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("look-ahead CSV contains no strategy result")
    for row in rows:
        normalized = {key.strip().lower(): str(value).strip().lower() for key, value in row.items()}
        if normalized.get("has_bias") in {"true", "1", "yes"}:
            raise ValueError("look-ahead bias detected")
        signal_text = normalized.get("total_signals") or normalized.get("total signals")
        if signal_text is not None and int(float(signal_text)) < minimum_signals:
            raise ValueError(f"only {signal_text} signals; analysis is inconclusive")
    log = log_path.read_text(errors="replace").lower() if log_path.exists() else ""
    inconclusive_phrases = ("too few trades", "not enough trades", "no trades")
    if any(phrase in log for phrase in inconclusive_phrases):
        raise ValueError("look-ahead log reports insufficient trades")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--minimum-signals", type=int, default=10)
    args = parser.parse_args()
    validate(args.csv, args.log, args.outcome, args.minimum_signals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
