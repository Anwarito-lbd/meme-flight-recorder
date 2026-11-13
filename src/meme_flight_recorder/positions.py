"""Paper positions: opening, scaling out, closing, and honest accounting.

This is where a decision becomes a number. Everything upstream produces
opinions; a closed position produces a P&L that can be added up, and expectancy
is only computable once those exist.

Costs are charged on both legs and are never optional. At this account's size
they dominate: a $4 position paying roughly 3% each way needs a 6% move simply
to break even, so a strategy reported gross would look profitable while losing
money on every trade. The temptation to quote gross returns is exactly what this
module exists to remove.

Partial exits are tracked properly rather than averaged, because scaling out at
1R and 2R changes the arithmetic. A position that takes a third off at 1R and
then stops out at breakeven is a small winner, not a scratch, and a model that
collapses it to a single exit price cannot see that.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from .exits import ExitReason


@dataclass(frozen=True)
class Fill:
    """One execution against a position, entry or exit."""

    at: datetime
    price: float
    quantity: float
    cost_usd: float
    reason: str = ""

    @property
    def gross_usd(self) -> float:
        return self.price * self.quantity


@dataclass(frozen=True)
class PaperPosition:
    """An open or closed simulated position. Immutable; operations return copies."""

    mint: str
    symbol: str
    opened_at: datetime
    entry_price: float
    quantity: float
    stop_price: float
    target_price: float
    breakout_level: float
    atr: float = 0.0
    entry_liquidity_usd: float = 0.0
    peak_liquidity_usd: float = 0.0
    high_water_price: float = 0.0
    fills: tuple[Fill, ...] = field(default_factory=tuple)
    closed_at: datetime | None = None
    close_reason: ExitReason | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.closed_at is None and self.remaining_quantity > 1e-12

    @property
    def entry_fill(self) -> Fill | None:
        return self.fills[0] if self.fills else None

    @property
    def exit_fills(self) -> tuple[Fill, ...]:
        return self.fills[1:]

    @property
    def exited_quantity(self) -> float:
        return sum(fill.quantity for fill in self.exit_fills)

    @property
    def remaining_quantity(self) -> float:
        return max(self.quantity - self.exited_quantity, 0.0)

    @property
    def scaled_out_fraction(self) -> float:
        return self.exited_quantity / self.quantity if self.quantity else 0.0

    @property
    def cost_basis_usd(self) -> float:
        entry = self.entry_fill
        return (entry.gross_usd + entry.cost_usd) if entry else 0.0

    @property
    def realised_usd(self) -> float:
        """Proceeds of exits so far, net of their costs and their share of entry."""
        if not self.entry_fill or not self.quantity:
            return 0.0
        proceeds = sum(fill.gross_usd - fill.cost_usd for fill in self.exit_fills)
        basis = self.cost_basis_usd * (self.exited_quantity / self.quantity)
        return round(proceeds - basis, 6)

    def unrealised_usd(self, mark_price: float) -> float:
        if not self.entry_fill or not self.quantity or self.remaining_quantity <= 0:
            return 0.0
        basis = self.cost_basis_usd * (self.remaining_quantity / self.quantity)
        return round(mark_price * self.remaining_quantity - basis, 6)

    @property
    def realised_r(self) -> float | None:
        """Result in units of the risk originally taken."""
        risk_per_unit = self.entry_price - self.stop_price
        if risk_per_unit <= 0 or not self.quantity:
            return None
        return round(self.realised_usd / (risk_per_unit * self.quantity), 4)

    @property
    def return_pct(self) -> float | None:
        basis = self.cost_basis_usd
        return round(100.0 * self.realised_usd / basis, 4) if basis else None


def open_position(
    *,
    mint: str,
    symbol: str,
    at: datetime,
    price: float,
    position_usd: float,
    stop_price: float,
    target_price: float,
    breakout_level: float,
    atr: float = 0.0,
    liquidity_usd: float = 0.0,
    cost_pct: float = 3.0,
    metadata: dict[str, Any] | None = None,
) -> PaperPosition:
    """Open a position, charging entry cost immediately.

    ``position_usd`` is the notional the risk engine approved. Entry cost is
    charged on top rather than netted out, so the recorded quantity matches what
    the notional would actually have bought.
    """
    if price <= 0 or position_usd <= 0:
        raise ValueError("price and position_usd must be positive")
    quantity = position_usd / price
    entry = Fill(
        at=at,
        price=price,
        quantity=quantity,
        cost_usd=position_usd * cost_pct / 100.0,
        reason="entry",
    )
    return PaperPosition(
        mint=mint,
        symbol=symbol,
        opened_at=at,
        entry_price=price,
        quantity=quantity,
        stop_price=stop_price,
        target_price=target_price,
        breakout_level=breakout_level,
        atr=atr,
        entry_liquidity_usd=liquidity_usd,
        peak_liquidity_usd=liquidity_usd,
        high_water_price=price,
        fills=(entry,),
        metadata=dict(metadata or {}),
    )


def apply_exit(
    position: PaperPosition,
    *,
    at: datetime,
    price: float,
    fraction: float,
    reason: ExitReason,
    cost_pct: float = 3.0,
) -> PaperPosition:
    """Sell part or all of a position at an already-modelled fill price.

    ``price`` must be the realistic fill, not the quoted price -- see
    ``exits.realistic_fill_price``. ``fraction`` is of the *original* quantity,
    matching how the exit hierarchy expresses its scale-outs.
    """
    if not position.is_open:
        return position
    quantity = min(position.quantity * max(fraction, 0.0), position.remaining_quantity)
    if quantity <= 0:
        return position

    fill = Fill(
        at=at,
        price=price,
        quantity=quantity,
        cost_usd=price * quantity * cost_pct / 100.0,
        reason=reason.value,
    )
    updated = replace(position, fills=position.fills + (fill,))
    if updated.remaining_quantity <= 1e-12:
        updated = replace(updated, closed_at=at, close_reason=reason)
    return updated


def mark(position: PaperPosition, *, price: float, liquidity_usd: float | None = None) -> PaperPosition:
    """Record a new observation against an open position.

    High-water price and peak liquidity are tracked because the exit rules
    measure against the best the position has seen, not against entry. A pool
    that doubled and then fell back to its entry level has still lost half its
    depth, and a drawdown measured from entry would miss that entirely.
    """
    if not position.is_open:
        return position
    return replace(
        position,
        high_water_price=max(position.high_water_price, price),
        peak_liquidity_usd=max(
            position.peak_liquidity_usd,
            liquidity_usd if liquidity_usd is not None else 0.0,
        ),
    )


@dataclass(frozen=True)
class TradeSummary:
    """A closed trade reduced to the fields expectancy needs."""

    mint: str
    symbol: str
    opened_at: datetime
    closed_at: datetime | None
    reason: str
    realised_usd: float
    return_pct: float | None
    realised_r: float | None
    holding_minutes: float | None

    @property
    def is_win(self) -> bool:
        return self.realised_usd > 0


def summarise(position: PaperPosition) -> TradeSummary:
    holding = None
    if position.closed_at is not None:
        holding = (position.closed_at - position.opened_at).total_seconds() / 60
    return TradeSummary(
        mint=position.mint,
        symbol=position.symbol,
        opened_at=position.opened_at,
        closed_at=position.closed_at,
        reason=position.close_reason.value if position.close_reason else "open",
        realised_usd=position.realised_usd,
        return_pct=position.return_pct,
        realised_r=position.realised_r,
        holding_minutes=holding,
    )
