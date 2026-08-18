"""Decide what the mandate says about one candidate, and journal the answer.

The mandate this implements is an LLM prompt: three strictness levels, a
confluence minimum, an age cap, a volume floor, a no-re-entry rule and a
first-minute scam-pump filter. The prompt asks an agent to weigh those and emit a
signal.

**This module is what the prompt asks for, computed instead of judged.** That is
the one structural change made to the operator's design, and the reason is in
`CLAUDE.md`: gates are computed, not judged. An LLM counting confluence signals
produces a number, but not a *reproducible* one -- run it twice on the same
evidence and it may count differently, and there is then no way to tell whether a
verdict changed because the market moved or because the model did. The agent's
job is to rank and explain what this module decided; it cannot open a position.

Three properties hold by construction:

  * **Anything other than an explicit pass blocks entry.** `UNKNOWN` from a gate
    and `UNKNOWN` from a confluence signal both count against the candidate,
    never for it.
  * **Every decision is journalled, rejections included**, with the resolved
    parameter set attached. A journal that records verdicts and discards the
    evidence behind them cannot be re-examined, which is exactly why the
    concentration study had to start its sample from scratch.
  * **No level can open a position until its `readiness_policy` is widened.**
    That tuple ships empty, so the default outcome of a passing candidate is
    WATCH rather than ENTER.

`evaluate` is pure: it takes evidence a caller fetched and returns a decision, so
it tests without a network and cannot spend API budget. The I/O lives in
`run_scout`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .config import ScoutFilters, StrictnessLevel
from .confluence import ConfluenceEvidence, ConfluenceResult, SignalState, score
from .journal import FlightRecorder
from .scout_gates import (
    GateOutcome,
    GateVerdict,
    blocking,
    check_first_candle_not_vertical,
    check_market_cap_band,
    check_no_reentry,
    check_token_age,
)


class ScoutVerdict(StrEnum):
    ENTER = "enter"
    WATCH = "watch"
    REJECT = "reject"


@dataclass(frozen=True)
class CandidateEvidence:
    """Everything the scout needs about one candidate, as fetched by the caller.

    Every field is optional and defaults to None, so a caller that could not
    resolve something produces UNKNOWN rather than a flattering default.
    """

    mint: str
    symbol: str = ""
    age_hours: float | None = None
    market_cap_usd: float | None = None
    first_candle_open: float | None = None
    first_candle_high: float | None = None
    first_candle_volume: float | None = None
    confluence: ConfluenceEvidence = field(default_factory=ConfluenceEvidence)
    lifecycle_state: str | None = None
    # Carried through to the journal so a whale verdict can never be read without
    # knowing how much of the wallet's activity was actually visible. Measured
    # coverage runs 0.17% to 12.07% of a wallet's transactions.
    wallet_coverage_pct: float | None = None
    # Number of news articles found. Zero is a measurement; None means the
    # provider was not reachable. They are not the same and must not merge.
    news_article_count: int | None = None


@dataclass(frozen=True)
class ScoutDecision:
    mint: str
    symbol: str
    verdict: ScoutVerdict
    gates: tuple[GateVerdict, ...]
    confluence: ConfluenceResult
    level_name: str
    reasons: tuple[str, ...]

    @property
    def blocking_gates(self) -> tuple[GateVerdict, ...]:
        return blocking(self.gates)

    def to_payload(
        self, filters: ScoutFilters, level: StrictnessLevel, evidence: CandidateEvidence
    ) -> dict[str, Any]:
        """The journal payload: verdict, evidence, and the policy that produced it.

        The parameter set is embedded rather than referenced. A config file can be
        edited between runs, so a decision that points at "the config" is not
        reproducible; one that carries its thresholds is.
        """
        return {
            "verdict": self.verdict.value,
            "level": self.level_name,
            "reasons": list(self.reasons),
            "confluence": {
                "present": self.confluence.present_count,
                "absent": self.confluence.absent_count,
                "unknown": self.confluence.unknown_count,
                "required": level.minimum_confluence_signals,
                "present_signals": [s.value for s in self.confluence.by_state(SignalState.PRESENT)],
                "unknown_signals": [s.value for s in self.confluence.by_state(SignalState.UNKNOWN)],
            },
            "gates": [
                {
                    "gate": g.gate,
                    "outcome": g.outcome.value,
                    "reason": g.reason,
                    "observed": g.observed,
                    "threshold": g.threshold,
                }
                for g in self.gates
            ],
            "evidence": {
                "age_hours": evidence.age_hours,
                "market_cap_usd": evidence.market_cap_usd,
                # Journalled alongside pool depth, never instead of it: market
                # capitalisation is not exit liquidity.
                "liquidity_usd": evidence.confluence.liquidity_usd,
                "volume_usd": evidence.confluence.volume_usd,
                "spread_pct": evidence.confluence.spread_pct,
                "lifecycle_state": evidence.lifecycle_state,
                "wallet_coverage_pct": evidence.wallet_coverage_pct,
                "news_article_count": evidence.news_article_count,
            },
            "parameters": {
                "filters": vars(filters),
                "level": {
                    "minimum_confluence_signals": level.minimum_confluence_signals,
                    "minimum_volume_expansion": level.minimum_volume_expansion,
                    "maximum_trades_per_session": level.maximum_trades_per_session,
                    "consecutive_loss_stop": level.consecutive_loss_stop,
                    "risk_pct_of_equity": level.risk_pct_of_equity,
                    "readiness_policy": list(level.readiness_policy),
                },
            },
        }


def evaluate(
    evidence: CandidateEvidence,
    filters: ScoutFilters,
    level: StrictnessLevel,
    level_name: str,
    previously_held_mints: frozenset[str] | None,
) -> ScoutDecision:
    """Apply the mandate to one candidate. Pure.

    Order matters and mirrors the mandate: "Token age must be verified before any
    further analysis. If token age exceeds 48 hours: Reject immediately. Do not
    analyze." So the hard gates run first and a rejection short-circuits, which
    also means the scout does not spend confluence evidence on a token it has
    already declined.
    """
    gates = (
        check_token_age(evidence.age_hours, filters.maximum_token_age_hours),
        check_no_reentry(
            evidence.mint, previously_held_mints, enabled=filters.forbid_reentry
        ),
        check_first_candle_not_vertical(
            evidence.first_candle_open,
            evidence.first_candle_high,
            evidence.first_candle_volume,
            filters.maximum_first_candle_multiple,
        ),
        check_market_cap_band(
            evidence.market_cap_usd,
            filters.minimum_market_cap_usd,
            filters.maximum_market_cap_usd,
        ),
    )

    confluence = score(
        evidence.confluence,
        minimum_liquidity_usd=filters.minimum_liquidity_usd,
        maximum_spread_pct=filters.maximum_spread_pct,
        minimum_volume_usd=filters.minimum_volume_usd,
        # These four come from the safety config rather than the questionnaire, so
        # the scout cannot loosen a gate the rest of the system enforces.
        maximum_concentration_pct=30.0,
        maximum_developer_sold_pct=1.0,
        minimum_bonding_progress_per_minute=0.5,
        minimum_topic_inflow_usd=1_000.0,
        minimum_buy_share_pct=55.0,
        minimum_whale_holders=1.0,
        minimum_volume_expansion=level.minimum_volume_expansion,
    )

    reasons: list[str] = []
    blocked = blocking(gates)
    if blocked:
        for gate in blocked:
            prefix = "rejected" if gate.outcome is GateOutcome.REJECT else "unknown"
            reasons.append(f"{prefix}:{gate.gate}:{gate.reason}")
        return ScoutDecision(
            evidence.mint,
            evidence.symbol,
            ScoutVerdict.REJECT,
            gates,
            confluence,
            level_name,
            tuple(reasons),
        )

    if not confluence.meets(level.minimum_confluence_signals):
        reasons.append(
            f"confluence:{confluence.present_count}/{level.minimum_confluence_signals}"
            f" present, {confluence.unknown_count} unknown"
        )
        return ScoutDecision(
            evidence.mint,
            evidence.symbol,
            ScoutVerdict.REJECT,
            gates,
            confluence,
            level_name,
            tuple(reasons),
        )

    # The candidate has cleared every gate and the confluence bar. Whether that
    # may become a position is a separate question, and the answer defaults to no.
    if not level.admits_entry:
        reasons.append(
            f"level {level_name} admits no lifecycle state; widening readiness_policy"
            " requires a study"
        )
        return ScoutDecision(
            evidence.mint,
            evidence.symbol,
            ScoutVerdict.WATCH,
            gates,
            confluence,
            level_name,
            tuple(reasons),
        )
    if evidence.lifecycle_state not in level.readiness_policy:
        reasons.append(
            f"lifecycle {evidence.lifecycle_state!r} not admitted by {level_name}"
        )
        return ScoutDecision(
            evidence.mint,
            evidence.symbol,
            ScoutVerdict.WATCH,
            gates,
            confluence,
            level_name,
            tuple(reasons),
        )

    reasons.append(
        f"cleared {len(gates)} gates with {confluence.present_count} confluence signals"
    )
    return ScoutDecision(
        evidence.mint,
        evidence.symbol,
        ScoutVerdict.ENTER,
        gates,
        confluence,
        level_name,
        tuple(reasons),
    )


def held_mints(recorder: FlightRecorder) -> frozenset[str]:
    """Every mint this book has ever opened, by replaying the position ledger.

    Reads `paper_position_opened` and `cohort_position_opened` separately because
    they are deliberately distinct ledgers replayed into different dataclasses;
    merging their payload shapes is explicitly forbidden. For the re-entry
    question only the mint matters, so both contribute.
    """
    mints: set[str] = set()
    for event_type in ("paper_position_opened", "cohort_position_opened"):
        for event in recorder.events_by_type(event_type):
            payload = event.get("payload") or {}
            mint = payload.get("mint") or event.get("entity_id")
            if mint:
                mints.add(str(mint))
    return frozenset(mints)


def journal_decision(
    recorder: FlightRecorder,
    decision: ScoutDecision,
    filters: ScoutFilters,
    level: StrictnessLevel,
    evidence: CandidateEvidence,
    observed_at: datetime | None = None,
) -> str:
    """Append one scout decision, rejections included.

    A new event type rather than a reused one: `CLAUDE.md` forbids reusing an
    existing name with a different payload shape, and this payload carries gates,
    confluence and a parameter set that no existing event has.
    """
    return recorder.append(
        "scout_candidate_evaluated",
        decision.mint,
        decision.to_payload(filters, level, evidence),
        observed_at or datetime.now(UTC),
    )
