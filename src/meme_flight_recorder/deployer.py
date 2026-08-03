"""Stage 3: who created this token, and what have they done before?

A token is a contract plus a person. The contract can be inspected directly and
already is, by ``safety.py`` and ``transferability.py``. The person shows up
only in wallet history: how many tokens they have launched, whether they have
added liquidity and then taken it back, who funded them, and whether they are
selling into the buyers they attracted.

**This is a risk gate and it must never be judged on return.** That distinction
was paid for here. Across 402 recorded outcomes, cluster-disqualified tokens
outperformed cluster-clear ones, and the largest single winner the system ever
saw was rejected for `developer_selling`. A developer actively selling is a
developer actively promoting, so adverse-deployer tokens *do* pump. The gate is
not there to catch the winners. It is there so that the account is still solvent
when one arrives.

Two rules protect this module from a failure it would otherwise be prone to.

**A wallet with no history is UNRESOLVED, never CLEAN.** Absence of evidence for
a brand-new deployer is the expected state, not a clean record, and mapping it
to CLEAN would invert the gate silently: the newest and least accountable
deployers would score best. The manual is explicit on this point and it is
enforced in code rather than in a comment.

**Truncated history is disclosed, not smoothed over.** The wallet endpoint
paginates and routinely reports that more records exist than were fetched. Wallet
age derived from a truncated page is a *lower bound* on activity and an upper
bound on age, and any verdict that depends on having seen the whole history is
downgraded to UNRESOLVED rather than asserted from a fragment.

This module is pure. It assesses evidence that a caller has already fetched, so
it can be tested without a network and cannot itself spend API budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .clusters import DEFAULT_INFRASTRUCTURE_ADDRESSES
from .config import DeployerLimits

# Enhanced-transaction type strings that indicate token creation. The endpoint's
# vocabulary varies by source program, so matching is done on substrings and the
# set is treated as incomplete -- a miss produces a lower launch count, which
# makes the gate more permissive, which is why a low count alone never clears a
# wallet.
CREATION_TYPES: frozenset[str] = frozenset({"CREATE", "TOKEN_MINT", "INITIALIZE_MINT"})
LIQUIDITY_ADD_TYPES: frozenset[str] = frozenset({"ADD_LIQUIDITY", "DEPOSIT"})
LIQUIDITY_REMOVE_TYPES: frozenset[str] = frozenset({"REMOVE_LIQUIDITY", "WITHDRAW"})


class DeployerVerdict(StrEnum):
    CLEAN = "clean"
    UNRESOLVED = "unresolved"
    ADVERSE = "adverse"


@dataclass(frozen=True)
class DeployerEvidence:
    """Everything observed about a deployer wallet, before judgement.

    Separated from the verdict so the journal keeps the facts even when the
    thresholds later change. A stored verdict cannot be re-derived; stored
    evidence can.
    """

    address: str
    transactions_observed: int = 0
    history_truncated: bool = True
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    prior_launches: int = 0
    prior_mints: tuple[str, ...] = ()
    liquidity_adds: int = 0
    liquidity_removals: int = 0
    funded_by: str | None = None
    outbound_destinations: tuple[str, ...] = ()
    # Vendor-reported, when the discovery feed supplies it. Kept separate from
    # the chain-derived fields above so the two sources never blur together.
    vendor_dev_sell_pct: float | None = None
    vendor_dev_holding_pct: float | None = None
    vendor_prior_migrations: int | None = None
    vendor_wash_trading: bool | None = None

    @property
    def wallet_age_hours(self) -> float | None:
        """Age from the earliest transaction actually seen.

        When history is truncated this is a *lower* bound -- the wallet is at
        least this old and probably older. It is therefore safe to use for
        rejecting a young wallet and unsafe for clearing an old one, which is
        how ``assess_deployer`` uses it.
        """
        if self.first_seen is None:
            return None
        return (datetime.now(UTC) - self.first_seen).total_seconds() / 3600


@dataclass(frozen=True)
class DeployerAssessment:
    """Graded finding about a token's creator."""

    verdict: DeployerVerdict
    evidence: DeployerEvidence
    failures: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocks_entry(self) -> bool:
        """Only positive adverse findings block.

        UNRESOLVED does not block on its own. The safety engine is already
        fail-closed on the evidence it owns, and making an unfetchable wallet
        history a second independent rejection would reject the entire feed on
        an API budget limit rather than on a fact about the token.
        """
        return self.verdict is DeployerVerdict.ADVERSE

    def developer_selling(self, limits: DeployerLimits | None = None) -> bool | None:
        """Feeds ``TokenSnapshot.developer_selling``, which ``exits.py`` already
        consumes as an urgent security exit. None means unknown, never False.

        This uses the same band as the cluster gate rather than ``> 0``. A flat
        nonzero test here would reintroduce through this door the exact defect
        that was just removed from the other one: rejecting on dust, and
        treating a developer who has fully exited -- the safest measured state --
        as though they were mid-distribution.
        """
        limits = limits or DeployerLimits()
        sold = self.evidence.vendor_dev_sell_pct
        if sold is None:
            return None
        return limits.maximum_dev_sell_pct < sold < limits.exhausted_dev_sell_pct


