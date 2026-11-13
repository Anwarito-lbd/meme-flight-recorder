#!/usr/bin/env python3
"""Split every rejection into "we measured something bad" and "we could not find out".

Both look identical in a failure histogram, and only the first is a judgement
about a token. The second is a provider outage recorded as if it were risk. This
project has already paid for that confusion once: of 56 tokens that went on to
double or better, 13 were rejected *purely* on missing evidence -- route, impact
and liquidity unknown -- which is a quarter of the winners lost to an exhausted
API quota rather than to anything about the tokens.

The question this answers is how much of the rejection mass is recoverable
without touching a single threshold.

Decision rule, fixed before the numbers were seen:

  * A reason is MISSING if it names an absence -- the `*_unknown` suffix,
    `cluster_evidence_insufficient`, or `identity_unverified`. Everything else is
    MEASURED. The rule is mechanical rather than curated so it cannot be tuned
    per reason after the fact, and any new reason is classified by its own name.
  * A candidate is `missing_only` when it carries at least one failure and every
    one of them is MISSING. That is the recoverable set: supply the evidence and
    the rejection may or may not survive, but today it was never a decision.
  * A candidate carrying any MEASURED failure is `measured` regardless of how
    many MISSING ones sit beside it. A real finding is not cancelled by a gap.
  * Every observation lands in exactly one bucket and the buckets reconcile to
    the total. A study that reports only its measured subset can turn missing
    data into a finding, which is the error being measured here.

Read-only. Reads the journal and nothing else, so it costs no API budget and can
be re-run as often as it is useful.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict

from meme_flight_recorder.config import load_settings

# A reason names an absence rather than a finding. Mechanical on purpose: a
# hand-kept list of "missing" reasons would drift the moment someone adds a gate.
MISSING_SUFFIX = "_unknown"
MISSING_EXACT = frozenset(
    {
        # The cluster engine says outright that it had too little to judge on.
        "cluster_evidence_insufficient",
        # The mint could not be verified against chain -- an absence, not a
        # finding about the token.
        "identity_unverified",
    }
)


def is_missing(reason: str) -> bool:
    """Whether a failure reason names an absence of evidence."""
    return reason.endswith(MISSING_SUFFIX) or reason in MISSING_EXACT


def load_observations(database_path: str) -> list[tuple[str, str, tuple[str, ...]]]:
    """Every journalled observation as (mint, status, failures). Read-only."""
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT entity_id, payload_json FROM events WHERE event_type = 'candidate_observed'"
        ).fetchall()
    finally:
        connection.close()

    observations = []
    for entity_id, payload_json in rows:
        payload = json.loads(payload_json)
        failures = payload.get("failures") or []
        observations.append(
            (
                str(payload.get("mint") or entity_id),
                str(payload.get("status") or "unknown"),
                tuple(str(f) for f in failures),
            )
        )
    return observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--by-mint",
        action="store_true",
        help="collapse repeated observations of the same mint to its worst case",
    )
    args = parser.parse_args()

    settings = load_settings()
    observations = load_observations(str(settings.database_path))
    if not observations:
        print("No observations journalled. Nothing to split.")
        return 1

    if args.by_mint:
        # Worst case per mint: any measured failure anywhere makes the mint
        # measured. Collapsing to "best" would let one clean observation erase a
        # real finding from another.
        worst: dict[str, tuple[str, tuple[str, ...]]] = {}
        for mint, status, failures in observations:
            current = worst.get(mint)
            if current is None or (
                any(not is_missing(f) for f in failures)
                and not any(not is_missing(f) for f in current[1])
            ):
                worst[mint] = (status, failures)
        units = [(mint, status, failures) for mint, (status, failures) in worst.items()]
        unit_name = "mints"
    else:
        units = observations
        unit_name = "observations"

    buckets: Counter[str] = Counter()
    missing_only_reasons: Counter[str] = Counter()
    reason_kind: dict[str, str] = {}
    reason_totals: Counter[str] = Counter()
    per_status: dict[str, Counter[str]] = defaultdict(Counter)

    for _mint, status, failures in units:
        for reason in failures:
            reason_totals[reason] += 1
            reason_kind[reason] = "missing" if is_missing(reason) else "measured"

        if not failures:
            bucket = "no_failures"
        elif all(is_missing(reason) for reason in failures):
            bucket = "missing_only"
            for reason in failures:
                missing_only_reasons[reason] += 1
        elif any(is_missing(reason) for reason in failures):
            bucket = "measured_plus_missing"
        else:
            bucket = "measured_only"

        buckets[bucket] += 1
        per_status[status][bucket] += 1

    total = len(units)
    print(f"\n=== rejection split over {total:,} {unit_name} ===\n")

    order = ("no_failures", "missing_only", "measured_plus_missing", "measured_only")
    labels = {
        "no_failures": "cleared every gate",
        "missing_only": "rejected ONLY on missing evidence",
        "measured_plus_missing": "measured failure, plus gaps",
        "measured_only": "measured failure only",
    }
    for bucket in order:
        count = buckets[bucket]
        print(f"  {labels[bucket]:<38}{count:>8,}  ({100 * count / total:5.2f}%)")

    reconciled = sum(buckets[b] for b in order)
    print(f"\n  {'buckets sum':<38}{reconciled:>8,}  of {total:,}")
    if reconciled != total:
        print("  DENOMINATOR DOES NOT RECONCILE -- the numbers above cannot be trusted.")
        return 1

    rejected = total - buckets["no_failures"]
    if rejected:
        share = 100 * buckets["missing_only"] / rejected
        print(
            f"\n  Of {rejected:,} rejected {unit_name}, "
            f"{buckets['missing_only']:,} ({share:.2f}%) were never a decision about"
            f"\n  the token at all -- every failure they carry is an absence of evidence."
        )

    print("\n=== which gap does the recovering ===\n")
    print("  Reasons carried by candidates rejected ONLY on missing evidence.")
    print("  Fixing the top of this list is what moves recall without moving a threshold.\n")
    for reason, count in missing_only_reasons.most_common(12):
        print(f"  {reason:<44}{count:>8,}")
    if not missing_only_reasons:
        print("  none")

    print("\n=== every reason, classified ===\n")
    print(f"  {'reason':<44}{'kind':>10}{'count':>10}")
    for reason, count in reason_totals.most_common():
        print(f"  {reason:<44}{reason_kind[reason]:>10}{count:>10,}")

    print("\n=== by recorded status ===\n")
    print(f"  {'status':<22}{'cleared':>10}{'missing':>10}{'mixed':>10}{'measured':>10}")
    for status in sorted(per_status, key=lambda s: -sum(per_status[s].values())):
        counts = per_status[status]
        print(
            f"  {status:<22}{counts['no_failures']:>10,}{counts['missing_only']:>10,}"
            f"{counts['measured_plus_missing']:>10,}{counts['measured_only']:>10,}"
        )

    print(
        "\nMissing evidence is not a verdict. Supply it from chain or a second"
        "\nprovider; do not relax the gate that reported it.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
