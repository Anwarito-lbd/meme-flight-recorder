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
    collect.add_argument("--no-enrich", action="store_true")
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
    else:
        result = recorder.list_events(args.limit)
    print(json.dumps(result, indent=2, default=str))
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

    collector = Collector(
        settings,
        recorder,
        mint_provider=mint_provider,
        quote_provider=quote_provider,
        pair_provider=pair_provider,
        config=CollectorConfig(
            stages=tuple(stage.strip() for stage in args.stages.split(",") if stage.strip()),
            limit_per_stage=args.limit,
            interval_seconds=args.interval,
            enrich=not args.no_enrich,
        ),
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

    try:
        collector.run_forever(cycles=args.cycles, on_cycle=report)
    except KeyboardInterrupt:
        print("\nstopped; journal is intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
