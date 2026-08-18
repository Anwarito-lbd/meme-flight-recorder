from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

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
        choices=["launchpad", "movers", "topics", "both", "all"],
        default="all",
        help=(
            "Which population to poll. 'all' combines filtered launchpad, "
            "AI hot topics, and established trending pools."
        ),
    )
    collect.add_argument(
        "--filter-rush",
        action="store_true",
        help="Apply server-side dev integrity and wash trading filters to meme-rush.",
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
    collect.add_argument(
        "--take-profit",
        type=float,
        default=2.0,
        help="Take profit multiple (e.g. 2.0 = exit at 2x). Set 0 to disable.",
    )
    collect.add_argument(
        "--stop-loss",
        type=float,
        default=0.0,
        help="Stop loss percent (e.g. 50.0 = exit at -50%%). Set 0 to disable.",
    )
    collect.add_argument(
        "--position-usd",
        type=float,
        default=None,
        help="Fixed dollar position size override (default: 2%% equity sizing).",
    )

    scout = sub.add_parser(
        "scout",
        help="Apply the operator's memecoin mandate to the live feed. Paper only.",
    )
    # No default, and required. The mandate is explicit -- "If level is not
    # specified, ask before trading" -- so a missing level stops the run rather
    # than silently choosing the loosest or the strictest setting.
    scout.add_argument(
        "--level",
        required=True,
        help="Strictness level from [strictness.*] in config, e.g. level_1.",
    )
    scout.add_argument("--limit", type=int, default=50)
    scout.add_argument(
        "--readiness",
        action="store_true",
        help="Print the evidence gate as computed numbers and exit.",
    )
    scout.add_argument(
        "--no-first-candle",
        action="store_true",
        help="Skip Birdeye first-candle resolution. The gate then reports UNKNOWN and blocks.",
    )
    scout.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate and print without appending to the journal.",
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
    elif args.command == "scout":
        return _scout(settings, recorder, args)
    elif args.command == "score-sources":
        return _score_sources(recorder, args)
    elif args.command == "calibrate":
        return _calibrate(recorder)
    else:
        result = recorder.list_events(args.limit)
    print(json.dumps(result, indent=2, default=str))
    return 0


def _scout(settings: Any, recorder: FlightRecorder, args: Any) -> int:
    """Run the operator's mandate against the live launchpad feed.

    Paper only, and there is no execution path here at all -- not a disabled one.
    A candidate that clears every gate becomes a journalled WATCH, because every
    shipped `readiness_policy` is empty and widening one requires a study.
    """
    from .confluence import ConfluenceEvidence
    from .providers.binance_web3 import BinanceWeb3Provider
    from .providers.birdeye import BirdeyeProvider
    from .scout import CandidateEvidence, ScoutVerdict, evaluate, held_mints, journal_decision

    if settings.scout is None:
        print(
            "No [scout.filters] block in config. The mandate does not permit assuming\n"
            "missing values, so the scout will not run on invented parameters."
        )
        return 1
    try:
        level = settings.level(args.level)
    except KeyError as error:
        print(str(error))
        return 1

    if args.readiness:
        return _scout_readiness(settings, recorder, args.level, level)

    filters = settings.scout
    print(f"level={args.level} equity=${settings.starting_equity_usd:.2f}")
    print(
        f"gates: age<={filters.maximum_token_age_hours}h, volume>=${filters.minimum_volume_usd:,.0f},"
        f" mcap ${filters.minimum_market_cap_usd:,.0f}-${filters.maximum_market_cap_usd:,.0f},"
        f" first candle<={filters.maximum_first_candle_multiple}x,"
        f" reentry_forbidden={filters.forbid_reentry}"
    )
    print(
        f"level: {level.minimum_confluence_signals} confluence signals,"
        f" volume expansion >={level.minimum_volume_expansion}x,"
        f" <={level.maximum_trades_per_session} trades/session,"
        f" admits_entry={level.admits_entry}"
    )

    try:
        rows = BinanceWeb3Provider().meme_rush(limit=args.limit)
    except Exception as error:  # noqa: BLE001 - a feed outage is not an empty market
        print(f"discovery feed unavailable: {type(error).__name__}: {error}")
        print("This is a provider failure, not a market with no candidates.")
        return 1

    # The vendor feed does not carry first-candle OHLC, so without this the
    # scam-pump gate reports UNKNOWN on every candidate and blocks the whole run.
    # That is the gate behaving correctly -- and the fix is to supply the missing
    # evidence from a price source rather than to relax the gate, because
    # relaxing produces trades immediately and makes every number after it
    # worthless.
    birdeye = BirdeyeProvider()
    first_candles: dict[str, tuple[float | None, float | None, float | None]] = {}
    # Why every failure is counted and named rather than swallowed. The first
    # version of this loop ended in a bare `except: continue` and reported
    # "resolved 0 of 25", which reads as "these tokens have no candles". The real
    # cause was the provider answering HTTP 400 with
    # {"message":"Compute units usage limit exceeded"} -- an exhausted quota
    # wearing a bad-request status code, which is worse than a 429 because the
    # status invites you to blame your own parameters. A budget failure is not
    # evidence about a token.
    resolve_failures: dict[str, int] = {}
    if birdeye.configured and not args.no_first_candle:
        print(f"\nresolving first traded minute for {len(rows)} mints via Birdeye...")
        for row in rows:
            created = row.created_at
            if created is None:
                resolve_failures["no_created_at"] = resolve_failures.get("no_created_at", 0) + 1
                continue
            start = int(created.timestamp())
            try:
                series = birdeye.candles(row.contract_address, start - 60, start + 1800, "1m")
            except Exception as error:  # noqa: BLE001 - named, counted, never silent
                # `str(HTTPError)` is only "HTTP Error 400: Bad Request"; the
                # actual cause lives in the response body, which is where Birdeye
                # puts "Compute units usage limit exceeded". Reading it is the
                # difference between "quota exhausted" and "malformed request",
                # and those call for opposite responses.
                body = ""
                reader = getattr(error, "read", None)
                if callable(reader):
                    try:
                        body = reader().decode("utf-8", "replace")
                    except Exception:  # noqa: BLE001 - body is best effort
                        body = ""
                text = f"{error} {body}"
                key = (
                    "quota_exhausted"
                    if "Compute units" in text or "usage limit" in text
                    else type(error).__name__
                )
                resolve_failures[key] = resolve_failures.get(key, 0) + 1
                continue
            if series is None:
                resolve_failures["no_series"] = resolve_failures.get("no_series", 0) + 1
                continue
            traded = series.trades_only
            if not traded:
                # Candles exist but none carry volume, so there is no first
                # *traded* minute to measure. Stays UNKNOWN rather than passing.
                resolve_failures["no_traded_candle"] = (
                    resolve_failures.get("no_traded_candle", 0) + 1
                )
                continue
            first = traded[0]
            first_candles[row.contract_address] = (first.open, first.high, first.volume)
        print(f"resolved {len(first_candles)} of {len(rows)}")
        for reason, count in sorted(resolve_failures.items(), key=lambda kv: -kv[1]):
            print(f"  unresolved: {reason:<24}{count:>5}")
        if resolve_failures.get("quota_exhausted"):
            print(
                "  NOTE: the Birdeye quota is exhausted, so the first-candle gate is\n"
                "  UNKNOWN for provider reasons, not because these tokens went vertical.\n"
                "  Every candidate will block. This is the gate working, not a result."
            )
    elif not birdeye.configured:
        print("\nBIRDEYE_API_KEY absent: first-candle gate will report UNKNOWN and block.")

    previously_held = held_mints(recorder)
    print(f"\n{len(rows)} candidates; {len(previously_held)} mints previously held\n")

    verdicts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for row in rows:
        age_hours = None if row.age_minutes is None else row.age_minutes / 60.0
        first_candle = first_candles.get(row.contract_address, (None, None, None))
        evidence = CandidateEvidence(
            mint=row.contract_address,
            symbol=row.symbol,
            age_hours=age_hours,
            market_cap_usd=row.market_cap_usd,
            first_candle_open=first_candle[0],
            first_candle_high=first_candle[1],
            first_candle_volume=first_candle[2],
            lifecycle_state="GRADUATED" if row.migrated else "BONDING",
            confluence=ConfluenceEvidence(
                liquidity_usd=row.liquidity_usd,
                volume_usd=row.volume_usd,
                # Buy share from transaction counts. Named for what it contains:
                # these are transactions, not unique buyers -- one wallet makes a
                # hundred, and approximating unique buyers from counts would be a
                # weak number wearing a strong name.
                buy_transaction_share_pct=_buy_share_pct(row.buy_count, row.sell_count),
            ),
        )
        decision = evaluate(evidence, filters, level, args.level, previously_held)
        verdicts[decision.verdict.value] = verdicts.get(decision.verdict.value, 0) + 1
        for reason in decision.reasons:
            key = reason.split(":")[0] + ":" + reason.split(":")[1] if ":" in reason else reason
            reasons[key] = reasons.get(key, 0) + 1
        if not args.dry_run:
            journal_decision(recorder, decision, filters, level, evidence)
        if decision.verdict is not ScoutVerdict.REJECT:
            print(
                f"  {decision.verdict.value.upper():<6} {row.symbol:<12}"
                f" confluence {decision.confluence.present_count}"
                f"/{level.minimum_confluence_signals}"
                f" ({decision.confluence.unknown_count} unknown)"
            )

    print("\n=== verdict reconciliation ===")
    for verdict, count in sorted(verdicts.items()):
        print(f"  {verdict:<28}{count:>5}")
    print(f"  {'total':<28}{sum(verdicts.values()):>5} of {len(rows)} candidates")
    if sum(verdicts.values()) != len(rows):
        print("  MISMATCH: candidates do not reconcile against verdicts")
        return 1
    print("\n=== blocking reasons ===")
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<48}{count:>5}")
    if args.dry_run:
        print("\nDry run: nothing was journalled.")
    return 0


