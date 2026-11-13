"""Vendor labels are opportunistic, and a miss must never look like a pass.

`developer_selling` reaches this project through exactly one vendor field, and
only through a ranked feed. Everything here pins the distinction between "the
feed says the developer is not distributing" and "the feed did not carry this
mint at all" -- which the safety engine treats very differently, and which a
`.get()` returning a falsy default would silently collapse.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

from meme_flight_recorder.models import TokenIdentity, TokenSnapshot, Universe
from meme_flight_recorder.providers.binance_web3 import VendorClusterLabels
from meme_flight_recorder.vendor_labels import (
    LIFECYCLE_RANKS,
    VendorLabelIndex,
    apply_vendor_labels,
)

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
MINT = "5z7gxdrf8NpBrELaB29kXAk7SHBiNG5u726LtyYvpump"
OTHER = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


class Row:
    def __init__(self, address, labels):
        self.contract_address = address
        self.labels = labels


class FakeFeed:
    """Returns one row per rank type, and counts calls so TTL can be asserted."""

    def __init__(self, rows_by_rank=None, error=None):
        self.calls = 0
        self.rank_calls = []
        self._rows = rows_by_rank or {}
        self._error = error

    def meme_rush(self, chain_id, rank_type, limit):
        self.calls += 1
        self.rank_calls.append(rank_type)
        if self._error:
            raise self._error
        return self._rows.get(rank_type, [])


def snapshot(**kw):
    base = {
        "identity": TokenIdentity("solana", MINT, "HAROLD", "Harold The Duck", verified=False),
        "universe": Universe.SOLANA_EMERGING,
        "observed_at": NOW,
    }
    base.update(kw)
    return TokenSnapshot(**base)


class VendorIndexTest(unittest.TestCase):
    def test_index_covers_the_whole_launchpad_lifecycle(self):
        """One rank type is one Pulse column. Missing one blinds a whole stage."""
        feed = FakeFeed()
        VendorLabelIndex(feed, clock=lambda: 0.0).refresh()
        self.assertEqual(tuple(feed.rank_calls), LIFECYCLE_RANKS)

    def test_a_mint_the_feed_did_not_carry_returns_None_not_a_default(self):
        feed = FakeFeed({10: [Row(OTHER, VendorClusterLabels(dev_sell_pct=0.0))]})
        index = VendorLabelIndex(feed, clock=lambda: 0.0)
        self.assertIsNone(index.labels_for(MINT))
        self.assertIsNotNone(index.labels_for(OTHER))

    def test_refresh_is_shared_across_lookups_inside_the_ttl(self):
        """A per-candidate fetch would spend the rate limit on bulk-fetched data."""
        t = {"now": 0.0}
        feed = FakeFeed({10: [Row(MINT, VendorClusterLabels(dev_sell_pct=1.0))]})
        index = VendorLabelIndex(feed, ttl_seconds=60.0, clock=lambda: t["now"])
        for _ in range(5):
            index.labels_for(MINT)
        self.assertEqual(feed.calls, len(LIFECYCLE_RANKS))
        t["now"] = 61.0
        index.labels_for(MINT)
        self.assertEqual(feed.calls, 2 * len(LIFECYCLE_RANKS))

    def test_a_dead_feed_does_not_look_fresh_and_does_not_blank_the_index(self):
        rows = {10: [Row(MINT, VendorClusterLabels(dev_sell_pct=1.0))]}
        feed = FakeFeed(rows)
        index = VendorLabelIndex(feed, ttl_seconds=0.0, clock=lambda: 0.0)
        self.assertIsNotNone(index.labels_for(MINT))

        feed._error = ConnectionError("feed down")
        index.refresh(force=True)
        self.assertTrue(index.coverage.errors)
        # The good data survives; what must NOT happen is the failure reading as
        # a successful refresh that found nothing.
        self.assertIsNotNone(index._by_mint.get(MINT))

    def test_a_row_with_more_evidence_wins_over_an_emptier_duplicate(self):
        thin = VendorClusterLabels(top10_pct=50.0)
        rich = VendorClusterLabels(top10_pct=50.0, dev_sell_pct=2.0, dev_pct=1.0)
        feed = FakeFeed({10: [Row(MINT, rich)], 20: [Row(MINT, thin)]})
        index = VendorLabelIndex(feed, clock=lambda: 0.0)
        self.assertEqual(index.labels_for(MINT).dev_sell_pct, 2.0)


class ApplyVendorLabelsTest(unittest.TestCase):
    def test_no_labels_resolves_nothing(self):
        snap = snapshot()
        out, resolved = apply_vendor_labels(snap, None)
        self.assertIs(out, snap)
        self.assertEqual(resolved, ())
        self.assertIsNone(out.developer_selling)

    def test_absent_dev_sell_leaves_developer_selling_unknown(self):
        """Absent is never False. False means measured-and-not-distributing."""
        out, resolved = apply_vendor_labels(snapshot(), VendorClusterLabels(top10_pct=12.0))
        self.assertIsNone(out.developer_selling)
        self.assertNotIn("developer_selling", resolved)

    def test_dev_sell_resolves_developer_selling_through_the_shared_band(self):
        out, resolved = apply_vendor_labels(snapshot(), VendorClusterLabels(dev_sell_pct=50.0))
        self.assertIn("developer_selling", resolved)
        self.assertIsNotNone(out.developer_selling)

    def test_a_fully_exited_developer_is_not_treated_as_distributing(self):
        """The safest measured state. A flat `> 0` test would reject it."""
        out, _ = apply_vendor_labels(snapshot(), VendorClusterLabels(dev_sell_pct=100.0))
        self.assertIs(out.developer_selling, False)

    def test_chain_evidence_outranks_the_vendor_and_is_never_overwritten(self):
        snap = snapshot(top10_private_holder_pct=9.0)
        out, resolved = apply_vendor_labels(snap, VendorClusterLabels(top10_pct=88.0))
        self.assertEqual(out.top10_private_holder_pct, 9.0)
        self.assertNotIn("top10_private_holder_pct", resolved)

    def test_raw_labels_are_kept_so_a_verdict_can_be_re_derived(self):
        out, _ = apply_vendor_labels(snapshot(), VendorClusterLabels(dev_sell_pct=50.0, dev_pct=3.0))
        kept = out.raw_evidence["binance_web3_labels"]
        self.assertEqual(kept["dev_sell_pct"], 50.0)
        self.assertEqual(kept["dev_pct"], 3.0)


if __name__ == "__main__":
    unittest.main()
