"""Tests for deployer history fetching and caching."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from meme_flight_recorder.deployer_history import (
    cached_pages,
    evidence_from_cache,
    fetch_evidence,
    write_page,
)


class FakePage:
    def __init__(self, transactions: list[dict[str, Any]], has_more: bool, cursor: str | None):
        self.transactions = tuple(transactions)
        self.has_more = has_more
        self.next_cursor = cursor
        self.raw = {
            "data": transactions,
            "pagination": {"hasMore": has_more, "nextCursor": cursor},
        }


class FakeProvider:
    def __init__(self, pages: list[FakePage]):
        self.pages = pages
        self.calls: list[str | None] = []

    def wallet_history(self, address: str, before: str | None = None) -> FakePage:
        self.calls.append(before)
        return self.pages[len(self.calls) - 1]


class DeadProvider:
    def wallet_history(self, address: str, before: str | None = None) -> FakePage:
        raise RuntimeError("rate limited")


def test_missing_wallet_returns_no_pages() -> None:
    with TemporaryDirectory() as folder:
        assert cached_pages("NOBODY", Path(folder)) == []
        assert evidence_from_cache("NOBODY", Path(folder)) is None


def test_written_page_is_readable_and_content_named() -> None:
    with TemporaryDirectory() as folder:
        cache = Path(folder)
        path = write_page("DEV111", 0, {"data": [{"type": "CREATE"}]}, cache)
        assert path.exists()
        assert path.name.startswith("page-00000-")
        assert json.loads(path.read_text(encoding="utf-8"))["address"] == "DEV111"


def test_identical_payload_does_not_create_a_second_file() -> None:
    """Content-derived names make a repeated fetch idempotent."""
    with TemporaryDirectory() as folder:
        cache = Path(folder)
        raw = {"data": [{"type": "TRANSFER"}]}
        write_page("DEV111", 0, raw, cache)
        write_page("DEV111", 0, raw, cache)
        assert len(cached_pages("DEV111", cache)) == 1


def test_fetch_walks_pages_until_exhausted() -> None:
    with TemporaryDirectory() as folder:
        provider = FakeProvider(
            [
                FakePage([{"type": "CREATE", "tokenTransfers": [{"mint": "A"}]}], True, "cur1"),
                FakePage([{"type": "CREATE", "tokenTransfers": [{"mint": "B"}]}], False, None),
            ]
        )
        evidence = fetch_evidence("DEV111", provider, cache_dir=Path(folder))
        assert evidence is not None
        assert evidence.prior_launches == 2
        assert evidence.history_truncated is False
        assert provider.calls == [None, "cur1"]


def test_fetch_respects_max_pages_and_reports_truncation() -> None:
    """Stopping early must leave the record marked partial, never complete."""
    with TemporaryDirectory() as folder:
        provider = FakeProvider(
            [
                FakePage([{"type": "TRANSFER"}], True, "cur1"),
                FakePage([{"type": "TRANSFER"}], True, "cur2"),
            ]
        )
        evidence = fetch_evidence("DEV111", provider, max_pages=2, cache_dir=Path(folder))
        assert evidence is not None
        assert evidence.history_truncated is True


def test_second_fetch_uses_the_cache() -> None:
    with TemporaryDirectory() as folder:
        cache = Path(folder)
        provider = FakeProvider([FakePage([{"type": "TRANSFER"}], False, None)])
        fetch_evidence("DEV111", provider, cache_dir=cache)
        fetch_evidence("DEV111", provider, cache_dir=cache)
        assert len(provider.calls) == 1


def test_provider_failure_returns_none_rather_than_raising() -> None:
    """A dead API must not end the collector cycle."""
    with TemporaryDirectory() as folder:
        assert fetch_evidence("DEV111", DeadProvider(), cache_dir=Path(folder)) is None


def test_corrupt_page_is_skipped_not_fatal() -> None:
    with TemporaryDirectory() as folder:
        cache = Path(folder)
        write_page("DEV111", 0, {"data": [{"type": "TRANSFER"}]}, cache)
        (cache / "DEV111" / "page-00001-deadbeef.json").write_text("{not json", encoding="utf-8")
        assert len(cached_pages("DEV111", cache)) == 1


def test_any_page_reporting_more_marks_the_record_truncated() -> None:
    with TemporaryDirectory() as folder:
        cache = Path(folder)
        write_page("DEV111", 0, {"data": [{"type": "T"}], "pagination": {"hasMore": False}}, cache)
        write_page("DEV111", 1, {"data": [{"type": "T"}], "pagination": {"hasMore": True}}, cache)
        evidence = evidence_from_cache("DEV111", cache)
        assert evidence is not None
        assert evidence.history_truncated is True
