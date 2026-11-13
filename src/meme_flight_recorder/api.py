from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .cex_engine import CexPaperEngine
from .config import load_settings
from .desk_ui import build_desk_state, render_desk_html
from .enrichment import apply_pair_evidence, enrich_snapshot
from .journal import FlightRecorder
from .models import TokenIdentity, TokenSnapshot, Universe
from .notebook import (
    EvidenceConfidence,
    HypothesisStatus,
    ResearchFinding,
    ResearchNotebook,
)
from .paper import PaperBroker
from .research_team import AutonomyLevel, ResearchTeamCoordinator, generate_desk_diary
from .risk import PortfolioState, RiskEngine
from .safety import SafetyEngine
from .service import CandidateService
from .shadow import SolanaShadowEngine
from .social_intelligence import (
    SocialEvent,
    SocialSource,
    author_credibility,
    spam_probability,
)
from .strategy import Candle
from .vendor_labels import VendorLabelIndex, apply_vendor_labels


class CandidateInput(BaseModel):
    chain: str
    address: str
    symbol: str
    name: str
    verified: bool = False
    universe: Universe
    observed_at: datetime
    provider_observed_at: datetime | None = None
    facts: dict[str, Any] = Field(default_factory=dict)


class EnrichCandidateInput(CandidateInput):
    """A candidate plus the size of the order actually contemplated.

    Price impact is a function of order size, so a nominal quote followed by a
    larger trade measures the wrong trade. The caller knows its own position
    size; it must say so rather than have a default stand in for it.
    """

    intended_order_sol: float = Field(default=0.05, gt=0)


class PaperOpenInput(BaseModel):
    asset_id: str
    universe: Universe
    entry_price: float = Field(gt=0)
    stop_price: float | None = None
    equity_usd: float = Field(gt=0)
    daily_realized_pnl_usd: float = 0
    aggregate_open_risk_usd: float = Field(default=0, ge=0)
    consecutive_losses: int = Field(default=0, ge=0)
    kill_switch: bool = False


class CandleInput(BaseModel):
    timestamp: int
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)


class CexObserveInput(BaseModel):
    asset_id: str
    candles_1h: list[CandleInput]
    candles_15m: list[CandleInput]
    equity_usd: float = Field(gt=0)
    daily_realized_pnl_usd: float = 0
    aggregate_open_risk_usd: float = Field(default=0, ge=0)
    consecutive_losses: int = Field(default=0, ge=0)
    kill_switch: bool = False


class ShadowOpenInput(BaseModel):
    mint: str
    quoted_price: float = Field(gt=0)
    entry_price_impact_pct: float = Field(ge=0)
    exit_route_verified: bool
    simulation_ok: bool
    equity_usd: float = Field(gt=0)
    daily_realized_pnl_usd: float = 0
    aggregate_open_risk_usd: float = Field(default=0, ge=0)
    consecutive_losses: int = Field(default=0, ge=0)
    kill_switch: bool = False


class ResearchFindingInput(BaseModel):
    finding_id: str
    actor: str
    claim: str
    repeatable_mechanism: str
    hypothesis: str
    observed_at: datetime
    source_published_at: datetime
    source_urls: list[str]
    confidence: EvidenceConfidence = EvidenceConfidence.UNVERIFIED
    status: HypothesisStatus = HypothesisStatus.LEAD
    exact_mint: str | None = None
    wallet_address: str | None = None
    invalidation_condition: str = ""


@lru_cache
def components() -> tuple[
    CandidateService,
    FlightRecorder,
    PaperBroker,
    RiskEngine,
    CexPaperEngine,
    SolanaShadowEngine,
]:
    settings = load_settings()
    recorder = FlightRecorder(settings.database_path)
    safety = SafetyEngine(settings.cex_safety, settings.solana_safety, settings.stale_after_seconds)
    broker = PaperBroker(recorder)
    risk = RiskEngine(settings.risk)
    return (
        CandidateService(recorder, safety),
        recorder,
        broker,
        risk,
        CexPaperEngine(recorder, broker, risk),
        SolanaShadowEngine(recorder, broker, risk),
    )


