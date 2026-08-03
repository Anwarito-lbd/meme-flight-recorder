"""Stage 8: which wallets are worth watching, and which merely got lucky?

A leaderboard sorts by realised profit, and realised profit is exactly the
statistic that cannot distinguish a trader from someone who bought one token
that went up. This module reconstructs what a leaderboard hides: how many trades
there actually were, how concentrated the profit is in the best one, how long
positions were held, and whether the wallet is taking positions at all or just
cycling supply between addresses it controls.

**The hard rules are enforced in code, not documented as guidance.** A wallet
with fewer than the configured number of observable trades cannot be classified
as a repeatable trader at any level of performance, and a wallet whose single
best trade carries more than a third of its gross profit is marked outlier
dependent and likewise cannot. These are the same two conditions this system
applies before it will consider trading its own strategy live; applying a
weaker standard to strangers than to itself would be incoherent.

**What this cannot see is stated rather than guessed.** Entry market cap and
pool liquidity at the time of each trade are not in wallet history and are not
reconstructed -- they would need a price oracle per mint per timestamp. Promoter
and coordinated-insider classification needs social data this system does not
have. Those classifications exist in the enum so the vocabulary is complete, and
they are never assigned from chain data alone. A wallet that does not match a
supported pattern is UNCLASSIFIED, which is an honest answer.

**A profitable wallet is not a recommendation.** Copy trading is prohibited by
the operating profile. The output here is a research prompt: a cohort of
independent wallets accumulating the same mint is a reason to look, never a
reason to buy.

Pure: it assesses transactions a caller has already fetched.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .config import CohortLimits

LAMPORTS_PER_SOL = 1_000_000_000


class WalletClass(StrEnum):
    REPEATABLE_TRADER = "repeatable_trader"
    OUTLIER_DEPENDENT = "outlier_dependent"
    UNPROFITABLE = "unprofitable"
    SNIPER = "sniper"
    WASH_TRADER = "wash_trader"
    DEPLOYER = "deployer"
    LIQUIDITY_PROVIDER = "liquidity_provider"
    INSUFFICIENT_HISTORY = "insufficient_history"
    # Present so the vocabulary matches the operating manual. Never assigned
    # from chain data, because distinguishing a promoter or a coordinated
    # insider from an ordinary early buyer requires social evidence this system
    # does not collect.
    PROMOTER = "promoter"
    COORDINATED_INSIDER = "coordinated_insider"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class WalletTrade:
    """One completed round trip in a single mint, priced in SOL."""

    mint: str
    opened_at: datetime
    closed_at: datetime
    cost_sol: float
    proceeds_sol: float

    @property
    def pnl_sol(self) -> float:
        return round(self.proceeds_sol - self.cost_sol, 9)

    @property
    def return_pct(self) -> float | None:
        if self.cost_sol <= 0:
            return None
        return round(100.0 * self.pnl_sol / self.cost_sol, 4)

    @property
    def holding_seconds(self) -> float:
        return (self.closed_at - self.opened_at).total_seconds()

    @property
    def is_win(self) -> bool:
        return self.pnl_sol > 0


@dataclass(frozen=True)
class WalletProfile:
    """A wallet reduced to the statistics that separate skill from luck."""

    address: str
    transactions_observed: int = 0
    history_truncated: bool = True
    trades: tuple[WalletTrade, ...] = field(default_factory=tuple)
    distinct_tokens: int = 0
    mints_created: tuple[str, ...] = ()
    liquidity_events: int = 0
    # Transfers with no matching swap: deposits and withdrawals that make the
    # realised P&L an incomplete picture of the wallet's actual results.
    unexplained_transfers: int = 0

    # How much of the wallet's activity this method could actually read.
    #
    # Measured across 8,100 real transactions from six wallets, only 3.2% were
    # SOL-paired and therefore priceable; 4.5% were token-to-token swaps this
    # method cannot value, and 88.3% were single-token events, overwhelmingly
    # airdrop spam rather than trades. A trade count reported without this
    # context invites the reading that a wallet with 1,400 transactions traded
    # nine times, when what happened is that nine trades were *visible*.
    priceable_transactions: int = 0
    unpriceable_swaps: int = 0

    @property
    def coverage_pct(self) -> float | None:
        """Share of transactions this method could price. None when unknown."""
        if self.transactions_observed <= 0:
            return None
        return round(100.0 * self.priceable_transactions / self.transactions_observed, 2)

    # Not reconstructable from wallet history. Present so their absence is
    # visible rather than silently omitted from any scoring that follows.
    average_entry_market_cap_usd: float | None = None
    average_pool_liquidity_usd: float | None = None

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def realised_pnl_sol(self) -> float:
        return round(sum(trade.pnl_sol for trade in self.trades), 9)

    @property
    def win_rate_pct(self) -> float | None:
        if not self.trades:
            return None
        return round(100.0 * sum(1 for t in self.trades if t.is_win) / len(self.trades), 2)

    @property
    def gross_profit_sol(self) -> float:
        return round(sum(t.pnl_sol for t in self.trades if t.pnl_sol > 0), 9)

    @property
    def gross_loss_sol(self) -> float:
        return round(abs(sum(t.pnl_sol for t in self.trades if t.pnl_sol < 0)), 9)

    @property
    def profit_factor(self) -> float | None:
        """None when there are no losses -- a ratio over zero is not infinity,
        it is an unmeasured denominator, and reporting it as a huge number is
        how a three-trade wallet outranks a hundred-trade one."""
        if self.gross_loss_sol <= 0:
            return None
        return round(self.gross_profit_sol / self.gross_loss_sol, 4)

    @property
    def median_return_pct(self) -> float | None:
        values = [t.return_pct for t in self.trades if t.return_pct is not None]
        return round(statistics.median(values), 4) if values else None

    @property
    def median_holding_seconds(self) -> float | None:
        if not self.trades:
            return None
        return round(statistics.median(t.holding_seconds for t in self.trades), 2)

    @property
    def best_trade_profit_share(self) -> float | None:
        """Share of gross profit carried by the single best trade."""
        if self.gross_profit_sol <= 0:
            return None
        best = max((t.pnl_sol for t in self.trades), default=0.0)
        return round(best / self.gross_profit_sol, 4)

    @property
    def maximum_drawdown_sol(self) -> float:
        """Worst peak-to-trough decline of the realised equity curve."""
        equity = 0.0
        peak = 0.0
        worst = 0.0
        for trade in sorted(self.trades, key=lambda t: t.closed_at):
            equity += trade.pnl_sol
            peak = max(peak, equity)
            worst = min(worst, equity - peak)
        return round(abs(worst), 9)


def _timestamp(value: Any) -> datetime | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _amount(transfer: dict[str, Any]) -> float:
    for key in ("tokenAmount", "amount"):
        value = transfer.get(key)
        if value is None:
            continue
        try:
            return abs(float(value))
        except (TypeError, ValueError):
            continue
    return 0.0


WRAPPED_SOL = "So11111111111111111111111111111111111111112"


def swap_legs(transaction: dict[str, Any], address: str) -> tuple[float, dict[str, float]]:
    """Normalise one transaction into a SOL delta and per-mint token deltas.

    Two payload shapes exist and only one of them was supported, which is why
    this module reported zero trades across 1,200 real transactions while its
    tests passed: the fixtures were written to match the assumption rather than
    the API.

    * **Wallet API** (`/v1/wallet/{address}/history`) returns `balanceChanges`,
      a list of signed per-mint deltas already scoped to the wallet. Wrapped SOL
      is the quote leg.
    * **Enhanced Transactions API** returns `tokenTransfers` and
      `nativeTransfers` with explicit from/to accounts.

    Both reduce to the same thing: what the wallet gained or lost, by mint.
    """
    changes = transaction.get("balanceChanges")
    if isinstance(changes, list) and changes:
        sol = 0.0
        tokens: dict[str, float] = {}
        for change in changes:
            mint = str(change.get("mint") or "")
            try:
                amount = float(change.get("amount") or 0.0)
            except (TypeError, ValueError):
                continue
            if not mint or amount == 0.0:
                continue
            if mint == WRAPPED_SOL:
                sol += amount
            else:
                tokens[mint] = tokens.get(mint, 0.0) + amount
        return sol, tokens

    # Enhanced Transactions shape.
    sol = _sol_delta(transaction, address)
    tokens = {}
    for transfer in transaction.get("tokenTransfers") or []:
        mint = str(transfer.get("mint") or "")
        if not mint:
            continue
        quantity = _amount(transfer)
        if quantity <= 0:
            continue
        if transfer.get("toUserAccount") == address:
            tokens[mint] = tokens.get(mint, 0.0) + quantity
        elif transfer.get("fromUserAccount") == address:
            tokens[mint] = tokens.get(mint, 0.0) - quantity
    return sol, tokens


def _sol_delta(transaction: dict[str, Any], address: str) -> float:
    """Net SOL the wallet gained (positive) or spent (negative), in SOL."""
    lamports = 0.0
    for transfer in transaction.get("nativeTransfers") or []:
        try:
            value = float(transfer.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if transfer.get("toUserAccount") == address:
            lamports += value
        elif transfer.get("fromUserAccount") == address:
            lamports -= value
    return lamports / LAMPORTS_PER_SOL


def profile_wallet(
    address: str,
    transactions: list[dict[str, Any]],
    *,
    history_truncated: bool = True,
) -> WalletProfile:
    """Reconstruct trades and statistics from enhanced transaction history.

    Round trips are matched FIFO within each mint. A buy that is never sold is
    an open position and is excluded, because counting it at cost would report a
    losing hold as a scratch and counting it at zero would invent a loss. That
    exclusion is the same choice the strategy backtest makes for the same
    reason, and it means these figures describe *closed* results only.
    """
    ordered = sorted(transactions, key=lambda item: float(item.get("timestamp") or 0))

    open_lots: dict[str, list[tuple[datetime, float, float]]] = {}
    trades: list[WalletTrade] = []
    mints_created: list[str] = []
    liquidity_events = 0
    unexplained = 0
    priceable = 0
    unpriceable = 0
    seen_mints: set[str] = set()

    for transaction in ordered:
        moment = _timestamp(transaction.get("timestamp"))
        kind = str(transaction.get("type") or "").upper()
        if "CREATE" in kind or "INITIALIZE_MINT" in kind:
            for transfer in transaction.get("tokenTransfers") or []:
                mint = str(transfer.get("mint") or "")
                if mint and mint not in mints_created:
                    mints_created.append(mint)
        if "LIQUIDITY" in kind:
            liquidity_events += 1

        sol, tokens = swap_legs(transaction, address)
        if not tokens or moment is None:
            continue

        moved = [delta for delta in tokens.values() if delta != 0]
        if sol == 0 and len(moved) >= 2:
            # A token-to-token swap. Valuing it needs a price for one leg at
            # that timestamp, which this data does not carry. Counted rather
            # than dropped: a silently skipped swap is indistinguishable from a
            # wallet that did not trade, and that is the reading to avoid.
            unpriceable += 1
            for mint in tokens:
                seen_mints.add(mint)
            continue

        if sol == 0:
            # A single-token event with no SOL leg: an airdrop, a transfer in or
            # out, a burn. Not a trade and not priceable, and by far the most
            # common thing in a memecoin wallet -- 88% of 8,100 real
            # transactions. Counting it as readable would report 96% coverage
            # for a method that can actually price 3%.
            continue

        priceable += 1

        for mint, delta in tokens.items():
            if delta == 0:
                continue
            seen_mints.add(mint)

            if delta > 0 and sol < 0:
                # Bought: tokens in, SOL out.
                open_lots.setdefault(mint, []).append((moment, delta, abs(sol)))
            elif delta < 0 and sol > 0:
                # Sold: tokens out, SOL in.
                quantity = abs(delta)
                lots = open_lots.get(mint) or []
                if not lots:
                    # Sold something never seen bought: an airdrop, a transfer in
                    # from another wallet, or history that starts mid-position.
                    unexplained += 1
                    continue
                remaining = quantity
                proceeds_rate = sol / quantity
                while remaining > 1e-12 and lots:
                    opened_at, lot_quantity, lot_cost = lots[0]
                    used = min(remaining, lot_quantity)
                    share = used / lot_quantity if lot_quantity else 0.0
                    trades.append(
                        WalletTrade(
                            mint=mint,
                            opened_at=opened_at,
                            closed_at=moment,
                            cost_sol=round(lot_cost * share, 9),
                            proceeds_sol=round(proceeds_rate * used, 9),
                        )
                    )
                    remaining -= used
                    if used >= lot_quantity - 1e-12:
                        lots.pop(0)
                    else:
                        lots[0] = (opened_at, lot_quantity - used, lot_cost * (1 - share))
                if remaining > 1e-12:
                    unexplained += 1

    return WalletProfile(
        address=address,
        transactions_observed=len(transactions),
        history_truncated=history_truncated,
        trades=tuple(trades),
        distinct_tokens=len(seen_mints),
        mints_created=tuple(mints_created),
        liquidity_events=liquidity_events,
        unexplained_transfers=unexplained,
        priceable_transactions=priceable,
        unpriceable_swaps=unpriceable,
    )


def classify_wallet(
    profile: WalletProfile, limits: CohortLimits | None = None
) -> tuple[WalletClass, tuple[str, ...]]:
    """Assign a class and the reasons behind it.

    Returns the class plus the notes that produced it, so a classification can
    be argued with rather than merely accepted.
    """
    limits = limits or CohortLimits()
    notes: list[str] = []

    if profile.mints_created:
        notes.append(f"created_{len(profile.mints_created)}_mints")
        return WalletClass.DEPLOYER, tuple(notes)

    if profile.liquidity_events and not profile.trades:
        return WalletClass.LIQUIDITY_PROVIDER, ("liquidity_events_without_trades",)

    if profile.trade_count == 0:
        return WalletClass.INSUFFICIENT_HISTORY, ("no_completed_round_trips",)

    # Volume without position: many round trips that net to approximately
    # nothing is manufactured activity, not trading.
    if (
        profile.trade_count >= limits.minimum_wash_trade_count
        and profile.gross_profit_sol + profile.gross_loss_sol > 0
    ):
        turnover = profile.gross_profit_sol + profile.gross_loss_sol
        net_share = 100.0 * abs(profile.realised_pnl_sol) / turnover
        if net_share < limits.wash_trade_net_tolerance_pct:
            notes.append(f"net_{net_share:.2f}pct_of_turnover")
            return WalletClass.WASH_TRADER, tuple(notes)

    median_hold = profile.median_holding_seconds
    if median_hold is not None and median_hold < limits.sniper_median_hold_seconds:
        notes.append(f"median_hold_{median_hold:.0f}s")
        return WalletClass.SNIPER, tuple(notes)

    # From here the wallet is a candidate trader, and the two hard rules apply.
    # Order matters: sample size is checked before performance, so a spectacular
    # short record can never be talked into a classification.
    if profile.trade_count < limits.minimum_observable_trades:
        notes.append(f"only_{profile.trade_count}_trades")
        return WalletClass.INSUFFICIENT_HISTORY, tuple(notes)

    if profile.distinct_tokens < limits.minimum_distinct_tokens:
        notes.append(f"only_{profile.distinct_tokens}_tokens")
        return WalletClass.INSUFFICIENT_HISTORY, tuple(notes)

    share = profile.best_trade_profit_share
    if share is not None and share > limits.maximum_single_trade_profit_share:
        notes.append(f"best_trade_is_{100 * share:.0f}pct_of_gross_profit")
        return WalletClass.OUTLIER_DEPENDENT, tuple(notes)

    factor = profile.profit_factor
    if profile.realised_pnl_sol <= 0:
        notes.append("realised_pnl_not_positive")
        return WalletClass.UNPROFITABLE, tuple(notes)
    if factor is None:
        # No losing trades at all across a long record is not a perfect trader;
        # it is a record whose losses are somewhere this history cannot see.
        notes.append("no_losing_trades_observed")
        return WalletClass.UNCLASSIFIED, tuple(notes)
    if factor < limits.minimum_profit_factor:
        notes.append(f"profit_factor_{factor}")
        return WalletClass.UNPROFITABLE, tuple(notes)

    if profile.history_truncated:
        notes.append("history_truncated")
    if profile.unexplained_transfers:
        notes.append(f"{profile.unexplained_transfers}_unexplained_transfers")
    notes.append(f"{profile.trade_count}_trades_pf_{factor}")
    return WalletClass.REPEATABLE_TRADER, tuple(notes)
