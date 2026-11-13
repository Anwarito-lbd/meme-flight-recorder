"""The enrich-then-grade route.

``/candidates/evaluate`` grades only the facts the caller hands it. A caller
whose feed is social or cockpit-shaped -- the J7/Axiom agent -- holds none of
the chain facts the gates require, so every candidate it submits returns
rejected on ``*_unknown``. That reads like a safety verdict but is only an
absence of data. These tests pin the difference.
"""

from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime

from meme_flight_recorder import api
from meme_flight_recorder.api import EnrichCandidateInput
from meme_flight_recorder.models import Universe
from meme_flight_recorder.providers.helius import MintEvidence

SPL_TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
MINT = "5vUQ9pMnVfpjM3Y8Xm2rWq7kTt4hLbG6cRd1sZa3NpUx"


def _body(**kw):
    base = {
        "chain": "solana",
        "address": MINT,
        "symbol": "AGENTS",
        "name": "agents.srl",
        "universe": Universe.SOLANA_LAUNCH,
        "observed_at": NOW,
        "facts": {},
    }
    base.update(kw)
    return EnrichCandidateInput(**base)


class FakeMints:
    def mint_evidence(self, mint):
        return MintEvidence(
            mint=mint,
            slot=1,
            decimals=6,
            supply_raw=1_000_000_000,
            mint_authority_disabled=True,
            freeze_authority_disabled=True,
            gross_top10_account_pct=12.0,
            largest_accounts_observed=10,
            largest_accounts_error=None,
            raw={},
            program_id=SPL_TOKEN,
        )


class DeadMints:
    """A provider that is down. Unavailable is not the same as safe."""

    def mint_evidence(self, mint):
        raise ConnectionError("rpc unreachable")


class ApiEnrichTest(unittest.TestCase):
    def setUp(self):
        # Reaching a route calls `load_settings()`, and `env.py` copies the
        # project's .env into `os.environ`. That leaks: `test_core`'s Helius
        # test asserts that a *missing* HELIUS_API_KEY raises, and a key left
        # behind here turns that assertion into a real network call. Snapshot
        # and restore so this file cannot decide another file's outcome.
        self._environ = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._environ)
        api._enrichment_providers.cache_clear()

    def _run(self, providers, **kw):
        api._enrichment_providers.cache_clear()
        original = api._enrichment_providers
        try:
            api._enrichment_providers = lambda: providers
            return api.enrich_and_evaluate_candidate(_body(**kw))
        finally:
            api._enrichment_providers = original

    def test_enrichment_report_names_what_was_and_was_not_established(self):
        result = self._run((FakeMints(), None, None, None))
        report = result["enrichment"]
        self.assertIn("identity_verified", report["resolved"])
        self.assertIn("mint_authority_disabled", report["resolved"])
        # Routes were never asked for -- no quote provider was wired.
        self.assertIsInstance(report["unresolved"], list)
        self.assertFalse(report["providers"]["quote"])
        self.assertTrue(report["providers"]["mint"])

    def test_a_provider_that_is_down_never_reads_as_a_pass(self):
        """The whole point of failing closed: an outage must not approve."""
        result = self._run((DeadMints(), None, None, None))
        self.assertNotEqual(result["status"], "approve")
        report = result["enrichment"]
        self.assertIn("mint_evidence", report["errors"])
        self.assertIn("identity_verified", report["unresolved"])

    def test_the_plain_route_still_grades_only_what_it_was_given(self):
        """/candidates/evaluate must not silently start enriching."""
        from meme_flight_recorder.api import CandidateInput

        plain = api.evaluate_candidate(
            CandidateInput(
                chain="solana", address=MINT, symbol="AGENTS", name="agents.srl",
                universe=Universe.SOLANA_LAUNCH, observed_at=NOW, facts={},
            )
        )
        self.assertNotIn("enrichment", plain)
        self.assertIn("identity_unverified", plain["failures"])

    def test_order_size_is_the_callers_to_state(self):
        """Price impact is a function of order size, so it cannot be assumed."""
        from pydantic import ValidationError

        self.assertEqual(_body().intended_order_sol, 0.05)
        self.assertEqual(_body(intended_order_sol=0.25).intended_order_sol, 0.25)
        with self.assertRaises(ValidationError):
            _body(intended_order_sol=0)


if __name__ == "__main__":
    unittest.main()