app = FastAPI(title="Meme Flight Recorder", version="0.4.0")


@app.get("/health")
def health() -> dict[str, object]:
    _, recorder, _, _, _, _ = components()
    return {"status": "ok", "execution_mode": "paper", "journal_valid": recorder.verify_chain()}


def _snapshot_from(body: CandidateInput) -> TokenSnapshot:
    known = set(TokenSnapshot.__dataclass_fields__) - {
        "identity",
        "universe",
        "observed_at",
        "provider_observed_at",
    }
    unknown = set(body.facts) - known
    if unknown:
        raise HTTPException(422, f"Unknown facts: {sorted(unknown)}")
    return TokenSnapshot(
        identity=TokenIdentity(
            body.chain, body.address, body.symbol, body.name, verified=body.verified
        ),
        universe=body.universe,
        observed_at=body.observed_at,
        provider_observed_at=body.provider_observed_at,
        **body.facts,
    )


@lru_cache
def _vendor_index() -> Any:
    """Shared vendor-label index, or None when the feed cannot be reached.

    Cached because it is a TTL feed index, not a per-mint lookup: one refresh
    serves every candidate evaluated inside the TTL. Building it per request
    would spend the vendor's rate limit re-fetching the same 300 rows.
    """
    try:
        from .providers.binance_web3 import BinanceWeb3Provider

        return VendorLabelIndex(BinanceWeb3Provider())
    except Exception:  # noqa: BLE001 - unavailable is not the same as unsafe
        return None


@lru_cache
def _enrichment_providers() -> tuple[Any, Any, Any, Any]:
    """Wire only the providers that are actually available.

    Constructed once and cached because each holds a session and a rate-limit
    budget; rebuilding them per request would multiply both. Degrades rather
    than guesses, exactly as ``cli._collect`` does: a provider that cannot be
    built stays ``None``, its fields stay unknown, and the gate keeps rejecting.
    A missing provider must never look like a pass.
    """
    from .providers.dexscreener import DexScreenerProvider
    from .providers.goplus import GoPlusProvider
    from .providers.helius import HeliusProvider
    from .providers.jupiter import JupiterQuoteProvider

    def _try(factory: Any) -> Any:
        try:
            return factory()
        except Exception:  # noqa: BLE001 - unavailable is not the same as unsafe
            return None

    return (
        _try(HeliusProvider),
        _try(JupiterQuoteProvider),
        _try(DexScreenerProvider),
        _try(GoPlusProvider),
    )


@app.post("/candidates/evaluate")
def evaluate_candidate(body: CandidateInput) -> dict[str, object]:
    service, _, _, _, _, _ = components()
    return service.evaluate(_snapshot_from(body))


