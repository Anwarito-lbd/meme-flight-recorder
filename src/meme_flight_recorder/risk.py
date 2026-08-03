from __future__ import annotations

from dataclasses import dataclass

from .config import MicroCapitalLimits, RiskLimits
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
    def __init__(self, limits: RiskLimits, micro: MicroCapitalLimits | None = None) -> None:
        self.limits = limits
        # Opt-in, like the cluster gates. Without micro limits the engine
        # behaves exactly as before; with them, small accounts get
        # catastrophic-loss sizing and pool-relative liquidity checks.
        self.micro = micro

    def approve(
        self,
        universe: Universe,
        state: PortfolioState,
        entry_price: float,
        stop_price: float | None = None,
        *,
        pool_liquidity_usd: float | None = None,
        estimated_round_trip_cost_pct: float | None = None,
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

        micro_mode = self.micro is not None and state.equity_usd < self.micro.equity_threshold_usd

        if micro_mode:
            assert self.micro is not None
            # Catastrophic-loss sizing: assume the whole position can be lost,
            # because a thin meme coin can gap to zero or stop being sellable
            # before any stop can fill. Percentage-of-equity sizing at this
            # scale produces positions too small to execute.
            position = min(self.micro.max_position_usd, state.equity_usd)
            risk = position
            if position < self.micro.minimum_viable_position_usd:
                return RiskDecision(False, "position_below_viable_minimum")
        elif universe == Universe.CEX_ESTABLISHED:
            if stop_price is None or not (0 < stop_price < entry_price):
                return RiskDecision(False, "invalid_or_missing_stop")
            risk = state.equity_usd * self.limits.risk_per_cex_trade_pct / 100
            stop_pct = (entry_price - stop_price) / entry_price
            # Spot-only invariant: a tight stop must never imply leveraged notional.
            position = min(risk / stop_pct, state.equity_usd)
        else:
            risk = state.equity_usd * self.limits.capital_at_risk_per_solana_trade_pct / 100
            position = risk

        if micro_mode:
            assert self.micro is not None
            open_cap = (
                state.equity_usd * self.micro.maximum_aggregate_open_position_pct / 100
            )
            if state.aggregate_open_risk_usd + position > open_cap:
                return RiskDecision(False, "aggregate_open_position_exceeded")
        elif state.aggregate_open_risk_usd + risk > max_open_risk:
            return RiskDecision(False, "aggregate_open_risk_exceeded")

        if self.micro is not None and universe != Universe.CEX_ESTABLISHED:
            liquidity_reason = self._liquidity(position, pool_liquidity_usd, self.micro)
            if liquidity_reason:
                return RiskDecision(False, liquidity_reason)
            # A trade whose round trip eats most of the expected move is a loss
            # dressed as an opportunity. At micro size this rejects most
            # candidates, which is the informative outcome rather than a fault.
            if estimated_round_trip_cost_pct is None:
                return RiskDecision(False, "round_trip_cost_unknown")
            if estimated_round_trip_cost_pct > self.micro.maximum_round_trip_cost_pct:
                return RiskDecision(False, "round_trip_cost_exceeds_limit")

        return RiskDecision(True, "approved", round(position, 2), round(risk, 2))

    @staticmethod
    def _liquidity(
        position: float, pool_liquidity_usd: float | None, micro: MicroCapitalLimits
    ) -> str | None:
        """Reject on pool depth relative to the order actually being placed.

        Absolute liquidity floors encode an account size. What matters is the
        share of the pool the order represents, bounded below by a floor that
        keeps a tiny order out of a pool nobody can exit.
        """
        if pool_liquidity_usd is None:
            return "pool_liquidity_unknown"
        if pool_liquidity_usd < micro.minimum_pool_liquidity_usd:
            return "pool_liquidity_below_backstop"
        if 100.0 * position / pool_liquidity_usd > micro.maximum_pool_share_pct:
            return "order_too_large_for_pool"
        return None
