"""Coordinated-wallet ("insider network") detection.

The purpose of this module is defensive. Meme-coin launches are routinely
organised by a group of wallets that fund each other, buy in the same block,
and sell into the retail flow that arrives afterwards. Detecting that structure
*before* entry is the single most valuable pre-trade check available, because it
identifies the trades where the buyer is the exit liquidity.

Two independent evidence tiers are supported, and they are deliberately kept
apart:

``assess_vendor_labels``
    A fast pre-filter over precomputed insider / sniper / bundler / fresh-wallet
    percentages supplied by a discovery feed. Cheap, wide coverage, and entirely
    unverifiable — the vendor publishes neither methodology nor false-negative
    rate. Good enough to discard obvious garbage before spending RPC budget.

``assess_funding_graph``
    Independent reconstruction from on-chain funding relationships. Slower and
    rate-limited, but it is our own evidence.

Three invariants hold throughout:

1. **Absent evidence is never a pass.** A missing field yields
   ``INSUFFICIENT_EVIDENCE``, which callers must treat as a rejection. Coercing
   a missing percentage to zero would convert ignorance into a safety clearance.

2. **Clusters are never asserted as common ownership.** Shared funding is
   consistent with one actor, but also with an airdrop, an exchange withdrawal,
   or a shared router. Every verdict carries a ``confidence`` derived from how
   much evidence was actually observed, and the repository's own methodology
   notes already record why fee-payer matching is evidence rather than proof:
   sponsored and routed transactions can be paid for by another address.

3. **Infrastructure is excluded before measuring concentration.** Pools, burn
   addresses, routers and bridges legitimately hold large balances. Counting
   them as an insider cluster is the classic false positive.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .config import ClusterLimits

# Addresses that hold supply for structural reasons and must never be counted
# as part of an insider cluster. Callers may extend this per chain.
DEFAULT_INFRASTRUCTURE_ADDRESSES: frozenset[str] = frozenset(
    {
        "So11111111111111111111111111111111111111112",  # wrapped SOL
        "11111111111111111111111111111111",  # system program
        "1nc1nerator11111111111111111111111111111111",  # burn
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL token program
        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j",  # Raydium AMM authority
    }
)


def adjusted_top_holder_pct(
    holdings: Iterable[tuple[str, float]],
    supply: float,
    *,
    top_n: int = 10,
    excluded: Iterable[str] = (),
) -> float | None:
    """Top-N concentration as a share of supply, excluding infrastructure.

    ``holdings`` are ``(owner, amount)`` pairs in raw token units, keyed by the
    *owner* rather than the token account, so several accounts belonging to one
    owner count once.

    This is the figure `top10_private_holder_pct` has always meant and never
    received. The raw `getTokenLargestAccounts` number counts the AMM pool's own
    vault, and a freshly migrated token keeps most of its supply there, so the
    gross figure approaches 100% for perfectly ordinary tokens. Writing it into
    the private field once rejected 25 of 25 candidates on a number describing
    the pool.

    **The exclusion list is incomplete by construction** -- it covers the AMM
    authorities and burn addresses that are known, and new venues appear
    constantly. An unrecognised pool therefore counts as a holder and *overstates*
    concentration, which rejects candidates rather than admitting them. That is
    the safe direction for an incomplete list to fail in, and it is why this is
    usable despite the gap.

    Returns None when supply is unknown or nothing is left after exclusion,
    because a concentration of "zero holders" is an absence of measurement
    rather than a perfectly distributed token.
    """
    if supply <= 0:
        return None
    blocked = set(excluded) | DEFAULT_INFRASTRUCTURE_ADDRESSES

    combined: dict[str, float] = {}
    for owner, amount in holdings:
        if not owner or owner in blocked or amount <= 0:
            continue
        combined[owner] = combined.get(owner, 0.0) + amount

    if not combined:
        return None
    largest = sorted(combined.values(), reverse=True)[:top_n]
    return round(100.0 * sum(largest) / supply, 6)


class ClusterVerdict(StrEnum):
    CLEAR = "clear"
    SUSPECT = "suspect"
    DISQUALIFIED = "disqualified"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True)
class ClusterAssessment:
    """Graded finding about coordinated control of a token's supply."""

    verdict: ClusterVerdict
    confidence: float
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def blocks_entry(self) -> bool:
        """True when a fail-closed gate must reject on this assessment."""
        return self.verdict is not ClusterVerdict.CLEAR