@app.post("/candidates/enrich_evaluate")
def enrich_and_evaluate_candidate(body: EnrichCandidateInput) -> dict[str, object]:
    """Resolve chain evidence first, then grade it.

    ``/candidates/evaluate`` judges only the facts the caller supplies. A caller
    with a social or cockpit feed has none of the chain facts the safety gates
    require -- mint and freeze authority, routes, price impact, holder
    concentration -- so every candidate it submits comes back rejected on
    ``*_unknown``, which reads like a safety verdict but is only an absence of
    data. This route closes that gap by running the same enrichment stage the
    collector runs before grading.

    The enrichment report is returned alongside the decision so a caller can
    tell "this token failed a gate" apart from "this provider was down", which
    the failure list alone cannot express.
    """
    service, _, _, _, _, _ = components()
    snapshot = _snapshot_from(body)
    mint_provider, quote_provider, pair_provider, security_provider = _enrichment_providers()

    snapshot, _pair = apply_pair_evidence(snapshot, body.address, pair_provider)
    snapshot, report = enrich_snapshot(
        snapshot,
        mint_provider=mint_provider,
        quote_provider=quote_provider,
        security_provider=security_provider,
        intended_order_sol=body.intended_order_sol,
    )
    # Vendor labels last, and only into fields still unresolved. No chain call
    # answers "is the deployer currently distributing", so `developer_selling`
    # can only come from here -- and only when the ranked feed happened to
    # carry this mint. A miss leaves it None and the gate keeps rejecting.
    vendor_resolved: tuple[str, ...] = ()
    vendor_coverage: dict[str, Any] | None = None
    index = _vendor_index()
    if index is not None:
        try:
            labels = index.labels_for(body.address)
            snapshot, vendor_resolved = apply_vendor_labels(snapshot, labels)
            cov = index.coverage
            vendor_coverage = {
                "hit": labels is not None,
                "rows": cov.rows,
                "with_dev_sell": cov.with_dev_sell,
                "stale": cov.stale,
                "errors": list(cov.errors),
            }
        except Exception as error:  # noqa: BLE001 - a dead feed must not pass a gate
            vendor_coverage = {"hit": False, "error": f"{type(error).__name__}: {error}"}

    result = dict(service.evaluate(snapshot))
    result["enrichment"] = {
        "resolved": list(report.resolved),
        "unresolved": list(report.unresolved),
        "errors": report.errors,
        "coverage_pct": report.coverage_pct,
        "vendor_resolved": list(vendor_resolved),
        "vendor": vendor_coverage,
        "providers": {
            "mint": mint_provider is not None,
            "quote": quote_provider is not None,
            "pair": pair_provider is not None,
            "security": security_provider is not None,
        },
    }
    return result


@app.get("/events")
def events(limit: int = 100, entity_id: str | None = None) -> list[dict[str, Any]]:
    _, recorder, _, _, _, _ = components()
    return recorder.list_events(limit, entity_id)


@app.post("/research/findings")
def record_research_finding(body: ResearchFindingInput) -> dict[str, str]:
    _, recorder, _, _, _, _ = components()
    finding = ResearchFinding(
        **body.model_dump(exclude={"source_urls"}),
        source_urls=tuple(body.source_urls),
    )
    try:
        event_hash = ResearchNotebook(recorder).record_finding(finding)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return {"finding_id": finding.finding_id, "event_hash": event_hash}


@app.get("/paper/positions")
def paper_positions() -> list[dict[str, object]]:
    _, _, broker, _, _, _ = components()
    return [
        asdict(position) | {"universe": position.universe.value}
        for position in broker.positions.values()
    ]


@app.post("/paper/positions")
def open_paper_position(body: PaperOpenInput) -> dict[str, object]:
    _, recorder, broker, risk_engine, _, _ = components()
    chain = "cex" if body.universe == Universe.CEX_ESTABLISHED else "solana"
    evaluation = recorder.last_event(f"{chain}:{body.asset_id}", "candidate_evaluated")
    if evaluation is None:
        raise HTTPException(409, "Candidate must be evaluated before a paper position can open")
    status = evaluation["payload"].get("status")
    if status != "eligible_for_strategy_review":
        raise HTTPException(409, f"Candidate status is {status}; paper entry blocked")
    evaluated_universe = evaluation["payload"].get("universe")
    if evaluated_universe is not None and evaluated_universe != body.universe.value:
        raise HTTPException(409, "Candidate was evaluated for a different universe")
    state = PortfolioState(
        equity_usd=body.equity_usd,
        daily_realized_pnl_usd=body.daily_realized_pnl_usd,
        open_positions=len(broker.positions),
        aggregate_open_risk_usd=body.aggregate_open_risk_usd,
        consecutive_losses=body.consecutive_losses,
        kill_switch=body.kill_switch,
    )
    decision = risk_engine.approve(body.universe, state, body.entry_price, body.stop_price)
    if not decision.approved:
        raise HTTPException(409, f"Risk gate blocked entry: {decision.reason}")
    position = broker.open_position(
        body.asset_id,
        body.universe,
        body.entry_price,
        decision.position_value_usd,
        body.stop_price,
        risk_amount_usd=decision.risk_amount_usd,
    )
    return asdict(position) | {"universe": position.universe.value}


