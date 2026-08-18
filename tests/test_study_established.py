"""Tests for the established-population study's load-bearing assumptions.

Every assertion here corresponds to a claim the study makes in a comment. This
project has already been burned by a comment that was right about the numbers
and wrong about the word, and by fixtures that encoded the same bug as the code,
so the claims that decide the result are asserted rather than reviewed.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

from meme_flight_recorder.config import CohortLimits
from meme_flight_recorder.strategy import Candle, detect_breakout

SCRIPT = Path(__file__).parents[1] / "scripts" / "study_established_population.py"
SPEC = importlib.util.spec_from_file_location("study_established_population", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
# `@dataclass` resolves its own module out of `sys.modules`, so a spec-loaded
# script must be registered there before it is executed.
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

BACKFILL = Path(__file__).parents[1] / "scripts" / "backfill_cex_history.py"
BSPEC = importlib.util.spec_from_file_location("backfill_cex_history", BACKFILL)
BMODULE = importlib.util.module_from_spec(BSPEC)
assert BSPEC and BSPEC.loader
sys.modules[BSPEC.name] = BMODULE
BSPEC.loader.exec_module(BMODULE)


def _series(count: int, *, breakout: bool) -> list[Candle]:
    """A tight consolidation, optionally followed by a high-volume breakout."""
    candles = []
    for index in range(count - 1):
        base = 100.0 + (index % 3) * 0.1
        candles.append(Candle(index * 900_000, base, base + 0.2, base - 0.2, base, 1_000.0))
    last = candles[-1]
    if breakout:
        # Close must clear the consolidation high (100.4) without exceeding it
        # by more than 2 ATR, and volume must be at least 1.5x the trailing
        # average. An earlier version of this fixture closed at 101.4, which
        # overshot the extension guard and returned None -- so the exactness
        # test below was comparing None to None and asserting nothing. That is
        # the fixture encoding the bug, which this project has paid for before.
        candles.append(Candle(count * 900_000, last.close, 100.9, last.close, 100.7, 5_000.0))
    else:
        candles.append(Candle(count * 900_000, last.close, 100.3, 99.9, 100.0, 900.0))
    return candles


class TestDetectLookbackIsExact:
    """The study truncates `detect_breakout`'s input to the trailing 22 candles.

    That is an optimisation on the hot path, and an optimisation that changes
    the answer is a defect. The comment claims exactness; these assert it.
    """

    def test_truncated_input_matches_full_series_on_a_breakout(self) -> None:
        full = _series(400, breakout=True)
        truncated = full[-MODULE.DETECT_LOOKBACK :]
        assert detect_breakout(full) == detect_breakout(truncated)
        assert detect_breakout(full) is not None

    def test_truncated_input_matches_full_series_without_a_breakout(self) -> None:
        full = _series(400, breakout=False)
        truncated = full[-MODULE.DETECT_LOOKBACK :]
        assert detect_breakout(full) == detect_breakout(truncated)
        assert detect_breakout(full) is None

    def test_lookback_satisfies_the_functions_own_guard(self) -> None:
        # 21 candles of history plus the breakout bar; the guard needs >= 22.
        assert MODULE.DETECT_LOOKBACK >= 22


class TestResolveExitIsPessimistic:
    def test_stop_wins_when_one_bar_contains_both_levels(self) -> None:
        # OHLC cannot say which level was touched first, so the loss is assumed.
        candles = [Candle(0, 100.0, 120.0, 80.0, 110.0, 1.0)]
        index, price, reason = MODULE.resolve_exit(candles, 0, 90.0, 115.0, 10)
        assert (index, price, reason) == (0, 90.0, "stop")

    def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop(self) -> None:
        candles = [Candle(0, 70.0, 75.0, 65.0, 72.0, 1.0)]
        index, price, reason = MODULE.resolve_exit(candles, 0, 90.0, 115.0, 10)
        assert (index, price, reason) == (0, 70.0, "stop_gap")

    def test_a_gap_through_the_target_fills_at_the_open(self) -> None:
        candles = [Candle(0, 130.0, 140.0, 128.0, 135.0, 1.0)]
        index, price, reason = MODULE.resolve_exit(candles, 0, 90.0, 115.0, 10)
        assert (index, price, reason) == (0, 130.0, "target_gap")

    def test_running_out_of_bars_times_out_rather_than_reporting_a_loss(self) -> None:
        # "No further bars" is missing data, not a total loss. Reporting it as a
        # loss is the exact error the maturity study committed and had to undo.
        candles = [Candle(index, 100.0, 101.0, 99.0, 100.0, 1.0) for index in range(3)]
        index, price, reason = MODULE.resolve_exit(candles, 0, 50.0, 200.0, 10)
        assert reason == "timeout"
        assert (index, price) == (2, 100.0)


class TestTrendCursorExcludesTheFormingCandle:
    def test_a_candle_is_not_usable_until_it_has_closed(self) -> None:
        trend = [Candle(index * 3_600_000, 1.0, 1.0, 1.0, 1.0, 1.0) for index in range(5)]
        # At exactly the close of the first 1h candle, one candle is complete.
        assert MODULE.advance_trend_cursor(trend, 0, 3_600_000, 3_600_000) == 1
        # One millisecond earlier, none is.
        assert MODULE.advance_trend_cursor(trend, 0, 3_599_999, 3_600_000) == 0

    def test_the_cursor_only_moves_forward(self) -> None:
        trend = [Candle(index * 3_600_000, 1.0, 1.0, 1.0, 1.0, 1.0) for index in range(5)]
        assert MODULE.advance_trend_cursor(trend, 3, 0, 3_600_000) == 3


class TestZeroVolumeCandlesAreNotPrices:
    def test_load_series_drops_them(self, tmp_path: Path) -> None:
        path = tmp_path / "cache.db"
        cache = BMODULE.open_cache(str(path))
        rows = [
            ("binance", "DOGE/USDT", "15m", 0, 1.0, 1.1, 0.9, 1.0, 500.0),
            ("binance", "DOGE/USDT", "15m", 900_000, 1.0, 1.0, 1.0, 1.0, 0.0),
            ("binance", "DOGE/USDT", "15m", 1_800_000, 1.0, 1.2, 0.8, 1.1, 250.0),
        ]
        cache.executemany(
            "insert into candles values (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        cache.commit()
        cache.close()
        series = MODULE.load_series(str(path), "binance", "USDT", "15m")
        assert [candle.timestamp for candle in series["DOGE/USDT"]] == [0, 1_800_000]


class TestFetchOutcomesAreDistinguishable:
    """A transport failure must never be storable as an absence of trading."""

    def test_a_symbol_that_returned_nothing_is_no_candles_not_error(self, tmp_path: Path) -> None:
        cache = BMODULE.open_cache(str(tmp_path / "c.db"))
        outcome = BMODULE.FetchOutcome("no_candles", None, [])
        summary = BMODULE.store(cache, "binance", "X/USDT", "15m", 0, outcome)
        assert summary["status"] == "no_candles"
        assert summary["candles"] == 0
        cache.close()

    def test_a_partial_fetch_keeps_its_candles_and_records_why_it_stopped(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "c.db"
        cache = BMODULE.open_cache(str(path))
        outcome = BMODULE.FetchOutcome("partial", "RequestTimeout: boom", [[0, 1.0, 1.0, 1.0, 1.0, 5.0]])
        summary = BMODULE.store(cache, "binance", "X/USDT", "15m", 0, outcome)
        cache.close()
        assert summary["status"] == "partial"
        assert summary["candles"] == 1
        stored = sqlite3.connect(path).execute("select status, detail from fetches").fetchone()
        assert stored[0] == "partial"
        assert "RequestTimeout" in stored[1]

    def test_zero_volume_rows_are_counted_rather_than_silently_dropped(
        self, tmp_path: Path
    ) -> None:
        cache = BMODULE.open_cache(str(tmp_path / "c.db"))
        rows = [[0, 1.0, 1.0, 1.0, 1.0, 0.0], [900_000, 1.0, 1.0, 1.0, 1.0, 7.0]]
        summary = BMODULE.store(cache, "binance", "X/USDT", "15m", 0, BMODULE.FetchOutcome("ok", None, rows))
        cache.close()
        assert summary["candles"] == 2
        assert summary["zero_volume"] == 1


class TestVerdictIsComputedNotJudged:
    def test_a_sample_below_the_trade_floor_cannot_pass(self) -> None:
        stats = {
            "n": 29,
            "expectancy_usd": 1.0,
            "profit_factor": 5.0,
            "top_trade_share": 0.1,
            "expectancy_drop_best": 0.9,
        }
        passed, failures = MODULE.verdict(stats, CohortLimits())
        assert not passed
        assert any("29" in failure for failure in failures)

    def test_an_edge_carried_by_one_trade_cannot_pass(self) -> None:
        stats = {
            "n": 50,
            "expectancy_usd": 1.0,
            "profit_factor": 5.0,
            "top_trade_share": 0.9,
            "expectancy_drop_best": -0.2,
        }
        passed, failures = MODULE.verdict(stats, CohortLimits())
        assert not passed
        assert any("top trade share" in failure for failure in failures)
        assert any("without best trade" in failure for failure in failures)

    def test_all_five_conditions_together_pass(self) -> None:
        stats = {
            "n": 30,
            "expectancy_usd": 0.01,
            "profit_factor": 1.21,
            "top_trade_share": 0.3,
            "expectancy_drop_best": 0.005,
        }
        passed, failures = MODULE.verdict(stats, CohortLimits())
        assert passed, failures

    def test_the_bar_is_the_projects_own_gate_not_a_local_constant(self) -> None:
        limits = CohortLimits()
        assert limits.minimum_observable_trades == 30
        assert limits.minimum_profit_factor == 1.2
        assert abs(limits.maximum_single_trade_profit_share - 1 / 3) < 0.001
