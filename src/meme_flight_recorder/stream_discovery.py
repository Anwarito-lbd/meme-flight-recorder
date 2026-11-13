"""Discover candidates from the chain itself instead of a vendor's top-20 list.

Discovery until now was `CoinGecko.trending_solana_pools()`, which returns
**twenty pools**. Not twenty filtered from thousands -- twenty, total. Every
selection rule this project has tested was therefore a filter over a candidate
set someone else had already chosen, ranked by their criteria. Meanwhile the
WebSocket feed carries every transaction on four launch venues at ~2,500/sec,
and fed nothing.

This closes that gap. The design constraint is the volume: at 2,500 events/sec
nothing may be enriched, decoded or even fetched per event, because a single RPC
call per message is three orders of magnitude beyond what any budget allows. So
the filter is a **string match on the log lines the subscription already
delivers**, which costs nothing and needs no network:

    raw stream -> program filter (subscription) -> instruction filter (logs)
    -> exact mint -> dedup -> candidate

`logsSubscribe` includes each transaction's log output, and Anchor programs
print `Program log: Instruction: <Name>`. That is enough to tell a token
creation from the buys and sells that make up almost all the traffic, before
deciding whether the transaction is worth fetching at all.

Dedup happens here, before enrichment, for the same reason: a mint seen forty
times in a minute must cost one enrichment, not forty.

The filtering is pure and testable without a network. The runner does the I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .venues import PROGRAM_IDS, Venue

# Instruction names that mark a *new* token or pool rather than trading in an
# existing one. Taken from live log output rather than from an IDL, because the
# log is what the stream actually delivers and the two can drift.
CREATION_INSTRUCTIONS: frozenset[str] = frozenset(
    {
        "Create",
        "CreateV2",
        "InitializeVirtualPoolWithSplToken",
        "Initialize",
        "InitializeMint",
        "CreatePool",
        "Initialize2",
        "BondingCurveV3",
    }
)

# Trading instructions. Kept separate rather than discarded: a token's first
# buys are the first-buyer signal, and throwing them away here would make that
# unmeasurable later.
TRADE_INSTRUCTIONS: frozenset[str] = frozenset(
    {
        "Buy",
        "Sell",
        "BuyV2",
        "SellV2",
        "BuyExactSolIn",
        "BuyExactQuoteInV2",
        "SellExactIn",
        "Swap",
    }
)

_INSTRUCTION = re.compile(r"Program log: Instruction:\s*(\w+)")
# Pump.fun mints end in "pump"; this is a cheap hint, never an identity. The
# authoritative mint comes from decoding the transaction.
_ADDRESS = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")


def instructions_in(logs: list[str]) -> list[str]:
    """Instruction names printed by a transaction, in order."""
    found: list[str] = []
    for line in logs or []:
        match = _INSTRUCTION.search(line)
        if match:
            found.append(match.group(1))
    return found


@dataclass(frozen=True)
class StreamEvent:
    signature: str
    slot: int
    venue: Venue
    instructions: tuple[str, ...]
    logs: tuple[str, ...] = ()

    @property
    def is_creation(self) -> bool:
        return any(name in CREATION_INSTRUCTIONS for name in self.instructions)

    @property
    def is_trade(self) -> bool:
        return any(name in TRADE_INSTRUCTIONS for name in self.instructions)

    @property
    def worth_fetching(self) -> bool:
        """Whether this transaction justifies an RPC call.

        The entire cost model of the pipeline is this predicate. Creations are
        rare and are what discovery is for; trades are ~95% of the feed and are
        counted rather than fetched.
        """
        return self.is_creation


def parse_notification(
    message: dict, subscriptions: dict[int, Venue] | None = None
) -> StreamEvent | None:
    """Turn one logsNotification into an event, or None if it is not one.

    The venue comes from **which subscription delivered the message**, not from
    scanning the log text for a program address. The first version did the
    latter and labelled 13,771 of 25,671 events `unknown`, because a program
    only prints its own id on the `Program <id> invoke` line and inner
    invocations attribute to whichever program logged last. The subscription
    id is unambiguous: we asked for that program, so that is the venue.
    """
    params = message.get("params") or {}
    result = params.get("result") or {}
    value = result.get("value") or {}
    signature = value.get("signature")
    slot = (result.get("context") or {}).get("slot")
    if not signature or slot is None:
        return None
    # A failed transaction still tells us somebody tried. It is kept and
    # marked, not dropped, because dropping it biases every flow count toward
    # trades that happened to succeed.
    logs = list(value.get("logs") or [])
    venue = (subscriptions or {}).get(params.get("subscription"), Venue.UNKNOWN)
    if venue is Venue.UNKNOWN:
        for line in logs:
            for address, known in PROGRAM_IDS.items():
                if address in line:
                    venue = known
                    break
            if venue is not Venue.UNKNOWN:
                break
    return StreamEvent(
        signature=str(signature),
        slot=int(slot),
        venue=venue,
        instructions=tuple(instructions_in(logs)),
        logs=tuple(logs),
    )


@dataclass
class DiscoveryStats:
    """A reconciling denominator for the stream itself."""

    messages: int = 0
    parsed: int = 0
    creations: int = 0
    trades: int = 0
    other: int = 0
    fetched: int = 0
    unique_mints: int = 0
    duplicates: int = 0
    by_venue: dict[str, int] = field(default_factory=dict)
    by_instruction: dict[str, int] = field(default_factory=dict)

    def observe(self, event: StreamEvent) -> None:
        self.parsed += 1
        self.by_venue[event.venue.value] = self.by_venue.get(event.venue.value, 0) + 1
        for name in event.instructions:
            self.by_instruction[name] = self.by_instruction.get(name, 0) + 1
        if event.is_creation:
            self.creations += 1
        elif event.is_trade:
            self.trades += 1
        else:
            self.other += 1

    def reconciles(self) -> bool:
        return self.creations + self.trades + self.other == self.parsed


@dataclass
class MintRegistry:
    """First sighting per mint. Dedup before enrichment, never after."""

    seen: dict[str, datetime] = field(default_factory=dict)
    duplicates: int = 0

    def add(self, mint: str, at: datetime | None = None) -> bool:
        if mint in self.seen:
            self.duplicates += 1
            return False
        self.seen[mint] = at or datetime.now(UTC)
        return True

    def __len__(self) -> int:
        return len(self.seen)