@app.post("/strategy/cex/observe")
def observe_cex_strategy(body: CexObserveInput) -> dict[str, object]:
    _, _, broker, _, engine, _ = components()
    state = PortfolioState(
        equity_usd=body.equity_usd,
        daily_realized_pnl_usd=body.daily_realized_pnl_usd,
        open_positions=len(broker.positions),
        aggregate_open_risk_usd=body.aggregate_open_risk_usd,
        consecutive_losses=body.consecutive_losses,
        kill_switch=body.kill_switch,
    )
    convert = lambda item: Candle(**item.model_dump())
    result = engine.observe(
        body.asset_id,
        [convert(item) for item in body.candles_1h],
        [convert(item) for item in body.candles_15m],
        state,
    )
    payload = asdict(result)
    if result.position is not None:
        payload["position"]["universe"] = result.position.universe.value
    return payload


@app.post("/shadow/solana/positions")
def open_solana_shadow_position(body: ShadowOpenInput) -> dict[str, object]:
    _, recorder, broker, _, _, engine = components()
    evaluation = recorder.last_event(f"solana:{body.mint}", "candidate_evaluated")
    if evaluation is None or evaluation["payload"].get("status") != "eligible_for_strategy_review":
        raise HTTPException(409, "Exact mint must first pass candidate safety evaluation")
    state = PortfolioState(
        equity_usd=body.equity_usd,
        daily_realized_pnl_usd=body.daily_realized_pnl_usd,
        open_positions=len(broker.positions),
        aggregate_open_risk_usd=body.aggregate_open_risk_usd,
        consecutive_losses=body.consecutive_losses,
        kill_switch=body.kill_switch,
    )
    decision = engine.open(
        body.mint,
        body.quoted_price,
        body.entry_price_impact_pct,
        body.exit_route_verified,
        body.simulation_ok,
        state,
    )
    if not decision.approved:
        raise HTTPException(409, f"Shadow entry blocked: {decision.reason}")
    assert decision.position is not None
    return asdict(decision.position) | {"universe": decision.position.universe.value}


class DeskSocialInput(BaseModel):
    source: SocialSource = SocialSource.REDDIT
    author: str
    text: str
    published_at: datetime | None = None
    engagement: int | None = None
    account_age_days: float | None = None


class DeskEvaluateInput(CandidateInput):
    social_events: list[DeskSocialInput] = Field(default_factory=list)
    equity_usd: float | None = Field(default=None, gt=0)
    execute_paper_open: bool = False
    autonomy_level: AutonomyLevel | None = None
    proposed_position_usd: float | None = Field(default=None, gt=0)


@app.get("/desk", response_class=HTMLResponse)
def desk_ui() -> HTMLResponse:
    """Serve the self-contained Multi-Agent Research Desk UI."""
    return HTMLResponse(content=render_desk_html())


@app.get("/api/desk/state")
def desk_state() -> dict[str, Any]:
    """Return live research desk state, packets, pareto, and paper ledger."""
    _, recorder, broker, _, _, _ = components()
    settings = load_settings()
    return build_desk_state(recorder, broker, settings)


@app.get("/api/desk/diary", response_class=PlainTextResponse)
def desk_diary() -> PlainTextResponse:
    """Render the Markdown Desk Diary from the hash-chained journal."""
    _, recorder, _, _, _, _ = components()
    return PlainTextResponse(content=generate_desk_diary(recorder), media_type="text/markdown")


class DeskDecisionInput(BaseModel):
    packet_event_hash: str
    decision: str