def _buy_share_pct(buy_count: int | None, sell_count: int | None) -> float | None:
    """Share of transactions that are buys, or None when it cannot be computed.

    Returns None rather than 50.0 when there are no transactions. A token nobody
    has traded does not have a balanced order flow; it has no order flow, and
    those are different facts.
    """
    if buy_count is None or sell_count is None:
        return None
    total = buy_count + sell_count
    if total <= 0:
        return None
    return 100.0 * buy_count / total


def _scout_readiness(settings: Any, recorder: FlightRecorder, name: str, level: Any) -> int:
    """Print the evidence gate as computed numbers.

    The gate is the only definition of "ready" in this project, and it is computed
    rather than judged. This surfaces it inline so the scout states whether live
    execution would be permitted instead of anyone forming an impression.
    """
    from .scout import held_mints

    limits = settings.cohort
    closed = recorder.events_by_type("paper_position_closed")
    realised = [
        (event.get("payload") or {}).get("realised_usd")
        for event in closed
        if (event.get("payload") or {}).get("realised_usd") is not None
    ]
    values = [float(v) for v in realised]
    wins = [v for v in values if v > 0]
    losses = [-v for v in values if v < 0]
    gross_profit = sum(wins)
    profit_factor = (gross_profit / sum(losses)) if losses else None
    top_share = (max(wins) / gross_profit) if wins and gross_profit > 0 else None

    print(f"=== evidence gate, level {name} ===")
    print(f"  complete forward trades   {len(values)} of {limits.minimum_observable_trades}")
    print(
        "  expectancy after costs    "
        + (f"${sum(values) / len(values):+.4f}" if values else "no trades")
    )
    print(
        "  profit factor             "
        + (f"{profit_factor:.3f} (needs > {limits.minimum_profit_factor})"
           if profit_factor is not None else "no losing trades to divide by")
    )
    print(
        "  largest trade share       "
        + (f"{top_share:.3f} (needs <= {limits.maximum_single_trade_profit_share})"
           if top_share is not None else "no winning trades")
    )
    print(f"  level admits entry        {level.admits_entry}")
    print(f"  mints previously held     {len(held_mints(recorder))}")
    unmet = len(values) < limits.minimum_observable_trades or not level.admits_entry
    print(
        "\nLive execution is NOT permitted."
        if unmet
        else "\nEvery computable condition is met; a human decision is still required."
    )
    print("Nothing in this system can sign or broadcast a transaction regardless.")
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
    from .providers.goplus import GoPlusProvider
    from .providers.helius import HeliusProvider
    from .providers.jupiter import JupiterQuoteProvider

    mint_provider = None
    quote_provider = None
    pair_provider = None
    security_provider = None
    if not args.no_enrich:
        quote_provider = JupiterQuoteProvider()
        pair_provider = DexScreenerProvider()
        security_provider = GoPlusProvider()
        try:
            mint_provider = HeliusProvider()
        except ValueError as error:
            # Degrade rather than guess: without a Solana RPC the identity and
            # authority fields stay unknown and keep failing closed.
            print(f"note: Helius unavailable ({error}); authority evidence stays unknown.")

    monitor = None
    if getattr(args, "paper_trade", False):
        from .monitor import MonitorConfig, PositionMonitor
        from .readiness import ReadinessPolicy

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
                position_usd_override=args.position_usd,
                take_profit_multiple=args.take_profit,
                stop_loss_pct=args.stop_loss,
                readiness_policy=ReadinessPolicy.permissive_for_deep_pools(),
            ),
        )
        print(
            f"paper trading ON: pool >= ${args.minimum_pool_usd:,.0f}, "
            f"max {args.max_open} open, TP {args.take_profit}x. "
            f"No key is loaded and nothing can be signed."
        )

    movers_provider = None
    if args.source in ("movers", "both", "all"):
        from .providers.coingecko import CoinGeckoProvider

        movers_provider = CoinGeckoProvider()
        print(f"discovery: {args.source} (established trending pools included)")

    filter_rush_kwargs: dict[str, Any] = {}
    if getattr(args, "filter_rush", False):
        filter_rush_kwargs = {
            "excludeDevWashTrading": 1,
            "excludeInsiderWashTrading": 1,
            "devMigrateCountMin": 1,
        }
        print("meme-rush filters ON: dev wash excluded, insider wash excluded, dev migrate min 1")

    stages = () if args.source in ("movers", "topics") else tuple(
        stage.strip() for stage in args.stages.split(",") if stage.strip()
    )
    include_topics = args.source in ("topics", "all")
    collector = Collector(
        settings,
        recorder,
        mint_provider=mint_provider,
        quote_provider=quote_provider,
        pair_provider=pair_provider,
        security_provider=security_provider,
        monitor=monitor,
        movers_provider=movers_provider,
        config=CollectorConfig(
            stages=stages,
            limit_per_stage=args.limit,
            interval_seconds=args.interval,
            enrich=not args.no_enrich,
            include_topics=include_topics,
            filter_rush_kwargs=filter_rush_kwargs,
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
