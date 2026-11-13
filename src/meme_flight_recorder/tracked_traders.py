"""Qualified traders, their skill, their influence, and whether they agree.

FOMO (fomo.family) sits behind a login, so this never calls it. The design
inverts the dependency instead: a human maps a FOMO trader name to a **public**
Solana wallet once, and from then on the wallet is watched directly on chain via
Helius. That is strictly better than waiting for a UI notification -- the trade
is visible when it lands, not when a product decides to show it -- and it needs
no private endpoint, no scraping and no credential.

Three things are kept rigorously apart, because conflating them is the classic
error of every copy-trading product:

  * **SELECTION_SKILL** -- does this wallet pick tokens that go up on their own?
  * **INFLUENCE** -- does the token go up *because* this wallet was seen buying?
  * **REMAINING ALPHA** -- whatever is left by the time we could actually fill.

A wallet with high influence and no selection skill is a wallet whose followers
pay for its entries. Only the third number is tradeable, and it is measured
after detection, after quote, and after modelled fill, because those latencies
are where a copy strategy actually dies.

Confluence requires **independent** wallets. Wallets funded from a common source
are one actor wearing several hats, and counting them as agreement manufactures
confirmation out of nothing.

Pure. A caller supplies observed trades; this module never fetches.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

# Reaction horizons after a tracked wallet's entry. The short ones exist to show
# how fast the opportunity disappears, not because we could ever act on them.
REACTION_HORIZONS_SECONDS: tuple[int, ...] = (1, 3, 5, 15, 30, 60, 300)

# A profile below this many observed trades is reported as unqualified rather
# than scored. The project's own wallet study found one wallet clearing 30
# trades out of six, and its verdict covered 12% of its activity.
MINIMUM_TRADES_FOR_QUALIFICATION = 30

# Confluence window: independent entries inside this are treated as agreement.
CONFLUENCE_WINDOW_SECONDS = 300


class TraderTier(StrEnum):
    UNQUALIFIED = "unqualified"
    QUALIFIED = "qualified"
    OUTLIER_DEPENDENT = "outlier_dependent"
    DISQUALIFIED = "disqualified"


@dataclass(frozen=True)
class TrackedWallet:
    """A public wallet, and how we came to watch it."""

    address: str
    label: str
    source: str = "manual"
    fomo_handle: str | None = None
    funded_by: str | None = None
    added_at: datetime | None = None


@dataclass(frozen=True)
class ObservedTrade:
    wallet: str
    mint: str
    side: str
    at: datetime
    sol_amount: float | None = None
    price_usd: float | None = None
    slot: int | None = None
    signature: str | None = None


@dataclass(frozen=True)
class TraderProfile:
    wallet: str
    trades_observed: int
    coverage_pct: float | None
    win_rate: float | None
    median_return: float | None
    profit_factor: float | None
    max_drawdown: float | None
    best_trade_dependency: float | None
    rug_exposure: float | None
    average_hold_minutes: float | None
    entry_market_cap_bands: dict[str, int] = field(default_factory=dict)
    narrative_specialisation: dict[str, int] = field(default_factory=dict)
    tier: TraderTier = TraderTier.UNQUALIFIED

    @property
    def is_qualified(self) -> bool:
        return self.tier is TraderTier.QUALIFIED


def profile_trader(
    wallet: str,
    closed_returns: list[float],
    *,
    coverage_pct: float | None = None,
    hold_minutes: list[float] | None = None,
    rug_count: int = 0,
    market_cap_bands: dict[str, int] | None = None,
    narratives: dict[str, int] | None = None,
) -> TraderProfile:
    """Score a wallet on returns it actually produced.

    `closed_returns` are multiples where 1.0 is flat. Coverage is carried
    through because the wallet study measured 0.17%-12% readable coverage on
    real wallets: a verdict computed on 3% of a wallet's activity is a verdict
    about 3% of its activity, and saying so is the difference between a number
    and a claim.
    """
    trades = len(closed_returns)
    if trades == 0:
        return TraderProfile(
            wallet=wallet,
            trades_observed=0,
            coverage_pct=coverage_pct,
            win_rate=None,
            median_return=None,
            profit_factor=None,
            max_drawdown=None,
            best_trade_dependency=None,
            rug_exposure=None,
            average_hold_minutes=None,
            tier=TraderTier.UNQUALIFIED,
        )

    profits = [value - 1.0 for value in closed_returns if value > 1.0]
    losses = [1.0 - value for value in closed_returns if value < 1.0]
    profit_factor = (sum(profits) / sum(losses)) if losses else None

    equity, peak, drawdown = 0.0, 0.0, 0.0
    for value in closed_returns:
        equity += value - 1.0
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)

    gross = sum(profits)
    best_dependency = (max(profits) / gross) if profits and gross > 0 else None

    tier = TraderTier.UNQUALIFIED
    if trades >= MINIMUM_TRADES_FOR_QUALIFICATION:
        if profit_factor is not None and profit_factor > 1.2:
            # One trade carrying most of the profit is not skill, it is a
            # lottery ticket that already paid. The project has seen exactly
            # this shape and named it.
            tier = (
                TraderTier.OUTLIER_DEPENDENT
                if best_dependency is not None and best_dependency > 0.5
                else TraderTier.QUALIFIED
            )
        else:
            tier = TraderTier.DISQUALIFIED

    return TraderProfile(
        wallet=wallet,
        trades_observed=trades,
        coverage_pct=coverage_pct,
        win_rate=100.0 * len(profits) / trades,
        median_return=statistics.median(closed_returns),
        profit_factor=profit_factor,
        max_drawdown=drawdown,
        best_trade_dependency=best_dependency,
        rug_exposure=100.0 * rug_count / trades,
        average_hold_minutes=statistics.fmean(hold_minutes) if hold_minutes else None,
        entry_market_cap_bands=dict(market_cap_bands or {}),
        narrative_specialisation=dict(narratives or {}),
        tier=tier,
    )


@dataclass(frozen=True)
class ConfluenceEvent:
    mint: str
    at: datetime
    wallets: tuple[str, ...]
    independent_wallets: tuple[str, ...]
    qualified_wallets: tuple[str, ...]
    window_seconds: int

    @property
    def independent_count(self) -> int:
        return len(self.independent_wallets)

    @property
    def is_confirmation(self) -> bool:
        """Two or more independent *qualified* wallets. Both words are load-bearing."""
        independent = set(self.independent_wallets)
        return len([w for w in self.qualified_wallets if w in independent]) >= 2


def independent_subset(
    wallets: list[str], registry: dict[str, TrackedWallet]
) -> tuple[str, ...]:
    """Drop wallets that share a funding source, keeping one per source.

    Common funding is the cheapest available evidence that two addresses are one
    actor. It is not proof of it, and it is not exhaustive -- but counting
    sibling wallets as independent agreement is a much worse error than
    discarding a genuinely separate trader who happened to share a funder.
    """
    kept: list[str] = []
    seen_funders: set[str] = set()
    for wallet in wallets:
        entry = registry.get(wallet)
        funder = entry.funded_by if entry else None
        if funder:
            if funder in seen_funders:
                continue
            seen_funders.add(funder)
        kept.append(wallet)
    return tuple(kept)


def find_confluence(
    trades: list[ObservedTrade],
    registry: dict[str, TrackedWallet],
    qualified: set[str],
    window_seconds: int = CONFLUENCE_WINDOW_SECONDS,
) -> list[ConfluenceEvent]:
    """Group buys of the same mint by independent wallets inside a window."""
    buys = sorted(
        (trade for trade in trades if trade.side == "buy"), key=lambda trade: trade.at
    )
    by_mint: dict[str, list[ObservedTrade]] = {}
    for trade in buys:
        by_mint.setdefault(trade.mint, []).append(trade)

    events: list[ConfluenceEvent] = []
    for mint, mint_trades in by_mint.items():
        used: set[int] = set()
        for index, anchor in enumerate(mint_trades):
            if index in used:
                continue
            window_end = anchor.at + timedelta(seconds=window_seconds)
            group = [
                (position, trade)
                for position, trade in enumerate(mint_trades)
                if position not in used and anchor.at <= trade.at <= window_end
            ]
            wallets = list(dict.fromkeys(trade.wallet for _position, trade in group))
            if len(wallets) < 2:
                continue
            for position, _trade in group:
                used.add(position)
            independent = independent_subset(wallets, registry)
            events.append(
                ConfluenceEvent(
                    mint=mint,
                    at=anchor.at,
                    wallets=tuple(wallets),
                    independent_wallets=independent,
                    qualified_wallets=tuple(w for w in wallets if w in qualified),
                    window_seconds=window_seconds,
                )
            )
    return events
