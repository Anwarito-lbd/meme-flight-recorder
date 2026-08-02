from __future__ import annotations

from dataclasses import dataclass

from .config import RiskLimits
from .models import Universe


@dataclass(frozen=True)
class PortfolioState:
    equity_usd: float
    daily_realized_pnl_usd: float = 0.0
    open_positions: int = 0
    aggregate_open_risk_usd: float = 0.0
    consecutive_losses: int = 0
    kill_switch: bool = False


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason: str
    position_value_usd: float = 0.0
    risk_amount_usd: float = 0.0


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def approve(
        self,
        universe: Universe,
        state: PortfolioState,
        entry_price: float,
        stop_price: float | None = None,
    ) -> RiskDecision:
        if state.kill_switch:
            return RiskDecision(False, "kill_switch_active")
        daily_limit = state.equity_usd * self.limits.maximum_daily_loss_pct / 100
        if state.daily_realized_pnl_usd <= -daily_limit:
            return RiskDecision(False, "daily_loss_limit_reached")
        if state.consecutive_losses >= self.limits.consecutive_loss_limit:
            return RiskDecision(False, "consecutive_loss_limit_reached")
        if state.open_positions >= self.limits.maximum_open_positions:
            return RiskDecision(False, "maximum_open_positions_reached")
        max_open_risk = state.equity_usd * self.limits.maximum_aggregate_open_risk_pct / 100

        if universe == Universe.CEX_ESTABLISHED:
            if stop_price is None or not (0 < stop_price < entry_price):
                return RiskDecision(False, "invalid_or_missing_stop")
            risk = state.equity_usd * self.limits.risk_per_cex_trade_pct / 100
            stop_pct = (entry_price - stop_price) / entry_price
            # Spot-only invariant: a tight stop must never imply leveraged notional.
            position = min(risk / stop_pct, state.equity_usd)
        else:
            risk = state.equity_usd * self.limits.capital_at_risk_per_solana_trade_pct / 100
            position = risk

        if state.aggregate_open_risk_usd + risk > max_open_risk:
            return RiskDecision(False, "aggregate_open_risk_exceeded")
        return RiskDecision(True, "approved", round(position, 2), round(risk, 2))
