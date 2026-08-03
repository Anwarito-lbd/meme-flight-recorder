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
class ClusterLimits:
    """Thresholds for coordinated-wallet detection.

    Defaults are deliberately strict. The published base rates for this market
    are bad enough that a permissive default would pass most of what it sees.
    """

    # Vendor-label tier.
    maximum_insider_pct: float = 15.0
    maximum_sniper_pct: float = 15.0
    maximum_bundler_pct: float = 5.0
    maximum_fresh_wallet_pct: float = 20.0
    maximum_dev_sell_pct: float = 0.0
    maximum_dev_prior_launches: int = 2
    minimum_vendor_confidence: float = 0.6

    # Independent funding-graph tier.
    maximum_linked_cluster_pct: float = 10.0
    maximum_single_funder_fresh_wallet_pct: float = 30.0
    minimum_holder_coverage_pct: float = 80.0
    minimum_graph_confidence: float = 0.5
    reject_on_launch_bundle: bool = True
    require_bundle_evidence: bool = False


@dataclass(frozen=True)
class MicroCapitalLimits:
    """Sizing rules for accounts too small for percentage-of-equity risk.

    At $40 of equity the standard 0.25% Solana rule yields a $0.10 position,
    which is not a trade. Below ``equity_threshold_usd`` the engine switches to
    catastrophic-loss sizing: the position *is* the risk, because a meme coin
    can reach zero or become unsellable regardless of any chart stop.

    The liquidity rule is expressed as a share of the pool rather than an
    absolute floor, because an absolute floor silently encodes an account size.
    ``minimum_pool_liquidity_usd`` is the backstop that stops the ratio from
    walking a tiny order into a pool nobody can exit.
    """

    equity_threshold_usd: float = 100.0
    max_position_usd: float = 10.0
    minimum_viable_position_usd: float = 3.0
    maximum_round_trip_cost_pct: float = 8.0
    maximum_pool_share_pct: float = 0.5
    minimum_pool_liquidity_usd: float = 5_000.0
    # Under catastrophic-loss sizing risk equals position, so the standard
    # aggregate-risk cap (1% of equity) would reject every trade it sizes. The
    # meaningful limit at this scale is how much of the account may sit in open
    # positions at once.
    maximum_aggregate_open_position_pct: float = 50.0


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
    clusters: ClusterLimits = ClusterLimits()
    micro: MicroCapitalLimits = MicroCapitalLimits()

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
        # [risk.micro] parses as a nested table inside [risk], so it must be
        # split out before the flat RiskLimits fields are expanded.
        risk=RiskLimits(**{k: v for k, v in data["risk"].items() if k != "micro"}),
        # Absent section keeps the strict dataclass defaults rather than
        # disabling the gates, so an older config file cannot silently opt out
        # of cluster detection.
        clusters=ClusterLimits(**data["safety"].get("clusters", {})),
        micro=MicroCapitalLimits(**data["risk"].get("micro", {})),
    )
