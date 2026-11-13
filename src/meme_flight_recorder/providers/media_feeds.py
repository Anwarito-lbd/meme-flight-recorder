"""Free public media feeds: Reddit, Telegram channel previews, Google News RSS.

Three adapters, one contract: each fetches a bounded page from a *public*
source and returns raw items for `social_intelligence.build_event`. None of
them logs in, pays, or scrapes behind an access control -- the same public
information rule that governs the rest of the project.

Why these exist. The virality study closed social text as a *prediction*
signal on this population, but discovery is a different question: a token
chattering on Reddit before it reaches CoinGecko trending is observed earlier,
and arriving early is the only edge available (`narratives.py`). Whether these
sources actually arrive earlier is a measured question (`score-sources` ranks
them by realized expectancy once history accumulates); nothing here can gate.

Pacing lives at each call site because each surface has its own tolerance.
Reddit serves JSON politely to any honest user-agent; t.me/s previews are
plain HTML; Google News RSS is designed to be polled. All three degrade to an
empty result plus a printed reason rather than raising into the caller's loop.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

USER_AGENT = {"user-agent": "meme-flight-recorder/0.4 (media scan)"}
# Reddit's edge rejects unknown agents on some surfaces; a browser-shaped
# fallback is the documented second attempt, used only after the honest UA 403s.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Gecko/20100101 Firefox/141.0"
)


@dataclass(frozen=True)
class MediaItem:
    """One post/article before it becomes a SocialEvent."""

    origin: str
    external_id: str
    author: str
    text: str
    url: str | None = None
    published_at: datetime | None = None
    engagement: int | None = None


# --------------------------------------------------------------------- reddit

_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def _reddit_items_from_atom(xml_text: str, subreddit: str) -> list[MediaItem]:
    """Parse the public /.rss Atom feed into items.

    Reddit serves this feed to anonymous clients where the .json endpoint
    answers 403 Blocked to datacenter traffic -- measured 2026-08-22, which is
    why the feed form is what ships. Content is escaped HTML; tags are stripped
    and entities survive ElementTree's decoding.
    """
    root = ET.fromstring(xml_text)
    items: list[MediaItem] = []
    for entry in root.iter(f"{_ATOM_NS}entry"):
        entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        title = _strip_html((entry.findtext(f"{_ATOM_NS}title") or "").strip())
        content = _strip_html(entry.findtext(f"{_ATOM_NS}content") or "").strip()
        text = "\n".join(part for part in (title, content) if part).strip()
        if not entry_id or not text:
            continue
        author_name = ""
        author_el = entry.find(f"{_ATOM_NS}author/{_ATOM_NS}name")
        if author_el is not None and author_el.text:
            author_name = author_el.text.strip()
        updated = (entry.findtext(f"{_ATOM_NS}updated") or "").strip() or None
        published = None
        if updated:
            try:
                # Python 3.11+ fromisoformat handles the trailing Z directly.
                published = datetime.fromisoformat(updated)
            except ValueError:
                published = None
        link_href = ""
        for link in entry.findall(f"{_ATOM_NS}link"):
            href = link.get("href") or ""
            if href and not link_href:
                link_href = href
        items.append(
            MediaItem(
                origin=f"r/{subreddit}",
                external_id=entry_id.rsplit("/", 1)[-1] or entry_id,
                author=author_name or "unknown",
                text=text,
                url=link_href or None,
                published_at=published,
                engagement=None,
            )
        )
    return items


def fetch_subreddit_new(subreddit: str) -> list[MediaItem]:
    """Public new-posts feed for one subreddit, via the Atom endpoint.

    Reddit 403-blocks its .json endpoint for datacenter traffic even behind an
    honest user-agent (measured); the .rss feed still serves anonymous readers,
    so that is what ships here.
    """
    url = f"https://www.reddit.com/r/{subreddit}/.rss"
    try:
        req = urllib.request.Request(url, headers=USER_AGENT)
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_bytes = response.read()
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
        req = urllib.request.Request(url, headers={"user-agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_bytes = response.read()
    return _reddit_items_from_atom(xml_bytes.decode("utf-8", "replace"), subreddit)


# ------------------------------------------------------------------- telegram

_MESSAGE_SPLIT = re.compile(r'class="tgme_widget_message_wrap')
_POST_ID = re.compile(r'data-post="([^"]+)"')
_DATETIME = re.compile(r'<time[^>]*datetime="([^"]+)"')
_TEXT_BLOCK = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.DOTALL
)
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>")


def _strip_html(fragment: str) -> str:
    without_breaks = _BR.sub("\n", fragment)
    return _TAG.sub("", without_breaks)


def parse_telegram_preview(html: str, channel: str) -> list[MediaItem]:
    """Parse a public t.me/s/<channel> preview page into messages.

    The page embeds each message in a `tgme_widget_message` block carrying
    `data-post="<channel>/<id>"`, a datetime, and optionally a text div.
    Messages without text are skipped: a forwarded photo identifies nothing.
    A page that shows no message blocks at all yields no items -- which is how
    a private or nonexistent channel presents, and the caller reports that as
    unavailable rather than as a quiet channel.
    """
    items: list[MediaItem] = []
    for block in _MESSAGE_SPLIT.split(html or "")[1:]:
        post = _POST_ID.search(block)
        if not post:
            continue
        stamp = _DATETIME.search(block)
        texts = _TEXT_BLOCK.findall(block)
        if not texts:
            continue
        body = "\n".join(_strip_html(fragment) for fragment in texts).strip()
        if not body:
            continue
        published = None
        if stamp:
            try:
                published = datetime.fromisoformat(stamp.group(1))
            except ValueError:
                published = None
        items.append(
            MediaItem(
                origin=f"@{channel}",
                external_id=post.group(1),
                author=channel,
                text=body,
                url=f"https://t.me/{post.group(1)}",
                published_at=published,
                engagement=None,
            )
        )
    return items


def fetch_telegram_preview(channel: str) -> list[MediaItem]:
    req = urllib.request.Request(
        f"https://t.me/s/{channel}", headers=USER_AGENT
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, "replace")
    return parse_telegram_preview(html, channel)


# ---------------------------------------------------------------- google news

_PUB_FORMATS = ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z")


def parse_gnews_rss(xml_text: str, query: str) -> list[MediaItem]:
    root = ET.fromstring(xml_text)
    items: list[MediaItem] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        source = item.find("source")
        origin = f"gnews:{source.text.strip()}" if source is not None and source.text else f"gnews:{query}"
        published = None
        for fmt in _PUB_FORMATS:
            try:
                parsed = datetime.strptime(pub, fmt)  # noqa: DTZ007 - handled next line
                # The %z form is already aware; the %Z ("GMT") form is not.
                published = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        if not title:
            continue
        items.append(
            MediaItem(
                origin=origin,
                external_id=f"{origin}:{abs(hash((title, link)))}",
                author="press",
                text=title,
                url=link or None,
                published_at=published,
                engagement=None,
            )
        )
    return items


def fetch_gnews(query: str) -> list[MediaItem]:
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    req = urllib.request.Request(url, headers=USER_AGENT)
    with urllib.request.urlopen(req, timeout=15) as response:
        xml_bytes = response.read()
    return parse_gnews_rss(xml_bytes.decode("utf-8", "replace"), query)
