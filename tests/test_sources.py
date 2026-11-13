from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from meme_flight_recorder.sources import (
    SourceCall,
    SourceKind,
    SourceRole,
    extract_mints,
    load_calls_csv,
    observations_from_events,
    score_all,
    score_call,
)

MINT = "Dz9mQ9NzkBcCsuGPFJ3r1bS4wgqKMHBPiVuniW8Mbonk"
CALLED_AT = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)


def _event(mint: str, offset_minutes: float, price: float, status: str = "monitor"):
    observed = CALLED_AT + timedelta(minutes=offset_minutes)
    return {
        "entity_id": mint,
        "payload": {
            "observed_at": observed.isoformat(),
            "price_usd": price,
            "liquidity_usd": 20_000.0,
            "status": status,
        },
    }


def _call(author: str = "someone", mint: str = MINT) -> SourceCall:
    return SourceCall(SourceKind.X, author, mint, CALLED_AT)


class MintExtractionTests(unittest.TestCase):
    def test_extracts_an_address_from_free_text(self):
        self.assertEqual(extract_mints(f"buy {MINT} now"), (MINT,))

    def test_ignores_tickers(self):
        """Tickers are duplicated deliberately; only the address identifies."""
        self.assertEqual(extract_mints("$BONK is running"), ())

    def test_deduplicates_repeats(self):
        self.assertEqual(extract_mints(f"{MINT} {MINT}"), (MINT,))


class CsvImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.path = Path(self._temp.name) / "calls.csv"

    def _write(self, body: str) -> Path:
        self.path.write_text(body, encoding="utf-8")
        return self.path

    def test_imports_a_padre_style_export(self):
        path = self._write(
            "source,author,mint,called_at,url\n"
            f"padre,AlphaGroup,{MINT},2026-08-02T12:00:00Z,https://example.test/1\n"
        )
        calls = load_calls_csv(path)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].source, SourceKind.PADRE)
        self.assertEqual(calls[0].mint, MINT)
        self.assertEqual(calls[0].called_at, CALLED_AT)

    def test_recovers_the_mint_from_post_text(self):
        path = self._write(
            "source,author,called_at,text\n"
            f"x,trader,2026-08-02T12:00:00Z,aping {MINT} here\n"
        )
        self.assertEqual(load_calls_csv(path)[0].mint, MINT)

    def test_a_ticker_only_row_is_skipped_not_guessed(self):
        """Resolving a ticker to an address is how people buy the clone."""
        path = self._write(
            "source,author,called_at,text\nx,trader,2026-08-02T12:00:00Z,$BONK looks good\n"
        )
        self.assertEqual(load_calls_csv(path), [])

    def test_unknown_source_falls_back_without_raising(self):
        path = self._write(
            f"source,author,mint,called_at\nnewsletter,bob,{MINT},2026-08-02T12:00:00Z\n"
        )
        self.assertEqual(load_calls_csv(path)[0].source, SourceKind.OTHER)


class CallScoringTests(unittest.TestCase):
    def test_entry_is_taken_at_or_after_the_call(self):
        """A pre-call entry would credit a move followers could not capture."""
        records = observations_from_events(
            [_event(MINT, -30, 1.0), _event(MINT, 0, 2.0), _event(MINT, 60, 3.0)]
        )[MINT]
        outcome = score_call(_call(), records, round_trip_cost_pct=0.0)
        self.assertEqual(outcome.entry_price, 2.0)
        self.assertEqual(outcome.returns_pct["1h"], 50.0)

    def test_costs_are_subtracted_from_every_horizon(self):
        records = observations_from_events([_event(MINT, 0, 1.0), _event(MINT, 60, 1.05)])[MINT]
        outcome = score_call(_call(), records, round_trip_cost_pct=6.0)
        self.assertAlmostEqual(outcome.returns_pct["1h"], 5.0, places=3)
        self.assertAlmostEqual(outcome.net_returns_pct["1h"], -1.0, places=3)

    def test_a_gross_winner_can_be_a_net_loser(self):
        """The whole reason costs are modelled at this account size."""
        records = observations_from_events([_event(MINT, 0, 1.0), _event(MINT, 60, 1.04)])[MINT]
        outcome = score_call(_call(), records, round_trip_cost_pct=6.0)
        self.assertGreater(outcome.returns_pct["1h"], 0)
        self.assertLess(outcome.net_returns_pct["1h"], 0)

    def test_a_call_with_no_observation_is_unmeasurable(self):
        outcome = score_call(_call(), [])
        self.assertFalse(outcome.measurable)
        self.assertIn("entry_not_observed", outcome.unresolved)


class SourceScoringTests(unittest.TestCase):
    def _events_and_calls(self, author: str, drift_pct: float, count: int):
        events = []
        calls = []
        for index in range(count):
            # Distinct per author: shared mint keys would merge the two series
            # and give both authors the same measured outcome.
            mint = f"Mint{author}{index:0>32}"
            events.append(_event(mint, 0, 1.0))
            events.append(_event(mint, 60, 1.0 * (1 + drift_pct / 100)))
            calls.append(SourceCall(SourceKind.X, author, mint, CALLED_AT))
        return calls, events

    def test_small_sample_is_never_classified(self):
        """One lucky token makes three calls look spectacular."""
        calls, events = self._events_and_calls("lucky", 500.0, 3)
        score = score_all(calls, events)[0]
        self.assertEqual(score.role, SourceRole.UNPROVEN)
        self.assertTrue(any("sample_below_minimum" in note for note in score.notes))

    def test_profitable_source_is_an_originator(self):
        calls, events = self._events_and_calls("good", 40.0, 12)
        score = score_all(calls, events)[0]
        self.assertEqual(score.role, SourceRole.ORIGINATOR)
        self.assertGreater(score.mean_net_pct["1h"], 0)

    def test_break_even_source_is_an_amplifier(self):
        calls, events = self._events_and_calls("late", 4.0, 12)
        score = score_all(calls, events)[0]
        self.assertEqual(score.role, SourceRole.AMPLIFIER)

    def test_reliably_negative_source_is_a_distributor(self):
        calls, events = self._events_and_calls("adverse", -20.0, 12)
        score = score_all(calls, events)[0]
        self.assertEqual(score.role, SourceRole.DISTRIBUTOR)
        self.assertLess(score.mean_net_pct["1h"], 0)

    def test_sources_are_ranked_worst_first(self):
        good_calls, good_events = self._events_and_calls("good", 40.0, 12)
        bad_calls, bad_events = self._events_and_calls("adverse", -20.0, 12)
        scores = score_all(good_calls + bad_calls, good_events + bad_events)
        self.assertEqual(scores[0].role, SourceRole.DISTRIBUTOR)
        self.assertEqual(scores[-1].role, SourceRole.ORIGINATOR)

    def test_gate_rejections_are_reported_against_the_source(self):
        events = [_event(MINT, 0, 1.0, status="reject"), _event(MINT, 60, 2.0)]
        score = score_all([_call("promoter")], events)[0]
        self.assertEqual(score.rejected_by_gates, 1)
        self.assertTrue(any("gate_rejected_calls" in note for note in score.notes))

    def test_coverage_reports_how_much_was_measurable(self):
        calls, events = self._events_and_calls("partial", 10.0, 4)
        calls.append(SourceCall(SourceKind.X, "partial", "NeverSeenMint", CALLED_AT))
        score = score_all(calls, events)[0]
        self.assertEqual(score.calls, 5)
        self.assertEqual(score.measurable, 4)
        self.assertEqual(score.coverage_pct, 80.0)


if __name__ == "__main__":
    unittest.main()
