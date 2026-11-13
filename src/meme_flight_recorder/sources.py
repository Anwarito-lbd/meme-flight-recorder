"""Timestamped calls from external sources, and what happened after them.

The question this exists to answer is not "what is being called" but "which
sources are worth following". Those are different questions, and only the second
one survives contact with costs.

A call is recorded as a ``SourceCall``: who said it, when, and about which exact
mint. The mint matters more than the ticker, because tickers are duplicated
deliberately to capture search traffic for a trending name.

Import paths, chosen for what each source actually permits:

* **Manual CSV** for Padre, Fomo and X. None exposes a usable public API. Padre
  and Fomo are behind a login, so collecting them automatically would mean
  defeating an access control. X discontinued its free tier and now bills per
  post read, which at this account's size costs more per month than the account
  itself. Pasting the calls you care about keeps the timestamps honest and the
  cost at zero, and sampling is sufficient because the goal is to characterise a
  source rather than to catch every call.
* **Public JSON APIs** for Reddit and YouTube, which permit polling.

Expectancy is computed against the collector's journal rather than a price API,
so a source is only credited with returns on tokens the pipeline actually saw
and graded. That coupling is intentional: it prevents a source from scoring well
on a token that could never have been bought.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

# Base58, 32-44 chars: the shape of a Solana mint. Deliberately strict, because
# a loose pattern matches ticker text and turns noise into false calls.
SOLANA_MINT = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

DEFAULT_HORIZONS: tuple[timedelta, ...] = (
    timedelta(seconds=30),
    timedelta(minutes=5),
    timedelta(minutes=60),
    timedelta(hours=24),
)


class SourceKind(StrEnum):
    X = "x"
    REDDIT = "reddit"
    YOUTUBE = "youtube"
    PADRE = "padre"
    FOMO = "fomo"
    TELEGRAM = "telegram"
    OTHER = "other"


class SourceRole(StrEnum):
    """What a source's calls have historically been worth after costs."""

    ORIGINATOR = "originator"
    AMPLIFIER = "amplifier"
    DISTRIBUTOR = "distributor"
    UNPROVEN = "unproven"


@dataclass(frozen=True)
class SourceCall:
    """One attributable, timestamped call about one exact mint."""

    source: SourceKind
    author: str
    mint: str
    called_at: datetime
    url: str = ""
    text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        return f"{self.source.value}:{self.author}"


def extract_mints(text: str) -> tuple[str, ...]:
    """Pull candidate mint addresses out of free text.

    Matching on the address rather than the ticker is the whole point: a
    trending name spawns dozens of impersonating tokens within minutes, and only
    the address distinguishes them.
    """
    return tuple(dict.fromkeys(SOLANA_MINT.findall(text or "")))


