from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "select_kraken_pairs.py"
WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "kraken-research.yml"
SPEC = importlib.util.spec_from_file_location("select_kraken_pairs", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_select_pairs_is_fail_closed_for_missing_inactive_and_non_spot_markets() -> None:
    markets = {
        "DOGE/USD": {
            "active": True,
            "spot": True,
            "limits": {"amount": {"min": 10}, "cost": {"min": 1}},
        },
        "SHIB/USD": {"active": False, "spot": True},
        "PEPE/USD": {"active": True, "spot": False},
    }
    selected, evidence = MODULE.select_active_spot_pairs(
        markets, ("DOGE", "SHIB", "PEPE", "BONK"), "USD"
    )
    assert selected == ["DOGE/USD"]
    assert evidence["DOGE"]["cost_min"] == 1
    assert evidence["SHIB"]["available"] is False
    assert evidence["PEPE"]["available"] is False
    assert evidence["BONK"]["reason"] == "not_listed"


def test_generated_config_is_always_credentialless_spot_dry_run() -> None:
    template = {
        "dry_run": False,
        "trading_mode": "futures",
        "stake_currency": "USDT",
        "exchange": {"name": "other", "key": "secret", "secret": "secret"},
    }
    config = MODULE.build_config(template, ["DOGE/USD"], "USD", 10)
    assert config["dry_run"] is True
    assert config["dry_run_wallet"] == 10
    assert config["trading_mode"] == "spot"
    assert config["stake_currency"] == "USD"
    assert config["exchange"]["pair_whitelist"] == ["DOGE/USD"]
    assert config["exchange"]["key"] == ""
    assert config["exchange"]["secret"] == ""


def test_market_order_analysis_uses_compatible_pricing_without_changing_template() -> None:
    template = {
        "entry_pricing": {"price_side": "same"},
        "exit_pricing": {"price_side": "same"},
        "exchange": {},
    }
    config = MODULE.build_config(template, ["DOGE/USD"], "USD", 10_000, True)
    assert config["entry_pricing"]["price_side"] == "other"
    assert config["exit_pricing"]["price_side"] == "other"
    assert template["entry_pricing"]["price_side"] == "same"


def test_kraken_workflow_propagates_pipeline_failures_and_uses_project_userdir() -> None:
    workflow = WORKFLOW.read_text()
    assert "shell: bash --noprofile --norc -eo pipefail {0}" in workflow
    assert workflow.count("--userdir integrations/freqtrade") == 4
    assert "REQUESTED_DAYS: ${{ inputs.days }}" in workflow
    assert workflow.count('--timerange "$FT_TIMERANGE"') == 2
    assert '--config "$FT_LOOKAHEAD_CONFIG"' in workflow
    assert "--market-order-pricing" in workflow
