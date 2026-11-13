"""Tests for the free media feed adapters and their journaling.

Nothing here touches the network. The properties under test: parsers survive
the shapes the real surfaces produce, a mint is recorded only when an address
appears literally, tickers never become identity, and re-running a scan does
not duplicate journal rows.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.providers.media_feeds import (
    parse_gnews_rss,
    parse_telegram_preview,
)
from meme_flight_recorder.social_intelligence import (
    SocialSource,
    build_event,
)

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"

REDDIT_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <id>t3_abc123</id>
  <title>Watching this one</title>
  <content type="html">&amp;#x200B;ca: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 dyor</content>
  <author><name>/u/degen_1</name></author>
  <updated>2026-08-22T09:15:00+00:00</updated>
  <link href="https://www.reddit.com/r/SolanaMemeCoins/comments/abc123/x/"/>
 </entry>
</feed>"""

TELEGRAM_HTML = """
<div class="tgme_widget_message_wrap js-widget_message_wrap">
 <div class="tgme_widget_message text_not_supported_wrap" data-post="somechannel/456">
  <div class="tgme_widget_message_bubble">
   <div class="tgme_widget_message_text js-message_text" dir="auto">
    New launch is live
    <br/>CA: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
   </div>
   <time datetime="2026-08-22T10:30:00+00:00"></time>
  </div>
 </div>
</div>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
 <div class="tgme_widget_message text_not_supported_wrap" data-post="somechannel/457">
  <div class="tgme_widget_message_photo_wrap"></div>
 </div>
</div>
"""

GNEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Google News</title>
<item>
  <title>Solana memecoin rallies - Example Press</title>
  <link>https://example.com/story</link>
  <pubDate>Fri, 21 Aug 2026 09:30:00 +0000</pubDate>
  <source url="https://example.com">Example Press</source>
</item>
<item>
  <title></title>
  <link>https://example.com/empty</link>
</item>
</channel></rss>"""


class TestReddit:
    def test_posts_parse_from_the_atom_feed(self) -> None:
        from meme_flight_recorder.providers.media_feeds import _reddit_items_from_atom

        items = _reddit_items_from_atom(REDDIT_ATOM, "SolanaMemeCoins")
        assert len(items) == 1
        item = items[0]
        assert item.external_id == "t3_abc123"
        assert MINT in item.text
        assert item.author == "/u/degen_1"
        assert item.published_at is not None and item.published_at.tzinfo is not None

    def test_event_keeps_literal_mint_and_never_the_ticker(self) -> None:
        from meme_flight_recorder.providers.media_feeds import _reddit_items_from_atom

        item = _reddit_items_from_atom(REDDIT_ATOM, "SolanaMemeCoins")[0]
        event = build_event(
            SocialSource.REDDIT,
            item.author,
            item.text,
            published_at=item.published_at,
            external_id=item.external_id,
        )
        assert event.mint == MINT
        # Tickers are narrative labels, never identity: five CATE mints in one night.
        assert all(symbol != "CA" for symbol in event.symbols)


class TestTelegramPreview:
    def test_messages_parse_and_empty_blocks_are_skipped(self) -> None:
        items = parse_telegram_preview(TELEGRAM_HTML, "somechannel")
        assert len(items) == 1
        assert items[0].external_id == "somechannel/456"
        assert MINT in items[0].text
        assert items[0].published_at == datetime.fromisoformat("2026-08-22T10:30:00+00:00")

    def test_a_page_without_message_blocks_yields_nothing(self) -> None:
        assert parse_telegram_preview("<html><body>Login to view</body></html>", "x") == []


class TestGoogleNews:
    def test_headlines_parse_and_blank_titles_drop(self) -> None:
        items = parse_gnews_rss(GNEWS_RSS, "solana memecoin")
        assert len(items) == 1
        assert items[0].origin == "gnews:Example Press"
        assert items[0].published_at is not None


class TestJournalRoundTrip:
    def test_append_once_makes_rescans_idempotent(self) -> None:
        with TemporaryDirectory() as folder:
            recorder = FlightRecorder(Path(folder) / "book.db")
            payload = {"source": "reddit", "mint": MINT}
            first = recorder.append_once(
                "media_mention",
                entity_id=MINT,
                payload=payload,
                idempotency_key="media:reddit:t3_abc123",
            )
            second = recorder.append_once(
                "media_mention",
                entity_id=MINT,
                payload=payload,
                idempotency_key="media:reddit:t3_abc123",
            )
            events = recorder.events_by_type("media_mention")
            assert len(events) == 1
            assert first == second
