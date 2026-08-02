from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SafetyLimits:
    minimum_age_minutes: int
    minimum_liquidity_usd: float
    maximum_entry_price_impact_pct: float
    maximum_exit_price_impact_pct: float
    maximum_top10_private_holder_pct: float


@dataclass(frozen=True)
class RiskLimits:
    risk_per_cex_trade_pct: float
    capital_at_risk_per_solana_trade_pct: float
    maximum_daily_loss_pct: float
    maximum_open_positions: int
    maximum_aggregate_open_risk_pct: float
    consecutive_loss_limit: int


@dataclass(frozen=True)
class Settings:
    execution_mode: str
    database_path: Path
    starting_equity_usd: float
    stale_after_seconds: int
    cex_safety: SafetyLimits
    solana_safety: SafetyLimits
    risk: RiskLimits

    def __post_init__(self) -> None:
        if self.execution_mode != "paper":
            raise ValueError("Live execution is not supported; MFR_EXECUTION_MODE must be 'paper'")
        if self.starting_equity_usd <= 0:
            raise ValueError("starting_equity_usd must be positive")


def _safety(values: dict[str, Any]) -> SafetyLimits:
    return SafetyLimits(**values)


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = Path(path or os.getenv("MFR_CONFIG", "config/default.toml"))
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    system = data["system"]
    mode = os.getenv("MFR_EXECUTION_MODE", system["execution_mode"]).strip().lower()
    database_path = Path(os.getenv("MFR_DATABASE_PATH", system["database_path"]))
    equity = float(os.getenv("MFR_STARTING_EQUITY_USD", system["starting_equity_usd"]))
    return Settings(
        execution_mode=mode,
        database_path=database_path,
        starting_equity_usd=equity,
        stale_after_seconds=int(system["stale_after_seconds"]),
        cex_safety=_safety(data["safety"]["cex"]),
        solana_safety=_safety(data["safety"]["solana_emerging"]),
        risk=RiskLimits(**data["risk"]),
    )
