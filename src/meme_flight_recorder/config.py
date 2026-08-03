from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .env import load_env


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

    # Developer distribution, as a band rather than a flag.
    #
    # This was 0.0, meaning any nonzero value rejected. Measured across 871
    # journalled mints with resolved outcomes, that gate ran backwards on the
    # only thing a risk gate is for. Tokens whose developer had sold >=50% of
    # supply died 7% of the time; tokens with no developer selling at all died
    # 28%. The effect held inside every lifecycle stage (new 1% vs 19%,
    # finalizing 11% vs 32%, migrated 18% vs 35%) and held coverage-matched, so
    # it is neither a liveness proxy nor an artifact of the vendor staying quiet
    # about dead tokens.
    #
    # Mechanism, which is what licenses the change rather than the correlation:
    # a developer who has already exited holds no supply left to sell. The
    # overhang is spent. A token whose developer has *not* sold still has that
    # supply pointed at it, and no stop survives the moment it arrives.
    #
    # A fifth of the old rejections were also dust -- one candidate was rejected
    # on 1.47e-08 percent of supply, which is floating-point noise rather than
    # distribution. The observed values are bimodal with almost nothing between
    # 0.01% and 5%, so 1.0 separates noise from real selling without landing in
    # a populated region.
    #
    # What now rejects is the middle: a developer actively distributing while
    # still holding. Recorded honestly, that band had only 4 measured outcomes,
    # so the new gate's active range is the part we know least about. It is
    # justified by mechanism, not by its own measurement, and should be revisited
    # once the collector has journalled more of it.
    maximum_dev_sell_pct: float = 1.0
    exhausted_dev_sell_pct: float = 50.0

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
class CostModel:
    """What a round trip actually costs, split into fixed and proportional parts.

    Every backtest in this project charged a flat 3% per leg. That number was
    never derived from anything, and a flat percentage is structurally wrong at
    this account's size: network fees do not shrink with the order. On a $4
    position a fixed fee is a rounding detail; on a $0.50 position it can be a
    tenth of the stake. Since the whole question is whether a smaller position
    makes the account survivable, the term that decides it cannot be hidden
    inside a percentage.

    Defaults are conservative and documented rather than measured, because they
    depend on network conditions at the moment of the trade. ``sol_price_usd``
    must be supplied by the caller for anything load-bearing -- a stale price
    silently rescales every fixed cost.
    """

    # Solana's base signature fee, fixed by the protocol at 5,000 lamports.
    base_fee_sol: float = 0.000005
    # Priority fee. This is the number that separates a patient buyer from a
    # sniper: competitive launch sniping bids this up by orders of magnitude,
    # which is one reason a small account cannot win that race.
    priority_fee_sol: float = 0.0001
    # Router and pool fee taken from the swap itself.
    dex_fee_pct: float = 0.25
    # Overridden by a live quote wherever one is available.
    assumed_impact_pct: float = 0.5
    sol_price_usd: float = 150.0

    @property
    def fixed_cost_per_leg_usd(self) -> float:
        return (self.base_fee_sol + self.priority_fee_sol) * self.sol_price_usd


@dataclass(frozen=True)
class CohortLimits:
    """What a wallet must demonstrate before it counts as worth following.

    These mirror the evidence gate this system applies to its own strategy, and
    for the same reason. A leaderboard cannot distinguish a trader from someone
    who bought one token that went up, and the difference is the entire value of
    the signal. Every threshold here exists to stop one lottery winner being
    labelled smart money.
    """

    # Below this, no classification as a repeatable trader is permitted at all,
    # regardless of how good the returns look.
    minimum_observable_trades: int = 30
    # A record carried by one trade is a record of one trade. The same one-third
    # rule gates this system's own live-execution decision.
    maximum_single_trade_profit_share: float = 0.3333
    # Trading one token is a position, not a strategy.
    minimum_distinct_tokens: int = 5
    minimum_profit_factor: float = 1.2
    # Round trips completing in seconds are sniping or arbitrage infrastructure,
    # not a strategy a human on a five-minute polling loop can follow.
    sniper_median_hold_seconds: float = 60.0
    # A wallet whose buys and sells net to nothing across many transactions is
    # manufacturing volume rather than taking positions.
    wash_trade_net_tolerance_pct: float = 1.0
    minimum_wash_trade_count: int = 20


@dataclass(frozen=True)
class DeployerLimits:
    """Thresholds for judging who created a token.

    This is a *risk* gate and must be measured as one. The project's own data
    already showed that adverse-developer tokens outperform: the single biggest
    winner it ever recorded was rejected for `developer_selling`. A dev actively
    selling is a dev actively promoting. That does not make the gate wrong -- it
    means the gate buys survival, not return, and judging it on return would
    argue for removing the one thing standing between this account and a
    deployer who has done this before.
    """

    # A wallet with almost no visible history cannot be cleared. It can only be
    # unresolved -- the manual is explicit that a fresh deployer is unresolved
    # risk rather than evidence of innocence.
    minimum_transactions_to_clear: int = 20
    minimum_wallet_age_hours: float = 168.0

    # Repeat launching is the strongest available adverse signal, because it is
    # the behaviour of someone running a production line rather than a project.
    maximum_prior_launches: int = 2
    # Adding liquidity to a token and later removing it is the mechanical shape
    # of a rug, independent of intent.
    maximum_prior_liquidity_removals: int = 0
    # Developer distribution, as a band. Mirrors ClusterLimits so the same
    # question cannot get two different answers depending on which module asked
    # it -- see the measurement recorded there.
    maximum_dev_sell_pct: float = 1.0
    exhausted_dev_sell_pct: float = 50.0


