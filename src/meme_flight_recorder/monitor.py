"""Opening and managing paper positions, forward, on live data.

This is the piece that has never existed. The journal holds thousands of
observations and zero positions, so every expectancy figure in this project is
backward-looking, drawn from tokens sampled after their outcomes were known. No
filter can be trusted on that basis, which is why the deep-pool result -- the
first to survive removal of its own best trades -- is still not wired into
anything. Forward paper trades are the only way to change that.

**The entry rule here is structural, not chart-based, and that is deliberate.**
Measured over 1,694 outcomes, filtering on pool depth beat the tradeable
baseline both before and after deleting its three largest winners, at 3.6x on
the tail-adjusted number. The breakout entry it replaces lost money under every
exit policy tested. So the rule is: pool deep enough, gates passed, enter, hold.

**Holding is the policy, not an omission.** Positions open with no stop and no
breakout level, which disables the stop and thesis exits in `evaluate_exit` --
both are guarded on `> 0`. What remains active is what the measurement assumed:
security exits, liquidity exits, the stale-position exit, and a time limit. A
stop would contradict the thesis being tested. It would also be theatre: a
replayed collapse fell 1,700x below its stop inside one candle, so only position
size protects the account, and it already does.

**It cannot trade.** No key, no signing, no broadcast. It writes journal rows.
`tests/test_no_live_execution.py` stays green and unmodified.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .config import ExitLimits, MicroCapitalLimits, Settings
from .costs import round_trip_cost
from .exits import (
    PositionObservation,
    PositionView,
    evaluate_exit,
    realistic_fill_price,
)
from .journal import FlightRecorder
from .models import CandidateStatus, Universe
from .position_store import open_positions, save_closed, save_opened
from .positions import apply_exit, mark, open_position
from .risk import PortfolioState, RiskEngine


@dataclass(frozen=True)
class MonitorConfig:
    """Rules for the forward paper book."""

    # The filter that survived its own tail test. Deliberately higher than the
    # risk engine's $5,000 floor: at $5,000 the same study showed +0.072 per
    # dollar after removing the top three winners, against +0.264 here.
    minimum_pool_liquidity_usd: float = 50_000.0

    # One position per mint, and a ceiling on how many run at once. Ten $4
    # positions is $40, the whole account, so this is also the aggregate
    # exposure limit expressed in the only unit that matters at this size.
    maximum_open_positions: int = 10

    # Far longer than the exit engine's 1,440-minute default. The thesis under
    # test is tail capture, and a one-day limit would close positions before the
    # tail it is trying to measure had a chance to arrive.
    maximum_hold_minutes: int = 10_080  # seven days

    # A position whose price cannot be read for this many consecutive cycles is
    # closed rather than held. Everywhere else missing evidence blocks a new
    # position; for money already at risk the rule inverts, because an asset
    # that cannot be observed is a hope rather than a position.
    stale_after_failed_checks: int = 3


@dataclass(frozen=True)
class MonitorSummary:
    considered: int = 0
    opened: int = 0
    closed: int = 0
    still_open: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


class PositionMonitor:
    """Marks open paper positions, exits them, and opens new ones."""

    def __init__(
        self,
        settings: Settings,
        recorder: FlightRecorder,
        pair_provider: Any,
        config: MonitorConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.recorder = recorder
        self.pair_provider = pair_provider
        self.config = config or MonitorConfig()
        self.clock = clock
        self.risk = RiskEngine(settings.risk, settings.micro)
        # Consecutive failed observations per mint. Held in memory rather than
        # journalled: after a restart a position starts with a clean slate,
        # which errs toward keeping a position rather than closing one on a
        # count that predates the process.
        self._failed_checks: dict[str, int] = {}

    # ------------------------------------------------------------------ exits

    def _exit_limits(self) -> ExitLimits:
        return ExitLimits(
            maximum_hold_minutes=self.config.maximum_hold_minutes,
            stale_after_failed_checks=self.config.stale_after_failed_checks,
            # No-progress closure is disabled: "it has not moved yet" is the
            # normal state of a position waiting for a tail event, and closing
            # on it would systematically remove the outcomes being measured.
            no_progress_minutes=self.config.maximum_hold_minutes,
        )

    def _observe(self, mint: str) -> tuple[float | None, float | None]:
        try:
            pair = self.pair_provider.deepest_pair(mint)
        except Exception:  # noqa: BLE001 - a dead provider must not end the cycle
            return None, None
        if pair is None:
            return None, None
        return pair.price_usd, pair.liquidity_usd

    def manage_open(self) -> tuple[int, int, list[str]]:
        """Mark every open position and exit the ones that must be exited."""
        closed = 0
        errors: list[str] = []
        limits = self._exit_limits()
        live = open_positions(self.recorder)

        for mint, position in live.items():
            price, liquidity = self._observe(mint)
            if price is None:
                self._failed_checks[mint] = self._failed_checks.get(mint, 0) + 1
            else:
                self._failed_checks[mint] = 0

            moment = self.clock()
            marked = mark(position, price=price or position.high_water_price, liquidity_usd=liquidity)
            view = PositionView(
                mint=mint,
                opened_at=marked.opened_at,
                entry_price=marked.entry_price,
                stop_price=marked.stop_price,
                breakout_level=marked.breakout_level,
                entry_liquidity_usd=marked.entry_liquidity_usd,
                atr=marked.atr,
                high_water_price=marked.high_water_price,
                scaled_out_fraction=marked.scaled_out_fraction,
                peak_liquidity_usd=marked.peak_liquidity_usd,
            )
            decision = evaluate_exit(
                view,
                PositionObservation(
                    observed_at=moment,
                    price_usd=price,
                    liquidity_usd=liquidity,
                    consecutive_failed_checks=self._failed_checks.get(mint, 0),
                ),
                limits,
            )
            if not decision.should_exit:
                continue

            # An unobservable position has no price to sell at. Recording the
            # last seen price would invent a fill; the honest floor is zero,
            # matching how sizing already assumes total loss.
            fill_reference = price if price is not None else 0.0
            fill = realistic_fill_price(
                fill_reference,
                self.settings.costs.assumed_impact_pct,
                urgent=decision.urgent,
            )
            leg_cost_pct = (
                round_trip_cost(
                    max(marked.entry_price * marked.quantity, 1e-9), self.settings.costs
                ).pct_of_position
                / 2.0
            )
            try:
                exited = apply_exit(
                    marked,
                    at=moment,
                    price=fill,
                    fraction=decision.fraction,
                    reason=decision.reason,
                    cost_pct=leg_cost_pct,
                )
            except Exception as error:  # noqa: BLE001
                errors.append(f"{mint}: {type(error).__name__}: {error}")
                continue

            if not exited.is_open:
                save_closed(self.recorder, exited)
                self._failed_checks.pop(mint, None)
                closed += 1

        return len(live), closed, errors

    # ----------------------------------------------------------------- entries

    def consider(self, candidates: list[dict[str, Any]]) -> tuple[int, dict[str, int], list[str]]:
        """Open positions on candidates that pass the structural filter."""
        opened = 0
        skipped: dict[str, int] = {}
        errors: list[str] = []
        live = open_positions(self.recorder)
        micro: MicroCapitalLimits = self.settings.micro

        def skip(reason: str) -> None:
            skipped[reason] = skipped.get(reason, 0) + 1

        for candidate in candidates:
            mint = str(candidate.get("mint") or "")
            if not mint:
                skip("no_mint")
                continue
            if mint in live:
                skip("already_open")
                continue
            # `live` already grows as positions open below, so counting
            # `opened` again here would halve the effective cap.
            if len(live) >= self.config.maximum_open_positions:
                skip("book_full")
                continue
            if candidate.get("status") != CandidateStatus.ELIGIBLE_FOR_STRATEGY_REVIEW.value:
                skip("gates_not_passed")
                continue

            liquidity = float(candidate.get("liquidity_usd") or 0.0)
            if liquidity < self.config.minimum_pool_liquidity_usd:
                skip("pool_too_shallow")
                continue

            price = float(candidate.get("price_usd") or 0.0)
            if price <= 0:
                skip("no_price")
                continue

            approval = self.risk.approve(
                Universe.SOLANA_EMERGING,
                PortfolioState(equity_usd=self.settings.starting_equity_usd),
                entry_price=price,
                pool_liquidity_usd=liquidity,
                estimated_round_trip_cost_pct=round_trip_cost(
                    self.settings.starting_equity_usd * micro.position_pct_of_equity / 100.0,
                    self.settings.costs,
                ).pct_of_position,
            )
            if not approval.approved:
                skip(f"risk:{approval.reason}")
                continue

            leg_cost_pct = (
                round_trip_cost(approval.position_value_usd, self.settings.costs).pct_of_position
                / 2.0
            )
            try:
                # stop_price and breakout_level are zero on purpose: both exits
                # are guarded on `> 0`, so this is a hold, which is the policy
                # the deep-pool result was measured under.
                position = open_position(
                    mint=mint,
                    symbol=str(candidate.get("symbol") or ""),
                    at=self.clock(),
                    price=price,
                    position_usd=approval.position_value_usd,
                    stop_price=0.0,
                    target_price=0.0,
                    breakout_level=0.0,
                    liquidity_usd=liquidity,
                    cost_pct=leg_cost_pct,
                    metadata={
                        "entry_rule": "structural_deep_pool",
                        "minimum_pool_liquidity_usd": self.config.minimum_pool_liquidity_usd,
                        "observed_liquidity_usd": liquidity,
                    },
                )
            except Exception as error:  # noqa: BLE001
                errors.append(f"{mint}: {type(error).__name__}: {error}")
                continue

            save_opened(self.recorder, position)
            live[mint] = position
            opened += 1

        return opened, skipped, errors

    # ------------------------------------------------------------------ cycle

    def run_cycle(self, candidates: list[dict[str, Any]] | None = None) -> MonitorSummary:
        """Manage the book, then consider new entries.

        Exits are evaluated before entries so that a failing discovery feed can
        never prevent a held position from being closed. That ordering is the
        whole reason the monitor runs at the top of the collector cycle.
        """
        _live, closed, exit_errors = self.manage_open()
        opened, skipped, entry_errors = self.consider(candidates or [])
        return MonitorSummary(
            considered=len(candidates or []),
            opened=opened,
            closed=closed,
            still_open=len(open_positions(self.recorder)),
            skipped=skipped,
            errors=tuple(exit_errors + entry_errors),
        )
