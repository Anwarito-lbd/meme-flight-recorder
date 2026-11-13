#!/usr/bin/env python3
"""Does the organic-flow verdict predict anything?

Stage 6 exists because the safety gates measure risk and were shown not to
predict return. Adding a second gate that also predicts nothing would be worse
than adding none, because it would look like progress. So the verdict is
measured before it is trusted, exactly as the age-survival hypothesis was
measured and then withdrawn.

**The decision rule is fixed here, in writing, before any number is seen:** if
ORGANIC candidates do not show materially better forward outcomes than
SUSPICIOUS ones, the gate is recorded as unproven and is not wired into entry.
It stays journalled. A gate is earned, not assumed.

**No look-ahead.** The verdict is formed from the first ``--decision-window``
observations only. The outcome is measured from the price at the *end* of that
window forward to now. The observations that produced the verdict are never
part of the return they are being judged on.

**The denominator is printed.** Every mint is accounted for: too few
observations, no usable price, no current price resolved, or measured. A study
that reports only its measured subset can turn "no data" into a finding, which
has already happened once in this project and produced a fabricated result.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

from meme_flight_recorder.config import FlowLimits, load_settings
from meme_flight_recorder.flow import FlowVerdict, assess_flow, observations_from_events
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.dexscreener import DexScreenerProvider

VERDICT_ORDER: tuple[FlowVerdict, ...] = (
    FlowVerdict.ORGANIC,
    FlowVerdict.INSUFFICIENT_EVIDENCE,
    FlowVerdict.SUSPICIOUS,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decision-window",
        type=int,
        default=3,
        help="Observations used to form the verdict. The rest are the outcome.",
    )
    parser.add_argument("--dead-below", type=float, default=0.10, help="Multiple counted as dead.")
    arguments = parser.parse_args()

    if arguments.decision_window < 2:
        print("A decision window under 2 observations cannot express a change.")
        return 1

    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled yet.")
        return 1

    histories = observations_from_events(events)
    limits: FlowLimits = settings.flow

    # Every mint lands in exactly one bucket below. These must sum to the total.
    too_few: list[str] = []
    no_entry_price: list[str] = []
    verdicts: dict[str, FlowVerdict] = {}
    entry_prices: dict[str, float] = {}

    for mint, history in histories.items():
        if len(history) < arguments.decision_window:
            too_few.append(mint)
            continue
        window = history[: arguments.decision_window]
        # The last observation in the window is the moment a trader could have
        # acted on the verdict, so it is the entry price.
        entry = window[-1].price_usd
        if not entry or entry <= 0:
            no_entry_price.append(mint)
            continue
        verdicts[mint] = assess_flow(window, limits).verdict
        entry_prices[mint] = entry

    print(f"{len(histories)} distinct mints journalled")
    print(f"  {len(too_few)} with fewer than {arguments.decision_window} observations")
    print(f"  {len(no_entry_price)} with no usable price at the decision point")
    print(f"  {len(verdicts)} with a verdict, awaiting outcome lookup\n")

    # Which inputs actually existed. Without this, an empty verdict class reads
    # as "nothing was suspicious" when it may mean "nothing could be checked" --
    # a distinction that has already produced one fabricated finding in this
    # project, where a timeframe sweep reported no trades because the provider
    # was silently returning no candles.
    window_readings = [
        reading
        for mint in verdicts
        for reading in histories[mint][: arguments.decision_window]
    ]
    coverage = {
        field: sum(1 for reading in window_readings if getattr(reading, field) is not None)
        for field in (
            "holders",
            "liquidity_usd",
            "volume_5m_usd",
            "volume_to_liquidity_5m",
            "buy_txns_5m",
        )
    }
    print(f"input coverage across {len(window_readings)} decision-window observations")
    for field, present in coverage.items():
        share = 100.0 * present / len(window_readings) if window_readings else 0.0
        print(f"  {field:<24} {present:>5} ({share:>3.0f}%)")

    wash_inputs = coverage["volume_5m_usd"] + coverage["volume_to_liquidity_5m"]
    if wash_inputs == 0:
        print(
            "\n  WARNING: no volume data in this sample, so every wash-trading flag\n"
            "  was untestable. A SUSPICIOUS count of zero below means 'not checked',\n"
            "  NOT 'nothing suspicious found'. Only the ORGANIC / INSUFFICIENT split\n"
            "  is readable from this run."
        )
    if coverage["buy_txns_5m"] == 0:
        print("  WARNING: no transaction counts, so the buy-share veto never fired.")
    print()

    prices = DexScreenerProvider().prices_for_tokens(list(verdicts))
    unresolved = [mint for mint in verdicts if mint not in prices]
    print(f"resolved current prices for {len(prices)}, unresolved {len(unresolved)}\n")

    by_verdict: dict[FlowVerdict, list[float]] = defaultdict(list)
    for mint, verdict in verdicts.items():
        now = prices.get(mint)
        if not now:
            continue
        by_verdict[verdict].append(now / entry_prices[mint])

    measured = sum(len(values) for values in by_verdict.values())

    reconciled = len(too_few) + len(no_entry_price) + len(unresolved) + measured
    print(f"denominator: {len(too_few)} + {len(no_entry_price)} + {len(unresolved)} + {measured}")
    print(f"           = {reconciled}, total mints {len(histories)}")
    if reconciled != len(histories):
        print("  MISMATCH -- some mints are unaccounted for; do not read the table below.")
        return 1
    print()

    if not measured:
        print("No outcomes could be measured. This is 'no data', not 'no effect'.")
        print("Collect for longer before drawing any conclusion from Stage 6.")
        return 0

    header = f"{'flow verdict':<24} {'n':>4} {'median':>8} {'mean':>9} {'dead':>6} {'>2x':>5}"
    print(header)
    print("-" * len(header))
    for verdict in VERDICT_ORDER:
        values = by_verdict.get(verdict) or []
        if not values:
            print(f"{verdict.value:<24} {0:>4}       --        --     --    --")
            continue
        dead = sum(1 for value in values if value < arguments.dead_below)
        winners = sum(1 for value in values if value >= 2.0)
        print(
            f"{verdict.value:<24} {len(values):>4} {statistics.median(values):>8.2f} "
            f"{statistics.mean(values):>9.2f} "
            f"{100 * dead / len(values):>5.0f}% {100 * winners / len(values):>4.0f}%"
        )

    organic = by_verdict.get(FlowVerdict.ORGANIC) or []
    suspicious = by_verdict.get(FlowVerdict.SUSPICIOUS) or []

    print(f"\n'dead' = fell below {arguments.dead_below:.0%} of the price at the decision point.")
    print("unique buyers were not measurable and are absent from every verdict above.")
    print()

    if len(organic) < 20 or len(suspicious) < 20:
        print("VERDICT: UNPROVEN -- sample too small to separate the groups.")
        print("Stage 6 stays journalled and is not wired into entry.")
        return 0

    if statistics.median(organic) > statistics.median(suspicious):
        print("VERDICT: the ORGANIC group outperformed. Worth wiring in as a veto,")
        print("but re-run this on a larger sample before relying on it.")
    else:
        print("VERDICT: UNPROVEN -- ORGANIC did not beat SUSPICIOUS on forward return.")
        print("Per the rule fixed before this ran, Stage 6 stays journalled and is")
        print("NOT wired into entry. Record the negative result and move on.")
        print()
        print("Note: this is the same shape as the cluster-gate finding, where")
        print("manipulated tokens outperformed clean ones. That did not make the")
        print("cluster gate wrong -- it made it a risk gate. If SUSPICIOUS tokens")
        print("pump here too, the honest reading is that flow is also measuring")
        print("risk, not return, and the return thesis is still missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