@dataclass(frozen=True)
class LaunchBundleEvidence:
    """Whether initial liquidity and the first buys landed atomically.

    On Solana, insiders bundle the liquidity-add and their own buys into one
    Jito bundle so no outside buyer can get in first. When the pool creation and
    the opening buys share a bundle id, the opening distribution was decided
    before the public ever saw the token.
    """

    bundle_id: str | None = None
    liquidity_add_in_bundle: bool | None = None
    buys_in_same_bundle: int | None = None
    buyer_supply_pct_in_bundle: float | None = None

    @property
    def observed(self) -> bool:
        return self.liquidity_add_in_bundle is not None or self.buys_in_same_bundle is not None


def _confidence(present: int, expected: int) -> float:
    if expected <= 0:
        return 0.0
    return round(min(present / expected, 1.0), 3)


def _verdict(
    failures: list[str], warnings: list[str], confidence: float, minimum_confidence: float
) -> ClusterVerdict:
    if failures:
        return ClusterVerdict.DISQUALIFIED
    if confidence < minimum_confidence:
        return ClusterVerdict.INSUFFICIENT_EVIDENCE
    if warnings:
        return ClusterVerdict.SUSPECT
    return ClusterVerdict.CLEAR


def assess_vendor_labels(labels: Any, limits: ClusterLimits) -> ClusterAssessment:
    """Grade a discovery feed's precomputed concentration labels.

    ``labels`` is duck-typed against
    ``providers.binance_web3.VendorClusterLabels`` so alternative feeds can be
    substituted without importing a provider here.
    """
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    checks = (
        ("insider_pct", limits.maximum_insider_pct, "vendor_insider_concentration"),
        ("sniper_pct", limits.maximum_sniper_pct, "vendor_sniper_concentration"),
        ("bundler_pct", limits.maximum_bundler_pct, "vendor_bundler_concentration"),
        ("new_wallet_pct", limits.maximum_fresh_wallet_pct, "vendor_fresh_wallet_concentration"),
    )

    present = 0
    for attribute, maximum, failure in checks:
        value = getattr(labels, attribute, None)
        metrics[attribute] = value
        if value is None:
            continue
        present += 1
        if value > maximum:
            failures.append(failure)

    for attribute, failure in (
        ("dev_wash_trading", "vendor_dev_wash_trading"),
        ("insider_wash_trading", "vendor_insider_wash_trading"),
    ):
        value = getattr(labels, attribute, None)
        metrics[attribute] = value
        if value is True:
            failures.append(failure)

    # Developer distribution is a band, not a flag. Below the lower bound is
    # dust; at or above the upper bound the developer has exited and has no
    # supply left to sell, which is the state that measured *safest*. What
    # rejects is the middle: still holding, and actively selling into buyers.
    # See ClusterLimits.maximum_dev_sell_pct for the measurement behind this.
    dev_sell = getattr(labels, "dev_sell_pct", None)
    metrics["dev_sell_pct"] = dev_sell
    if dev_sell is not None:
        present += 1
        if limits.maximum_dev_sell_pct < dev_sell < limits.exhausted_dev_sell_pct:
            failures.append("vendor_developer_distribution")
        elif dev_sell >= limits.exhausted_dev_sell_pct:
            # Not a pass on its own -- it says the overhang is gone, which is a
            # fact worth carrying rather than silently discarding.
            warnings.append("developer_supply_exhausted")

    # A developer who has migrated many prior tokens is a serial launcher. That
    # is not disqualifying on its own, but it materially raises the prior.
    migrate_count = getattr(labels, "dev_migrate_count", None)
    metrics["dev_migrate_count"] = migrate_count
    if migrate_count is not None and migrate_count > limits.maximum_dev_prior_launches:
        warnings.append("developer_is_serial_launcher")

    confidence = _confidence(present, len(checks) + 1)
    metrics["confidence"] = confidence
    return ClusterAssessment(
        _verdict(failures, warnings, confidence, limits.minimum_vendor_confidence),
        confidence,
        tuple(failures),
        tuple(warnings),
        metrics,
    )