def developer_is_distributing(
    dev_sell_pct: float | None, limits: DeployerLimits | None = None
) -> bool | None:
    """Is the developer *currently* selling into buyers, as opposed to done?

    The single source of truth for that question. It previously had three
    independent implementations -- the cluster gate, the deployer assessment and
    the discovery feed's snapshot mapping -- all written as ``> 0``, and fixing
    two of them left the third quietly rejecting on the same defect.

    ``> 0`` is wrong in both directions. It fires on dust (one candidate was
    rejected on 1.47e-08 percent of supply), and it fires on a developer who has
    already sold everything -- the state that measured *safest* across 871 mints,
    at 7% dead against 28% for tokens where the developer still held.

    None means unknown and must never collapse to False: ``exits.py`` treats
    False as safe and would hold through an unmeasured risk.
    """
    limits = limits or DeployerLimits()
    if dev_sell_pct is None:
        return None
    return limits.maximum_dev_sell_pct < dev_sell_pct < limits.exhausted_dev_sell_pct


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    seconds = _as_float(value)
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _matches(transaction_type: str, vocabulary: frozenset[str]) -> bool:
    upper = transaction_type.upper()
    return any(term in upper for term in vocabulary)


def extract_evidence(
    address: str,
    transactions: list[dict[str, Any]],
    *,
    history_truncated: bool = True,
    vendor_labels: Any | None = None,
) -> DeployerEvidence:
    """Reduce fetched wallet history to the fields the verdict needs.

    ``transactions`` are enhanced-transaction dicts, newest first as the
    provider returns them. ``history_truncated`` must be the provider's own
    ``has_more`` flag: assuming a complete history when the caller only fetched
    one page is how a serial deployer reads as a first-timer.
    """
    launches: list[str] = []
    liquidity_adds = 0
    liquidity_removals = 0
    timestamps: list[datetime] = []
    destinations: list[str] = []
    inbound: list[tuple[datetime, str]] = []

    for transaction in transactions:
        moment = _timestamp(transaction.get("timestamp"))
        if moment is not None:
            timestamps.append(moment)

        kind = str(transaction.get("type") or "")
        if _matches(kind, CREATION_TYPES):
            for transfer in transaction.get("tokenTransfers") or []:
                mint = str(transfer.get("mint") or "")
                if mint and mint not in launches:
                    launches.append(mint)
        if _matches(kind, LIQUIDITY_ADD_TYPES):
            liquidity_adds += 1
        if _matches(kind, LIQUIDITY_REMOVE_TYPES):
            liquidity_removals += 1

        for transfer in transaction.get("nativeTransfers") or []:
            source = str(transfer.get("fromUserAccount") or "")
            target = str(transfer.get("toUserAccount") or "")
            if source == address and target and target not in DEFAULT_INFRASTRUCTURE_ADDRESSES:
                destinations.append(target)
            if (
                target == address
                and source
                and source not in DEFAULT_INFRASTRUCTURE_ADDRESSES
                and moment is not None
            ):
                inbound.append((moment, source))

    # The earliest inbound native transfer is the wallet's funding source. With
    # a truncated history this may not be the true first funder, which is why
    # the verdict never clears a wallet on funding evidence alone.
    funder = min(inbound, key=lambda item: item[0])[1] if inbound else None

    labels = vendor_labels
    return DeployerEvidence(
        address=address,
        transactions_observed=len(transactions),
        history_truncated=history_truncated,
        first_seen=min(timestamps) if timestamps else None,
        last_seen=max(timestamps) if timestamps else None,
        prior_launches=len(launches),
        prior_mints=tuple(launches),
        liquidity_adds=liquidity_adds,
        liquidity_removals=liquidity_removals,
        funded_by=funder,
        outbound_destinations=tuple(dict.fromkeys(destinations)),
        vendor_dev_sell_pct=_as_float(getattr(labels, "dev_sell_pct", None)),
        vendor_dev_holding_pct=_as_float(getattr(labels, "dev_pct", None)),
        vendor_prior_migrations=(
            int(value)
            if (value := _as_float(getattr(labels, "dev_migrate_count", None))) is not None
            else None
        ),
        vendor_wash_trading=getattr(labels, "dev_wash_trading", None),
    )


