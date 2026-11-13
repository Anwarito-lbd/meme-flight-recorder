"""Narrative features and lifecycle, computed point-in-time.

The instruction is explicit and it is the whole design constraint: **do not use
raw mention count as the strategy.** A mention count is trivially manufactured --
one account posting forty times, or one post forwarded into forty channels,
produces the same number as forty people independently noticing something. So
every feature here is built from *who* and *how fast*, not *how many*:

  * unique and credible authors, not mentions
  * source diversity, so one platform cannot carry a narrative alone
  * velocity and acceleration, because arriving early is the only edge available
  * novelty and saturation, because a narrative that everyone already holds has
    no buyers left

Every feature takes an `as_of` and reads **only** events observed at or before
it. That is not a convenience -- it is the property that makes these usable in
the historical replay without leaking forward information.

Pure. No network.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .social_intelligence import SocialEvent


class NarrativeState(StrEnum):
    DISCOVERED = "discovered"
    EMERGING = "emerging"
    ACCELERATING = "accelerating"
    BREAKOUT = "breakout"
    SATURATED = "saturated"
    DECAYING = "decaying"
    DEAD = "dead"


# Thresholds are named constants so the lifecycle can be re-derived from data
# later without hunting through comparisons. They are conventional starting
# points chosen before any outcome was measured, and none of them is wired into
# an entry decision -- `StrategyReadiness` still decides that, and its default
# admits nothing.
MINIMUM_CREDIBLE_AUTHORS = 3
EMERGING_AUTHORS = 2
ACCELERATING_VELOCITY = 1.0  # events per minute
BREAKOUT_ACCELERATION = 0.5
SATURATION_RATIO = 0.75
DECAY_SILENCE_MINUTES = 60.0
DEAD_SILENCE_MINUTES = 360.0


@dataclass(frozen=True)
class NarrativeFeatures:
    narrative_id: str
    as_of: datetime
    first_seen: datetime | None
    mentions: int
    unique_authors: int
    credible_authors: int
    source_diversity: int
    attention_velocity: float | None
    attention_acceleration: float | None
    engagement_velocity: float | None
    cross_platform_spread: float | None
    novelty: float | None
    saturation: float | None
    decay: float | None
    qualified_trader_adoption: int
    spam_share: float | None
    state: NarrativeState

    def as_dict(self) -> dict[str, object]:
        record = {
            "narrative_id": self.narrative_id,
            "as_of": self.as_of.isoformat(),
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "mentions": self.mentions,
            "unique_authors": self.unique_authors,
            "credible_authors": self.credible_authors,
            "source_diversity": self.source_diversity,
            "attention_velocity": self.attention_velocity,
            "attention_acceleration": self.attention_acceleration,
            "engagement_velocity": self.engagement_velocity,
            "cross_platform_spread": self.cross_platform_spread,
            "novelty": self.novelty,
            "saturation": self.saturation,
            "decay": self.decay,
            "qualified_trader_adoption": self.qualified_trader_adoption,
            "spam_share": self.spam_share,
            "state": self.state.value,
        }
        return record


def _timestamp(event: SocialEvent) -> datetime:
    """Prefer when it was published; fall back to when we saw it."""
    return event.published_at or event.observed_at


def _rate(events: list[SocialEvent], end: datetime, window_minutes: float) -> float | None:
    """Events per minute in the window ending at `end`, or None if unmeasurable."""
    if window_minutes <= 0:
        return None
    start = end - timedelta(minutes=window_minutes)
    count = sum(1 for event in events if start < _timestamp(event) <= end)
    return count / window_minutes


def compute_features(
    narrative_id: str,
    events: list[SocialEvent],
    as_of: datetime,
    *,
    window_minutes: float = 30.0,
    universe_authors: int | None = None,
    qualified_trader_adoption: int = 0,
) -> NarrativeFeatures:
    """Features for one narrative as of a moment, using nothing after it."""
    visible = [event for event in events if _timestamp(event) <= as_of]
    if not visible:
        return NarrativeFeatures(
            narrative_id=narrative_id,
            as_of=as_of,
            first_seen=None,
            mentions=0,
            unique_authors=0,
            credible_authors=0,
            source_diversity=0,
            attention_velocity=None,
            attention_acceleration=None,
            engagement_velocity=None,
            cross_platform_spread=None,
            novelty=None,
            saturation=None,
            decay=None,
            qualified_trader_adoption=qualified_trader_adoption,
            spam_share=None,
            state=NarrativeState.DISCOVERED,
        )

    first_seen = min(_timestamp(event) for event in visible)
    last_seen = max(_timestamp(event) for event in visible)
    authors = {event.author for event in visible}
    credible = {event.author for event in visible if event.is_credible}
    sources = {event.source for event in visible}

    current = _rate(visible, as_of, window_minutes)
    previous = _rate(visible, as_of - timedelta(minutes=window_minutes), window_minutes)
    acceleration = None if current is None or previous is None else current - previous

    engagements = [event.engagement for event in visible if event.engagement is not None]
    engagement_velocity = (
        sum(engagements) / window_minutes if engagements else None
    )

    # Novelty decays with age: a narrative first seen an hour ago is not new,
    # however loud it is now.
    age_minutes = max(0.0, (as_of - first_seen).total_seconds() / 60.0)
    novelty = round(math.exp(-age_minutes / 120.0), 4)

    # Saturation is the share of the observable author universe already talking.
    # Without a universe it is unknown -- not zero, which would read as "nobody
    # knows yet" and is the most dangerous possible default here.
    saturation = None
    if universe_authors and universe_authors > 0:
        saturation = round(min(1.0, len(authors) / universe_authors), 4)

    silence_minutes = (as_of - last_seen).total_seconds() / 60.0
    decay = round(min(1.0, silence_minutes / DEAD_SILENCE_MINUTES), 4)

    spam_scores = [
        event.spam_probability for event in visible if event.spam_probability is not None
    ]
    spam_share = round(statistics.fmean(spam_scores), 4) if spam_scores else None

    cross_platform = round(len(sources) / len(visible), 4) if visible else None

    state = classify(
        credible_authors=len(credible),
        unique_authors=len(authors),
        velocity=current,
        acceleration=acceleration,
        saturation=saturation,
        silence_minutes=silence_minutes,
    )

    return NarrativeFeatures(
        narrative_id=narrative_id,
        as_of=as_of,
        first_seen=first_seen,
        mentions=len(visible),
        unique_authors=len(authors),
        credible_authors=len(credible),
        source_diversity=len(sources),
        attention_velocity=current,
        attention_acceleration=acceleration,
        engagement_velocity=engagement_velocity,
        cross_platform_spread=cross_platform,
        novelty=novelty,
        saturation=saturation,
        decay=decay,
        qualified_trader_adoption=qualified_trader_adoption,
        spam_share=spam_share,
        state=state,
    )


def classify(
    *,
    credible_authors: int,
    unique_authors: int,
    velocity: float | None,
    acceleration: float | None,
    saturation: float | None,
    silence_minutes: float,
) -> NarrativeState:
    """Place a narrative on its lifecycle.

    Death and decay are checked first, because a narrative that stopped being
    talked about is decaying whatever its historical velocity was. Ordering the
    checks the other way lets a burst an hour ago keep reporting BREAKOUT.
    """
    if silence_minutes >= DEAD_SILENCE_MINUTES:
        return NarrativeState.DEAD
    if silence_minutes >= DECAY_SILENCE_MINUTES:
        return NarrativeState.DECAYING
    if saturation is not None and saturation >= SATURATION_RATIO:
        return NarrativeState.SATURATED
    if (
        credible_authors >= MINIMUM_CREDIBLE_AUTHORS
        and (velocity or 0.0) >= ACCELERATING_VELOCITY
        and (acceleration or 0.0) >= BREAKOUT_ACCELERATION
    ):
        return NarrativeState.BREAKOUT
    if (acceleration or 0.0) > 0 and (velocity or 0.0) >= ACCELERATING_VELOCITY:
        return NarrativeState.ACCELERATING
    if unique_authors >= EMERGING_AUTHORS:
        return NarrativeState.EMERGING
    return NarrativeState.DISCOVERED


def resolve_to_mint(
    events: list[SocialEvent], known_mints: set[str] | None = None
) -> str | None:
    """The mint a narrative is about, only when it was stated.

    Returns None rather than guessing from a ticker. A narrative with no stated
    address is still a valid narrative to *track*; it is simply not yet
    something that can be traded, and pretending otherwise would resolve to the
    wrong one of five identically named mints.
    """
    counts: dict[str, int] = {}
    for event in events:
        if not event.mint:
            continue
        if known_mints is not None and event.mint not in known_mints:
            continue
        counts[event.mint] = counts.get(event.mint, 0) + 1
    if not counts:
        return None
    best = max(counts.values())
    leaders = [mint for mint, count in counts.items() if count == best]
    # An exact tie is genuinely ambiguous and resolving it arbitrarily would
    # attach a narrative to a coin flip.
    return leaders[0] if len(leaders) == 1 else None
