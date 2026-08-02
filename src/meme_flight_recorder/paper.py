from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .journal import FlightRecorder
from .models import Universe


@dataclass(frozen=True)
class PaperPosition:
    position_id: str
    asset_id: str
    universe: Universe
    entry_price: float
    quantity: float
    stop_price: float | None
    target_price: float | None
    opened_at: str
    risk_amount_usd: float = 0.0
    fees_paid_usd: float = 0.0
    modeled_slippage_pct: float = 0.0


class PaperBroker:
    """In-memory paper broker. It has no exchange credentials or broadcast methods."""

    def __init__(self, recorder: FlightRecorder) -> None:
        self.recorder = recorder
        self.positions: dict[str, PaperPosition] = {}
        self._restore_open_positions()

    def open_position(
        self,
        asset_id: str,
        universe: Universe,
        entry_price: float,
        position_value_usd: float,
        stop_price: float | None = None,
        risk_amount_usd: float = 0.0,
        fees_paid_usd: float = 0.0,
        modeled_slippage_pct: float = 0.0,
    ) -> PaperPosition:
        if entry_price <= 0 or position_value_usd <= 0:
            raise ValueError("entry price and position value must be positive")
        target = None
        if universe == Universe.CEX_ESTABLISHED:
            if stop_price is None or stop_price >= entry_price:
                raise ValueError("CEX paper position requires a valid stop below entry")
            target = entry_price + 2 * (entry_price - stop_price)
        position = PaperPosition(
            position_id=str(uuid4()),
            asset_id=asset_id,
            universe=universe,
            entry_price=entry_price,
            quantity=position_value_usd / entry_price,
            stop_price=stop_price,
            target_price=target,
            opened_at=datetime.now(UTC).isoformat(),
            risk_amount_usd=risk_amount_usd,
            fees_paid_usd=fees_paid_usd,
            modeled_slippage_pct=modeled_slippage_pct,
        )
        self.positions[position.position_id] = position
        self.recorder.append("paper_position_opened", asset_id, _serialize(position))
        return position

    def close_position(
        self, position_id: str, exit_price: float, reason: str
    ) -> dict[str, float | str]:
        if exit_price <= 0:
            raise ValueError("exit price must be positive")
        position = self.positions.pop(position_id)
        gross_pnl = (exit_price - position.entry_price) * position.quantity
        pnl = gross_pnl - position.fees_paid_usd
        r_multiple = pnl / position.risk_amount_usd if position.risk_amount_usd > 0 else 0.0
        result: dict[str, float | str] = {
            "position_id": position_id,
            "asset_id": position.asset_id,
            "exit_price": exit_price,
            "pnl_usd": round(pnl, 4),
            "gross_pnl_usd": round(gross_pnl, 4),
            "r_multiple": round(r_multiple, 4),
            "reason": reason,
        }
        self.recorder.append("paper_position_closed", position.asset_id, result)
        return result

    def _restore_open_positions(self) -> None:
        """Rebuild open paper state from the append-only journal after a restart."""
        events = self.recorder.position_events()
        restored: dict[str, PaperPosition] = {}
        for event in events:
            payload = event["payload"]
            if event["event_type"] == "paper_position_opened":
                data = dict(payload)
                data["universe"] = Universe(data["universe"])
                restored[data["position_id"]] = PaperPosition(**data)
            elif event["event_type"] == "paper_position_closed":
                restored.pop(payload["position_id"], None)
        self.positions = restored


def _serialize(position: PaperPosition) -> dict[str, object]:
    data = asdict(position)
    data["universe"] = position.universe.value
    return data
