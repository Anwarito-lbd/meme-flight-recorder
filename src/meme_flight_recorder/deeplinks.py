"""Outbound dashboard links for a mint, for the terminals that have no API.

Photon, BullX, Trojan, Axiom and GMGN are execution terminals rather than data
sources. None of them publishes a developer API this project should depend on,
and Trojan is a custodial Telegram bot that holds private keys, so none of them
can be a provider here.

What they *can* be is a destination. A link costs nothing, transmits nothing,
requires no key and cannot fail closed. It navigates a human to a page for the
exact mint so that a judgement made here can be checked against the tool a
trader would actually use.

**This module is pure string construction.** It performs no I/O, so it cannot
leak a candidate to a third party merely by being called -- the link is only
followed if a person clicks it. That property is the reason the integration is
shaped this way rather than as an API client.

Identity is the mint address, never the ticker: five CATE mints and four SAOF
mints appeared here in one night.
"""

from __future__ import annotations

from dataclasses import dataclass

# Each entry is a format template taking the mint. Kept as data rather than
# code so a changed URL is a one-line edit and cannot break a decision path.
TERMINALS: dict[str, str] = {
    "photon": "https://photon-sol.tinyastro.io/en/lp/{mint}",
    "bullx": "https://neo.bullx.io/terminal?chainId=1399811149&address={mint}",
    "axiom": "https://axiom.trade/t/{mint}",
    "gmgn": "https://gmgn.ai/sol/token/{mint}",
    "trojan": "https://t.me/solana_trojanbot?start=r-{mint}",
}

ANALYTICS: dict[str, str] = {
    "dexscreener": "https://dexscreener.com/solana/{mint}",
    "geckoterminal": "https://www.geckoterminal.com/solana/tokens/{mint}",
    "birdeye": "https://birdeye.so/token/{mint}?chain=solana",
    "dextools": "https://www.dextools.io/app/en/solana/pair-explorer/{mint}",
    "solscan": "https://solscan.io/token/{mint}",
    "rugcheck": "https://rugcheck.xyz/tokens/{mint}",
}


@dataclass(frozen=True)
class DeepLinks:
    mint: str
    terminals: dict[str, str]
    analytics: dict[str, str]

    def as_dict(self) -> dict[str, str]:
        return {**self.terminals, **self.analytics}


def links_for(mint: str) -> DeepLinks:
    """Every dashboard URL for one mint.

    Raises on an empty mint rather than returning links to nowhere: a link built
    from a missing identifier points at whatever the site does with an empty
    path, which is worse than no link.
    """
    if not mint or not mint.strip():
        raise ValueError("mint is required to build deep links")
    cleaned = mint.strip()
    return DeepLinks(
        mint=cleaned,
        terminals={name: template.format(mint=cleaned) for name, template in TERMINALS.items()},
        analytics={name: template.format(mint=cleaned) for name, template in ANALYTICS.items()},
    )


def render(mint: str, symbol: str | None = None) -> str:
    """A block a human can paste or click, for a journalled candidate."""
    links = links_for(mint)
    heading = f"{symbol or ''} {mint}".strip()
    lines = [heading, "  terminals:"]
    lines += [f"    {name:<14}{url}" for name, url in links.terminals.items()]
    lines.append("  analytics:")
    lines += [f"    {name:<14}{url}" for name, url in links.analytics.items()]
    return "\n".join(lines)