def assess_deployer(
    evidence: DeployerEvidence,
    limits: DeployerLimits | None = None,
    *,
    current_mint: str | None = None,
) -> DeployerAssessment:
    """Grade a deployer from observed evidence.

    ``current_mint`` is excluded from the prior-launch count when supplied --
    creating the token under analysis is not a prior launch, and counting it
    would make every first-time deployer look like a repeat offender.
    """
    limits = limits or DeployerLimits()
    failures: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []

    prior_launches = evidence.prior_launches
    if current_mint and current_mint in evidence.prior_mints:
        prior_launches -= 1

    # Chain-derived adverse signals.
    if prior_launches > limits.maximum_prior_launches:
        failures.append("deployer_serial_launcher")
    if evidence.liquidity_removals > limits.maximum_prior_liquidity_removals:
        failures.append("deployer_removed_liquidity_before")

    # Vendor-derived adverse signals, kept under their own names so a future
    # reader can tell which source produced a rejection.
    if (
        evidence.vendor_dev_sell_pct is not None
        and limits.maximum_dev_sell_pct
        < evidence.vendor_dev_sell_pct
        < limits.exhausted_dev_sell_pct
    ):
        failures.append("vendor_developer_distribution")
    if (
        evidence.vendor_prior_migrations is not None
        and evidence.vendor_prior_migrations > limits.maximum_prior_launches
    ):
        failures.append("vendor_deployer_serial_launcher")
    if evidence.vendor_wash_trading is True:
        failures.append("vendor_developer_wash_trading")

    if failures:
        return DeployerAssessment(
            verdict=DeployerVerdict.ADVERSE,
            evidence=evidence,
            failures=tuple(failures),
            warnings=tuple(warnings),
            missing=tuple(missing),
        )

    # Nothing adverse found. Whether that is a clean record or simply an
    # unexamined one depends entirely on how much was actually seen.
    if evidence.transactions_observed == 0:
        missing.append("wallet_history")
        return DeployerAssessment(
            verdict=DeployerVerdict.UNRESOLVED,
            evidence=evidence,
            missing=tuple(missing),
        )

    if evidence.transactions_observed < limits.minimum_transactions_to_clear:
        warnings.append("deployer_history_thin")
        missing.append("sufficient_history")
        return DeployerAssessment(
            verdict=DeployerVerdict.UNRESOLVED,
            evidence=evidence,
            warnings=tuple(warnings),
            missing=tuple(missing),
        )

    age_hours = evidence.wallet_age_hours
    if age_hours is None:
        missing.append("wallet_age")
        return DeployerAssessment(
            verdict=DeployerVerdict.UNRESOLVED,
            evidence=evidence,
            missing=tuple(missing),
        )
    if age_hours < limits.minimum_wallet_age_hours:
        warnings.append("deployer_wallet_recently_created")
        return DeployerAssessment(
            verdict=DeployerVerdict.UNRESOLVED,
            evidence=evidence,
            warnings=tuple(warnings),
        )

    if evidence.history_truncated:
        # Enough was seen to be reassuring, but not enough to assert a clean
        # record. Claiming CLEAN from a fragment is how a serial deployer whose
        # rugs are older than one page reads as trustworthy.
        warnings.append("deployer_history_truncated")
        missing.append("complete_history")
        return DeployerAssessment(
            verdict=DeployerVerdict.UNRESOLVED,
            evidence=evidence,
            warnings=tuple(warnings),
            missing=tuple(missing),
        )

    return DeployerAssessment(verdict=DeployerVerdict.CLEAN, evidence=evidence)
