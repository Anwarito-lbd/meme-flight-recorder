"""Multi-Agent Meme-Coin Research Team (strictly paper-only, fail-closed).

Implements the **5 Agents + 1 Desk App + 1 Master Prompt** architecture with
One Front Door, a Read-Only Bridge, and explicit Autonomy Levels (L0/L1/L2):

1. **Discovery & Trend Scout** (``DiscoveryTrendScout``):
   Validates discovery provenance, pool depth tier, pair age, and mint-level
   deduplication across CoinGecko/GMGN/PumpFun/media feeds.
2. **Virality & Narrative Analyst** (``ViralityNarrativeAnalyst``):
   Assesses public media/social chatter into point-in-time ``NarrativeFeatures``
   and an explainable ``ViralityReport``. Strictly advisory: social hype can
   never override a safety or risk gate.
3. **Adversarial On-Chain Investigator** (``AdversarialOnChainInvestigator``):
   Overlays opportunistic vendor labels (without overwriting chain facts), runs
   ``SafetyEngine`` + ``infer_lifecycle`` + ``score_candidate``, and produces a
   dimension-by-dimension ``InvestigationVerdict``.
4. **Risk & Decision Judge** (``PaperRiskJudge`` / ``PaperRiskGate``):
   Synthesizes agent evidence with equity-capped position sizing, max open
   positions, pool-share limits, and round-trip cost ceilings via ``RiskEngine``.
5. **Flight Recorder & Journal Auditor** (``FlightRecorderAuditor``):
   Verifies SHA-256 hash-chain integrity, records immutable desk packets, and
   renders an auditable Markdown Desk Diary (``generate_desk_diary``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .clusters import ClusterAssessment
from .config import CostModel
from .costs import round_trip_cost
from .journal import FlightRecorder
from .lifecycle import infer_lifecycle
from .models import (
    CandidateStatus,
    Lifecycle,
    TokenSnapshot,
    Universe,
)
from .narratives import NarrativeFeatures, NarrativeState, compute_features
from .paper import PaperBroker, PaperPosition
from .providers.binance_web3 import VendorClusterLabels
from .providers.media_feeds import MediaItem
from .readiness import SafetyVerdict, verdict_from_decision
from .risk import CapitalTier, PortfolioState, RiskDecision, RiskEngine, capital_tier
from .safety import SafetyEngine
from .scoring import score_candidate
from .social_intelligence import (
    SocialEvent,
    SocialSource,
    author_credibility,
    extract_mints,
    extract_symbols,
    extract_urls,
    spam_probability,
)
from .sources import SourceRole
from .vendor_labels import apply_vendor_labels


def _source_from_origin(origin: str) -> SocialSource:
    low = (origin or "").lower()
    if low.startswith("r/"):
        return SocialSource.REDDIT
    if low.startswith("@"):
        return SocialSource.TELEGRAM
    if low.startswith("gnews:"):
        return SocialSource.WEB_NEWS
    return SocialSource.OTHER


def normalize_media_items(
    items: Sequence[MediaItem],
    observed_at: datetime,
    *,
    known_mints: set[str] | None = None,
    narrative_id: str | None = None,
) -> list[SocialEvent]:
    """Translate public ``MediaItem`` records into ``SocialEvent`` objects.

    Enforces the literal-mint rule: a mint is populated only when a base58
    address appears verbatim in the item text. Tickers are stored separately
    in ``symbols`` and never treated as identity.
    """
    author_counts = Counter(item.author for item in items)
    unique_authors = len(author_counts)
    events: list[SocialEvent] = []
    for item in items:
        mints = extract_mints(item.text, known=known_mints)
        symbols = extract_symbols(item.text)
        spam = spam_probability(
            item.text,
            author_post_count=author_counts[item.author],
            unique_authors=unique_authors,
        )
        credibility = author_credibility(
            posts_observed=author_counts[item.author],
            account_age_days=None,
            median_engagement=float(item.engagement) if item.engagement is not None else None,
            spam_rate=spam,
        )
        events.append(
            SocialEvent(
                source=_source_from_origin(item.origin),
                author=item.author,
                text=item.text,
                observed_at=observed_at,
                published_at=item.published_at,
                urls=extract_urls(item.text),
                mint=mints[0] if len(mints) == 1 else None,
                symbols=symbols,
                narrative_id=narrative_id or (mints[0] if len(mints) == 1 else None),
                engagement=item.engagement,
                author_credibility=credibility,
                spam_probability=spam,
                external_id=item.external_id,
            )
        )
    return events


@dataclass(frozen=True)
class DiscoveryScoutReport:
    """Output of Agent 1 (Discovery & Trend Scout)."""

    mint: str
    symbol: str
    universe: Universe
    source_channel: str
    deep_pool_qualified: bool
    liquidity_usd: float | None
    age_minutes: float | None
    volume_5m_usd: float | None
    read_only_source: bool = True
    rationale: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["universe"] = self.universe.value
        return data


class DiscoveryTrendScout:
    """Agent 1: characterizes discovery provenance and pool-depth regime."""

    DEEP_POOL_THRESHOLD_USD = 50_000.0

    def scout(
        self,
        snapshot: TokenSnapshot,
        *,
        source_channel: str = "coingecko_trending",
    ) -> DiscoveryScoutReport:
        liq = snapshot.liquidity_usd
        deep = liq is not None and liq >= self.DEEP_POOL_THRESHOLD_USD
        rationale: list[str] = [
            f"Discovered via read-only source '{source_channel}' in universe={snapshot.universe.value}."
        ]
        if deep:
            rationale.append(
                f"Pool liquidity ${liq:,.0f} meets >=$50k deep-pool population threshold."
            )
        elif liq is not None:
            rationale.append(
                f"Pool liquidity ${liq:,.0f} is below $50k deep-pool threshold (launchpad/thin tier)."
            )
        else:
            rationale.append("Pool liquidity is unknown (absent is not zero).")
        return DiscoveryScoutReport(
            mint=snapshot.identity.address,
            symbol=snapshot.identity.symbol,
            universe=snapshot.universe,
            source_channel=source_channel,
            deep_pool_qualified=deep,
            liquidity_usd=liq,
            age_minutes=snapshot.age_minutes,
            volume_5m_usd=snapshot.volume_5m_usd,
            read_only_source=True,
            rationale=tuple(rationale),
        )


@dataclass(frozen=True)
class ViralityReport:
    """Output of Agent 1 (Virality & Narrative Analyst). Advisory only."""

    mint: str
    symbol: str
    as_of: datetime
    narrative_state: NarrativeState
    mentions: int
    unique_authors: int
    credible_authors: int
    source_diversity: int
    attention_velocity: float | None
    attention_acceleration: float | None
    spam_share: float | None
    novelty: float | None
    saturation: float | None
    momentum_score: float
    advisory_stance: str
    advisory_only: bool
    rationale: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["as_of"] = self.as_of.isoformat()
        data["narrative_state"] = self.narrative_state.value
        return data


class ViralityNarrativeAnalyst:
    """Agent 1: evaluates narrative momentum, author credibility, and spam share.

    Pure and deterministic. Never authorizes a trade or bypasses a safety gate.
    """

    def analyze(
        self,
        mint: str,
        symbol: str,
        events: Sequence[SocialEvent],
        as_of: datetime,
        *,
        window_minutes: float = 30.0,
        universe_authors: int | None = None,
        source_roles: dict[str, SourceRole] | None = None,
    ) -> ViralityReport:
        relevant = [
            ev
            for ev in events
            if ev.mint == mint or (ev.mint is None and ev.narrative_id == mint)
        ]
        # Deduplicate relays so one forwarded message cannot inflate momentum.
        deduped: dict[str, SocialEvent] = {}
        for ev in relevant:
            deduped.setdefault(ev.dedup_key, ev)
        unique_events = list(deduped.values())

        features: NarrativeFeatures = compute_features(
            narrative_id=mint,
            events=unique_events,
            as_of=as_of,
            window_minutes=window_minutes,
            universe_authors=universe_authors,
        )

        # Compute transparent advisory momentum score (0..100).
        cred_pts = min(35.0, features.credible_authors * 8.75)
        div_pts = min(20.0, features.source_diversity * 10.0)
        vel_pts = min(25.0, (features.attention_velocity or 0.0) * 25.0)
        nov_pts = (features.novelty or 0.0) * 20.0
        spam_penalty = (features.spam_share or 0.0) * 40.0
        raw_score = max(0.0, min(100.0, cred_pts + div_pts + vel_pts + nov_pts - spam_penalty))
        momentum_score = round(raw_score, 2)

        rationale: list[str] = []
        if features.mentions == 0:
            stance = "quiet_or_absent"
            rationale.append("No literal-mint social events observed before as_of.")
        elif (features.spam_share or 0.0) >= 0.5:
            stance = "spam_heavy"
            rationale.append(
                f"High promotional spam share ({features.spam_share:.0%}); "
                "discounted despite raw chatter."
            )
        elif (
            features.credible_authors >= 3
            and features.source_diversity >= 2
            and features.state
            in {NarrativeState.EMERGING, NarrativeState.ACCELERATING, NarrativeState.BREAKOUT}
        ):
            stance = "strong_discovery"
            rationale.append(
                f"{features.credible_authors} credible authors across "
                f"{features.source_diversity} public sources ({features.state.value})."
            )
        else:
            stance = "emerging_chatter"
            rationale.append(
                f"Observed {features.mentions} deduplicated mentions "
                f"({features.credible_authors} credible, state={features.state.value})."
            )

        if source_roles:
            originators = [
                k for k, role in source_roles.items() if role == SourceRole.ORIGINATOR
            ]
            if originators:
                rationale.append(
                    f"Source registry tags {len(originators)} originator source(s): "
                    f"{', '.join(sorted(originators)[:3])}."
                )

        rationale.append(
            "Advisory-only output: virality never waives on-chain safety or paper risk caps."
        )

        return ViralityReport(
            mint=mint,
            symbol=symbol,
            as_of=as_of,
            narrative_state=features.state,
            mentions=features.mentions,
            unique_authors=features.unique_authors,
            credible_authors=features.credible_authors,
            source_diversity=features.source_diversity,
            attention_velocity=features.attention_velocity,
            attention_acceleration=features.attention_acceleration,
            spam_share=features.spam_share,
            novelty=features.novelty,
            saturation=features.saturation,
            momentum_score=momentum_score,
            advisory_stance=stance,
            advisory_only=True,
            rationale=tuple(rationale),
        )


@dataclass(frozen=True)
class InvestigationVerdict:
    """Output of Agent 2 (Adversarial On-Chain Investigator)."""

    entity_id: str
    mint: str
    symbol: str
    universe: Universe
    safety_verdict: SafetyVerdict
    candidate_status: CandidateStatus
    lifecycle: Lifecycle
    quality_score: float
    confidence: float
    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    vendor_resolved: tuple[str, ...]
    gate_breakdown: dict[str, str]
    rationale: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["universe"] = self.universe.value
        data["safety_verdict"] = self.safety_verdict.value
        data["candidate_status"] = self.candidate_status.value
        data["lifecycle"] = self.lifecycle.value
        return data


def _classify_dimension(
    failures: tuple[str, ...], warnings: tuple[str, ...], prefixes: tuple[str, ...]
) -> str:
    matched_f = [f for f in failures if any(f.startswith(p) or f == p for p in prefixes)]
    if matched_f:
        if all(f.endswith(("_unknown", "_missing", "_insufficient", "_invalid")) for f in matched_f):
            return "unknown"
        return "fail"
    if any(any(w.startswith(p) or w == p for p in prefixes) for w in warnings):
        return "warn"
    return "pass"


class AdversarialOnChainInvestigator:
    """Agent 2: fail-closed on-chain safety, concentration, liquidity, & vendor audit."""

    def __init__(self, safety_engine: SafetyEngine) -> None:
        self.safety_engine = safety_engine

    def investigate(
        self,
        snapshot: TokenSnapshot,
        *,
        now: datetime | None = None,
        cluster: ClusterAssessment | None = None,
        vendor_labels: VendorClusterLabels | None = None,
    ) -> tuple[TokenSnapshot, InvestigationVerdict]:
        enriched_snapshot, vendor_resolved = apply_vendor_labels(snapshot, vendor_labels)
        decision = self.safety_engine.evaluate(enriched_snapshot, now=now, cluster=cluster)
        safety_verdict = verdict_from_decision(decision)
        lifecycle = infer_lifecycle(enriched_snapshot)
        score = score_candidate(enriched_snapshot)

        breakdown = {
            "identity_and_freshness": _classify_dimension(
                decision.failures,
                decision.warnings,
                ("identity_", "snapshot_stale", "token_age_", "token_too_young"),
            ),
            "authority_and_rug_flags": _classify_dimension(
                decision.failures,
                decision.warnings,
                ("mint_authority_", "freeze_authority_", "developer_", "connected_wallet_"),
            ),
            "concentration_and_clusters": _classify_dimension(
                decision.failures,
                decision.warnings,
                ("holder_concentration_", "cluster_"),
            ),
            "liquidity_and_exit_route": _classify_dimension(
                decision.failures,
                decision.warnings,
                (
                    "liquidity_",
                    "entry_route_",
                    "exit_route_",
                    "transaction_simulation_",
                    "entry_price_impact_",
                    "exit_price_impact_",
                ),
            ),
        }

        rationale: list[str] = [
            (
                f"Safety verdict={safety_verdict.value}, status={decision.status.value}, "
                f"lifecycle={lifecycle.value}."
            )
        ]
        if vendor_resolved:
            rationale.append(
                f"Opportunistic vendor labels resolved: {', '.join(vendor_resolved)}."
            )
        if decision.failures:
            rationale.append(f"Fail-closed rejections: {', '.join(decision.failures)}.")
        else:
            rationale.append("All adversarial on-chain and exit-route checks passed.")
        if decision.warnings:
            rationale.append(f"Warnings: {', '.join(decision.warnings)}.")

        entity_id = f"{enriched_snapshot.identity.chain}:{enriched_snapshot.identity.address}"
        verdict = InvestigationVerdict(
            entity_id=entity_id,
            mint=enriched_snapshot.identity.address,
            symbol=enriched_snapshot.identity.symbol,
            universe=enriched_snapshot.universe,
            safety_verdict=safety_verdict,
            candidate_status=decision.status,
            lifecycle=lifecycle,
            quality_score=score.score,
            confidence=score.confidence,
            failures=decision.failures,
            warnings=decision.warnings,
            vendor_resolved=vendor_resolved,
            gate_breakdown=breakdown,
            rationale=tuple(rationale),
        )
        return enriched_snapshot, verdict


@dataclass(frozen=True)
class PaperRiskVerdict:
    """Output of Agent 3 (Paper Risk Gate). Strictly paper-only sizing & caps."""

    approved: bool
    reason: str
    capital_tier: CapitalTier
    position_value_usd: float
    risk_amount_usd: float
    estimated_round_trip_cost_pct: float | None
    breakeven_multiple: float | None
    equity_cap_pct: float
    rationale: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capital_tier"] = self.capital_tier.value
        return data


class PaperRiskGate:
    """Agent 3: enforces equity-capped sizing, position limits, and cost ceilings."""

    def __init__(self, risk_engine: RiskEngine, cost_model: CostModel | None = None) -> None:
        self.risk_engine = risk_engine
        self.cost_model = cost_model or CostModel()

    def evaluate(
        self,
        investigation: InvestigationVerdict,
        snapshot: TokenSnapshot,
        state: PortfolioState,
        *,
        stop_price: float | None = None,
        proposed_position_usd: float | None = None,
    ) -> PaperRiskVerdict:
        tier = capital_tier(state.equity_usd, self.risk_engine.micro)
        if investigation.candidate_status != CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW:
            return PaperRiskVerdict(
                approved=False,
                reason=f"safety_gate_{investigation.candidate_status.value}",
                capital_tier=tier,
                position_value_usd=0.0,
                risk_amount_usd=0.0,
                estimated_round_trip_cost_pct=None,
                breakeven_multiple=None,
                equity_cap_pct=0.0,
                rationale=(
                    (
                        "Paper Risk Gate blocked entry because On-Chain Investigator "
                        f"returned {investigation.candidate_status.value}."
                    ),
                ),
            )

        if snapshot.price_usd is None or snapshot.price_usd <= 0:
            return PaperRiskVerdict(
                approved=False,
                reason="entry_price_unknown",
                capital_tier=tier,
                position_value_usd=0.0,
                risk_amount_usd=0.0,
                estimated_round_trip_cost_pct=None,
                breakeven_multiple=None,
                equity_cap_pct=0.0,
                rationale=("Fail-closed: snapshot.price_usd is missing or non-positive.",),
            )

        # Estimate the contemplated stake first so cost modeling reflects fixed fees.
        probe_usd = proposed_position_usd
        if probe_usd is None:
            if self.risk_engine.micro is not None and state.equity_usd < (
                self.risk_engine.micro.equity_threshold_usd
            ):
                probe_usd = min(
                    self.risk_engine.micro.max_position_usd,
                    state.equity_usd * self.risk_engine.micro.position_pct_of_equity / 100.0,
                    state.equity_usd,
                )
            else:
                probe_usd = max(
                    0.01,
                    state.equity_usd
                    * self.risk_engine.limits.capital_at_risk_per_solana_trade_pct
                    / 100.0,
                )

        cost_breakdown = (
            round_trip_cost(
                probe_usd,
                self.cost_model,
                entry_impact_pct=snapshot.entry_price_impact_pct,
                exit_impact_pct=snapshot.exit_price_impact_pct,
            )
            if probe_usd > 0
            else None
        )
        est_cost_pct = cost_breakdown.pct_of_position if cost_breakdown is not None else None
        breakeven_mult = cost_breakdown.breakeven_multiple if cost_breakdown is not None else None

        decision: RiskDecision = self.risk_engine.approve(
            snapshot.universe,
            state,
            snapshot.price_usd,
            stop_price,
            pool_liquidity_usd=snapshot.liquidity_usd,
            estimated_round_trip_cost_pct=est_cost_pct,
            proposed_position_usd=proposed_position_usd,
        )

        equity_cap_pct = (
            round(100.0 * decision.position_value_usd / state.equity_usd, 2)
            if state.equity_usd > 0 and decision.approved
            else 0.0
        )

        rationale: list[str] = []
        if decision.approved:
            rationale.append(
                f"Approved paper position ${decision.position_value_usd:.2f} "
                f"({equity_cap_pct:.1f}% of ${state.equity_usd:.2f} equity, tier={tier.value})."
            )
            if est_cost_pct is not None:
                rationale.append(
                    f"Modeled round-trip cost={est_cost_pct:.2f}% "
                    f"(breakeven {breakeven_mult:.4f}x)."
                )
        else:
            rationale.append(f"Rejected by Paper Risk Gate: {decision.reason}.")

        return PaperRiskVerdict(
            approved=decision.approved,
            reason=decision.reason,
            capital_tier=tier,
            position_value_usd=decision.position_value_usd,
            risk_amount_usd=decision.risk_amount_usd,
            estimated_round_trip_cost_pct=est_cost_pct,
            breakeven_multiple=breakeven_mult,
            equity_cap_pct=equity_cap_pct,
            rationale=tuple(rationale),
        )


PaperRiskJudge = PaperRiskGate


@dataclass(frozen=True)
class AuditSeal:
    """Output of Agent 5 (Flight Recorder & Journal Auditor)."""

    event_hash: str
    chain_verified: bool
    total_events: int
    autonomy_level: AutonomyLevel
    execution_mode: str = "paper"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["autonomy_level"] = self.autonomy_level.value
        return data


class FlightRecorderAuditor:
    """Agent 5: appends hash-chained packets and verifies cryptographic integrity."""

    def __init__(self, recorder: FlightRecorder) -> None:
        self.recorder = recorder

    def record_packet(
        self,
        entity_id: str,
        payload: dict[str, Any],
        observed_at: datetime,
        autonomy_level: AutonomyLevel,
    ) -> AuditSeal:
        event_hash = self.recorder.append("research_desk_packet", entity_id, payload, observed_at)
        chain_ok = self.recorder.verify_chain()
        total = len(self.recorder.list_events(limit=1000))
        return AuditSeal(
            event_hash=event_hash,
            chain_verified=chain_ok,
            total_events=total,
            autonomy_level=autonomy_level,
        )


class AutonomyLevel(StrEnum):
    """Explicit operator autonomy levels; nothing ever signs or moves real funds.

    Follows the 'one front door + decision layer + read-only bridge + explicit
    autonomy levels' control architecture:
      - L0: observe and evaluate only (no paper position mutations).
      - L1: paper replay and hash-chained journaling in PaperBroker.
      - L2: emits an unsigned HumanApprovalDraft requiring operator confirmation.
    """

    L0_OBSERVE_ONLY = "L0_observe_only"
    L1_PAPER_REPLAY_AND_JOURNAL = "L1_paper_replay_and_journal"
    L2_HUMAN_APPROVAL_DRAFT = "L2_human_approval_draft"


@dataclass(frozen=True)
class BridgeSkillDescriptor:
    """Metadata for a strictly read-only market/on-chain telemetry adapter."""

    name: str
    source: str
    role: str
    write_capable: bool = False
    signing_capable: bool = False


class ReadOnlyBridge:
    """Read-only bridge separating the Decision Layer from market/chain data.

    Neither internal providers nor documented ecosystem reference patterns
    can sign transactions or mutate external state.
    """

    INTERNAL_ADAPTERS: tuple[BridgeSkillDescriptor, ...] = (
        BridgeSkillDescriptor(
            "coingecko_onchain", "providers/coingecko.py", "Trending & pool OHLCV discovery"
        ),
        BridgeSkillDescriptor(
            "dexscreener_pairs", "providers/dexscreener.py", "Liquidity, pair age & batch pricing"
        ),
        BridgeSkillDescriptor(
            "jupiter_quotes", "providers/jupiter.py", "Entry & reverse-route impact quotes"
        ),
        BridgeSkillDescriptor(
            "helius_rpc", "providers/helius.py", "Mint/freeze authority & largest accounts"
        ),
        BridgeSkillDescriptor(
            "binance_web3_labels", "vendor_labels.py", "Opportunistic dev-sell & cluster feed"
        ),
        BridgeSkillDescriptor(
            "goplus_security", "providers/goplus.py", "Token security metadata audit"
        ),
        BridgeSkillDescriptor(
            "public_media_feeds", "providers/media_feeds.py", "Reddit Atom, TG preview & GNews RSS"
        ),
    )

    ECOSYSTEM_REFERENCES: tuple[BridgeSkillDescriptor, ...] = (
        BridgeSkillDescriptor(
            "gmgn-skills",
            "github.com/GMGNAI/gmgn-skills",
            "Smart-money & holder concentration query patterns (read-only)",
        ),
        BridgeSkillDescriptor(
            "pump-public-docs",
            "github.com/pump-fun/pump-public-docs",
            "Pump.fun bonding curve & PumpSwap AMM specifications (read-only)",
        ),
        BridgeSkillDescriptor(
            "pumpfun-cli",
            "github.com/chainstacklabs/pumpfun-cli",
            "Bonding-curve CLI telemetry pattern (read-only reference)",
        ),
        BridgeSkillDescriptor(
            "pumpfun-bonkfun-bot",
            "github.com/chainstacklabs/pumpfun-bonkfun-bot",
            "Launchpad stream parsing reference (execution paths excluded)",
        ),
        BridgeSkillDescriptor(
            "solana-agent-kit",
            "github.com/sendaifun/solana-agent-kit",
            "Solana token-analytics & read-only agent tool patterns",
        ),
    )

    @classmethod
    def manifest(cls) -> dict[str, Any]:
        return {
            "write_capable": False,
            "signing_capable": False,
            "internal_adapters": [asdict(item) for item in cls.INTERNAL_ADAPTERS],
            "ecosystem_references": [asdict(item) for item in cls.ECOSYSTEM_REFERENCES],
        }


@dataclass(frozen=True)
class HumanApprovalDraft:
    """Unsigned proposal emitted at L2_HUMAN_APPROVAL_DRAFT; never executes."""

    entity_id: str
    symbol: str
    proposed_position_usd: float
    estimated_round_trip_cost_pct: float | None
    breakeven_multiple: float | None
    safety_verdict: str
    requires_human_confirmation: bool = True
    execution_blocked: bool = True
    signing_capable: bool = False


@dataclass(frozen=True)
class ResearchDeskPacket:
    """Complete 5-agent research dossier journalled into FlightRecorder."""

    entity_id: str
    observed_at: datetime
    autonomy_level: AutonomyLevel
    final_action: str
    discovery: DiscoveryScoutReport
    virality: ViralityReport
    investigation: InvestigationVerdict
    risk: PaperRiskVerdict
    audit: AuditSeal
    paper_position: PaperPosition | None
    human_approval_draft: HumanApprovalDraft | None
    event_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "observed_at": self.observed_at.isoformat(),
            "autonomy_level": self.autonomy_level.value,
            "final_action": self.final_action,
            "discovery": self.discovery.as_dict(),
            "virality": self.virality.as_dict(),
            "investigation": self.investigation.as_dict(),
            "risk": self.risk.as_dict(),
            "audit": self.audit.as_dict(),
            "paper_position": asdict(self.paper_position) if self.paper_position else None,
            "human_approval_draft": (
                asdict(self.human_approval_draft) if self.human_approval_draft else None
            ),
            "event_hash": self.event_hash,
        }


class ResearchTeamCoordinator:
    """Orchestrates the 5 research agents + hash-chained FlightRecorder."""

    def __init__(
        self,
        recorder: FlightRecorder,
        safety_engine: SafetyEngine,
        risk_engine: RiskEngine,
        *,
        broker: PaperBroker | None = None,
        cost_model: CostModel | None = None,
    ) -> None:
        self.recorder = recorder
        self.discovery_scout = DiscoveryTrendScout()
        self.virality_analyst = ViralityNarrativeAnalyst()
        self.investigator = AdversarialOnChainInvestigator(safety_engine)
        self.risk_gate = PaperRiskJudge(risk_engine, cost_model=cost_model)
        self.auditor = FlightRecorderAuditor(recorder)
        self.broker = broker or PaperBroker(recorder)

    def evaluate_candidate(
        self,
        snapshot: TokenSnapshot,
        social_events: Sequence[SocialEvent],
        portfolio_state: PortfolioState,
        *,
        as_of: datetime | None = None,
        source_channel: str = "coingecko_trending",
        cluster: ClusterAssessment | None = None,
        vendor_labels: VendorClusterLabels | None = None,
        source_roles: dict[str, SourceRole] | None = None,
        stop_price: float | None = None,
        proposed_position_usd: float | None = None,
        execute_paper_open: bool = False,
        autonomy_level: AutonomyLevel | None = None,
    ) -> ResearchDeskPacket:
        now = as_of or snapshot.observed_at
        level = autonomy_level or (
            AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL
            if execute_paper_open
            else AutonomyLevel.L0_OBSERVE_ONLY
        )
        entity_id = f"{snapshot.identity.chain}:{snapshot.identity.address}"

        # 1. Agent 1: Discovery & Trend Scout
        discovery = self.discovery_scout.scout(snapshot, source_channel=source_channel)

        # 2. Agent 2: Virality & Narrative Analyst (advisory only)
        virality = self.virality_analyst.analyze(
            snapshot.identity.address,
            snapshot.identity.symbol,
            social_events,
            as_of=now,
            source_roles=source_roles,
        )

        # 3. Agent 3: Adversarial On-Chain Investigator
        enriched_snapshot, investigation = self.investigator.investigate(
            snapshot,
            now=now,
            cluster=cluster,
            vendor_labels=vendor_labels,
        )
        self.recorder.append(
            "snapshot_observed",
            entity_id,
            enriched_snapshot.as_dict(),
            enriched_snapshot.observed_at,
        )
        self.recorder.append(
            "candidate_evaluated",
            entity_id,
            {
                "entity_id": entity_id,
                "universe": enriched_snapshot.universe.value,
                "status": investigation.candidate_status.value,
                "failures": list(investigation.failures),
                "warnings": list(investigation.warnings),
                "lifecycle": investigation.lifecycle.value,
                "score": {
                    "score": investigation.quality_score,
                    "confidence": investigation.confidence,
                },
            },
            now,
        )

        # 3. Agent 3: Paper Risk Gate
        risk_verdict = self.risk_gate.evaluate(
            investigation,
            enriched_snapshot,
            portfolio_state,
            stop_price=stop_price,
            proposed_position_usd=proposed_position_usd,
        )

        paper_position: PaperPosition | None = None
        human_draft: HumanApprovalDraft | None = None
        if investigation.candidate_status == CandidateStatus.MONITOR:
            final_action = "MONITOR_ONLY"
        elif investigation.candidate_status == CandidateStatus.REJECT:
            final_action = "REJECTED"
        elif not risk_verdict.approved:
            final_action = "RISK_REJECTED"
            self.recorder.append(
                "paper_risk_rejected",
                entity_id,
                risk_verdict.as_dict(),
                now,
            )
        elif level == AutonomyLevel.L2_HUMAN_APPROVAL_DRAFT:
            human_draft = HumanApprovalDraft(
                entity_id=entity_id,
                symbol=enriched_snapshot.identity.symbol,
                proposed_position_usd=risk_verdict.position_value_usd,
                estimated_round_trip_cost_pct=risk_verdict.estimated_round_trip_cost_pct,
                breakeven_multiple=risk_verdict.breakeven_multiple,
                safety_verdict=investigation.safety_verdict.value,
            )
            final_action = "HUMAN_APPROVAL_DRAFT"
        elif level == AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL and execute_paper_open:
            assert enriched_snapshot.price_usd is not None
            fees_usd = 0.0
            if risk_verdict.estimated_round_trip_cost_pct is not None:
                fees_usd = round(
                    risk_verdict.position_value_usd
                    * risk_verdict.estimated_round_trip_cost_pct
                    / 100.0,
                    6,
                )
            paper_position = self.broker.open_position(
                asset_id=enriched_snapshot.identity.address,
                universe=enriched_snapshot.universe,
                entry_price=enriched_snapshot.price_usd,
                position_value_usd=risk_verdict.position_value_usd,
                stop_price=stop_price,
                risk_amount_usd=risk_verdict.risk_amount_usd,
                fees_paid_usd=fees_usd,
                modeled_slippage_pct=(
                    (enriched_snapshot.entry_price_impact_pct or 0.0)
                    + (enriched_snapshot.exit_price_impact_pct or 0.0)
                ),
            )
            final_action = "PAPER_OPEN"
        else:
            final_action = "ELIGIBLE_OBSERVED"

        # 5. Agent 5: Hash-chained Flight Recorder Auditor
        packet_payload = {
            "entity_id": entity_id,
            "symbol": enriched_snapshot.identity.symbol,
            "autonomy_level": level.value,
            "final_action": final_action,
            "discovery": discovery.as_dict(),
            "virality": virality.as_dict(),
            "investigation": investigation.as_dict(),
            "risk": risk_verdict.as_dict(),
            "paper_position_id": paper_position.position_id if paper_position else None,
            "human_approval_draft": asdict(human_draft) if human_draft else None,
        }
        audit = self.auditor.record_packet(entity_id, packet_payload, now, level)

        return ResearchDeskPacket(
            entity_id=entity_id,
            observed_at=now,
            autonomy_level=level,
            final_action=final_action,
            discovery=discovery,
            virality=virality,
            investigation=investigation,
            risk=risk_verdict,
            audit=audit,
            paper_position=paper_position,
            human_approval_draft=human_draft,
            event_hash=audit.event_hash,
        )


def generate_desk_diary(
    recorder: FlightRecorder,
    *,
    as_of: datetime | None = None,
    title: str = "Multi-Agent Paper Research Desk Diary",
) -> str:
    """Render an auditable Markdown Desk Diary from the hash-chained journal."""
    stamp = (as_of or datetime.now(UTC)).isoformat()
    chain_ok = recorder.verify_chain()
    packets = recorder.events_by_type("research_desk_packet")
    opened = recorder.events_by_type("paper_position_opened")
    closed = recorder.events_by_type("paper_position_closed")
    evaluations = recorder.events_by_type("candidate_evaluated")
    all_recent = recorder.list_events(limit=1000)
    head_hash = all_recent[0]["event_hash"][:16] if all_recent else "GENESIS"

    action_counts = Counter(p["payload"].get("final_action", "UNKNOWN") for p in packets)
    failure_counts: Counter[str] = Counter()
    for ev in evaluations:
        for f in ev["payload"].get("failures", []):
            failure_counts[f] += 1

    realized_pnl = sum(float(c["payload"].get("pnl_usd", 0.0)) for c in closed)
    wins = sum(1 for c in closed if float(c["payload"].get("pnl_usd", 0.0)) > 0)
    closed_ids = {c["payload"].get("position_id") for c in closed}
    open_positions = [o for o in opened if o["payload"].get("position_id") not in closed_ids]

    chain_line = (
        f"- **Hash Chain Integrity:** `{'VERIFIED' if chain_ok else 'CORRUPTED'}` "
        f"(Head SHA-256 prefix: `{head_hash}`)"
    )
    packets_line = (
        f"- **Research Packets Logged:** `{len(packets)}` "
        f"(Paper Open: {action_counts.get('PAPER_OPEN', 0)}, "
        f"L2 Draft: {action_counts.get('HUMAN_APPROVAL_DRAFT', 0)}, "
        f"Monitor: {action_counts.get('MONITOR_ONLY', 0)}, "
        f"Risk Rejected: {action_counts.get('RISK_REJECTED', 0)}, "
        f"Safety Rejected: {action_counts.get('REJECTED', 0)})"
    )
    book_line = (
        f"- **Paper Book:** `{len(open_positions)}` open / `{len(closed)}` closed "
        f"(Wins: `{wins}/{len(closed)}`, Realized PnL: `${realized_pnl:+.4f}`)"
    )

    lines: list[str] = [
        f"# {title}",
        "",
        f"- **Generated (UTC):** `{stamp}`",
        "- **Execution Mode:** `PAPER ONLY` (no private keys, no live signing, no swaps)",
        "- **Control Architecture:** One Front Door -> 3-Agent Decision Layer -> Read-Only Bridge",
        chain_line,
        packets_line,
        book_line,
        "",
        "## Gate Rejection Pareto",
        "",
    ]

    if failure_counts:
        lines.extend(["| Gate Failure Reason | Count |", "|---|---:|"])
        for reason, count in failure_counts.most_common():
            lines.append(f"| `{reason}` | {count} |")
    else:
        lines.append("_No gate failures recorded._")

    lines.extend(["", "## Multi-Agent Research Packets", ""])
    if packets:
        lines.extend([
            "| Symbol | Action | Virality Stance (Score) | Safety Verdict | Risk Gate | Hash |",
            "|---|---|---|---:|---|---|",
        ])
        for row in packets:
            p = row["payload"]
            vir = p.get("virality", {})
            inv = p.get("investigation", {})
            rsk = p.get("risk", {})
            sym = p.get("symbol") or p.get("entity_id", "?")
            lines.append(
                f"| `{sym}` | `{p.get('final_action')}` | "
                f"{vir.get('advisory_stance')} ({vir.get('momentum_score', 0):.1f}) | "
                f"`{inv.get('safety_verdict')}` / `{inv.get('candidate_status')}` | "
                f"{'APPROVED ($' + str(rsk.get('position_value_usd', 0)) + ')' if rsk.get('approved') else rsk.get('reason')} | "
                f"`{row['event_hash'][:10]}` |"
            )
    else:
        lines.append("_No multi-agent packets recorded yet._")

    lines.extend(["", "## Paper Book Closures", ""])
    if closed:
        lines.extend(["| Asset | Exit Reason | PnL (USD) | R-Multiple |", "|---|---|---:|---:|"])
        for row in closed:
            c = row["payload"]
            lines.append(
                f"| `{str(c.get('asset_id', ''))[:12]}` | `{c.get('reason')}` | "
                f"${float(c.get('pnl_usd', 0.0)):+.4f} | {float(c.get('r_multiple', 0.0)):+.2f}R |"
            )
    else:
        lines.append("_No closed paper positions recorded._")

    lines.append("")
    return "\n".join(lines)
