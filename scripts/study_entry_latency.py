#!/usr/bin/env python3
"""Does `enter on sight` survive its own best trades, and any real latency?

At n=166 this band measured -$0.28. At n=647 it measured **+$1.82 with a profit
factor of 3.7**. One of those is wrong, or the edge lives entirely in a handful
of trades. This study is built to find out, and it is built to be able to say no.

Two independent ways of killing it, both applied:

**Tail dependence.** Delete the best 1, 3, 5 and 10 trades and re-measure. A
lottery survives its own median; an edge survives its own tail being removed.
This project has already withdrawn one filter on exactly this test -- deleting a
single 209.7x flipped a positive result negative at n=166.

**Latency.** The band enters at the first print after observation. Nobody fills
there. The measured cost of a real decision in this system is a 109-196ms
Jupiter quote inside a 589ms quote/build/simulate cycle, and that is before the
event is even detected on a feed carrying ~2,500 events/sec.

**What the data can and cannot resolve, stated up front.** The requested
100ms/250ms/500ms/750ms cells are **not measurable with any free source**:
Birdeye reports trade `blockUnixTime` at one-second resolution, Solana blocks are
~400ms apart, and historical trade seek (`seek_by_time`) returned 401 on this
tier. Fabricating those cells would be inventing precision the instrument does
not have. What *is* measurable, from the cached one-minute candles:

  * `open` of the first traded minute -- the first print, latency ~0. The
    unreachable best case, and the arm the +$1.82 figure came from.
  * `close` of that same minute -- up to 60s later. Our real 0.6-5s latency sits
    between these two, so open-vs-close **brackets** it.
  * `high` of that minute -- the adversarial fill, what a sniper leaves you.
  * the open of the minute at +1m, +2m, +5m, +15m -- unambiguously reachable.

If the edge survives to the +1m arm it is real and tradeable. If it dies between
`open` and `close`, it lives inside the first sixty seconds and this study
cannot say whether we could reach it -- which is itself the finding, and the
argument for the paid tier rather than for trading it.

Costs on both legs from `costs.round_trip_cost`. Chronological held-out split.
Read-only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.costs import round_trip_cost

HOLD_MINUTES = 240.0
CUTOFF = "2026-08-05"

# (label, minutes after the first observation, which OHLC field to fill at)
ARMS: tuple[tuple[str, float, str], ...] = (
    ("first print (open)", 0.0, "open"),
    ("same minute (high)", 0.0, "high"),
    ("same minute (close)", 0.0, "close"),
    ("+1m", 1.0, "open"),
    ("+2m", 2.0, "open"),
    ("+5m", 5.0, "open"),
    ("+15m", 15.0, "open"),
)

# Requested but below the resolution of the available data. Reported, not faked.
UNMEASURABLE = ("100ms", "250ms", "500ms", "750ms", "1s", "2s", "5s")

FIELD_INDEX = {"open": 1, "high": 2, "low": 3, "close": 4}


@dataclass(frozen=True)
class Trade:
    mint: str
    observed_at: str
    entry: float
    exit: float
    gross: float
    net_pnl: float
    liquidatable: bool


def first_observations(path: str) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    first: dict[str, dict[str, Any]] = {}
    for mint, observed_at, payload in connection.execute(
        "select entity_id, observed_at, payload_json from events "
        "where event_type = 'candidate_observed' order by id"
    ):
        if mint in first:
            continue
        record = json.loads(payload)
        record["observed_at"] = observed_at
        first[mint] = record
    connection.close()
    return first


def load_cache(path: str) -> tuple[dict[str, str], dict[str, list[tuple]]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    keys = {
        mint: key
        for mint, key, status in connection.execute(
            "select mint, coalesce(series_key, pair), status from fetches"
        )
        if status == "ok"
    }
    series: dict[str, list[tuple]] = defaultdict(list)
    for key, ts, open_, high, low, close in connection.execute(
        "select pair, ts, open, high, low, close from candles order by pair, ts"
    ):
        series[key].append((ts, open_, high, low, close))
    connection.close()
    return keys, dict(series)


def candle_at_or_after(candles: list[tuple], moment: int) -> tuple | None:
    for candle in candles:
        if candle[0] >= moment:
            return candle
    return None


def exit_fill(candles: list[tuple], moment: int) -> float | None:
    """First print at or after the exit. A pool that never prints again is unsellable."""
    for candle in candles:
        if candle[0] >= moment:
            price = candle[4] or candle[1]
            if price and price > 0:
                return price
    return None


def summarise(trades: list[Trade], position_usd: float, drop: int = 0) -> dict[str, Any]:
    """Stats after deleting the `drop` most profitable trades."""
    if not trades:
        return {"n": 0}
    ranked = sorted(trades, key=lambda trade: trade.net_pnl, reverse=True)
    kept = ranked[drop:]
    if not kept:
        return {"n": 0}
    pnls = [trade.net_pnl for trade in kept]
    wins = [value for value in pnls if value > 0]
    losses = [-value for value in pnls if value < 0]
    equity, peak, drawdown = 0.0, 0.0, 0.0
    # Drawdown is walked in the order the trades happened, not in ranked order.
    for trade in sorted(kept, key=lambda item: item.observed_at):
        equity += trade.net_pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return {
        "n": len(kept),
        "expectancy": statistics.fmean(pnls),
        "profit_factor": (sum(wins) / sum(losses)) if losses else float("inf"),
        "win_pct": 100.0 * len(wins) / len(kept),
        "median": statistics.median(trade.gross for trade in kept),
        "dead_pct": 100.0 * sum(1 for t in kept if not t.liquidatable) / len(kept),
        "drawdown": drawdown,
        "total": sum(pnls),
    }


def render(title: str, rows: list[tuple[str, dict[str, Any]]]) -> None:
    print(f"\n=== {title} ===")
    header = f"{'arm':<22}{'n':>6}{'expectancy':>12}{'PF':>9}{'win%':>7}{'dead%':>7}{'drawdown':>11}{'total':>10}"
    print(header)
    print("-" * len(header))
    for label, stats in rows:
        if not stats.get("n"):
            print(f"{label:<22}{0:>6}   (no trades)")
            continue
        factor = stats["profit_factor"]
        text = "inf" if factor == float("inf") else f"{factor:.3f}"
        print(
            f"{label:<22}{stats['n']:>6}{stats['expectancy']:>12.4f}{text:>9}"
            f"{stats['win_pct']:>7.1f}{stats['dead_pct']:>7.1f}"
            f"{stats['drawdown']:>11.2f}{stats['total']:>10.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", default="data/pool_history.db")
    parser.add_argument("--hold-minutes", type=float, default=HOLD_MINUTES)
    arguments = parser.parse_args()

    settings = load_settings()
    position_usd = settings.starting_equity_usd * settings.micro.position_pct_of_equity / 100.0
    cost = round_trip_cost(position_usd, settings.costs)
    cost_multiple = cost.pct_of_position / 100.0

    print(
        f"position ${position_usd:.2f}   round trip {cost.pct_of_position:.3f}%   "
        f"breakeven {cost.breakeven_multiple:.4f}x   hold {arguments.hold_minutes:.0f}m"
    )
    print(
        "\nNOT MEASURABLE with free data, reported rather than fabricated: "
        + ", ".join(UNMEASURABLE)
    )
    print(
        "  Birdeye trade blockUnixTime is 1s; Solana blocks ~400ms; historical "
        "seek_by_time returned 401 (paid tier)."
    )
    print("  The open->close bracket of the first traded minute contains our real latency.")

    first = first_observations(str(settings.database_path))
    keys, series = load_cache(arguments.cache)

    arms: dict[str, list[Trade]] = defaultdict(list)
    buckets: dict[str, int] = defaultdict(int)

    for mint, record in first.items():
        key = keys.get(mint)
        if key is None:
            buckets["not_fetched"] += 1
            continue
        candles = series.get(key) or []
        if not candles:
            buckets["no_candles"] += 1
            continue
        observed_at = int(datetime.fromisoformat(record["observed_at"]).timestamp())
        counted = False
        for label, offset, field in ARMS:
            entry_candle = candle_at_or_after(candles, observed_at + int(offset * 60))
            if entry_candle is None:
                continue
            entry = entry_candle[FIELD_INDEX[field]] or entry_candle[1]
            if not entry or entry <= 0:
                continue
            exit_at = entry_candle[0] + int(arguments.hold_minutes * 60)
            marked = exit_fill(candles, exit_at)
            liquidatable = marked is not None
            gross = (marked / entry) if marked else 0.0
            arms[label].append(
                Trade(
                    mint=mint,
                    observed_at=record["observed_at"],
                    entry=entry,
                    exit=marked or 0.0,
                    gross=gross,
                    net_pnl=position_usd * (gross - cost_multiple) - position_usd,
                    liquidatable=liquidatable,
                )
            )
            counted = True
        buckets["entered" if counted else "no_usable_entry"] += 1

    total = sum(buckets.values())
    print(f"\n=== reconciliation === population {len(first)}, buckets sum {total}")
    for name, count in sorted(buckets.items(), key=lambda item: -item[1]):
        print(f"  {name:<20}{count:>7}")
    if total != len(first):
        raise AssertionError("buckets must reconcile to the population")

    def split(trades: list[Trade], held_out: bool) -> list[Trade]:
        return [
            trade
            for trade in trades
            if (trade.observed_at[:10] >= CUTOFF) == held_out
        ]

    for drop in (0, 1, 3, 5, 10):
        label = "all trades" if drop == 0 else f"top-{drop} removed"
        render(
            f"ALL DATA, {label}",
            [(name, summarise(arms[name], position_usd, drop)) for name, _o, _f in ARMS],
        )

    for drop in (0, 1, 3, 5, 10):
        label = "all trades" if drop == 0 else f"top-{drop} removed"
        render(
            f"HELD OUT (from {CUTOFF}), {label}",
            [
                (name, summarise(split(arms[name], True), position_usd, drop))
                for name, _o, _f in ARMS
            ],
        )

    # The headline the whole study exists to produce.
    print("\n" + "=" * 78)
    held = summarise(split(arms["same minute (close)"], True), position_usd, 5)
    dev = summarise(split(arms["same minute (close)"], False), position_usd, 5)
    print("HEADLINE -- enter_on_sight, realistic fill (same-minute close), top-5 removed")
    for name, stats in (("development", dev), ("HELD OUT", held)):
        if not stats.get("n"):
            print(f"  {name}: no trades")
            continue
        factor = stats["profit_factor"]
        text = "inf" if factor == float("inf") else f"{factor:.3f}"
        print(
            f"  {name:<12} N={stats['n']}  expectancy=${stats['expectancy']:.4f}  "
            f"PF={text}  drawdown=${stats['drawdown']:.2f}  win={stats['win_pct']:.1f}%"
        )
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
