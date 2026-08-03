from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from meme_flight_recorder.discovery import (
    MoverFilter,
    parse_trending_pools,
    select_movers,
)
from meme_flight_recorder.models import Universe

WINNING_CATE = "Ai66LHZG9MCzg1WKdawwqduVAXpNDUuV8M3uyq5ppump"


def _pool(
    mint: str = WINNING_CATE,
    name: str = "CATE / SOL",
    liquidity: float = 1_231_142.0,
    volume_24h: float = 20_594_044.0,
    age_hours: float = 190.0,
    change_5m: float = -1.1,
    buys: int = 91,
    sells: int = 37,
):
    created = datetime.now(UTC) - timedelta(hours=age_hours)
    return {
        "attributes": {
            "address": "PoolAddr1",
            "name": name,
            "base_token_price_usd": "0.0654",
            "reserve_in_usd": str(liquidity),
            "fdv_usd": "65829193",
            "pool_created_at": created.isoformat(),
            "volume_usd": {"m5": "12000", "h1": "900000", "h24": str(volume_24h)},
            "transactions": {"m5": {"buys": buys, "sells": sells}},
            "price_change_percentage": {"m5": str(change_5m), "h1": "23.1", "h24": "259.4"},
        },
        "relationships": {"base_token": {"data": {"id": f"solana_{mint}"}}},
    }


def _payload(*pools):
    return {"data": list(pools)}


class ParsingTests(unittest.TestCase):
    def test_mint_is_extracted_from_the_namespaced_id(self):
        pool = parse_trending_pools(_payload(_pool()))[0]
        self.assertEqual(pool.mint, WINNING_CATE)
        self.assertEqual(pool.symbol, "CATE")

    def test_rows_without_a_mint_are_dropped_not_guessed(self):
        broken = _pool()
        broken["relationships"] = {}
        self.assertEqual(parse_trending_pools(_payload(broken)), [])

    def test_buy_share_is_computed_from_trade_counts(self):
        pool = parse_trending_pools(_payload(_pool(buys=75, sells=25)))[0]
        self.assertEqual(pool.buy_share_5m_pct, 75.0)

    def test_buy_share_is_none_when_there_are_no_trades(self):
        pool = parse_trending_pools(_payload(_pool(buys=0, sells=0)))[0]
        self.assertIsNone(pool.buy_share_5m_pct)

    def test_age_comes_from_pool_creation(self):
        """A long-lived token can sit on a pool created minutes ago."""
        pool = parse_trending_pools(_payload(_pool(age_hours=2.0)))[0]
        self.assertAlmostEqual(pool.pool_age_minutes, 120.0, delta=1.0)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_carries_identity_and_liquidity(self):
        snapshot = parse_trending_pools(_payload(_pool()))[0].to_snapshot()
        self.assertEqual(snapshot.identity.address, WINNING_CATE)
        self.assertEqual(snapshot.universe, Universe.SOLANA_EMERGING)
        self.assertEqual(snapshot.liquidity_usd, 1_231_142.0)

    def test_snapshot_leaves_unverifiable_fields_unset(self):
        """This source cannot establish authorities or routes; gates must reject."""
        snapshot = parse_trending_pools(_payload(_pool()))[0].to_snapshot()
        self.assertFalse(snapshot.identity.verified)
        self.assertIsNone(snapshot.mint_authority_disabled)
        self.assertIsNone(snapshot.exit_route_found)
        self.assertIsNone(snapshot.transaction_simulation_ok)


class MoverFilterTests(unittest.TestCase):
    def test_the_winning_cate_profile_is_accepted(self):
        """Regression: this exact token was invisible to the launchpad feed."""
        movers, skipped = select_movers(_payload(_pool()))
        self.assertEqual(len(movers), 1)
        self.assertEqual(movers[0].mint, WINNING_CATE)
        self.assertEqual(skipped, {})

    def test_thin_pools_are_skipped_before_spending_calls(self):
        _, skipped = select_movers(_payload(_pool(liquidity=1_000.0)))
        self.assertEqual(skipped, {"liquidity_too_thin": 1})

    def test_quiet_pools_are_skipped(self):
        _, skipped = select_movers(_payload(_pool(volume_24h=500.0)))
        self.assertEqual(skipped, {"volume_too_low": 1})

    def test_brand_new_pools_are_skipped(self):
        _, skipped = select_movers(_payload(_pool(age_hours=0.2)))
        self.assertEqual(skipped, {"pool_too_new": 1})

    def test_a_vertical_candle_is_not_chased(self):
        """The move has been made; the remaining buyers are the exit."""
        _, skipped = select_movers(_payload(_pool(change_5m=60.0)))
        self.assertEqual(skipped, {"already_vertical": 1})

    def test_movers_are_ranked_by_volume(self):
        quiet = _pool(mint="Mint" + "q" * 40, name="QUIET / SOL", volume_24h=200_000.0)
        movers, _ = select_movers(_payload(quiet, _pool()))
        self.assertEqual(movers[0].symbol, "CATE")

    def test_filter_is_a_budget_gate_not_a_safety_gate(self):
        """Acceptance only means 'worth enriching', never 'safe to trade'."""
        movers, _ = select_movers(_payload(_pool()), MoverFilter())
        snapshot = movers[0].to_snapshot()
        self.assertIsNone(snapshot.transaction_simulation_ok)


if __name__ == "__main__":
    unittest.main()
