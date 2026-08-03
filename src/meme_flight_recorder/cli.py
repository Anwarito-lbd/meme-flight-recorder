from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .config import load_settings
from .journal import FlightRecorder


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper-only meme-coin flight recorder")
    parser.add_argument("--config", default="config/default.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    sub.add_parser("health")
    show = sub.add_parser("events")
    show.add_argument("--limit", type=int, default=20)
    collect = sub.add_parser(
        "collect", help="Poll the discovery feed and journal every candidate."
    )
    collect.add_argument("--interval", type=int, default=300)
    collect.add_argument("--limit", type=int, default=50)
    collect.add_argument(
        "--cycles", type=int, default=None, help="Stop after N cycles (default: run forever)."
    )
    collect.add_argument("--stages", default="new,finalizing,migrated")
    collect.add_argument(
        "--source",
        choices=["launchpad", "movers", "both"],
        default="launchpad",
        help=(
            "Which population to poll. 'movers' adds established trending pools, "
            "which is where the deep-pool filter actually finds candidates -- "
            "launchpad newborns had a median pool of $26."
        ),
    )
    collect.add_argument("--no-enrich", action="store_true")
    collect.add_argument(
        "--allow-sleep",
        action="store_true",
        help="Do not suppress system sleep (the host may then miss cycles).",
    )
    collect.add_argument(
        "--delay",
        type=float,
        default=None,
        help="Seconds between candidates. Paces router calls under its rate limit.",
    )
    collect.add_argument(
        "--paper-trade",
        action="store_true",
        help=(
            "Open and manage PAPER positions on candidates that pass the gates "
            "and the deep-pool filter. Writes journal rows only; it cannot sign "
            "or broadcast anything."
        ),
    )
    collect.add_argument(
        "--minimum-pool-usd",
        type=float,
        default=50_000.0,
        help="Pool depth required to open a paper position (default: 50000).",
    )
    collect.add_argument(
        "--max-open",
        type=int,
        default=10,
        help="Maximum concurrent paper positions (default: 10).",
    )
    sub.add_parser(
        "calibrate",
        help="Did the gates and the confidence score predict anything?",
    )
    score = sub.add_parser(
        "score-sources", help="Rank sources by post-call expectancy after costs."
    )
    score.add_argument("calls_csv", help="Manual export: source,author,mint,called_at[,text,url]")
    score.add_argument("--events", type=int, default=200_000)
    score.add_argument("--min-sample", type=int, default=10)
    score.add_argument("--horizon", default="1h", choices=["30s", "5m", "1h", "1d"])
    score.add_argument("--cost-pct", type=float, default=6.0)
    args = parser.parse_args()

    settings = load_settings(args.config)
    recorder = FlightRecorder(settings.database_path)
    if args.command == "init-db":
        result = {"database": str(settings.database_path), "created": True}
    elif args.command == "health":
        result = {
            "execution_mode": settings.execution_mode,
            "journal_valid": recorder.verify_chain(),
            "settings": asdict(settings),
        }
    elif args.command == "collect":
        return _collect(settings, recorder, args)
    elif args.command == "score-sources":
        return _score_sources(recorder, args)
    elif args.command == "calibrate":
        return _calibrate(recorder)
    else:
        result = recorder.list_events(args.limit)
    print(json.dumps(result, indent=2, default=str))
    return 0


def _calibrate(recorder: FlightRecorder) -> int:
    """Check whether the system's own judgement has predicted anything."""
    from .calibration import (
        confidence_report,
        gate_report,
        group_by,
        outcomes_from_events,
    )
    from .providers.http import get_json

    events = recorder.events_by_type("candidate_observed")
    if not events:
        print("No observations journalled yet. Run 'collect' first.")
        return 1

    mints = list(dict.fromkeys(event["entity_id"] for event in events))
    prices: dict[str, float] = {}
    for index in range(0, len(mints), 25):
        batch = mints[index : index + 25]
        try:
            payload = get_json(
                "https://api.dexscreener.com", f"/latest/dex/tokens/{','.join(batch)}"
            )
        except Exception as error:  # noqa: BLE001
            # A failed batch is missing data, not a zero outcome. Reporting it
            # keeps a silent price-lookup failure from looking like tokens that
            # simply had no result.
            print(f"  price batch {index // 25} unavailable: {type(error).__name__}")
            continue
        for pair in payload.get("pairs") or []:
            address = ((pair.get("baseToken") or {}).get("address")) or ""
            price = pair.get("priceUsd")
            if address and price is not None:
                prices[address] = float(price)

    outcomes = outcomes_from_events(events, prices)
    print(f"{len(mints)} mints observed, {len(outcomes)} with a measurable outcome\n")
    if not outcomes:
        print("Nothing measurable yet.")
        return 0

    def show(title: str, groups) -> None:
        print(title)
        header = f"  {'group':<26} {'n':>5} {'median':>8} {'dead':>6} {'>2x':>5}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for stats in groups:
            if stats.count == 0:
                continue
            flag = "" if stats.reliable else "  (small)"
            print(
                f"  {stats.label:<26} {stats.count:>5} {stats.median_multiple:>8.2f} "
                f"{stats.dead_pct:>5.0f}% {stats.winner_pct:>4.0f}%{flag}"
            )
        print()

    show("by gate outcome", list(group_by(outcomes, "status").values()))
    show("by cluster verdict", list(group_by(outcomes, "cluster_verdict").values()))
    show("by confidence band", list(group_by(outcomes, "confidence_band").values()))

    for label, result in (
        ("gates", gate_report(outcomes)),
        ("confidence", confidence_report(outcomes)),
    ):
        print(f"{label}: {result.verdict}")
        for note in result.notes:
            print(f"  note: {note}")

    print(
        "\nMedians, not means: one 500x drags a mean anywhere.\n"
        "'not_yet_distinguishable' is the expected answer until the sample grows,\n"
        "and position size must not scale with confidence until it separates."
    )
    return 0


def _score_sources(recorder: FlightRecorder, args) -> int:
    """Rank sources worst first, so the ones to drop are at the top."""
    from .sources import load_calls_csv, score_all

    calls = load_calls_csv(args.calls_csv)
    if not calls:
        print("No usable calls found. Rows need author, called_at, and a mint address.")
        return 1

    events = recorder.events_by_type("candidate_observed", args.events)
    if not events:
        print("No observations journalled yet. Run 'collect' first and let it gather data.")
        return 1

    scores = score_all(
        calls,
        events,
        minimum_sample=args.min_sample,
        horizon=args.horizon,
        round_trip_cost_pct=args.cost_pct,
    )

    print(
        f"{len(calls)} calls against {len(events)} observations, "
        f"net of {args.cost_pct}% round-trip cost, at {args.horizon}\n"
    )
    header = f"{'source':<28} {'calls':>6} {'meas':>6} {'net%':>9} {'win%':>7}  role"
    print(header)
    print("-" * len(header))
    for score in scores:
        net = score.mean_net_pct.get(args.horizon)
        win = score.win_rate_pct.get(args.horizon)
        net_text = "--" if net is None else f"{net:.2f}"
        win_text = "--" if win is None else f"{win:.1f}"
        print(
            f"{score.identity:<28} {score.calls:>6} {score.measurable:>6} "
            f"{net_text:>9} {win_text:>7}  {score.role.value}"
        )
        for note in score.notes:
            print(f"{'':<28} note: {note}")
    print("\nWorst first. UNPROVEN means too few measurable calls to judge, not neutral.")
    return 0


def _collect(settings, recorder: FlightRecorder, args) -> int:
    """Run the capture loop, wiring only the providers that are available."""
    from .collector import Collector, CollectorConfig
    from .providers.dexscreener import DexScreenerProvider
    from .providers.helius import HeliusProvider
    from .providers.jupiter import JupiterQuoteProvider

    mint_provider = None
    quote_provider = None
    pair_provider = None
    if not args.no_enrich:
        quote_provider = JupiterQuoteProvider()
        pair_provider = DexScreenerProvider()
        try:
            mint_provider = HeliusProvider()
        except ValueError as error:
            # Degrade rather than guess: without a Solana RPC the identity and
            # authority fields stay unknown and keep failing closed.
            print(f"note: Helius unavailable ({error}); authority evidence stays unknown.")

    monitor = None
    if getattr(args, "paper_trade", False):
        from .monitor import MonitorConfig, PositionMonitor

        if pair_provider is None:
            # Without a pair provider an open position cannot be observed, and
            # an unobservable position is one the exit engine must close. Opening
            # positions that are stale from birth would manufacture a track
            # record of forced exits rather than measure anything.
            print("error: --paper-trade requires enrichment; drop --no-enrich.")
            return 2
        monitor = PositionMonitor(
            settings,
            recorder,
            pair_provider,
            config=MonitorConfig(
                minimum_pool_liquidity_usd=args.minimum_pool_usd,
                maximum_open_positions=args.max_open,
            ),
        )
        print(
            f"paper trading ON: pool >= ${args.minimum_pool_usd:,.0f}, "
            f"max {args.max_open} open. No key is loaded and nothing can be signed."
        )

    movers_provider = None
    if args.source in ("movers", "both"):
        from .providers.coingecko import CoinGeckoProvider

        movers_provider = CoinGeckoProvider()
        print(f"discovery: {args.source} (established trending pools included)")

    stages = () if args.source == "movers" else tuple(
        stage.strip() for stage in args.stages.split(",") if stage.strip()
    )
    collector = Collector(
        settings,
        recorder,
        mint_provider=mint_provider,
        quote_provider=quote_provider,
        pair_provider=pair_provider,
        monitor=monitor,
        movers_provider=movers_provider,
        config=CollectorConfig(
            stages=stages,
            limit_per_stage=args.limit,
            interval_seconds=args.interval,
            enrich=not args.no_enrich,
            **({} if args.delay is None else {"per_candidate_delay_seconds": args.delay}),
        ),
    )

    # A cycle grades every candidate through six network calls each and takes
    # well over a minute, during which the process would otherwise print
    # nothing at all. For a tool meant to be left running unattended, silence
    # at startup is indistinguishable from a hang.
    expected = len(stages) * args.limit
    print(
        f"Collector started: {expected} candidates per cycle "
        f"({', '.join(stages)}), every {args.interval}s.\n"
        f"Journal: {settings.database_path}\n"
        f"Enrichment: {'on' if not args.no_enrich else 'off'}. "
        f"Paper only; this process cannot sign or broadcast.\n"
        f"First cycle takes roughly {max(1, round(expected * 1.7 / 60))} min. "
        f"Ctrl+C to stop.\n",
        flush=True,
    )

    def report(summary) -> None:
        top = sorted(summary.failure_counts.items(), key=lambda item: -item[1])[:3]
        reasons = ", ".join(f"{name}={count}" for name, count in top) or "none"
        print(
            f"[{summary.started_at:%Y-%m-%d %H:%M:%S}] "
            f"observed={summary.observed} recorded={summary.recorded} "
            f"eligible={summary.eligible} monitor={summary.monitor} "
            f"rejected={summary.rejected} | top: {reasons}",
            flush=True,
        )
        for error in summary.errors[:3]:
            print(f"  error: {error}", flush=True)

    from .keepawake import KeepAwake

    # The first unattended run journalled 9 cycles instead of 96 because the
    # host slept between cycles. Nothing errored, which is exactly why it has
    # to be handled rather than noticed.
    with KeepAwake(enabled=not args.allow_sleep) as awake:
        print(f"Power: {awake.status}.\n", flush=True)
        try:
            collector.run_forever(cycles=args.cycles, on_cycle=report)
        except KeyboardInterrupt:
            print("\nstopped; journal is intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
