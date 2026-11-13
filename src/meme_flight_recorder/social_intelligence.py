"""A provider-independent social event, and the spam filter it needs first.

Every social source has a different shape, so the pipeline gets one shape and
the adapters do the translating. That is what lets Telegram, Reddit, DexScreener
metadata and a future X adapter feed the same narrative engine without the
engine knowing which one spoke.

Two rules carried over from the rest of the system, and they matter more here
than anywhere else because social data is adversarial by construction:

**A mint is only a mint when it was stated.** `mint` is populated exclusively
from an address that appeared literally in the text. It is never inferred from a
ticker: five CATE mints appeared in one night, so "$CATE was mentioned" does not
identify a token. `symbols` is kept separately and is explicitly *not* an
identity.

**Absent is not zero.** Unknown engagement is None, never 0 -- an unmeasured post
must not average in as an unpopular one.

Spam probability is scored, never used to silently delete. A filtered event is
retained with its score so that "how much of this narrative was bots" is itself
a measurable feature rather than a decision taken invisibly upstream.

Pure. Adapters do the I/O; this module only assesses what they return.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

# Solana addresses are base58 and 32-44 characters. Anchored on word boundaries
# so a substring of a longer token is not harvested as an address.
_MINT_PATTERN = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_SYMBOL_PATTERN = re.compile(r"\$([A-Za-z][A-Za-z0-9_]{0,14})\b")
_URL_PATTERN = re.compile(r"https?://[^\s<>\"')]+")

# Phrases that mark promotional copy rather than discussion. Deliberately short
# and boring: a long clever list overfits to today's spam and quietly starts
# filtering real conversation.
_PROMOTIONAL = (
    "guaranteed",
    "100x",
    "1000x",
    "next pepe",
    "dont miss",
    "don't miss",
    "ape in",
    "financial freedom",
    "easy money",
    "pump it",
    "send it",
    "buy now",
)


class SocialSource(StrEnum):
    TELEGRAM = "telegram"
    REDDIT = "reddit"
    DEXSCREENER = "dexscreener"
    GMGN = "gmgn"
    WEB_NEWS = "web_news"
    MANUAL_CSV = "manual_csv"
    X = "x"


@dataclass(frozen=True)
class SocialEvent:
    source: SocialSource
    author: str
    text: str
    observed_at: datetime
    published_at: datetime | None = None
    urls: tuple[str, ...] = ()
    mint: str | None = None
    symbols: tuple[str, ...] = ()
    narrative_id: str | None = None
    engagement: int | None = None
    author_credibility: float | None = None
    spam_probability: float | None = None
    forwarded_from: str | None = None
    external_id: str | None = None

    @property
    def dedup_key(self) -> str:
        """Identity for deduplication across channels.

        A forwarded message keeps its *origin* identity, so the same post
        relayed into forty channels counts once. Counting relays as independent
        mentions is the single easiest way to manufacture a fake narrative.
        """
        origin = self.forwarded_from or self.author
        return f"{origin}:{(self.external_id or self.text)[:180]}"

    @property
    def is_credible(self) -> bool:
        return (self.author_credibility or 0.0) >= 0.5 and (self.spam_probability or 0.0) < 0.5


def extract_mints(text: str, known: set[str] | None = None) -> tuple[str, ...]:
    """Addresses stated literally in the text.

    When a set of known mints is supplied the match is restricted to it, which
    removes the false positives -- wallet addresses, signatures, program IDs all
    share the same character class and length.
    """
    found = [match.group(0) for match in _MINT_PATTERN.finditer(text or "")]
    if known is not None:
        return tuple(dict.fromkeys(value for value in found if value in known))
    return tuple(dict.fromkeys(found))


def extract_symbols(text: str) -> tuple[str, ...]:
    """Cashtags. **Not an identity** -- kept for narrative grouping only."""
    return tuple(dict.fromkeys(match.group(1).upper() for match in _SYMBOL_PATTERN.finditer(text or "")))


def extract_urls(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(0) for match in _URL_PATTERN.finditer(text or "")))


def spam_probability(text: str, author_post_count: int = 1, unique_authors: int = 1) -> float:
    """How likely this is promotional noise rather than signal. 0..1.

    Every term is an observable property of the text or the posting pattern.
    Nothing here is a judgement about the token, and the score is advisory: it
    is stored, and the narrative engine reports credible and total counts side
    by side rather than one filtered number.
    """
    body = (text or "").lower()
    if not body.strip():
        return 1.0
    score = 0.0
    hits = sum(1 for phrase in _PROMOTIONAL if phrase in body)
    score += min(0.45, hits * 0.15)

    letters = [character for character in text if character.isalpha()]
    if len(letters) >= 12:
        shouting = sum(1 for character in letters if character.isupper()) / len(letters)
        if shouting > 0.6:
            score += 0.15

    emoji_like = sum(1 for character in text if ord(character) > 0x2500)
    if emoji_like > 12:
        score += 0.15

    if len(extract_symbols(text)) >= 4:
        # Shotgunning many tickers in one post is advertising, not a thesis.
        score += 0.20

    if author_post_count >= 10 and unique_authors <= 2:
        # One account carrying a whole "narrative" is not a narrative.
        score += 0.25

    return round(min(1.0, score), 4)


def author_credibility(
    posts_observed: int,
    account_age_days: float | None,
    median_engagement: float | None,
    spam_rate: float,
) -> float:
    """A conservative 0..1 credibility score.

    Returns a *low* score when inputs are missing rather than a neutral one. An
    unknown author is not an average author: on an adversarial surface the
    unknown ones are disproportionately fresh burner accounts.
    """
    score = 0.15
    if account_age_days is not None:
        score += min(0.30, account_age_days / 365.0 * 0.30)
    if posts_observed >= 5:
        score += 0.15
    if median_engagement is not None and median_engagement > 0:
        score += min(0.25, median_engagement / 100.0 * 0.25)
    score -= min(0.50, spam_rate * 0.50)
    return round(max(0.0, min(1.0, score)), 4)


@dataclass
class SocialIngest:
    """Deduplicating sink. Dedup happens **before** enrichment, never after."""

    events: list[SocialEvent] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)
    duplicates: int = 0
    forwarded_collapsed: int = 0

    def add(self, event: SocialEvent) -> bool:
        key = event.dedup_key
        if key in self._seen:
            self.duplicates += 1
            if event.forwarded_from:
                self.forwarded_collapsed += 1
            return False
        self._seen.add(key)
        self.events.append(event)
        return True

    def for_mint(self, mint: str) -> list[SocialEvent]:
        return [event for event in self.events if event.mint == mint]

    def reconciliation(self) -> dict[str, int]:
        """Every ingested item lands in exactly one bucket."""
        return {
            "accepted": len(self.events),
            "duplicates": self.duplicates,
            "of_which_forwarded": self.forwarded_collapsed,
            "total_offered": len(self.events) + self.duplicates,
        }


def build_event(
    source: SocialSource,
    author: str,
    text: str,
    *,
    published_at: datetime | None = None,
    engagement: int | None = None,
    forwarded_from: str | None = None,
    external_id: str | None = None,
    known_mints: set[str] | None = None,
    author_post_count: int = 1,
    unique_authors: int = 1,
) -> SocialEvent:
    mints = extract_mints(text, known_mints)
    return SocialEvent(
        source=source,
        author=author,
        text=text,
        observed_at=datetime.now(UTC),
        published_at=published_at,
        urls=extract_urls(text),
        mint=mints[0] if mints else None,
        symbols=extract_symbols(text),
        engagement=engagement,
        spam_probability=spam_probability(text, author_post_count, unique_authors),
        forwarded_from=forwarded_from,
        external_id=external_id,
    )