@dataclass(frozen=True)
class FlowLimits:
    """Thresholds for deciding whether demand is organic.

    The manual's primary organic-flow metric is unique-buyer acceleration, and
    it is **not available here at any price this account can pay**. The free
    pool APIs publish transaction *counts*; one wallet can generate a hundred
    buys, so a count is not a buyer. Rather than rename a weak signal into a
    strong one, unique-buyer metrics stay unknown and the verdict is built from
    what can actually be observed: holder growth, liquidity persistence, and
    manipulation tells.

    That makes ORGANIC a weaker claim than the manual intends, which is the
    honest position. It is recorded as such rather than presented as the full
    test.
    """

    # Two observations are the minimum that can express a change at all.
    minimum_observations: int = 2

    # Required for ORGANIC. Holders must genuinely expand and the pool must not
    # be draining while they do.
    minimum_holder_growth_pct: float = 2.0
    maximum_liquidity_decline_pct: float = 5.0

    # A veto, not a requirement: when buy share is known and sellers dominate,
    # demand has turned over regardless of what the holder count says. When it
    # is unknown it does not block, because the two required signals above still
    # have to be present on their own.
    minimum_buy_share_pct: float = 45.0

    # Manipulation tells. Each is sufficient on its own for SUSPICIOUS.
    wash_volume_spike_multiple: float = 5.0
    wash_flat_holder_growth_pct: float = 0.5
    wash_flat_price_move_pct: float = 5.0
    # Turnover of several times pool depth inside five minutes is not trading a
    # pool this size can support organically.
    wash_volume_to_liquidity_5m: float = 3.0


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
    # Position size tracks account funding rather than sitting at a fixed
    # dollar cap. A flat $10 cap is a quarter of a $40 account but half of a
    # $20 one, so the same configuration means very different risk at
    # different funding levels. The percentage binds on small accounts and the
    # dollar cap binds as funding grows, until the tier threshold hands over
    # to percentage-of-equity sizing entirely.
    #
    # Set from the measured loss distribution, not from preference. Replaying a
    # real collapse showed a token falling from $0.0522 to $0.0000303 inside a
    # single five-minute candle, straight through a stop placed 10% below entry.
    # No exit rule survives a gap like that, so every position must be assumed
    # recoverable at zero. Observed total-loss rate was 2 of 9. At 25% of equity
    # per position that implies ruin within roughly twenty trades; at 10% the
    # account survives the same loss rate long enough for an edge to show.
    position_pct_of_equity: float = 10.0
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
class ExitLimits:
    """When to leave a position, in priority order.

    Entry is a decision made once with full attention. Exit is a decision that
    must survive being made badly, at the wrong hour, on incomplete data. So
    every threshold here is absolute and pre-committed rather than judged in the
    moment.
    """

    # Security: the pool is being dismantled underneath the position.
    liquidity_drop_pct: float = 15.0
    liquidity_drop_window_minutes: int = 10

    # Liquidity: the exit itself has become expensive.
    minimum_pool_liquidity_usd: float = 5_000.0
    maximum_exit_impact_pct: float = 3.0

    # Flow: demand has turned over.
    minimum_buy_share_pct: float = 40.0

    # Time: the move that justified entry never arrived.
    maximum_hold_minutes: int = 1_440
    no_progress_minutes: int = 240
    no_progress_threshold_pct: float = 2.0

    # Profit: scale out against the risk taken, not against hope.
    first_scale_r: float = 1.0
    first_scale_fraction: float = 0.34
    second_scale_r: float = 2.0
    second_scale_fraction: float = 0.33
    trail_atr_multiple: float = 1.5

    # Stale: the position can no longer be observed at all.
    stale_after_failed_checks: int = 3

    # Round-trip cost, used to locate true breakeven.
    #
    # A backtest produced four exits labelled "profit_trail_stop" that all lost
    # money, because the trail only required price to be above entry while entry
    # and exit together cost about 6%. An exit above entry but below
    # cost-adjusted breakeven is a loss wearing a nice name, and at this account
    # size costs are the dominant term rather than a rounding detail.
    round_trip_cost_pct: float = 6.0


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
    flow: FlowLimits = FlowLimits()
    deployer: DeployerLimits = DeployerLimits()
    cohort: CohortLimits = CohortLimits()
    costs: CostModel = CostModel()

    def __post_init__(self) -> None:
        if self.execution_mode != "paper":
            raise ValueError("Live execution is not supported; MFR_EXECUTION_MODE must be 'paper'")
        if self.starting_equity_usd <= 0:
            raise ValueError("starting_equity_usd must be positive")


def _safety(values: dict[str, Any]) -> SafetyLimits:
    return SafetyLimits(**values)


def load_settings(path: str | Path | None = None) -> Settings:
    # Every entry point reaches settings before it reaches a provider, so this
    # is the one place that guarantees .env is applied before any os.getenv.
    load_env()
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
        flow=FlowLimits(**data["safety"].get("flow", {})),
        deployer=DeployerLimits(**data["safety"].get("deployer", {})),
        cohort=CohortLimits(**data["safety"].get("cohort", {})),
        costs=CostModel(**data.get("costs", {})),
    )