def _parse_timestamp(value: str) -> datetime:
    text = (value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def load_calls_csv(path: str | Path, default_source: SourceKind | None = None) -> list[SourceCall]:
    """Read manually exported calls.

    Required columns: ``author`` and ``called_at``. Either ``mint`` or a
    ``text`` column containing an address must be present; a row naming only a
    ticker is skipped rather than guessed at, because resolving a ticker to an
    address is precisely the step that gets people into the wrong token.
    """
    calls: list[SourceCall] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            cleaned = {(key or "").strip().lower(): (value or "").strip()
                       for key, value in row.items()}
            mint = cleaned.get("mint") or ""
            if not mint:
                found = extract_mints(cleaned.get("text", ""))
                mint = found[0] if found else ""
            if not mint or not cleaned.get("called_at"):
                continue
            raw_source = cleaned.get("source", "")
            try:
                source = SourceKind(raw_source) if raw_source else (
                    default_source or SourceKind.OTHER
                )
            except ValueError:
                source = default_source or SourceKind.OTHER
            calls.append(
                SourceCall(
                    source=source,
                    author=cleaned.get("author", "unknown"),
                    mint=mint,
                    called_at=_parse_timestamp(cleaned["called_at"]),
                    url=cleaned.get("url", ""),
                    text=cleaned.get("text", ""),
                    raw=dict(row),
                )
            )
    return calls


@dataclass(frozen=True)
class Observation:
    """A point-in-time record the collector wrote for one mint."""

    mint: str
    observed_at: datetime
    price_usd: float | None
    liquidity_usd: float | None
    status: str = ""
    exit_price_impact_pct: float | None = None


def observations_from_events(events: Iterable[dict[str, Any]]) -> dict[str, list[Observation]]:
    """Reshape journal events into per-mint time series, oldest first."""
    series: dict[str, list[Observation]] = {}
    for event in events:
        payload = event.get("payload") or {}
        mint = event.get("entity_id") or ""
        stamp = payload.get("observed_at")
        if not mint or not stamp:
            continue
        try:
            observed_at = _parse_timestamp(stamp)
        except ValueError:
            continue
        series.setdefault(mint, []).append(
            Observation(
                mint=mint,
                observed_at=observed_at,
                price_usd=payload.get("price_usd"),
                liquidity_usd=payload.get("liquidity_usd"),
                status=payload.get("status", ""),
                exit_price_impact_pct=payload.get("exit_price_impact_pct"),
            )
        )
    for records in series.values():
        records.sort(key=lambda record: record.observed_at)
    return series


@dataclass(frozen=True)
class CallOutcome:
    call: SourceCall
    entry_price: float | None = None
    returns_pct: dict[str, float] = field(default_factory=dict)
    net_returns_pct: dict[str, float] = field(default_factory=dict)
    unresolved: tuple[str, ...] = ()

    @property
    def measurable(self) -> bool:
        return bool(self.net_returns_pct)


def _nearest_at_or_after(
    records: Sequence[Observation], moment: datetime, tolerance: timedelta
) -> Observation | None:
    best: Observation | None = None
    for record in records:
        if record.observed_at < moment:
            continue
        if record.observed_at - moment > tolerance:
            break
        best = record
        break
    return best


def score_call(
    call: SourceCall,
    records: Sequence[Observation],
    horizons: Sequence[timedelta] = DEFAULT_HORIZONS,
    round_trip_cost_pct: float = 6.0,
    tolerance: timedelta = timedelta(minutes=10),
) -> CallOutcome:
    """Measure what a follower would have made, after costs.

    The entry is the first observation at or after the call, never before it.
    Using a pre-call price would credit the source with a move its followers
    could not have captured, which is the single easiest way to manufacture a
    flattering track record.

    ``round_trip_cost_pct`` is subtracted from every horizon. At this account's
    size costs are the dominant term, and a gross return figure would be
    actively misleading.
    """
    unresolved: list[str] = []
    entry = _nearest_at_or_after(records, call.called_at, tolerance)
    if entry is None or not entry.price_usd:
        return CallOutcome(call, None, {}, {}, ("entry_not_observed",))

    returns: dict[str, float] = {}
    net: dict[str, float] = {}
    for horizon in horizons:
        label = _label(horizon)
        later = _nearest_at_or_after(records, call.called_at + horizon, tolerance)
        if later is None or not later.price_usd:
            unresolved.append(label)
            continue
        gross = 100.0 * (later.price_usd - entry.price_usd) / entry.price_usd
        returns[label] = round(gross, 4)
        net[label] = round(gross - round_trip_cost_pct, 4)

    return CallOutcome(call, entry.price_usd, returns, net, tuple(unresolved))


def _label(horizon: timedelta) -> str:
    seconds = int(horizon.total_seconds())
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


@dataclass(frozen=True)
class SourceScore:
    identity: str
    calls: int
    measurable: int
    role: SourceRole
    mean_net_pct: dict[str, float] = field(default_factory=dict)
    win_rate_pct: dict[str, float] = field(default_factory=dict)
    rejected_by_gates: int = 0
    notes: tuple[str, ...] = ()

    @property
    def coverage_pct(self) -> float:
        return round(100.0 * self.measurable / self.calls, 1) if self.calls else 0.0


def score_source(
    identity: str,
    outcomes: Sequence[CallOutcome],
    records_by_mint: dict[str, list[Observation]],
    minimum_sample: int = 10,
    horizon: str = "1h",
) -> SourceScore:
    """Summarise one source, refusing to classify on too small a sample.

    A source is called an originator only once enough measurable calls exist to
    mean anything. Below that it is UNPROVEN however good the numbers look: with
    three calls, one lucky token produces a spectacular average, and the whole
    point of this module is to not be fooled by that.
    """
    measurable = [outcome for outcome in outcomes if outcome.measurable]
    notes: list[str] = []

    rejected = sum(
        1
        for outcome in outcomes
        for record in records_by_mint.get(outcome.call.mint, [])[:1]
        if record.status == "reject"
    )

    mean_net: dict[str, float] = {}
    win_rate: dict[str, float] = {}
    labels = {label for outcome in measurable for label in outcome.net_returns_pct}
    for label in sorted(labels):
        values = [
            outcome.net_returns_pct[label]
            for outcome in measurable
            if label in outcome.net_returns_pct
        ]
        if not values:
            continue
        mean_net[label] = round(sum(values) / len(values), 4)
        win_rate[label] = round(100.0 * sum(1 for v in values if v > 0) / len(values), 1)

    if len(measurable) < minimum_sample:
        notes.append(f"sample_below_minimum({len(measurable)}<{minimum_sample})")
        role = SourceRole.UNPROVEN
    else:
        headline = mean_net.get(horizon)
        if headline is None:
            role = SourceRole.UNPROVEN
            notes.append(f"no_data_at_{horizon}")
        elif headline > 0:
            role = SourceRole.ORIGINATOR
        elif headline > -5:
            # Roughly break-even after costs: the call is real but late, so
            # followers pay the spread without capturing the move.
            role = SourceRole.AMPLIFIER
        else:
            # Reliably negative after costs. Whether or not it is deliberate,
            # following this source has cost money, and that is what matters.
            role = SourceRole.DISTRIBUTOR

    if rejected:
        notes.append(f"gate_rejected_calls={rejected}")

    return SourceScore(
        identity=identity,
        calls=len(outcomes),
        measurable=len(measurable),
        role=role,
        mean_net_pct=mean_net,
        win_rate_pct=win_rate,
        rejected_by_gates=rejected,
        notes=tuple(notes),
    )


def score_all(
    calls: Iterable[SourceCall],
    events: Iterable[dict[str, Any]],
    minimum_sample: int = 10,
    horizon: str = "1h",
    round_trip_cost_pct: float = 6.0,
) -> list[SourceScore]:
    """Score every source present in ``calls``, worst first."""
    records_by_mint = observations_from_events(events)
    grouped: dict[str, list[CallOutcome]] = {}
    for call in calls:
        outcome = score_call(
            call,
            records_by_mint.get(call.mint, []),
            round_trip_cost_pct=round_trip_cost_pct,
        )
        grouped.setdefault(call.identity, []).append(outcome)

    scores = [
        score_source(identity, outcomes, records_by_mint, minimum_sample, horizon)
        for identity, outcomes in grouped.items()
    ]
    scores.sort(key=lambda score: score.mean_net_pct.get(horizon, 0.0))
    return scores
