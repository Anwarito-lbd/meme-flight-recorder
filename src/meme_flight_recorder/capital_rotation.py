"""Where qualified capital goes when it leaves something else.

A wallet selling A and buying B within a short window is a different signal from
a wallet simply buying B: the first is a *choice between* two things by someone
who already had a position. Aggregated across independent qualified wallets it
describes where attention and capital are actually moving, at the token level
and at the narrative level.

**This is a research feature and it is not an entry signal.** Nothing here
returns a decision, and `StrategyReadiness` remains the only thing that can
authorise an entry. It is stated in the module because the obvious temptation
with a flow number is to trade it directly, and this project's own record is
that untested filters wired into entry produce trades immediately and make every
subsequent number worthless.

Pure. No network.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .tracked_traders import ObservedTrade, TrackedWallet, independent_subset

# A sell and a buy further apart than this are two decisions, not a rotation.
DEFAULT_ROTATION_WINDOW_SECONDS = 900


@dataclass(frozen=True)
class Rotation:
    wallet: str
    from_mint: str
    to_mint: str
    sold_at: datetime
    bought_at: datetime
    sol_out: float | None
    sol_in: float | None

    @property
    def gap_seconds(self) -> float:
        return (self.bought_at - self.sold_at).total_seconds()


@dataclass
class FlowEdge:
    """Aggregated rotation between two tokens, or two narratives."""

    source: str
    target: str
    wallets: set[str] = field(default_factory=set)
    independent_wallets: int = 0
    sol_amount: float = 0.0
    rotations: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None

    @property
    def wallet_count(self) -> int:
        return len(self.wallets)

    @property
    def flow_velocity(self) -> float | None:
        """SOL per minute across the observed span, or None if instantaneous."""
        if not self.first_at or not self.last_at:
            return None
        minutes = (self.last_at - self.first_at).total_seconds() / 60.0
        if minutes <= 0:
            # A single rotation has no rate. Reporting the raw amount as a
            # velocity would make one trade look like a torrent.
            return None
        return round(self.sol_amount / minutes, 6)


def find_rotations(
    trades: list[ObservedTrade],
    window_seconds: int = DEFAULT_ROTATION_WINDOW_SECONDS,
) -> list[Rotation]:
    """Sell of A followed by buy of B by the same wallet inside the window."""
    by_wallet: dict[str, list[ObservedTrade]] = defaultdict(list)
    for trade in trades:
        by_wallet[trade.wallet].append(trade)

    rotations: list[Rotation] = []
    for wallet, wallet_trades in by_wallet.items():
        ordered = sorted(wallet_trades, key=lambda trade: trade.at)
        for index, sale in enumerate(ordered):
            if sale.side != "sell":
                continue
            limit = sale.at + timedelta(seconds=window_seconds)
            for purchase in ordered[index + 1 :]:
                if purchase.at > limit:
                    break
                if purchase.side != "buy" or purchase.mint == sale.mint:
                    continue
                rotations.append(
                    Rotation(
                        wallet=wallet,
                        from_mint=sale.mint,
                        to_mint=purchase.mint,
                        sold_at=sale.at,
                        bought_at=purchase.at,
                        sol_out=sale.sol_amount,
                        sol_in=purchase.sol_amount,
                    )
                )
                break
    return rotations


def aggregate(
    rotations: list[Rotation],
    registry: dict[str, TrackedWallet] | None = None,
    key: str = "mint",
    narrative_of: dict[str, str] | None = None,
) -> list[FlowEdge]:
    """Roll rotations into edges, token->token or narrative->narrative.

    Independent wallet count is carried alongside the raw count for the same
    reason confluence needs it: several wallets funded from one source are one
    actor, and an edge built by one actor is not a flow.
    """
    edges: dict[tuple[str, str], FlowEdge] = {}
    for rotation in rotations:
        if key == "narrative":
            source = (narrative_of or {}).get(rotation.from_mint)
            target = (narrative_of or {}).get(rotation.to_mint)
            if not source or not target or source == target:
                continue
        else:
            source, target = rotation.from_mint, rotation.to_mint

        edge = edges.setdefault((source, target), FlowEdge(source=source, target=target))
        edge.wallets.add(rotation.wallet)
        edge.rotations += 1
        if rotation.sol_in is not None:
            edge.sol_amount += rotation.sol_in
        edge.first_at = (
            rotation.bought_at if edge.first_at is None else min(edge.first_at, rotation.bought_at)
        )
        edge.last_at = (
            rotation.bought_at if edge.last_at is None else max(edge.last_at, rotation.bought_at)
        )

    for edge in edges.values():
        edge.independent_wallets = len(
            independent_subset(sorted(edge.wallets), registry or {})
        )
    return sorted(edges.values(), key=lambda edge: -edge.sol_amount)


def net_flow(edges: list[FlowEdge]) -> dict[str, dict[str, float]]:
    """Inflow, outflow and net per node, so a hub is distinguishable from a sink."""
    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"inflow": 0.0, "outflow": 0.0, "net": 0.0}
    )
    for edge in edges:
        totals[edge.target]["inflow"] += edge.sol_amount
        totals[edge.source]["outflow"] += edge.sol_amount
    for record in totals.values():
        record["net"] = round(record["inflow"] - record["outflow"], 9)
    return dict(totals)
