"""Deep links are pure string construction, and that is the security property."""

from __future__ import annotations

import pytest

from meme_flight_recorder.deeplinks import ANALYTICS, TERMINALS, links_for, render

MINT = "UqrVTrZ6W9DjJCpWERbud5CaqDv574C7v4EBWQypump"


def test_every_terminal_and_analytics_site_gets_a_link() -> None:
    links = links_for(MINT)
    assert set(links.terminals) == set(TERMINALS)
    assert set(links.analytics) == set(ANALYTICS)


def test_the_mint_appears_in_every_url() -> None:
    """Identity is the mint address, never the ticker."""
    for url in links_for(MINT).as_dict().values():
        assert MINT in url


def test_an_empty_mint_is_refused_rather_than_linked_to_nowhere() -> None:
    for value in ("", "   "):
        with pytest.raises(ValueError, match="mint is required"):
            links_for(value)


def test_whitespace_is_stripped_before_building() -> None:
    assert links_for(f"  {MINT}  ").mint == MINT


def test_render_includes_the_symbol_when_known() -> None:
    assert "AORP" in render(MINT, "AORP")
    assert MINT in render(MINT, None)
