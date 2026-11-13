"""Tests for the scout's configuration contract.

Two properties are load-bearing and both encode the mandate's instruction that
missing values may never be assumed:

  * `ScoutFilters` has no defaults, so an incomplete block fails at load.
  * Every strictness level's `readiness_policy` is empty, so no level can open a
    position until a study widens it on evidence.
"""

from __future__ import annotations

import pytest

from meme_flight_recorder.config import ScoutFilters, StrictnessLevel, load_settings

COMPLETE = {
    "maximum_token_age_hours": 48.0,
    "minimum_volume_usd": 5_000.0,
    "minimum_liquidity_usd": 5_000.0,
    "minimum_market_cap_usd": 20_000.0,
    "maximum_market_cap_usd": 5_000_000.0,
    "maximum_spread_pct": 2.0,
    "slippage_tolerance_pct": 2.0,
    "maximum_daily_loss_usd": 10.0,
    "trade_duration": "intraday",
    "universe": "both",
    "forbid_reentry": True,
    "maximum_first_candle_multiple": 3.0,
}


class TestNoValueMayBeAssumed:
    def test_a_complete_block_loads(self) -> None:
        assert ScoutFilters(**COMPLETE).maximum_token_age_hours == 48.0

    @pytest.mark.parametrize("missing", sorted(COMPLETE))
    def test_omitting_any_single_parameter_raises(self, missing: str) -> None:
        # The mandate: "You are not permitted to assume missing values." A
        # dataclass default would be exactly such an assumption, so every one of
        # the twelve parameters is required and this is asserted per-field rather
        # than for one representative field.
        partial = {key: value for key, value in COMPLETE.items() if key != missing}
        with pytest.raises(TypeError):
            ScoutFilters(**partial)

    def test_an_inverted_market_cap_band_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="exceeds"):
            ScoutFilters(**{**COMPLETE, "minimum_market_cap_usd": 9_000_000.0})

    def test_an_unknown_universe_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="universe"):
            ScoutFilters(**{**COMPLETE, "universe": "everything"})

    def test_an_unknown_trade_duration_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="trade_duration"):
            ScoutFilters(**{**COMPLETE, "trade_duration": "swing"})

    def test_a_non_positive_daily_loss_cap_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="maximum_daily_loss_usd"):
            ScoutFilters(**{**COMPLETE, "maximum_daily_loss_usd": 0.0})


class TestStrictnessLevelsFailClosed:
    def test_an_empty_readiness_policy_admits_no_entry(self) -> None:
        level = StrictnessLevel(
            minimum_confluence_signals=4,
            minimum_volume_expansion=3.0,
            maximum_trades_per_session=2,
            consecutive_loss_stop=1,
            risk_pct_of_equity=1.0,
        )
        assert not level.admits_entry

    def test_a_widened_policy_admits_entry(self) -> None:
        level = StrictnessLevel(
            minimum_confluence_signals=4,
            minimum_volume_expansion=3.0,
            maximum_trades_per_session=2,
            consecutive_loss_stop=1,
            risk_pct_of_equity=1.0,
            readiness_policy=("GRADUATED",),
        )
        assert level.admits_entry

    def test_degenerate_levels_are_rejected(self) -> None:
        base = {
            "minimum_confluence_signals": 4,
            "minimum_volume_expansion": 3.0,
            "maximum_trades_per_session": 2,
            "consecutive_loss_stop": 1,
            "risk_pct_of_equity": 1.0,
        }
        with pytest.raises(ValueError, match="minimum_confluence_signals"):
            StrictnessLevel(**{**base, "minimum_confluence_signals": 0})
        with pytest.raises(ValueError, match="maximum_trades_per_session"):
            StrictnessLevel(**{**base, "maximum_trades_per_session": 0})
        with pytest.raises(ValueError, match="risk_pct_of_equity"):
            StrictnessLevel(**{**base, "risk_pct_of_equity": 0.0})


class TestShippedConfig:
    """The config actually shipped in config/default.toml."""

    def test_all_three_levels_are_present_and_ordered_by_strictness(self) -> None:
        settings = load_settings()
        levels = [settings.level(f"level_{n}") for n in (1, 2, 3)]
        signals = [level.minimum_confluence_signals for level in levels]
        assert signals == sorted(signals, reverse=True), signals
        caps = [level.maximum_trades_per_session for level in levels]
        assert caps == sorted(caps), caps

    def test_no_shipped_level_can_open_a_position(self) -> None:
        # The fail-closed default. Widening any readiness_policy is the single
        # visible act that turns research into trading, and it needs a study.
        settings = load_settings()
        for name in settings.strictness:
            assert not settings.level(name).admits_entry, name

    def test_the_questionnaire_is_complete_in_the_shipped_config(self) -> None:
        settings = load_settings()
        assert settings.scout is not None
        assert set(vars(settings.scout)) == set(COMPLETE)

    def test_an_unknown_level_names_what_is_available(self) -> None:
        settings = load_settings()
        with pytest.raises(KeyError, match="available"):
            settings.level("level_9")

    def test_execution_mode_remains_paper(self) -> None:
        assert load_settings().execution_mode == "paper"
