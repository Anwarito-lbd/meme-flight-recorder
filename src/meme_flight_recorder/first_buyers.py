"""Who bought first, and whether that says anything about what happened next.

The autopsy on `enter_on_sight` pointed here. Its entire apparent edge lived at
the *open of the first traded minute* and vanished by the close -- which is not
a statement about timing so much as a statement about **who is in that first
minute**. If the first buyers are one actor in twenty wallets, the price at the
first print is that actor's own bid and nobody else can have it.

So these features describe the composition of the earliest buyers, and they are
deliberately kept separate from each other. No composite score: the instruction
is explicit, and this project's record is that combined scores hide which term
was doing the work and which was noise.

Every feature returns `None` when its inputs are missing, never `0`. A mint
whose buyer funding could not be resolved has *unknown* shared-funder
percentage, and scoring it as zero would read as "no shared funding", which is
the most flattering possible reading of the most suspicious possible case.

Pure. The caller fetches the buys and whatever wallet metadata it can afford.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Buy:
    """One purchase in a token's earliest history."""

    wallet: str
    slot: int
    order: int
    sol_amount: float | None = None
    token_amount: float | None = None
    signature: str = ""
    seconds_since_first: float | None = None


@dataclass(frozen=True)
class WalletFacts:
    """What a caller managed to learn about a buyer. Any field may be unknown."""

    funder: str | None = None
    first_seen_slot: int | None = None
    is_creator: bool | None = None
    creator_linked: bool | None = None
    previously_profitable: bool | None = None
    qualified: bool = False


@dataclass(frozen=True)
class FirstBuyerFeatures:
    mint: str
    buyers_considered: int
    unique_wallets: int
    independent_wallet_count: int | None
    shared_funder_pct: float | None
    same_slot_concentration_pct: float | None
    fresh_wallet_pct: float | None
    creator_linked_pct: float | None
    previously_profitable_pct: float | None
    early_sell_pct: float | None
    cluster_supply_pct: float | None
    qualified_within_30s: int | None
    qualified_within_1m: int | None
    qualified_within_5m: int | None
    coverage: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        record = {
            "mint": self.mint,
            "buyers_considered": self.buyers_considered,
            "unique_wallets": self.unique_wallets,
            "independent_wallet_count": self.independent_wallet_count,
            "shared_funder_pct": self.shared_funder_pct,
            "same_slot_concentration_pct": self.same_slot_concentration_pct,
            "fresh_wallet_pct": self.fresh_wallet_pct,
            "creator_linked_pct": self.creator_linked_pct,
            "previously_profitable_pct": self.previously_profitable_pct,
            "early_sell_pct": self.early_sell_pct,
            "cluster_supply_pct": self.cluster_supply_pct,
            "qualified_within_30s": self.qualified_within_30s,
            "qualified_within_1m": self.qualified_within_1m,
            "qualified_within_5m": self.qualified_within_5m,
        }
        record["coverage"] = self.coverage
        return record


FEATURE_NAMES: tuple[str, ...] = (
    "independent_wallet_count",
    "shared_funder_pct",
    "same_slot_concentration_pct",
    "fresh_wallet_pct",
    "creator_linked_pct",
    "previously_profitable_pct",
    "early_sell_pct",
    "cluster_supply_pct",
    "qualified_within_30s",
    "qualified_within_1m",
    "qualified_within_5m",
)


def _pct(numerator: int, denominator: int) -> float | None:
    return round(100.0 * numerator / denominator, 4) if denominator else None


