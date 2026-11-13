"""Selection skill versus influence, and what survives the latency to a fill.

A wallet whose tokens rise the instant it is seen buying may be a good picker,
or it may simply have followers. The two look identical in a return column and
demand opposite responses: the first is worth copying, the second is worth
front-running or ignoring, and copying it is paying for someone else's exit.

The separation used here is mechanical rather than clever:

  * **SELECTION_SKILL** is the token's move over minutes, which is too slow to
    be caused by follower reflex alone.
  * **INFLUENCE** is the move in the first seconds after the entry becomes
    visible on chain, which is too fast to be anything else.

Then the only number that matters for us: **remaining alpha**. The move is
decomposed at three real checkpoints -- when we could detect the trade, when a
quote came back, and when a modelled fill would have landed. Measured latencies
from this system: detection is a WSS event, a Jupiter quote took 196ms, and a
full quote/build/simulate cycle took 589ms. Alpha consumed before that point is
not available to us at any position size.

Pure. The caller supplies the price path; this module only reads it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from .tracked_traders import REACTION_HORIZONS_SECONDS


@dataclass(frozen=True)
class ReactionCurve:
    """Token reaction after one tracked entry, per horizon, as a multiple."""

    wallet: str
    mint: str
    entry_at: datetime
    entry_price: float
    reactions: dict[int, float | None]

    def at(self, seconds: int) -> float | None:
        return self.reactions.get(seconds)


def measure_reaction(
    wallet: str,
    mint: str,
    entry_at: datetime,
    entry_price: float,
    price_path: list[tuple[datetime, float]],
) -> ReactionCurve:
    """Price relative to the entry at each horizon.

    A horizon with no print is None, never the entry price carried forward.
    Carrying forward would report "no reaction" for a token nobody traded, which
    is the Birdeye zero-volume trap in a different costume.
    """
    ordered = sorted(price_path, key=lambda row: row[0])
    reactions: dict[int, float | None] = {}
    for horizon in REACTION_HORIZONS_SECONDS:
        target = entry_at + timedelta(seconds=horizon)
        match = None
        for moment, price in ordered:
            if moment < entry_at:
                continue
            if moment <= target and price > 0:
                match = price
            if moment > target:
                break
        reactions[horizon] = (match / entry_price) if match and entry_price > 0 else None
    return ReactionCurve(wallet, mint, entry_at, entry_price, reactions)


@dataclass(frozen=True)
class InfluenceProfile:
    wallet: str
    samples: int
    influence: float | None
    selection_skill: float | None
    remaining_after_detection: float | None
    remaining_after_quote: float | None
    remaining_after_fill: float | None

    @property
    def is_influence_driven(self) -> bool:
        """True when the move is front-loaded into the reflex window.

        Such a wallet is not worth copying at our latency: by the time a quote
        returns, the part attributable to it has already happened.
        """
        if self.influence is None or self.selection_skill is None:
            return False
        return self.influence > 0.0 and self.influence >= self.selection_skill


def _median(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.median(present) if present else None


def profile_influence(
    wallet: str,
    curves: list[ReactionCurve],
    *,
    detection_latency_seconds: float = 3.0,
    quote_latency_seconds: float = 5.0,
    fill_latency_seconds: float = 15.0,
) -> InfluenceProfile:
    """Split a wallet's measured reactions into influence, skill and leftovers.

    Default latencies are deliberately generous rather than optimistic: 3s to
    detect on a shared WSS feed carrying ~2,500 events/sec, 5s by the time a
    quote returns, 15s to a modelled fill. Understating these is how a copy
    strategy backtests well and loses money.
    """
    if not curves:
        return InfluenceProfile(wallet, 0, None, None, None, None, None)

    # Influence: the reflex window, 1-5 seconds.
    influence_samples = [
        value
        for curve in curves
        for horizon, value in curve.reactions.items()
        if horizon <= 5 and value is not None
    ]
    influence = (statistics.median(influence_samples) - 1.0) if influence_samples else None

    # Selection skill: the 5-minute move, too slow to be follower reflex.
    skill_samples = [curve.at(300) for curve in curves]
    skill_median = _median(skill_samples)
    selection_skill = (skill_median - 1.0) if skill_median is not None else None

    def remaining(after_seconds: float) -> float | None:
        """What is left of the 5-minute move if entry happens `after_seconds` late."""
        entry_points = [
            curve.at(min(REACTION_HORIZONS_SECONDS, key=lambda h: abs(h - after_seconds)))
            for curve in curves
        ]
        entered_at = _median(entry_points)
        if entered_at is None or skill_median is None or entered_at <= 0:
            return None
        return round(skill_median / entered_at - 1.0, 6)

    return InfluenceProfile(
        wallet=wallet,
        samples=len(curves),
        influence=influence,
        selection_skill=selection_skill,
        remaining_after_detection=remaining(detection_latency_seconds),
        remaining_after_quote=remaining(quote_latency_seconds),
        remaining_after_fill=remaining(fill_latency_seconds),
    )
