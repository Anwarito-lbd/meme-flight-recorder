"""Classify text about a token, through whichever model the operator can afford.

The scout's mandate asks for a sentiment read on news and social posts. This
supplies it, and the design decision that matters is that **the model is a
swappable backend rather than a dependency**. Three are supported:

  * `none`     -- always UNKNOWN. The honest default when nothing is configured.
  * `ollama`   -- a local model over http://localhost:11434. Free, private, and
                  entirely adequate: sentiment and relevance on a headline is one
                  of the easiest tasks in NLP, and a 3B model does it about as
                  well as a frontier model because there is no reasoning depth to
                  lose.
  * `anthropic` -- a hosted model, if the operator ever wants one.

**Why the backend is swappable rather than chosen.** At $50 of capital, the
model bill is a capital-allocation decision, not a capability one. Classifying
every candidate every cycle on a hosted model costs roughly $22/month, which is
44% of this account, monthly. The same work on a local model costs nothing.
Hard-coding either choice would make a budget decision on the operator's behalf.

**Two rules this module exists to enforce, and they outrank the model choice.**

1. **Zero articles is UNKNOWN, never neutral.** A token four minutes old has
   nothing written about it. Scoring that as "neutral sentiment" would let an
   absence of evidence enter the confluence count as a passed check -- the
   "absent is not zero" error this project has committed three times. `None`
   article count means the provider could not be reached, which is a different
   fact again, and both are distinguishable here.

2. **The verdict ranks, it never gates.** `CLAUDE.md` requires gates be computed,
   and a model's classification is not reproducible run to run -- ask twice and
   you may get two answers, with no way to tell whether the market moved or the
   model did. `SentimentReading` therefore carries a label and a count for the
   journal and for a human to read; nothing in the entry path may branch on it.

The network call is isolated in `classify`; the parsing and the UNKNOWN rules are
pure functions that test without a network.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum

OLLAMA_URL = "http://localhost:11434/api/generate"

# Small is the right size here. Sentiment on a headline needs no reasoning depth,
# so a 3B model matches a frontier model at zero cost and runs on CPU.
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"

PROMPT = (
    "You classify short text about a cryptocurrency token.\n"
    "Answer with exactly one word: POSITIVE, NEGATIVE, or NEUTRAL.\n"
    "POSITIVE means the text suggests genuine interest or good news.\n"
    "NEGATIVE means it suggests a scam, rug, hack, or bad news.\n"
    "NEUTRAL means it is factual or unrelated.\n"
    "Answer with one word and nothing else.\n\n"
    "Token: {symbol}\nText: {text}\nAnswer:"
)


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SentimentReading:
    """One classification, with enough context to tell absence from silence.

    `article_count` is journalled beside the label for exactly one reason: a
    reader who sees only "neutral" cannot tell whether the model weighed ten
    articles and found them balanced, or whether there were no articles at all.
    Those are opposite facts.
    """

    sentiment: Sentiment
    article_count: int | None
    backend: str
    detail: str | None = None

    @property
    def is_evidence(self) -> bool:
        """Whether this reading may contribute anything at all.

        UNKNOWN is not evidence. It is the state a fail-closed consumer must
        treat as missing, never as a neutral or passing signal.
        """
        return self.sentiment is not Sentiment.UNKNOWN


def parse_label(raw: str) -> Sentiment:
    """Map a model's free text onto a label, defaulting to UNKNOWN.

    Deliberately strict. A model that answers "I think it's probably positive
    but..." has not followed the instruction, and guessing at intent would
    manufacture a signal out of a malformed answer. Anything unrecognised is
    UNKNOWN, which cannot help a candidate.
    """
    text = (raw or "").strip().upper()
    for label in (Sentiment.POSITIVE, Sentiment.NEGATIVE, Sentiment.NEUTRAL):
        if text.startswith(label.value.upper()):
            return label
    return Sentiment.UNKNOWN


def reading_for_articles(
    articles: list[str] | None, labels: list[Sentiment], backend: str
) -> SentimentReading:
    """Combine per-article labels into one reading, applying the UNKNOWN rules.

    Three distinct outcomes that a single "neutral" would collapse:

      * `articles is None` -- the provider could not be reached. UNKNOWN, and the
        detail says so, because a network failure is not a fact about the token.
      * `articles == []` -- the provider looked and found nothing. Still UNKNOWN,
        because no coverage is not neutral coverage, but the count is 0 rather
        than None so a reader can tell the two apart.
      * labels present -- majority wins; a tie is UNKNOWN rather than a coin flip.
    """
    if articles is None:
        return SentimentReading(Sentiment.UNKNOWN, None, backend, "provider unreachable")
    if not articles:
        return SentimentReading(Sentiment.UNKNOWN, 0, backend, "no articles found")

    scored = [label for label in labels if label is not Sentiment.UNKNOWN]
    if not scored:
        return SentimentReading(
            Sentiment.UNKNOWN, len(articles), backend, "no article could be classified"
        )
    counts = {label: scored.count(label) for label in set(scored)}
    best = max(counts.values())
    winners = [label for label, count in counts.items() if count == best]
    if len(winners) > 1:
        return SentimentReading(
            Sentiment.UNKNOWN, len(articles), backend, "classifications tied"
        )
    return SentimentReading(winners[0], len(articles), backend)


def _ollama(prompt: str, model: str, timeout: int) -> str | None:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            # Temperature 0 is the closest a model gets to reproducible. It does
            # not make the verdict a gate -- see the module docstring -- but it
            # does stop the same headline flipping label between runs.
            "options": {"temperature": 0.0, "num_predict": 8},
        }
    ).encode()
    request = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return str(json.loads(response.read().decode()).get("response", ""))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        # Every one of these is "the model did not answer", which is UNKNOWN.
        # None propagates that; it is never converted into a neutral label.
        return None


def classify(
    symbol: str,
    articles: list[str] | None,
    *,
    backend: str | None = None,
    model: str | None = None,
    timeout: int = 20,
) -> SentimentReading:
    """Classify each article and combine them into one reading.

    `backend` defaults to `SCOUT_SENTIMENT_BACKEND`, itself defaulting to
    `none` -- so an operator who has configured nothing gets UNKNOWN rather than
    a silent dependency on a model that may not be installed.
    """
    chosen = (backend or os.getenv("SCOUT_SENTIMENT_BACKEND") or "none").strip().lower()
    if chosen == "none":
        return SentimentReading(
            Sentiment.UNKNOWN, None if articles is None else len(articles), "none",
            "no sentiment backend configured",
        )
    if articles is None:
        return SentimentReading(Sentiment.UNKNOWN, None, chosen, "provider unreachable")
    if not articles:
        return SentimentReading(Sentiment.UNKNOWN, 0, chosen, "no articles found")

    if chosen == "ollama":
        name = model or os.getenv("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL
        labels = []
        for article in articles:
            raw = _ollama(PROMPT.format(symbol=symbol, text=article[:2000]), name, timeout)
            labels.append(Sentiment.UNKNOWN if raw is None else parse_label(raw))
        return reading_for_articles(articles, labels, f"ollama:{name}")

    if chosen == "anthropic":
        # Deliberately not implemented rather than half-implemented. Wiring a paid
        # backend before the local one has shown the node is worth anything would
        # be spending the operator's capital on an unmeasured feature -- and this
        # node is expected to read UNKNOWN on most candidates, because a token
        # minutes old has nothing written about it.
        return SentimentReading(
            Sentiment.UNKNOWN, len(articles), "anthropic",
            "hosted backend not implemented; run the local one first",
        )

    return SentimentReading(
        Sentiment.UNKNOWN, len(articles), chosen, f"unknown backend {chosen!r}"
    )


def available_backends() -> dict[str, bool]:
    """Which backends could actually answer right now.

    Used by the CLI so an operator is told "ollama is installed but has no models
    pulled" instead of silently receiving UNKNOWN on every candidate.
    """
    status = {"none": True, "ollama": False, "anthropic": False}
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as response:
            models = json.loads(response.read().decode()).get("models") or []
            status["ollama"] = bool(models)
    except Exception:  # noqa: BLE001 - absence is the answer, not an error
        status["ollama"] = False
    status["anthropic"] = bool(os.getenv("ANTHROPIC_API_KEY"))
    return status