class _UnionFind:
    """Disjoint-set over wallet addresses, used to merge funding components."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression keeps repeated lookups near-constant on wide graphs.
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root

    def groups(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for item in self._parent:
            result.setdefault(self.find(item), []).append(item)
        return result


def build_funding_clusters(
    holder_supply_pct: Mapping[str, float],
    funding_edges: Iterable[tuple[str, str]],
    excluded: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Group holders that share a funding ancestor.

    ``funding_edges`` are ``(funded_address, funder_address)`` pairs, typically
    several hops deep. Excluded addresses are dropped from both the holder set
    and the edge list so infrastructure cannot bridge two unrelated clusters
    into one artificially large component — an exchange hot wallet funds
    thousands of unrelated users, and treating it as a link would merge the
    entire holder base into a single meaningless cluster.
    """
    excluded_set = set(excluded) | DEFAULT_INFRASTRUCTURE_ADDRESSES
    holders = {
        address: pct
        for address, pct in holder_supply_pct.items()
        if address not in excluded_set
    }

    union = _UnionFind()
    for address in holders:
        union.add(address)
    for funded, funder in funding_edges:
        if funded in excluded_set or funder in excluded_set:
            continue
        if funded not in holders and funder not in holders:
            continue
        union.union(funded, funder)

    clusters: list[dict[str, Any]] = []
    for root, members in union.groups().items():
        holding_members = [member for member in members if member in holders]
        if not holding_members:
            continue
        clusters.append(
            {
                "root": root,
                "members": sorted(holding_members),
                "size": len(holding_members),
                "supply_pct": round(sum(holders[member] for member in holding_members), 6),
            }
        )
    clusters.sort(key=lambda cluster: cluster["supply_pct"], reverse=True)
    return clusters


