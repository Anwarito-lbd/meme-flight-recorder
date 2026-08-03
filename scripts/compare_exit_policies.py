#!/usr/bin/env python3
"""Take the double and leave, or hold for the tail?

The measured return distribution is a lottery: 68% of all gross profit came from
3 tokens out of 155, with winners at 10x and 87x. Any rule that caps gains
removes exactly that. But uncapped exposure is also what produced negative
geometric growth at the configured position size, because the median candidate
returns 0.255x and the account bleeds while waiting for a tail that mostly does
not arrive.

Lower expectancy with lower ruin risk can beat higher expectancy that bankrupts
the account first. Which one wins is arithmetic, not judgement, so this measures
it instead of arguing about it.

**Only the exit differs.** Every policy sees identical entry signals on identical
candles, so any difference in the result is attributable to the exit rule and
nothing else.

**Endpoint data cannot answer this.** Knowing a token finished at 0.3x says
nothing about whether it touched 2x on the way. That is why this runs on candle
paths through the backtest harness rather than over the distribution study's
endpoint multiples.

One thing worth stating plainly, because it caused confusion: the production exit
hierarchy scales at 1R and 2R where R is *risk*, not price. With a 15% stop, "2R"
is a 30% move, not a double. The current engine therefore exits far earlier than
the "double then leave" idea it superficially resembles.

Read-only. Nothing is traded.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest_strategy import TIMEFRAMES, backtest_token, to_candles

from meme_flight_recorder.config import ExitLimits, load_settings
from meme_flight_recorder.costs import round_trip_cost
from meme_flight_recorder.discovery import select_movers
from meme_flight_recorder.entry import EntryLimits
from meme_flight_recorder.exits import (
    ExitDecision,
    ExitReason,
    PositionObservation,
    PositionView,
    evaluate_exit,
)
from meme_flight_recorder.models import Universe
from meme_flight_recorder.positions import TradeSummary
from meme_flight_recorder.providers.coingecko import CoinGeckoProvider
from meme_flight_recorder.risk import PortfolioState, RiskEngine

# Reasons that exist to protect capital rather than to harvest profit. Every
# policy keeps these: the question under test is how long to hold a *working*
# trade, and a policy that also removed the stop would be testing two changes at
# once and could not be read.
PROTECTIVE: frozenset[ExitReason] = frozenset(
    {
        ExitReason.SECURITY_AUTHORITY,
        ExitReason.SECURITY_DEVELOPER,
        ExitReason.SECURITY_LIQUIDITY_PULL,
        ExitReason.STALE_UNOBSERVABLE,
        ExitReason.STOP_HIT,
        ExitReason.LIQUIDITY_THIN,
        ExitReason.LIQUIDITY_IMPACT,
    }
)

HOLD = ExitDecision(should_exit=False)


def _multiple(view: PositionView, observation: PositionObservation) -> float | None:
    if observation.price_usd is None or view.entry_price <= 0:
        return None
    return observation.price_usd / view.entry_price


def hierarchy(
    view: PositionView, observation: PositionObservation, limits: ExitLimits
) -> ExitDecision:
    """The production engine, unchanged. The baseline everything is judged against."""
    return evaluate_exit(view, observation, limits)


def take_2x(
    view: PositionView, observation: PositionObservation, limits: ExitLimits
) -> ExitDecision:
    """Leave entirely at twice entry. The "double then leave" rule."""
    decision = evaluate_exit(view, observation, limits)
    multiple = _multiple(view, observation)
    if multiple is not None and multiple >= 2.0:
        return ExitDecision(True, ExitReason.PROFIT_SECOND_SCALE, 1.0, False, ("take_2x",))
    if decision.should_exit and decision.reason in PROTECTIVE:
        return decision
    # Everything else -- partial profit scales, trailing, time stops -- is
    # suppressed, because they would close the position before it can reach 2x
    # and the policy would never actually be tested.
    return HOLD


def scale_runner(
    view: PositionView, observation: PositionObservation, limits: ExitLimits
) -> ExitDecision:
    """Half out at 2x, remainder rides. The "keep some" version.

    This is the shape that tries to have both: enough taken off to fund
    survival, a runner left on for the tail that carries the expectancy.
    """
    decision = evaluate_exit(view, observation, limits)
    multiple = _multiple(view, observation)
    if multiple is not None and multiple >= 2.0 and view.scaled_out_fraction <= 0:
        return ExitDecision(True, ExitReason.PROFIT_FIRST_SCALE, 0.5, False, ("half_at_2x",))
    if decision.should_exit and decision.reason in PROTECTIVE:
        return decision
    return HOLD


def hold_to_end(
    view: PositionView, observation: PositionObservation, limits: ExitLimits
) -> ExitDecision:
    """Never take profit. Pure tail capture, and the upper bound on it."""
    decision = evaluate_exit(view, observation, limits)
    if decision.should_exit and decision.reason in PROTECTIVE:
        return decision
    return HOLD


POLICIES = {
    "hierarchy": hierarchy,
    "take_2x": take_2x,
    "scale_runner": scale_runner,
    "hold_to_end": hold_to_end,
}


def summarise_policy(name: str, trades: list[TradeSummary]) -> None:
    if not trades:
        print(f"{name:<14} {0:>4}      --        --        --       --      --")
        return
    results = [trade.realised_usd for trade in trades]
    wins = [value for value in results if value > 0]
    losses = [value for value in results if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    top_share = 100.0 * max(wins) / gross_win if wins and gross_win > 0 else 0.0
    print(
        f"{name:<14} {len(trades):>4} {100 * len(wins) / len(trades):>6.1f}% "
        f"{statistics.mean(results):>+9.4f} {sum(results):>+9.2f} "
        f"{factor:>8.3f} {top_share:>6.0f}%"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", type=int, default=15)
    parser.add_argument("--timeframe", choices=sorted(TIMEFRAMES), default="15m")
    arguments = parser.parse_args()
    timeframe, aggregate = TIMEFRAMES[arguments.timeframe]

    settings = load_settings()
    provider = CoinGeckoProvider()
    engine = RiskEngine(settings.risk, settings.micro)

    nominal = settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0
    cost_pct = round_trip_cost(nominal, settings.costs).pct_of_position / 2.0

    movers, skipped = select_movers(provider.trending_solana_pools())
    movers = movers[: arguments.tokens]
    print(f"{len(movers)} tokens sampled from trending pools (skipped: {skipped})")
    print(
        f"costs {cost_pct:.3f}% per leg, candles {arguments.timeframe}, "
        f"equity ${settings.starting_equity_usd:.2f}\n"
    )

    # Candles are fetched once and reused across policies, so every policy is
    # judged on exactly the same price history.
    prepared: list[tuple[str, str, list, float, float]] = []
    unusable = 0
    for pool in movers:
        approval = engine.approve(
            Universe.SOLANA_EMERGING,
            PortfolioState(equity_usd=settings.starting_equity_usd),
            entry_price=pool.price_usd or 1.0,
            pool_liquidity_usd=pool.liquidity_usd,
            estimated_round_trip_cost_pct=cost_pct * 2,
        )
        if not approval.approved:
            unusable += 1
            continue
        try:
            payload = provider.pool_ohlcv(
                pool.pool_address, aggregate=aggregate, limit=1000, timeframe=timeframe
            )
        except Exception as error:  # noqa: BLE001
            print(f"  {pool.symbol:<14} candles unavailable: {type(error).__name__}: {error}")
            unusable += 1
            continue
        candles = to_candles(
            (payload.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        )
        if len(candles) < 40:
            unusable += 1
            continue
        prepared.append(
            (pool.mint, pool.symbol, candles, pool.liquidity_usd or 0.0, approval.position_value_usd)
        )

    print(f"{len(prepared)} tokens usable, {unusable} skipped\n")
    if not prepared:
        print("No usable tokens. This is missing data, not a result about exit policy.")
        return 1

    header = (
        f"{'policy':<14} {'n':>4} {'win':>7} {'expectancy':>9} "
        f"{'total':>9} {'factor':>8} {'top':>7}"
    )
    print(header)
    print("-" * len(header))

    outcomes: dict[str, list[TradeSummary]] = {}
    for name, policy in POLICIES.items():
        trades: list[TradeSummary] = []
        for mint, symbol, candles, liquidity, position_usd in prepared:
            trades.extend(
                backtest_token(
                    mint,
                    symbol,
                    candles,
                    liquidity,
                    position_usd,
                    cost_pct,
                    ExitLimits(),
                    EntryLimits(),
                    policy=policy,
                    exit_impact_pct=settings.costs.assumed_impact_pct,
                )
            )
        outcomes[name] = trades
        summarise_policy(name, trades)

    print("\n'top' is the share of gross profit from the single best trade.")
    print("A policy above 33% there fails this project's own outlier rule and is not")
    print("a validated edge however good its expectancy looks.\n")

    ranked = sorted(
        ((name, trades) for name, trades in outcomes.items() if trades),
        key=lambda item: statistics.mean([t.realised_usd for t in item[1]]),
        reverse=True,
    )
    if not ranked:
        print("No policy produced a completed trade.")
        return 0

    best_name, best_trades = ranked[0]
    best_ev = statistics.mean([trade.realised_usd for trade in best_trades])
    print(f"Best expectancy: {best_name} at ${best_ev:+.4f} per trade.")
    if best_ev <= 0:
        print("Every policy loses money. The exit rule is not what is wrong -- changing")
        print("when to leave cannot rescue entries that have no edge to harvest.")
    else:
        wins = [t.realised_usd for t in best_trades if t.realised_usd > 0]
        share = 100.0 * max(wins) / sum(wins) if wins else 0.0
        if share > 33.0:
            print(f"But one trade carries {share:.0f}% of its gross profit. Not validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
