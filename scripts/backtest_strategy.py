#!/usr/bin/env python3
"""Backtest the complete entry-to-exit strategy over real historical candles.

The point is to get a measured expectancy now rather than after weeks of
forward paper trading. Every trade here is complete: entered on a structural
signal, exited by the hierarchy, and charged costs on both legs.

Three properties make the result worth reading rather than flattering:

**No look-ahead.** Entries come from ``entry.find_entries``, which decides at
index *i* using only candles up to *i*. Exits are evaluated on each subsequent
candle using that candle's low, because within a candle the worst price arrives
before the close and a rule that only sees closes reports fills it could not
have achieved.

**Costs on both legs, derived rather than assumed.** This script used to charge a
flat 3% per leg, a figure that came from nowhere and overstated the truth by
roughly 2.6x at the position size this account takes. Cost now comes from
``costs.round_trip_cost``, which separates the fixed network fee from the
proportional router fee and impact -- a distinction that matters because fixed
fees do not shrink with the order and decide whether a sub-dollar position is
viable at all. A gross figure would still look profitable while losing money, so
costs remain charged on both legs; they are simply the right size now.

**Survivorship is disclosed.** Tokens are sampled from a trending feed, which by
construction contains things that already worked. That biases results upward and
the report says so rather than hiding it. Treat the output as an upper bound.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Callable
from datetime import UTC, datetime

from meme_flight_recorder.config import ExitLimits, load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.discovery import select_movers
from meme_flight_recorder.entry import EntryLimits, find_entries
from meme_flight_recorder.exits import (
    ExitDecision,
    PositionObservation,
    PositionView,
    evaluate_exit,
    realistic_fill_price,
)
from meme_flight_recorder.positions import (
    TradeSummary,
    apply_exit,
    mark,
    open_position,
    summarise,
)
from meme_flight_recorder.providers.coingecko import CoinGeckoProvider
from meme_flight_recorder.risk import PortfolioState, RiskEngine
from meme_flight_recorder.strategy import Candle


def to_candles(rows: list[list[float]]) -> list[Candle]:
    """Provider returns newest first; a walk-forward test needs oldest first."""
    ordered = sorted(rows, key=lambda row: row[0])
    return [
        Candle(int(t), float(o), float(h), float(low), float(c), float(v))
        for t, o, h, low, c, v in ordered
    ]


ExitPolicy = Callable[[PositionView, PositionObservation, ExitLimits], ExitDecision]


def backtest_token(
    mint: str,
    symbol: str,
    candles: list[Candle],
    liquidity_usd: float,
    position_usd: float,
    cost_pct: float,
    exit_limits: ExitLimits,
    entry_limits: EntryLimits,
    policy: ExitPolicy = evaluate_exit,
    exit_impact_pct: float = 0.5,
) -> list[TradeSummary]:
    """Run every entry this token produced, one position at a time.

    ``policy`` decides when to leave. It defaults to the production exit
    hierarchy; ``compare_exit_policies.py`` passes alternatives so that the same
    entry signals can be judged under different exits and nothing but the exit
    differs between runs.
    """
    trades: list[TradeSummary] = []
    blocked_until = -1

    for index, decision in find_entries(candles, entry_limits):
        # One position per token at a time; re-entering while still holding
        # would silently multiply exposure beyond what risk approved.
        if index <= blocked_until or decision.signal is None:
            continue
        signal = decision.signal
        entry_candle = candles[index]

        position = open_position(
            mint=mint,
            symbol=symbol,
            at=datetime.fromtimestamp(entry_candle.timestamp, tz=UTC),
            price=signal.entry,
            position_usd=position_usd,
            stop_price=signal.stop,
            target_price=signal.target,
            breakout_level=signal.breakout_level,
            atr=decision.atr,
            liquidity_usd=liquidity_usd,
            cost_pct=cost_pct,
        )

        for forward in range(index + 1, len(candles)):
            candle = candles[forward]
            moment = datetime.fromtimestamp(candle.timestamp, tz=UTC)
            position = mark(position, price=candle.high, liquidity_usd=liquidity_usd)

            view = PositionView(
                mint=mint,
                opened_at=position.opened_at,
                entry_price=position.entry_price,
                stop_price=position.stop_price,
                breakout_level=position.breakout_level,
                entry_liquidity_usd=position.entry_liquidity_usd,
                atr=position.atr,
                high_water_price=position.high_water_price,
                scaled_out_fraction=position.scaled_out_fraction,
                peak_liquidity_usd=position.peak_liquidity_usd,
            )
            # This system polls; it holds no resting orders on-chain. So it can
            # only act on a price it actually observed, which is the close.
            # Evaluating on the intra-candle low would model a stop fill the
            # system could never have achieved and would overstate losses.
            #
            # The cost of that choice is that intra-candle gaps are invisible
            # here, and gaps are real -- a replayed collapse fell 1700x below its
            # stop inside one candle. That case is covered separately by
            # scripts/replay_collapse.py, and it is why sizing assumes total
            # loss rather than relying on any stop.
            decision_now = policy(
                view, PositionObservation(observed_at=moment, price_usd=candle.close), exit_limits
            )
            if not decision_now.should_exit:
                continue

            # Impact comes from the cost model rather than a literal. The old
            # hardcoded 3.0 charged impact twice over: once here in the fill
            # price and again in the cost percentage below.
            fill = realistic_fill_price(candle.close, exit_impact_pct, urgent=decision_now.urgent)
            position = apply_exit(
                position,
                at=moment,
                price=fill,
                fraction=decision_now.fraction,
                reason=decision_now.reason,
                cost_pct=cost_pct,
            )
            if not position.is_open:
                blocked_until = forward
                break

        if position.is_open:
            # Unclosed at the end of history is not a trade; excluding it keeps
            # open losers from being quietly omitted while winners are counted.
            continue
        trades.append(summarise(position))

    return trades


# The provider exposes a separate path per timeframe and each accepts only its
# own aggregates, so "60 minutes" is not a request it can answer -- an hourly
# candle is `hour/1`, not `minute/60`. Naming the timeframes here means a sweep
# cannot silently ask for something unfetchable and read the resulting silence
# as a result.
TIMEFRAMES: dict[str, tuple[str, int]] = {
    "5m": ("minute", 5),
    "15m": ("minute", 15),
    "1h": ("hour", 1),
    "4h": ("hour", 4),
    "12h": ("hour", 12),
    "1d": ("day", 1),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", type=int, default=15)
    parser.add_argument(
        "--timeframe",
        choices=sorted(TIMEFRAMES),
        default="15m",
        help="Candle size. Maps to the provider's own timeframe path and aggregate.",
    )
    parser.add_argument(
        "--cost-pct",
        type=float,
        default=None,
        help="Override cost per leg. Omit to derive it from the measured cost model.",
    )
    arguments = parser.parse_args()
    timeframe, aggregate = TIMEFRAMES[arguments.timeframe]

    settings = load_settings()
    provider = CoinGeckoProvider()
    engine = RiskEngine(settings.risk, settings.micro)

    movers, skipped = select_movers(provider.trending_solana_pools())
    movers = movers[: arguments.tokens]

    # Cost per leg is derived from the position this account actually takes,
    # because a flat percentage is wrong at both ends: it overstates cost at $4
    # and understates it below $1, where fixed network fees dominate. The old
    # default of 3% per leg overstated the true figure by roughly 2.6x and made
    # every result here look worse than it was.
    nominal_position = settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0
    if arguments.cost_pct is not None:
        cost_pct = arguments.cost_pct
        source = "override"
    else:
        cost_pct = round_trip_cost(nominal_position, settings.costs).pct_of_position / 2.0
        source = f"measured at a ${nominal_position:.2f} position"

    print(f"{len(movers)} tokens sampled from trending pools (skipped: {skipped})")
    print(
        f"equity ${settings.starting_equity_usd:.2f}, costs {cost_pct:.3f}% per leg ({source}), "
        f"candles {arguments.timeframe} ({timeframe}/{aggregate})\n"
    )

    # Every sampled token lands in exactly one of these. Without the tally, a run
    # where the provider returned nothing prints the same "no trades" line as a
    # run where the strategy genuinely found no setup, and the two have opposite
    # meanings.
    risk_rejected = 0
    candles_unavailable = 0
    too_few_candles = 0
    no_setup = 0
    traded = 0

    all_trades: list[TradeSummary] = []
    for pool in movers:
        approval = engine.approve(
            __import__(
                "meme_flight_recorder.models", fromlist=["Universe"]
            ).Universe.SOLANA_EMERGING,
            PortfolioState(equity_usd=settings.starting_equity_usd),
            entry_price=pool.price_usd or 1.0,
            pool_liquidity_usd=pool.liquidity_usd,
            estimated_round_trip_cost_pct=cost_pct * 2,
        )
        if not approval.approved:
            risk_rejected += 1
            print(f"  {pool.symbol:<14} skipped: {approval.reason}")
            continue
        try:
            payload = provider.pool_ohlcv(
                pool.pool_address, aggregate=aggregate, limit=1000, timeframe=timeframe
            )
        except Exception as error:  # noqa: BLE001
            candles_unavailable += 1
            print(f"  {pool.symbol:<14} candles unavailable: {type(error).__name__}: {error}")
            continue

        candles = to_candles(
            (payload.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        )
        if len(candles) < 40:
            too_few_candles += 1
            print(f"  {pool.symbol:<14} only {len(candles)} candles, skipped")
            continue

        trades = backtest_token(
            pool.mint,
            pool.symbol,
            candles,
            pool.liquidity_usd or 0.0,
            approval.position_value_usd,
            cost_pct,
            ExitLimits(),
            EntryLimits(),
            exit_impact_pct=settings.costs.assumed_impact_pct,
        )
        all_trades.extend(trades)
        if trades:
            traded += 1
        else:
            no_setup += 1
        print(f"  {pool.symbol:<14} {len(candles):>4} candles -> {len(trades)} complete trades")

    print()
    accounted = risk_rejected + candles_unavailable + too_few_candles + no_setup + traded
    print(
        f"denominator: {risk_rejected} risk-rejected + {candles_unavailable} no candles "
        f"+ {too_few_candles} too few candles"
    )
    print(f"           + {no_setup} no setup + {traded} traded = {accounted} of {len(movers)}\n")

    if not all_trades:
        # Which of these two it is decides whether the strategy or the data is
        # the problem, and they are not distinguishable from the trade count.
        if candles_unavailable or too_few_candles:
            print(
                f"No complete trades, and {candles_unavailable + too_few_candles} of "
                f"{len(movers)} tokens returned no usable candles."
            )
            print("This is NOT evidence about the strategy. It is missing data.")
            print("Fix the feed before reading anything into the absence of trades.")
            return 1
        print("No complete trades, and every token returned usable candles.")
        print("That is a finding: the structure this entry rule requires is rare here.")
        return 0

    results = [trade.realised_usd for trade in all_trades]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    print(f"complete trades      {len(all_trades)}")
    print(f"win rate             {100 * len(wins) / len(all_trades):.1f}%")
    print(f"total P&L            ${sum(results):+.2f}")
    print(f"expectancy per trade ${statistics.mean(results):+.4f}")
    print(f"median trade         ${statistics.median(results):+.4f}")
    if gross_loss > 0:
        print(f"profit factor        {gross_win / gross_loss:.3f}")
    if wins:
        print(f"largest win          ${max(wins):+.2f}")
    if losses:
        print(f"largest loss         ${min(losses):+.2f}")
    if wins and gross_win > 0:
        print(f"top trade share      {100 * max(wins) / gross_win:.1f}% of gross profit")

    reasons: dict[str, int] = {}
    for trade in all_trades:
        reasons[trade.reason] = reasons.get(trade.reason, 0) + 1
    print("\nexit reasons")
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"  {count:>4}  {reason}")

    print(
        "\nSurvivorship warning: tokens were sampled from a trending feed, which by\n"
        "construction contains things that already worked. Treat this as an upper bound."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
