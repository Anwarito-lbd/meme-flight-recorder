"""The two hard gates the operator's mandate added, as pure predicates.

Both were supplied late in the mandate's revision history and both are worth
more than the rest of it, because each names a **structural mechanism** rather
than describing a single token that went wrong. That distinction is the bar this
project sets for encoding a rule without repetition data:

  * *No re-entry.* "This way he can't enter a farm token that keeps pumping,
    dumping and pumping again to attract ppl." A farm token's whole method is
    repeat cycles, so a book that re-enters is the intended customer.
  * *First-minute verticality.* A token that reaches a large capitalisation on
    its first traded minute has no organic accumulation behind it -- there was
    no time for any.

**Both are RISK gates, so both are judged on death rate, not on return.** That
distinction is not pedantry here: the developer-selling gate was inverted for
weeks because it was judged on the wrong axis, and it took 871 mints to find
that developers who had fully exited died at 7% against 28% for those who had
sold nothing. `study_reentry_value.py` and `study_first_candle_pump.py` decide
whether these two may be trusted, and each reports the 2x rate it costs
alongside the death rate it prevents -- because every risk filter measured in
this project so far has bought safety by removing upside.

Pure. The caller supplies the held-mint set and the candle; nothing here reads a
journal, opens a socket or looks at a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GateOutcome(StrEnum):
    PASS = "pass"
    REJECT = "reject"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GateVerdict:
    """One gate's answer, with the evidence that produced it.

    `UNKNOWN` is a distinct outcome and is **never** a pass. A gate that cannot
    see its input has not cleared anything, and treating missing evidence as
    approval is how this project's fail-closed rule gets quietly reversed.
    """

    gate: str
    outcome: GateOutcome
    reason: str
    observed: float | None = None
    threshold: float | None = None

    @property
    def blocks_entry(self) -> bool:
        """Anything but an explicit PASS blocks entry."""
        return self.outcome is not GateOutcome.PASS


def check_no_reentry(
    mint: str, previously_held_mints: frozenset[str] | None, *, enabled: bool = True
) -> GateVerdict:
    """Reject a mint this book has already held.

    `previously_held_mints` is `None` when the caller could not determine the
    history -- a journal that failed to open, say. That is UNKNOWN rather than
    "nothing was held", because reading an unreadable ledger as an empty one is
    the most permissive possible interpretation of the least certain input.

    Identity is the mint address. Five CATE mints and four SAOF mints appeared in
    one night in this journal, so a symbol-keyed version of this gate would
    reject unrelated tokens and admit the farm token it exists to stop.
    """
    if not enabled:
        return GateVerdict("no_reentry", GateOutcome.PASS, "gate disabled by config")
    if previously_held_mints is None:
        return GateVerdict(
            "no_reentry", GateOutcome.UNKNOWN, "position history unavailable"
        )
    if mint in previously_held_mints:
        return GateVerdict("no_reentry", GateOutcome.REJECT, "mint already held once")
    return GateVerdict("no_reentry", GateOutcome.PASS, "not previously held")


def check_first_candle_not_vertical(
    first_candle_open: float | None,
    first_candle_high: float | None,
    first_candle_volume: float | None,
    maximum_multiple: float,
) -> GateVerdict:
    """Reject a token whose first *traded* minute spans more than `maximum_multiple`.

    Three ways this returns UNKNOWN rather than passing, each deliberate:

      * no first candle at all -- the token's history is not visible;
      * a candle with **zero volume** -- that is not a price. Birdeye returned
        963 zero-volume candles for one rugged token, carrying its last close
        forward sixteen hours past its final trade. A zero-volume first bar would
        show a span of exactly 1.0x and sail through this gate, which is the
        precise shape of a corpse passing a liveness check;
      * a non-positive open, which cannot form a ratio.

    The span is `high / open` within the single first traded minute. The mandate's
    example reached $1.8M market capitalisation inside that minute, which is a
    ratio no organic book produces.
    """
    if first_candle_volume is None or first_candle_open is None or first_candle_high is None:
        return GateVerdict(
            "first_candle_vertical",
            GateOutcome.UNKNOWN,
            "no first-candle evidence",
            None,
            maximum_multiple,
        )
    if first_candle_volume <= 0:
        return GateVerdict(
            "first_candle_vertical",
            GateOutcome.UNKNOWN,
            "first candle has no volume, so it is not a price",
            None,
            maximum_multiple,
        )
    if first_candle_open <= 0:
        return GateVerdict(
            "first_candle_vertical",
            GateOutcome.UNKNOWN,
            "first candle open is not positive",
            None,
            maximum_multiple,
        )
    multiple = first_candle_high / first_candle_open
    if multiple > maximum_multiple:
        return GateVerdict(
            "first_candle_vertical",
            GateOutcome.REJECT,
            "first traded minute is vertical",
            multiple,
            maximum_multiple,
        )
    return GateVerdict(
        "first_candle_vertical",
        GateOutcome.PASS,
        "first traded minute within span limit",
        multiple,
        maximum_multiple,
    )


def check_token_age(age_hours: float | None, maximum_hours: float) -> GateVerdict:
    """Reject a token older than the mandate's cap.

    Measured non-binding on this population: the rejected cohort's p90 age is 7.2
    minutes over 18,942 mints, so a 48-hour cap admits everything the feeds
    return. It is implemented because the operator asked that no parameter be
    dropped, and because it would bind if the feed widened. An unknown age is
    UNKNOWN, not young.
    """
    if age_hours is None:
        return GateVerdict("token_age", GateOutcome.UNKNOWN, "age unknown", None, maximum_hours)
    if age_hours > maximum_hours:
        return GateVerdict(
            "token_age", GateOutcome.REJECT, "older than cap", age_hours, maximum_hours
        )
    return GateVerdict("token_age", GateOutcome.PASS, "within age cap", age_hours, maximum_hours)


def check_market_cap_band(
    market_cap_usd: float | None, minimum_usd: float, maximum_usd: float
) -> GateVerdict:
    """Band-check market capitalisation.

    Recorded here so it is journalled, and journalled **alongside** pool depth
    rather than instead of it. Market capitalisation is not exit liquidity: the
    workflow this mandate came from filtered on mcap alone, and the comparable
    *liquidity* filter was withdrawn at n=166 when removing one 209.7x token
    flipped growth negative at every position size. This gate is context, and
    the depth and impact gates are what actually protect an exit.
    """
    if market_cap_usd is None:
        return GateVerdict("market_cap_band", GateOutcome.UNKNOWN, "market cap unknown")
    if market_cap_usd < minimum_usd:
        return GateVerdict(
            "market_cap_band", GateOutcome.REJECT, "below band", market_cap_usd, minimum_usd
        )
    if market_cap_usd > maximum_usd:
        return GateVerdict(
            "market_cap_band", GateOutcome.REJECT, "above band", market_cap_usd, maximum_usd
        )
    return GateVerdict("market_cap_band", GateOutcome.PASS, "within band", market_cap_usd)


def blocking(verdicts: tuple[GateVerdict, ...]) -> tuple[GateVerdict, ...]:
    """Every verdict that prevents entry, rejections and unknowns alike."""
    return tuple(verdict for verdict in verdicts if verdict.blocks_entry)
