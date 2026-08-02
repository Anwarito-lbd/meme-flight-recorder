from __future__ import annotations

from dataclasses import asdict, dataclass

from .journal import FlightRecorder
from .models import Universe
from .paper import PaperBroker, PaperPosition
from .risk import PortfolioState, RiskEngine
from .strategy import (
    BreakoutSetup,
    Candle,
    confirm_retest,
    detect_breakout,
    established_meme_trend_ok,
)


@dataclass(frozen=True)
class CexEngineResult:
    status: str
    reason: str
    setup: BreakoutSetup | None = None
    position: PaperPosition | None = None


class CexPaperEngine:
    """Completed-candle state machine for established meme coins; paper orders only."""

    def __init__(self, recorder: FlightRecorder, broker: PaperBroker, risk: RiskEngine) -> None:
        self.recorder = recorder
        self.broker = broker
        self.risk = risk
        self.pending: dict[str, BreakoutSetup] = {}

    def observe(
        self,
        asset_id: str,
        candles_1h: list[Candle],
        candles_15m: list[Candle],
        state: PortfolioState,
    ) -> CexEngineResult:
        evaluation = self.recorder.last_event(f"cex:{asset_id}", "candidate_evaluated")
        if (
            evaluation is None
            or evaluation["payload"].get("status") != "eligible_for_strategy_review"
        ):
            return self._record(
                asset_id, CexEngineResult("blocked", "candidate_safety_gate_missing")
            )
        if not established_meme_trend_ok(candles_1h):
            return self._record(asset_id, CexEngineResult("blocked", "one_hour_trend_failed"))

        setup = self.pending.get(asset_id)
        if setup is None:
            setup = detect_breakout(candles_15m)
            if setup is None:
                return self._record(asset_id, CexEngineResult("watching", "no_breakout"))
            self.pending[asset_id] = setup
            return self._record(
                asset_id, CexEngineResult("breakout_detected", "wait_for_retest", setup)
            )

        after = [c for c in candles_15m if c.timestamp > setup.breakout_timestamp]
        if len(after) > setup.expires_after_candles:
            self.pending.pop(asset_id, None)
            return self._record(
                asset_id, CexEngineResult("expired", "retest_window_expired", setup)
            )
        signal = confirm_retest(setup, after)
        if signal is None:
            return self._record(asset_id, CexEngineResult("waiting", "no_valid_retest_yet", setup))

        decision = self.risk.approve(Universe.CEX_ESTABLISHED, state, signal.entry, signal.stop)
        if not decision.approved:
            return self._record(asset_id, CexEngineResult("risk_rejected", decision.reason, setup))
        position = self.broker.open_position(
            asset_id,
            Universe.CEX_ESTABLISHED,
            signal.entry,
            decision.position_value_usd,
            signal.stop,
            risk_amount_usd=decision.risk_amount_usd,
        )
        self.pending.pop(asset_id, None)
        return self._record(
            asset_id, CexEngineResult("opened", "paper_position_opened", setup, position)
        )

    def _record(self, asset_id: str, result: CexEngineResult) -> CexEngineResult:
        payload = asdict(result)
        if result.position is not None:
            payload["position"]["universe"] = result.position.universe.value
        self.recorder.append("cex_strategy_transition", asset_id, payload)
        return result
