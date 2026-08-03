"""Tests for Stage 8 wallet cohort profiling.

The tests that matter most are the ones enforcing that a short record and a
one-jackpot record can never be labelled a repeatable trader, because that is
the single failure mode a leaderboard cannot protect against.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from meme_flight_recorder.config import CohortLimits
from meme_flight_recorder.wallets import (
    LAMPORTS_PER_SOL,
    WalletClass,
    classify_wallet,
    profile_wallet,
)

BASE = datetime(2026, 8, 1, tzinfo=UTC)


def stamp(minutes: float) -> int:
    return int((BASE + timedelta(minutes=minutes)).timestamp())


def buy(mint: str, minutes: float, sol: float, quantity: float = 1000.0) -> dict:
    return {
        "type": "SWAP",
        "timestamp": stamp(minutes),
        "tokenTransfers": [
            {"mint": mint, "toUserAccount": "W1", "tokenAmount": quantity},
        ],
        "nativeTransfers": [
            {
                "fromUserAccount": "W1",
                "toUserAccount": "POOL",
                "amount": sol * LAMPORTS_PER_SOL,
            }
        ],
    }


def sell(mint: str, minutes: float, sol: float, quantity: float = 1000.0) -> dict:
    return {
        "type": "SWAP",
        "timestamp": stamp(minutes),
        "tokenTransfers": [
            {"mint": mint, "fromUserAccount": "W1", "tokenAmount": quantity},
        ],
        "nativeTransfers": [
            {
                "fromUserAccount": "POOL",
                "toUserAccount": "W1",
                "amount": sol * LAMPORTS_PER_SOL,
            }
        ],
    }


def round_trips(count: int, *, profit_sol: float = 0.1, hold_minutes: float = 60.0) -> list[dict]:
    """A record of `count` completed trades across distinct mints, all alike."""
    history: list[dict] = []
    for index in range(count):
        mint = f"MINT_{index}"
        start = index * 1000.0
        history.append(buy(mint, start, 1.0))
        history.append(sell(mint, start + hold_minutes, 1.0 + profit_sol))
    return history


def mixed_record(count: int, *, loss_every: int = 3, hold_minutes: float = 60.0) -> list[dict]:
    """A realistic record: mostly small wins, regular small losses.

    A wallet with no losing trades at all is deliberately *not* classifiable as
    a repeatable trader, so a fixture meant to qualify must contain losses.
    """
    history: list[dict] = []
    for index in range(count):
        mint = f"MINT_{index}"
        start = index * 1000.0
        history.append(buy(mint, start, 1.0))
        proceeds = 0.9 if index % loss_every == 0 else 1.1
        history.append(sell(mint, start + hold_minutes, proceeds))
    return history


def test_round_trip_is_reconstructed_with_pnl() -> None:
    profile = profile_wallet("W1", [buy("A", 0, 1.0), sell("A", 60, 1.5)])
    assert profile.trade_count == 1
    trade = profile.trades[0]
    assert round(trade.pnl_sol, 6) == 0.5
    assert trade.return_pct == 50.0
    assert trade.holding_seconds == 3600.0


def test_open_position_is_excluded_not_counted_as_a_loss() -> None:
    """An unsold buy is an open position; pricing it would invent a result."""
    profile = profile_wallet("W1", [buy("A", 0, 1.0)])
    assert profile.trade_count == 0
    assert profile.realised_pnl_sol == 0.0


def test_partial_exit_splits_the_lot() -> None:
    profile = profile_wallet(
        "W1", [buy("A", 0, 1.0, quantity=1000.0), sell("A", 60, 0.75, quantity=500.0)]
    )
    assert profile.trade_count == 1
    assert round(profile.trades[0].cost_sol, 6) == 0.5
    assert round(profile.trades[0].pnl_sol, 6) == 0.25


def test_sell_without_a_matching_buy_is_flagged_unexplained() -> None:
    profile = profile_wallet("W1", [sell("A", 60, 2.0)])
    assert profile.trade_count == 0
    assert profile.unexplained_transfers == 1


def test_fewer_than_thirty_trades_can_never_be_a_repeatable_trader() -> None:
    """The hard rule. Performance is not consulted until the sample qualifies."""
    profile = profile_wallet("W1", round_trips(29, profit_sol=5.0), history_truncated=False)
    assert profile.trade_count == 29
    verdict, notes = classify_wallet(profile)
    assert verdict is WalletClass.INSUFFICIENT_HISTORY
    assert "only_29_trades" in notes


def test_thirty_mixed_trades_qualify() -> None:
    profile = profile_wallet("W1", mixed_record(30), history_truncated=False)
    assert profile.profit_factor is not None
    verdict, _notes = classify_wallet(profile)
    assert verdict is WalletClass.REPEATABLE_TRADER


def test_one_jackpot_makes_a_wallet_outlier_dependent() -> None:
    """A record carried by one trade is a record of one trade."""
    history = mixed_record(30)
    history.append(buy("JACKPOT", 99_000, 1.0))
    history.append(sell("JACKPOT", 99_060, 500.0))
    profile = profile_wallet("W1", history, history_truncated=False)
    verdict, notes = classify_wallet(profile)
    assert verdict is WalletClass.OUTLIER_DEPENDENT
    assert any("gross_profit" in note for note in notes)


def test_one_token_record_cannot_qualify() -> None:
    history: list[dict] = []
    for index in range(40):
        history.append(buy("ONLY", index * 100, 1.0))
        history.append(sell("ONLY", index * 100 + 60, 1.1))
    profile = profile_wallet("W1", history, history_truncated=False)
    verdict, notes = classify_wallet(profile)
    assert verdict is WalletClass.INSUFFICIENT_HISTORY
    assert "only_1_tokens" in notes


def test_deployer_is_classified_before_anything_else() -> None:
    history = round_trips(30)
    history.append(
        {
            "type": "CREATE",
            "timestamp": stamp(1),
            "tokenTransfers": [{"mint": "NEW_TOKEN"}],
        }
    )
    profile = profile_wallet("W1", history, history_truncated=False)
    verdict, _notes = classify_wallet(profile)
    assert verdict is WalletClass.DEPLOYER


def test_fast_round_trips_are_a_sniper() -> None:
    profile = profile_wallet(
        "W1", round_trips(40, hold_minutes=0.5), history_truncated=False
    )
    verdict, _notes = classify_wallet(profile)
    assert verdict is WalletClass.SNIPER


def test_high_turnover_netting_to_nothing_is_wash_trading() -> None:
    history: list[dict] = []
    for index in range(30):
        mint = f"M{index}"
        start = index * 1000.0
        history.append(buy(mint, start, 1.0))
        # Alternating tiny wins and losses that cancel out.
        history.append(sell(mint, start + 60, 1.001 if index % 2 else 0.999))
    profile = profile_wallet("W1", history, history_truncated=False)
    verdict, _notes = classify_wallet(profile)
    assert verdict is WalletClass.WASH_TRADER


def test_losing_wallet_is_unprofitable() -> None:
    profile = profile_wallet("W1", round_trips(30, profit_sol=-0.1), history_truncated=False)
    verdict, _notes = classify_wallet(profile)
    assert verdict is WalletClass.UNPROFITABLE


def test_no_observed_losses_is_unclassified_not_perfect() -> None:
    """Losses that are not visible are not losses that did not happen.

    A flawless record across 30 trades is far more likely to mean the losing
    side sits in a wallet this history cannot see than that the trader never
    lost. It is refused a classification rather than given the best one.
    """
    profile = profile_wallet("W1", round_trips(30), history_truncated=False)
    assert profile.gross_loss_sol == 0.0
    assert profile.profit_factor is None
    verdict, notes = classify_wallet(profile)
    assert verdict is WalletClass.UNCLASSIFIED
    assert "no_losing_trades_observed" in notes


def test_profit_factor_is_none_rather_than_infinite() -> None:
    profile = profile_wallet("W1", [buy("A", 0, 1.0), sell("A", 60, 2.0)])
    assert profile.profit_factor is None


def test_maximum_drawdown_tracks_the_realised_equity_curve() -> None:
    history = [
        buy("A", 0, 1.0),
        sell("A", 10, 3.0),
        buy("B", 20, 1.0),
        sell("B", 30, 0.0001),
        buy("C", 40, 1.0),
        sell("C", 50, 0.0001),
    ]
    profile = profile_wallet("W1", history, history_truncated=False)
    assert round(profile.maximum_drawdown_sol, 2) == 2.0


def test_unreconstructable_fields_are_none_not_zero() -> None:
    """Entry market cap and pool liquidity are not in wallet history."""
    profile = profile_wallet("W1", round_trips(30))
    assert profile.average_entry_market_cap_usd is None
    assert profile.average_pool_liquidity_usd is None


def test_empty_history_is_insufficient() -> None:
    verdict, _notes = classify_wallet(profile_wallet("W1", []))
    assert verdict is WalletClass.INSUFFICIENT_HISTORY


def test_limits_are_configurable() -> None:
    strict = CohortLimits(minimum_observable_trades=100)
    profile = profile_wallet("W1", round_trips(30), history_truncated=False)
    verdict, _notes = classify_wallet(profile, strict)
    assert verdict is WalletClass.INSUFFICIENT_HISTORY
