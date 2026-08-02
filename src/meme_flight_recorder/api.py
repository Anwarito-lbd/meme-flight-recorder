from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .cex_engine import CexPaperEngine
from .config import load_settings
from .journal import FlightRecorder
from .models import TokenIdentity, TokenSnapshot, Universe
from .notebook import (
    EvidenceConfidence,
    HypothesisStatus,
    ResearchFinding,
    ResearchNotebook,
)
from .paper import PaperBroker
from .risk import PortfolioState, RiskEngine
from .safety import SafetyEngine
from .service import CandidateService
from .shadow import SolanaShadowEngine
from .strategy import Candle


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


@app.post("/candidates/evaluate")
def evaluate_candidate(body: CandidateInput) -> dict[str, object]:
    service, _, _, _, _, _ = components()
    known = set(TokenSnapshot.__dataclass_fields__) - {
        "identity",
        "universe",
        "observed_at",
        "provider_observed_at",
    }
    unknown = set(body.facts) - known
    if unknown:
        raise HTTPException(422, f"Unknown facts: {sorted(unknown)}")
    snapshot = TokenSnapshot(
        identity=TokenIdentity(
            body.chain, body.address, body.symbol, body.name, verified=body.verified
        ),
        universe=body.universe,
        observed_at=body.observed_at,
        provider_observed_at=body.provider_observed_at,
        **body.facts,
    )
    return service.evaluate(snapshot)


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
