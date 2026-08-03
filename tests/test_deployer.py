"""Tests for Stage 3 deployer analysis.

The load-bearing tests are the ones that stop the gate inverting: a wallet with
no history must never clear, and a fragment of history must never be reported as
a complete record.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from meme_flight_recorder.config import DeployerLimits
from meme_flight_recorder.deployer import (
    DeployerEvidence,
    DeployerVerdict,
    assess_deployer,
    extract_evidence,
)


def seconds_ago(hours: float) -> int:
    return int((datetime.now(UTC) - timedelta(hours=hours)).timestamp())


def established(**overrides: object) -> DeployerEvidence:
    """A wallet with enough visible history to be clearable."""
    defaults: dict[str, object] = {
        "address": "DEV111",
        "transactions_observed": 50,
        "history_truncated": False,
        "first_seen": datetime.now(UTC) - timedelta(days=30),
        "last_seen": datetime.now(UTC),
        "prior_launches": 0,
        "liquidity_removals": 0,
    }
    defaults.update(overrides)
    return DeployerEvidence(**defaults)  # type: ignore[arg-type]


def test_empty_history_is_unresolved_never_clean() -> None:
    """The single most important behaviour in this module.

    If a wallet with nothing known about it scored CLEAN, the newest and least
    accountable deployers would rank best and the gate would run backwards.
    """
    result = assess_deployer(DeployerEvidence(address="NEW111"))
    assert result.verdict is DeployerVerdict.UNRESOLVED
    assert "wallet_history" in result.missing
    assert not result.blocks_entry


def test_thin_history_is_unresolved() -> None:
    result = assess_deployer(established(transactions_observed=5))
    assert result.verdict is DeployerVerdict.UNRESOLVED
    assert "sufficient_history" in result.missing


def test_recently_created_wallet_is_unresolved() -> None:
    result = assess_deployer(established(first_seen=datetime.now(UTC) - timedelta(hours=2)))
    assert result.verdict is DeployerVerdict.UNRESOLVED
    assert "deployer_wallet_recently_created" in result.warnings


def test_truncated_history_cannot_be_clean() -> None:
    """A fragment is not a record. Older rugs may sit beyond the fetched page."""
    result = assess_deployer(established(history_truncated=True))
    assert result.verdict is DeployerVerdict.UNRESOLVED
    assert "complete_history" in result.missing


def test_established_untruncated_clean_wallet_clears() -> None:
    result = assess_deployer(established())
    assert result.verdict is DeployerVerdict.CLEAN
    assert not result.blocks_entry


def test_serial_launcher_is_adverse() -> None:
    result = assess_deployer(established(prior_launches=6))
    assert result.verdict is DeployerVerdict.ADVERSE
    assert "deployer_serial_launcher" in result.failures
    assert result.blocks_entry


def test_prior_liquidity_removal_is_adverse() -> None:
    result = assess_deployer(established(liquidity_removals=1))
    assert result.verdict is DeployerVerdict.ADVERSE
    assert "deployer_removed_liquidity_before" in result.failures


def test_current_mint_is_not_counted_as_a_prior_launch() -> None:
    """Creating the token under analysis is not a prior launch."""
    evidence = established(prior_launches=1, prior_mints=("MINT_UNDER_TEST",))
    result = assess_deployer(evidence, current_mint="MINT_UNDER_TEST")
    assert result.verdict is DeployerVerdict.CLEAN


def test_adverse_wins_over_thin_history() -> None:
    """A rejection found in a fragment is still a rejection.

    UNRESOLVED means "not enough seen to clear", never "not enough seen to
    reject" -- evidence of harm does not need corroboration to count.
    """
    result = assess_deployer(
        DeployerEvidence(address="DEV111", transactions_observed=2, liquidity_removals=3)
    )
    assert result.verdict is DeployerVerdict.ADVERSE


def test_developer_mid_distribution_is_adverse() -> None:
    """Still holding supply and actively selling into buyers: the danger band."""
    result = assess_deployer(established(vendor_dev_sell_pct=20.0))
    assert result.verdict is DeployerVerdict.ADVERSE
    assert "vendor_developer_distribution" in result.failures
    assert result.developer_selling() is True


def test_dust_developer_selling_does_not_reject() -> None:
    """The defect this band replaced.

    A candidate was rejected on a dev_sell_pct of 1.47e-08 -- floating-point
    noise being read as distribution.
    """
    result = assess_deployer(established(vendor_dev_sell_pct=1.474382279e-08))
    assert result.verdict is DeployerVerdict.CLEAN
    assert result.developer_selling() is False


def test_exhausted_developer_supply_does_not_reject() -> None:
    """A developer who has fully exited has no supply left to dump.

    Measured over 871 mints this band died 7% of the time versus 28% for
    tokens where the developer had sold nothing. Rejecting it was the gate
    running backwards on the only thing it exists to measure.
    """
    result = assess_deployer(established(vendor_dev_sell_pct=100.0))
    assert result.verdict is DeployerVerdict.CLEAN
    assert result.developer_selling() is False


def test_band_boundaries_are_exclusive_at_both_ends() -> None:
    limits = DeployerLimits()
    assert assess_deployer(
        established(vendor_dev_sell_pct=limits.maximum_dev_sell_pct)
    ).verdict is DeployerVerdict.CLEAN
    assert assess_deployer(
        established(vendor_dev_sell_pct=limits.exhausted_dev_sell_pct)
    ).verdict is DeployerVerdict.CLEAN


def test_developer_selling_is_none_when_unreported() -> None:
    """Unknown must not collapse to False; exits.py treats False as safe."""
    assert assess_deployer(established()).developer_selling() is None


def test_vendor_wash_trading_is_adverse() -> None:
    result = assess_deployer(established(vendor_wash_trading=True))
    assert "vendor_developer_wash_trading" in result.failures


def test_vendor_migration_count_is_adverse() -> None:
    result = assess_deployer(established(vendor_prior_migrations=9))
    assert "vendor_deployer_serial_launcher" in result.failures


def test_extract_counts_launches_and_liquidity_events() -> None:
    transactions = [
        {
            "type": "CREATE",
            "timestamp": seconds_ago(100),
            "tokenTransfers": [{"mint": "MINT_A"}],
        },
        {
            "type": "TOKEN_MINT",
            "timestamp": seconds_ago(200),
            "tokenTransfers": [{"mint": "MINT_B"}],
        },
        {"type": "ADD_LIQUIDITY", "timestamp": seconds_ago(150)},
        {"type": "REMOVE_LIQUIDITY", "timestamp": seconds_ago(50)},
    ]
    evidence = extract_evidence("DEV111", transactions, history_truncated=False)
    assert evidence.prior_launches == 2
    assert set(evidence.prior_mints) == {"MINT_A", "MINT_B"}
    assert evidence.liquidity_adds == 1
    assert evidence.liquidity_removals == 1
    assert evidence.transactions_observed == 4


def test_extract_finds_earliest_funder_not_latest() -> None:
    transactions = [
        {
            "type": "TRANSFER",
            "timestamp": seconds_ago(1),
            "nativeTransfers": [{"fromUserAccount": "RECENT", "toUserAccount": "DEV111"}],
        },
        {
            "type": "TRANSFER",
            "timestamp": seconds_ago(500),
            "nativeTransfers": [{"fromUserAccount": "ORIGINAL", "toUserAccount": "DEV111"}],
        },
    ]
    evidence = extract_evidence("DEV111", transactions, history_truncated=False)
    assert evidence.funded_by == "ORIGINAL"


def test_extract_excludes_infrastructure_from_funding_and_destinations() -> None:
    system_program = "11111111111111111111111111111111"
    transactions = [
        {
            "type": "TRANSFER",
            "timestamp": seconds_ago(10),
            "nativeTransfers": [
                {"fromUserAccount": system_program, "toUserAccount": "DEV111"},
                {"fromUserAccount": "DEV111", "toUserAccount": system_program},
            ],
        }
    ]
    evidence = extract_evidence("DEV111", transactions, history_truncated=False)
    assert evidence.funded_by is None
    assert evidence.outbound_destinations == ()


def test_extract_defaults_to_truncated() -> None:
    """The safe default: assume more history exists until told otherwise."""
    assert extract_evidence("DEV111", []).history_truncated is True


def test_wallet_age_is_none_without_timestamps() -> None:
    evidence = extract_evidence("DEV111", [{"type": "TRANSFER"}], history_truncated=False)
    assert evidence.wallet_age_hours is None
    assert assess_deployer(evidence).verdict is DeployerVerdict.UNRESOLVED


def test_limits_are_configurable() -> None:
    permissive = DeployerLimits(maximum_prior_launches=10)
    assert assess_deployer(established(prior_launches=6), permissive).verdict is (
        DeployerVerdict.CLEAN
    )


def test_all_paths_agree_on_whether_a_developer_is_distributing() -> None:
    """The regression that matters: three doors, one answer.

    This question had three independent implementations -- the cluster gate, the
    deployer assessment and the discovery feed's snapshot mapping -- each written
    as `dev_sell_pct > 0`. Two were fixed and the third kept rejecting live
    candidates on the old rule for a full session. Anything that answers this
    question must route through `developer_is_distributing`.
    """
    from meme_flight_recorder.clusters import assess_vendor_labels
    from meme_flight_recorder.config import ClusterLimits
    from meme_flight_recorder.deployer import developer_is_distributing
    from meme_flight_recorder.providers.binance_web3 import MemeRushRow, VendorClusterLabels

    cluster_limits = ClusterLimits()
    cases = {
        "dust": 1.474382279e-08,
        "mid_distribution": 20.0,
        "exhausted": 100.0,
        "zero": 0.0,
    }
    for label, value in cases.items():
        expected = developer_is_distributing(value)

        row = MemeRushRow(
            chain_id="CT_501",
            contract_address="MINT",
            symbol="X",
            name="X",
            created_at=None,
            migrated_at=None,
            price_usd=1.0,
            market_cap_usd=None,
            liquidity_usd=1.0,
            volume_usd=None,
            holders=None,
            buy_count=None,
            sell_count=None,
            progress_pct=None,
            migrated=True,
            labels=VendorClusterLabels(dev_sell_pct=value),
        )
        assert row.to_snapshot().developer_selling is expected, label

        rejected = "vendor_developer_distribution" in assess_vendor_labels(
            VendorClusterLabels(dev_sell_pct=value), cluster_limits
        ).failures
        assert rejected is expected, label
