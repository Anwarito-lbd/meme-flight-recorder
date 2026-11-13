"""Persisting paper positions, so a track record survives a restart.

`positions.py` models a position honestly -- partial fills, costs on both legs,
exit reasons -- but only in memory. `paper.py` persists, but through an older and
much weaker model that cannot express a scale-out. Nothing has ever written a
position of either kind: the journal holds 3,152 observations and zero
positions, which is why this system has a backtest and no track record.

This closes that gap for the richer model.

**Event names are deliberately not `paper_position_opened` / `_closed`.**
`journal.position_events()` queries exactly those, and
`paper.PaperBroker._restore_open_positions` feeds the results into its own,
different dataclass via `PaperPosition(**payload)`. Writing this model's richer
payload under those names would crash the CEX and shadow engines on their next
restart -- not at write time, but later, in a different component, which is the
worst way for a bug to arrive. New names keep the two ledgers separate.

The journal is append-only, so a position is reconstructed by replaying its
events rather than mutated in place. That is slower and entirely worth it: the
history of how a position was managed is itself the record being kept.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .exits import ExitReason
from .journal import FlightRecorder
from .positions import Fill, PaperPosition

EVENT_POSITION_OPENED = "cohort_position_opened"
EVENT_POSITION_CLOSED = "cohort_position_closed"


def _fill_to_dict(fill: Fill) -> dict[str, Any]:
    return {
        "at": fill.at.isoformat(),
        "price": fill.price,
        "quantity": fill.quantity,
        "cost_usd": fill.cost_usd,
        "reason": fill.reason,
    }


def _fill_from_dict(data: dict[str, Any]) -> Fill:
    return Fill(
        at=datetime.fromisoformat(str(data["at"])),
        price=float(data["price"]),
        quantity=float(data["quantity"]),
        cost_usd=float(data["cost_usd"]),
        reason=str(data.get("reason") or ""),
    )


def to_payload(position: PaperPosition) -> dict[str, Any]:
    """Serialise a position completely enough to rebuild it.

    Every field is stored, including the fills. A summary would be smaller and
    would quietly destroy the ability to recompute results under a different
    cost or exit assumption later, which is most of the point of keeping them.
    """
    return {
        "mint": position.mint,
        "symbol": position.symbol,
        "opened_at": position.opened_at.isoformat(),
        "entry_price": position.entry_price,
        "quantity": position.quantity,
        "stop_price": position.stop_price,
        "target_price": position.target_price,
        "breakout_level": position.breakout_level,
        "atr": position.atr,
        "entry_liquidity_usd": position.entry_liquidity_usd,
        "peak_liquidity_usd": position.peak_liquidity_usd,
        "high_water_price": position.high_water_price,
        "fills": [_fill_to_dict(fill) for fill in position.fills],
        "closed_at": position.closed_at.isoformat() if position.closed_at else None,
        "close_reason": position.close_reason.value if position.close_reason else None,
        "metadata": position.metadata,
        # Denormalised for analysis convenience. Recomputable from the fills, and
        # deliberately not trusted when rebuilding -- see from_payload.
        "realised_usd": position.realised_usd,
        "return_pct": position.return_pct,
        "realised_r": position.realised_r,
    }


def from_payload(payload: dict[str, Any]) -> PaperPosition:
    """Rebuild a position from a journalled payload.

    The denormalised result fields are ignored and recomputed from the fills. If
    the two ever disagree, the fills are the record and the summary is a stale
    copy of it.
    """
    close_reason = payload.get("close_reason")
    closed_at = payload.get("closed_at")
    return PaperPosition(
        mint=str(payload["mint"]),
        symbol=str(payload.get("symbol") or ""),
        opened_at=datetime.fromisoformat(str(payload["opened_at"])),
        entry_price=float(payload["entry_price"]),
        quantity=float(payload["quantity"]),
        stop_price=float(payload.get("stop_price") or 0.0),
        target_price=float(payload.get("target_price") or 0.0),
        breakout_level=float(payload.get("breakout_level") or 0.0),
        atr=float(payload.get("atr") or 0.0),
        entry_liquidity_usd=float(payload.get("entry_liquidity_usd") or 0.0),
        peak_liquidity_usd=float(payload.get("peak_liquidity_usd") or 0.0),
        high_water_price=float(payload.get("high_water_price") or 0.0),
        fills=tuple(_fill_from_dict(fill) for fill in payload.get("fills") or ()),
        closed_at=datetime.fromisoformat(str(closed_at)) if closed_at else None,
        close_reason=ExitReason(close_reason) if close_reason else None,
        metadata=dict(payload.get("metadata") or {}),
    )


def save_opened(recorder: FlightRecorder, position: PaperPosition) -> str:
    """Record a newly opened position.

    Idempotent on (mint, opened_at): a retried collector cycle cannot open the
    same position twice, which would double the recorded exposure and corrupt
    every expectancy figure computed afterwards.
    """
    return recorder.append_once(
        EVENT_POSITION_OPENED,
        entity_id=position.mint,
        payload=to_payload(position),
        idempotency_key=(
            f"{EVENT_POSITION_OPENED}:{position.mint}:"
            f"{position.opened_at.isoformat(timespec='seconds')}"
        ),
    )


def save_closed(recorder: FlightRecorder, position: PaperPosition) -> str:
    """Record a position that has been fully exited."""
    if position.is_open:
        raise ValueError("save_closed requires a fully exited position")
    return recorder.append(
        EVENT_POSITION_CLOSED,
        entity_id=position.mint,
        payload=to_payload(position),
    )


def _events(recorder: FlightRecorder) -> list[dict[str, Any]]:
    opened = recorder.events_by_type(EVENT_POSITION_OPENED)
    closed = recorder.events_by_type(EVENT_POSITION_CLOSED)
    return sorted(opened + closed, key=lambda event: int(event["id"]))


def open_positions(recorder: FlightRecorder) -> dict[str, PaperPosition]:
    """Rebuild the currently open positions by replaying the journal.

    Keyed by mint, because the monitor holds at most one position per mint --
    re-entering while still holding would multiply exposure past what risk
    approved, the same rule the backtest enforces with `blocked_until`.
    """
    live: dict[str, PaperPosition] = {}
    for event in _events(recorder):
        payload = event.get("payload") or json.loads(event["payload_json"])
        mint = str(payload.get("mint") or event["entity_id"])
        if event["event_type"] == EVENT_POSITION_OPENED:
            live[mint] = from_payload(payload)
        else:
            live.pop(mint, None)
    return live


def closed_positions(recorder: FlightRecorder) -> list[PaperPosition]:
    """Every completed trade, oldest first. This is the track record."""
    return [
        from_payload(event.get("payload") or json.loads(event["payload_json"]))
        for event in recorder.events_by_type(EVENT_POSITION_CLOSED)
    ]
