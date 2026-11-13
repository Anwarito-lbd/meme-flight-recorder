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


# --- Wallet API shape -------------------------------------------------------
#
# The bug these guard: profile_wallet only understood the Enhanced Transactions
# shape (tokenTransfers/nativeTransfers), while the Wallet API endpoint the
# backfill actually calls returns `balanceChanges`. Every test passed because the
# fixtures were written to match the assumption. Against 1,200 real transactions
# the module reported zero trades and zero tokens.

WSOL = "So11111111111111111111111111111111111111112"


def balance_change_tx(minutes: float, mint: str, token_delta: float, sol_delta: float) -> dict:
    return {
        "signature": f"sig{minutes}",
        "timestamp": stamp(minutes),
        "fee": 5000,
        "balanceChanges": [
            {"mint": mint, "amount": token_delta, "decimals": 0},
            {"mint": WSOL, "amount": sol_delta, "decimals": 9},
        ],
    }


def test_balance_changes_shape_reconstructs_a_round_trip() -> None:
    history = [
        balance_change_tx(0, "MINT_A", token_delta=1000.0, sol_delta=-1.0),
        balance_change_tx(60, "MINT_A", token_delta=-1000.0, sol_delta=1.5),
    ]
    profile = profile_wallet("W1", history, history_truncated=False)
    assert profile.trade_count == 1
    assert profile.distinct_tokens == 1
    assert round(profile.trades[0].pnl_sol, 6) == 0.5


def test_balance_changes_buy_without_sell_is_not_a_trade() -> None:
    profile = profile_wallet("W1", [balance_change_tx(0, "MINT_A", 1000.0, -1.0)])
    assert profile.trade_count == 0
    assert profile.distinct_tokens == 1


def test_balance_changes_sell_without_buy_is_unexplained() -> None:
    profile = profile_wallet("W1", [balance_change_tx(0, "MINT_A", -1000.0, 2.0)])
    assert profile.trade_count == 0
    assert profile.unexplained_transfers == 1


def test_wrapped_sol_is_the_quote_leg_not_a_position() -> None:
    """SOL must not be counted as a token being traded against itself."""
    profile = profile_wallet(
        "W1",
        [
            balance_change_tx(0, "MINT_A", 1000.0, -1.0),
            balance_change_tx(60, "MINT_A", -1000.0, 2.0),
        ],
        history_truncated=False,
    )
    assert WSOL not in {trade.mint for trade in profile.trades}
    assert profile.distinct_tokens == 1


def test_both_payload_shapes_agree_on_the_same_economic_event() -> None:
    enhanced = profile_wallet("W1", [buy("A", 0, 1.0), sell("A", 60, 1.5)])
    wallet_api = profile_wallet(
        "W1",
        [
            balance_change_tx(0, "A", 1000.0, -1.0),
            balance_change_tx(60, "A", -1000.0, 1.5),
        ],
    )
    assert enhanced.trade_count == wallet_api.trade_count == 1
    assert round(enhanced.trades[0].pnl_sol, 6) == round(wallet_api.trades[0].pnl_sol, 6)


def token_to_token_tx(minutes: float, sold: str, bought: str) -> dict:
    return {
        "timestamp": stamp(minutes),
        "balanceChanges": [
            {"mint": sold, "amount": -1000.0, "decimals": 0},
            {"mint": bought, "amount": 500.0, "decimals": 0},
        ],
    }


def test_token_to_token_swap_is_counted_not_silently_dropped() -> None:
    """A skipped swap must not look like a wallet that did not trade.

    Measured across 8,100 real transactions, 4.5% were token-to-token swaps
    this method cannot price. Dropping them silently makes an unreadable wallet
    indistinguishable from an inactive one.
    """
    profile = profile_wallet("W1", [token_to_token_tx(0, "MINT_A", "MINT_B")])
    assert profile.trade_count == 0
    assert profile.unpriceable_swaps == 1
    assert profile.distinct_tokens == 2


def test_coverage_reports_how_much_was_readable() -> None:
    history = [
        balance_change_tx(0, "MINT_A", 1000.0, -1.0),
        balance_change_tx(60, "MINT_A", -1000.0, 1.5),
        token_to_token_tx(120, "MINT_C", "MINT_D"),
        token_to_token_tx(180, "MINT_E", "MINT_F"),
    ]
    profile = profile_wallet("W1", history, history_truncated=False)
    assert profile.priceable_transactions == 2
    assert profile.unpriceable_swaps == 2
    assert profile.coverage_pct == 50.0


def test_coverage_is_none_when_nothing_was_observed() -> None:
    assert profile_wallet("W1", []).coverage_pct is None
