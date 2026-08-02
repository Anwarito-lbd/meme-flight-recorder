from __future__ import annotations

from dataclasses import asdict, dataclass

from .journal import FlightRecorder
from .models import Universe
from .paper import PaperBroker, PaperPosition
from .risk import PortfolioState, RiskEngine


@dataclass(frozen=True)
class ShadowAssumptions:
    latency_ms: int = 1500
    entry_slippage_pct: float = 0.75
    exit_slippage_pct: float = 1.0
    round_trip_fee_pct: float = 0.6
    failed_transaction_probability: float = 0.05


@dataclass(frozen=True)
class ShadowDecision:
    approved: bool
    reason: str
    position: PaperPosition | None = None


class SolanaShadowEngine:
    """Conservative hypothetical fills; never builds, signs, or broadcasts a transaction."""

    def __init__(
        self,
        recorder: FlightRecorder,
        broker: PaperBroker,
        risk: RiskEngine,
        assumptions: ShadowAssumptions | None = None,
    ) -> None:
        self.recorder = recorder
        self.broker = broker
        self.risk = risk
        self.assumptions = assumptions or ShadowAssumptions()

    def open(
        self,
        mint: str,
        quoted_price: float,
        entry_price_impact_pct: float,
        exit_route_verified: bool,
        simulation_ok: bool,
        state: PortfolioState,
    ) -> ShadowDecision:
        evaluation = self.recorder.last_event(f"solana:{mint}", "candidate_evaluated")
        if (
            evaluation is None
            or evaluation["payload"].get("status") != "eligible_for_strategy_review"
        ):
            return self._record(mint, ShadowDecision(False, "candidate_safety_gate_missing"))
        if quoted_price <= 0:
            return self._record(mint, ShadowDecision(False, "invalid_quote"))
        if not exit_route_verified:
            return self._record(mint, ShadowDecision(False, "exit_route_not_verified"))
        if not simulation_ok:
            return self._record(mint, ShadowDecision(False, "simulation_failed"))
        decision = self.risk.approve(Universe.SOLANA_EMERGING, state, quoted_price)
        if not decision.approved:
            return self._record(mint, ShadowDecision(False, decision.reason))

        slippage = max(0.0, entry_price_impact_pct) + self.assumptions.entry_slippage_pct
        fill_price = quoted_price * (1 + slippage / 100)
        fees = decision.position_value_usd * self.assumptions.round_trip_fee_pct / 100
        position = self.broker.open_position(
            mint,
            Universe.SOLANA_EMERGING,
            fill_price,
            decision.position_value_usd,
            risk_amount_usd=decision.risk_amount_usd,
            fees_paid_usd=fees,
            modeled_slippage_pct=slippage,
        )
        return self._record(mint, ShadowDecision(True, "shadow_position_opened", position))

    def close(
        self, position_id: str, quoted_exit_price: float, reason: str
    ) -> dict[str, float | str]:
        if quoted_exit_price <= 0:
            raise ValueError("quoted exit price must be positive")
        fill = quoted_exit_price * (1 - self.assumptions.exit_slippage_pct / 100)
        result = self.broker.close_position(position_id, fill, reason)
        self.recorder.append(
            "solana_shadow_exit",
            str(result["asset_id"]),
            result
            | {
                "quoted_exit_price": quoted_exit_price,
                "modeled_latency_ms": self.assumptions.latency_ms,
            },
        )
        return result

    def _record(self, mint: str, decision: ShadowDecision) -> ShadowDecision:
        payload = asdict(decision)
        if decision.position is not None:
            payload["position"]["universe"] = decision.position.universe.value
        payload["assumptions"] = asdict(self.assumptions)
        self.recorder.append("solana_shadow_decision", mint, payload)
        return decision