def assess_funding_graph(
    holder_supply_pct: Mapping[str, float],
    funding_edges: Iterable[tuple[str, str]],
    limits: ClusterLimits,
    *,
    bundle: LaunchBundleEvidence | None = None,
    fresh_wallet_funders: Mapping[str, str] | None = None,
    holder_coverage_pct: float | None = None,
    excluded: Iterable[str] = (),
) -> ClusterAssessment:
    """Independently assess coordinated control from on-chain funding data.

    ``holder_coverage_pct`` is the share of total supply the caller was actually
    able to enumerate. Wallet-history endpoints paginate and frequently report
    more records than were retrieved, so a cluster measured over 40% of supply
    cannot be reported with the same confidence as one measured over 95%.
    """
    failures: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    if not holder_supply_pct:
        return ClusterAssessment(
            ClusterVerdict.INSUFFICIENT_EVIDENCE,
            0.0,
            (),
            ("holder_set_empty",),
            {"holder_count": 0},
        )

    clusters = build_funding_clusters(holder_supply_pct, funding_edges, excluded)
    largest = clusters[0] if clusters else None
    largest_pct = float(largest["supply_pct"]) if largest else 0.0

    metrics["holder_count"] = len(holder_supply_pct)
    metrics["cluster_count"] = len(clusters)
    metrics["largest_cluster_pct"] = largest_pct
    metrics["largest_cluster_size"] = largest["size"] if largest else 0
    metrics["largest_cluster_members"] = (largest["members"][:20] if largest else [])

    # A single wallet holding a large share is a concentration problem, but it
    # is visible to anyone. A *cluster* of that size is concealed concentration,
    # which is why it is gated separately and more tightly.
    if largest and largest["size"] > 1 and largest_pct > limits.maximum_linked_cluster_pct:
        failures.append("linked_cluster_concentration")

    if fresh_wallet_funders:
        by_funder: dict[str, list[str]] = {}
        for wallet, funder in fresh_wallet_funders.items():
            if funder in DEFAULT_INFRASTRUCTURE_ADDRESSES:
                continue
            by_funder.setdefault(funder, []).append(wallet)
        if by_funder:
            dominant_funder, funded_wallets = max(
                by_funder.items(), key=lambda item: len(item[1])
            )
            share = 100.0 * len(funded_wallets) / max(len(fresh_wallet_funders), 1)
            metrics["fresh_wallet_count"] = len(fresh_wallet_funders)
            metrics["dominant_funder"] = dominant_funder
            metrics["dominant_funder_share_pct"] = round(share, 3)
            if share > limits.maximum_single_funder_fresh_wallet_pct:
                # Many wallets funded by one address are one economic actor
                # wearing many hats, not independent demand.
                failures.append("fresh_wallets_share_single_funder")

    if bundle is not None and bundle.observed:
        metrics["launch_bundle_id"] = bundle.bundle_id
        metrics["launch_bundle_buys"] = bundle.buys_in_same_bundle
        metrics["launch_bundle_supply_pct"] = bundle.buyer_supply_pct_in_bundle
        bundled_launch = bool(bundle.liquidity_add_in_bundle) and bool(
            bundle.buys_in_same_bundle
        )
        if bundled_launch and limits.reject_on_launch_bundle:
            failures.append("launch_liquidity_and_buys_bundled")
        elif bundled_launch:
            warnings.append("launch_liquidity_and_buys_bundled")
    elif limits.require_bundle_evidence:
        warnings.append("launch_bundle_evidence_missing")

    coverage = 100.0 if holder_coverage_pct is None else float(holder_coverage_pct)
    metrics["holder_coverage_pct"] = coverage
    if coverage < limits.minimum_holder_coverage_pct:
        warnings.append("holder_enumeration_incomplete")

    # Confidence tracks how much of the graph we actually saw, not how alarming
    # the result was. A frightening number measured over a sliver of supply is
    # a weak finding, and the verdict must say so rather than overclaim.
    evidence_present = 1 + int(bool(fresh_wallet_funders)) + int(
        bundle is not None and bundle.observed
    )
    confidence = round(_confidence(evidence_present, 3) * (coverage / 100.0), 3)
    metrics["confidence"] = confidence

    return ClusterAssessment(
        _verdict(failures, warnings, confidence, limits.minimum_graph_confidence),
        confidence,
        tuple(failures),
        tuple(warnings),
        metrics,
    )


def combine(*assessments: ClusterAssessment) -> ClusterAssessment:
    """Merge tiers, taking the worst verdict and the lowest confidence.

    Tiers corroborate rather than average out: one tier reporting a
    disqualifying cluster is not cancelled by another tier that saw nothing,
    because seeing nothing is the expected result when coverage is poor.
    """
    if not assessments:
        return ClusterAssessment(ClusterVerdict.INSUFFICIENT_EVIDENCE, 0.0)

    order = {
        ClusterVerdict.CLEAR: 0,
        ClusterVerdict.SUSPECT: 1,
        ClusterVerdict.INSUFFICIENT_EVIDENCE: 2,
        ClusterVerdict.DISQUALIFIED: 3,
    }
    worst = max(assessments, key=lambda item: order[item.verdict]).verdict
    metrics: dict[str, Any] = {}
    for index, assessment in enumerate(assessments):
        metrics[f"tier_{index}"] = assessment.metrics
    return ClusterAssessment(
        worst,
        min(assessment.confidence for assessment in assessments),
        tuple(failure for item in assessments for failure in item.failures),
        tuple(warning for item in assessments for warning in item.warnings),
        metrics,
    )
