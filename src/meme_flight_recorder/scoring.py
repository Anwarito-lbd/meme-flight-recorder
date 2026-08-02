from __future__ import annotations

from .models import ScoreResult, TokenSnapshot

WEIGHTS = {
    "liquidity_quality": 0.25,
    "buyer_acceleration": 0.20,
    "holder_growth_quality": 0.15,
    "insider_behaviour": 0.15,
    "social_acceleration": 0.10,
    "price_structure": 0.10,
    "market_regime": 0.05,
}


def _bounded(value: float | None, low: float, high: float) -> float | None:
    if value is None:
        return None
    if high <= low:
        raise ValueError("invalid normalization range")
    return max(0.0, min(100.0, (value - low) * 100.0 / (high - low)))


def score_candidate(snapshot: TokenSnapshot) -> ScoreResult:
    liquidity_base = 50_000 if snapshot.universe.value.startswith("solana") else 1_000_000
    components: dict[str, float | None] = {
        "liquidity_quality": _bounded(snapshot.liquidity_usd, liquidity_base, liquidity_base * 10),
        "buyer_acceleration": _bounded(snapshot.buyer_growth_pct, -20, 100),
        "holder_growth_quality": _bounded(snapshot.holder_growth_pct, -10, 60),
        "insider_behaviour": (
            None
            if snapshot.developer_selling is None and snapshot.universe.value.startswith("solana")
            else (0.0 if snapshot.developer_selling or snapshot.connected_wallet_risk else 100.0)
        ),
        "social_acceleration": _bounded(snapshot.social_acceleration, 0, 100),
        "price_structure": _bounded(snapshot.price_momentum, -20, 40),
        "market_regime": _bounded(snapshot.market_regime, -1, 1),
    }
    missing = tuple(key for key, value in components.items() if value is None)
    present_weight = sum(WEIGHTS[key] for key, value in components.items() if value is not None)
    weighted = sum((components[key] or 0) * WEIGHTS[key] for key in WEIGHTS)
    score = weighted / present_weight if present_weight else 0.0
    confidence = present_weight * 100
    return ScoreResult(round(score, 2), round(confidence, 2), components, missing)