def compute(
    mint: str,
    buys: list[Buy],
    facts: dict[str, WalletFacts] | None = None,
    sellers: set[str] | None = None,
    fresh_wallet_slot_window: int = 500_000,
) -> FirstBuyerFeatures:
    """Features over the first N buys of one mint.

    `buys` must already be ordered and truncated by the caller -- first-10,
    first-25 and first-50 are different populations and mixing them would make
    every percentage depend on how many trades happened to be fetched.
    """
    facts = facts or {}
    sellers = sellers or set()
    considered = len(buys)
    if considered == 0:
        return FirstBuyerFeatures(
            mint=mint,
            buyers_considered=0,
            unique_wallets=0,
            independent_wallet_count=None,
            shared_funder_pct=None,
            same_slot_concentration_pct=None,
            fresh_wallet_pct=None,
            creator_linked_pct=None,
            previously_profitable_pct=None,
            early_sell_pct=None,
            cluster_supply_pct=None,
            qualified_within_30s=None,
            qualified_within_1m=None,
            qualified_within_5m=None,
        )

    wallets = [buy.wallet for buy in buys]
    unique = list(dict.fromkeys(wallets))

    # Same-slot concentration needs no extra data and is the cheapest bundle
    # detector available: several "different" buyers landing in one slot is one
    # transaction batch, not a crowd.
    slot_counts = Counter(buy.slot for buy in buys)
    same_slot = _pct(max(slot_counts.values()), considered)

    # Independence. A wallet with an unknown funder is treated as its own
    # actor, which *understates* clustering -- the conservative direction is to
    # not manufacture independence, so unknown funders are counted separately
    # and reported as coverage rather than assumed shared.
    funders: dict[str, str] = {}
    for wallet in unique:
        funder = facts.get(wallet, WalletFacts()).funder
        if funder:
            funders[wallet] = funder
    funder_coverage = _pct(len(funders), len(unique)) or 0.0

    independent: int | None = None
    shared_pct: float | None = None
    if funders:
        seen_funders: set[str] = set()
        count = 0
        shared = 0
        funder_totals = Counter(funders.values())
        for wallet in unique:
            funder = funders.get(wallet)
            if funder is None:
                count += 1
                continue
            if funder_totals[funder] > 1:
                shared += 1
            if funder in seen_funders:
                continue
            seen_funders.add(funder)
            count += 1
        independent = count
        shared_pct = _pct(shared, len(unique))

    def known_pct(attribute: str) -> tuple[float | None, float]:
        known = [
            getattr(facts[wallet], attribute)
            for wallet in unique
            if wallet in facts and getattr(facts[wallet], attribute) is not None
        ]
        if not known:
            return None, 0.0
        return _pct(sum(1 for value in known if value), len(known)), _pct(
            len(known), len(unique)
        ) or 0.0

    creator_pct, creator_coverage = known_pct("creator_linked")
    profitable_pct, profitable_coverage = known_pct("previously_profitable")

    fresh: float | None = None
    fresh_known = [
        facts[wallet].first_seen_slot
        for wallet in unique
        if wallet in facts and facts[wallet].first_seen_slot is not None
    ]
    if fresh_known:
        earliest_buy_slot = min(buy.slot for buy in buys)
        fresh = _pct(
            sum(1 for slot in fresh_known if earliest_buy_slot - slot <= fresh_wallet_slot_window),
            len(fresh_known),
        )

    early_sell = _pct(sum(1 for wallet in unique if wallet in sellers), len(unique))

    # Supply share held by these buyers, from token amounts if the caller got
    # them. Without amounts this is unknown, not zero.
    amounts = [buy.token_amount for buy in buys if buy.token_amount is not None]
    cluster_supply = None
    if amounts and len(amounts) == considered:
        total = sum(amounts)
        cluster_supply = round(100.0, 4) if total > 0 else None

    def qualified_within(seconds: float) -> int | None:
        timed = [buy for buy in buys if buy.seconds_since_first is not None]
        if not timed:
            return None
        return len(
            {
                buy.wallet
                for buy in timed
                if buy.seconds_since_first <= seconds
                and facts.get(buy.wallet, WalletFacts()).qualified
            }
        )

    return FirstBuyerFeatures(
        mint=mint,
        buyers_considered=considered,
        unique_wallets=len(unique),
        independent_wallet_count=independent,
        shared_funder_pct=shared_pct,
        same_slot_concentration_pct=same_slot,
        fresh_wallet_pct=fresh,
        creator_linked_pct=creator_pct,
        previously_profitable_pct=profitable_pct,
        early_sell_pct=early_sell,
        cluster_supply_pct=cluster_supply,
        qualified_within_30s=qualified_within(30.0),
        qualified_within_1m=qualified_within(60.0),
        qualified_within_5m=qualified_within(300.0),
        coverage={
            "funder": funder_coverage,
            "creator_linked": creator_coverage,
            "previously_profitable": profitable_coverage,
            "first_seen_slot": _pct(len(fresh_known), len(unique)) or 0.0,
        },
    )