@app.post("/api/desk/decision")
def desk_decision(body: DeskDecisionInput) -> dict[str, Any]:
    """Single-tap paper-only approval/rejection for an L2_HUMAN_APPROVAL_DRAFT."""
    if body.decision not in {"approve", "reject"}:
        raise HTTPException(422, "decision must be 'approve' or 'reject'")
    _, recorder, broker, _, _, _ = components()
    packets = recorder.events_by_type("research_desk_packet")
    target = next((p for p in packets if p["event_hash"] == body.packet_event_hash), None)
    if target is None:
        raise HTTPException(404, "Research packet not found")
    payload = target["payload"]
    draft = payload.get("human_approval_draft")
    if payload.get("final_action") != "HUMAN_APPROVAL_DRAFT" or draft is None:
        raise HTTPException(409, "Packet is not an active HUMAN_APPROVAL_DRAFT")

    entity_id = payload["entity_id"]
    position_id: str | None = None
    if body.decision == "approve":
        snap_ev = recorder.last_event(entity_id, "snapshot_observed")
        price_usd = (
            float(snap_ev["payload"]["price_usd"])
            if snap_ev and snap_ev["payload"].get("price_usd")
            else None
        )
        if price_usd is None or price_usd <= 0:
            raise HTTPException(409, "Snapshot entry price missing; fail-closed")
        pos_usd = float(draft["proposed_position_usd"])
        cost_pct = draft.get("estimated_round_trip_cost_pct")
        fees = round(pos_usd * float(cost_pct) / 100.0, 6) if cost_pct is not None else 0.0
        universe = Universe(payload["investigation"]["universe"])
        pos = broker.open_position(
            asset_id=payload["investigation"]["mint"],
            universe=universe,
            entry_price=price_usd,
            position_value_usd=pos_usd,
            risk_amount_usd=float(payload["risk"]["risk_amount_usd"]),
            fees_paid_usd=fees,
        )
        position_id = pos.position_id

    ev_hash = recorder.append(
        "human_draft_decision",
        entity_id,
        {
            "packet_event_hash": body.packet_event_hash,
            "decision": body.decision,
            "paper_position_id": position_id,
            "execution_mode": "paper",
        },
    )
    return {
        "decision": body.decision,
        "paper_position_id": position_id,
        "event_hash": ev_hash,
        "execution_mode": "paper",
    }


@app.post("/api/desk/evaluate")
def desk_evaluate(body: DeskEvaluateInput) -> dict[str, Any]:
    """Run the 3-agent paper research desk on a candidate + social chatter."""
    service, recorder, broker, _, _, _ = components()
    settings = load_settings()
    snapshot = _snapshot_from(body)
    risk = RiskEngine(settings.risk, micro=settings.micro)
    coordinator = ResearchTeamCoordinator(
        recorder=recorder,
        safety_engine=service.safety,
        risk_engine=risk,
        broker=broker,
        cost_model=settings.costs,
    )
    events = [
        SocialEvent(
            source=item.source,
            author=item.author,
            text=item.text,
            observed_at=body.observed_at,
            published_at=item.published_at,
            mint=body.address,
            engagement=item.engagement,
            author_credibility=author_credibility(
                posts_observed=1,
                account_age_days=item.account_age_days,
                median_engagement=float(item.engagement)
                if item.engagement is not None
                else None,
                spam_rate=spam_probability(item.text),
            ),
            spam_probability=spam_probability(item.text),
        )
        for item in body.social_events
    ]
    open_risk = sum(
        p.risk_amount_usd if p.risk_amount_usd > 0 else p.entry_price * p.quantity
        for p in broker.positions.values()
    )
    state = PortfolioState(
        equity_usd=body.equity_usd or settings.starting_equity_usd,
        open_positions=len(broker.positions),
        aggregate_open_risk_usd=open_risk,
    )
    packet = coordinator.evaluate_candidate(
        snapshot,
        events,
        state,
        as_of=body.observed_at,
        proposed_position_usd=body.proposed_position_usd,
        execute_paper_open=body.execute_paper_open,
        autonomy_level=body.autonomy_level,
    )
    return packet.as_dict()
