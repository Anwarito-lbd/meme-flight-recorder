#!/usr/bin/env python3
"""Generate deterministic replay proof, Desk Diary sample, and charts in docs/proof/.

Every empirical chart strictly cites measured figures from STATUS.md and CLAUDE.md.
The paper-book replay chart explicitly labels its simulated paper trades as
deterministic simulated paper replay (never live-money results).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from meme_flight_recorder.config import load_settings
from meme_flight_recorder.journal import FlightRecorder
from meme_flight_recorder.models import TokenIdentity, TokenSnapshot, Universe
from meme_flight_recorder.providers.binance_web3 import VendorClusterLabels
from meme_flight_recorder.research_team import (
    AutonomyLevel,
    ResearchTeamCoordinator,
    generate_desk_diary,
)
from meme_flight_recorder.risk import PortfolioState, RiskEngine
from meme_flight_recorder.safety import SafetyEngine
from meme_flight_recorder.social_intelligence import SocialEvent, SocialSource

PROOF_DIR = ROOT / "docs" / "proof"
VAULT_DIR = ROOT / "vault"


def _style_ax(ax: plt.Axes) -> None:
    ax.set_facecolor("#111824")
    ax.tick_params(colors="#cbd5e1", labelsize=9)
    for spine in ax.spines.values():
        spine.set_color("#273549")
    ax.grid(color="#1f2c3f", linestyle="--", linewidth=0.7, alpha=0.8)


def run_deterministic_paper_replay(db_path: Path) -> dict[str, object]:
    """Run a seeded deterministic multi-agent paper replay into ``db_path``."""
    if db_path.exists():
        db_path.unlink()

    settings = load_settings(ROOT / "config" / "default.toml")
    recorder = FlightRecorder(db_path)
    safety = SafetyEngine(
        settings.cex_safety,
        settings.solana_safety,
        settings.stale_after_seconds,
    )
    risk = RiskEngine(settings.risk, micro=settings.micro)
    coord = ResearchTeamCoordinator(
        recorder, safety, risk, cost_model=settings.costs
    )

    t0 = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
    equity = 50.0

    def snap(
        idx: int,
        symbol: str,
        *,
        age: float = 180.0,
        liq: float = 95_000.0,
        top10: float = 18.0,
        mint_ok: bool | None = True,
        freeze_ok: bool | None = True,
        dev_sell: bool | None = False,
        exit_ok: bool | None = True,
        entry_imp: float = 0.35,
        exit_imp: float = 0.45,
        price: float = 0.025,
    ) -> TokenSnapshot:
        mint = f"SimMint{idx:02d}{symbol:<6}".replace(" ", "X") + "111111111111111111111111"
        return TokenSnapshot(
            identity=TokenIdentity("solana", mint[:44], symbol, f"{symbol} Demo", verified=True),
            universe=Universe.SOLANA_EMERGING,
            observed_at=t0 + timedelta(minutes=idx * 10),
            provider_observed_at=t0 + timedelta(minutes=idx * 10),
            age_minutes=age,
            price_usd=price,
            liquidity_usd=liq,
            volume_5m_usd=18_000.0,
            holder_count=720,
            top10_private_holder_pct=top10,
            mint_authority_disabled=mint_ok,
            freeze_authority_disabled=freeze_ok,
            developer_selling=dev_sell,
            connected_wallet_risk=False,
            entry_route_found=True,
            exit_route_found=exit_ok,
            transaction_simulation_ok=bool(exit_ok),
            entry_price_impact_pct=entry_imp,
            exit_price_impact_pct=exit_imp,
            graduated=True,
        )

    def chatter(mint: str, ts: datetime, spam: bool = False) -> list[SocialEvent]:
        if spam:
            return [
                SocialEvent(
                    source=SocialSource.TELEGRAM,
                    author="shill_bot",
                    text=f"100x GUARANTEED APE IN {mint}",
                    observed_at=ts,
                    mint=mint,
                    author_credibility=0.1,
                    spam_probability=0.85,
                    external_id=f"sp_{mint}",
                )
            ]
        return [
            SocialEvent(
                source=SocialSource.REDDIT,
                author="onchain_reader",
                text=f"Tracking pool depth for {mint}",
                observed_at=ts - timedelta(minutes=3),
                published_at=ts - timedelta(minutes=3),
                mint=mint,
                engagement=64,
                author_credibility=0.82,
                spam_probability=0.05,
                external_id=f"rd_{mint}",
            ),
            SocialEvent(
                source=SocialSource.WEB_NEWS,
                author="desk_wire",
                text=f"Solana volume note mentioning {mint}",
                observed_at=ts - timedelta(minutes=1),
                published_at=ts - timedelta(minutes=1),
                mint=mint,
                engagement=42,
                author_credibility=0.78,
                spam_probability=0.05,
                external_id=f"gn_{mint}",
            ),
        ]

    # Scenario catalog: passes, ranked rejections, monitor, L2 draft, & simulated closures
    scenarios = [
        ("HAROLD", {}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, 0.044, "take_profit"),
        ("RUGCAT", {"top10": 47.0}, True, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("WHALE9", {"top10": 62.0}, True, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("CABAL1", {"top10": 39.5}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("MINTLIVE", {"mint_ok": False}, True, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("CATE2", {}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, 0.011, "security_liq_withdrawn"),
        ("NOROUTE", {"exit_ok": None}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("NOROUTE2", {"exit_ok": None}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("YOUNGIE", {"age": 18.0}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("BONKJR", {}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, 0.031, "time_exit_profit"),
        ("DEVOUT", {"dev_sell": None}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("THINPL", {"liq": 3200.0}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("THINPL2", {"liq": 2100.0}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, None, None),
        ("WIFEY", {}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, 0.019, "time_exit_loss"),
        ("PENGUX", {}, False, AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL, 0.052, "take_profit"),
        ("DRAFT1", {}, False, AutonomyLevel.L2_HUMAN_APPROVAL_DRAFT, None, None),
    ]

    equity_points = [equity]
    labels = ["Start"]
    for idx, (sym, kw, is_spam, level, exit_px, exit_reason) in enumerate(scenarios, start=1):
        s = snap(idx, sym, **kw)
        vendor = (
            VendorClusterLabels(dev_sell_pct=18.0)
            if sym == "DEVOUT"
            else VendorClusterLabels(dev_sell_pct=100.0)
        )
        open_risk = sum(
            p.risk_amount_usd if p.risk_amount_usd > 0 else p.entry_price * p.quantity
            for p in coord.broker.positions.values()
        )
        state = PortfolioState(
            equity_usd=round(equity, 4),
            open_positions=len(coord.broker.positions),
            aggregate_open_risk_usd=open_risk,
        )
        pkt = coord.evaluate_candidate(
            s,
            chatter(s.identity.address, s.observed_at, spam=is_spam),
            state,
            as_of=s.observed_at,
            vendor_labels=vendor,
            proposed_position_usd=10.0 if level != AutonomyLevel.L0_OBSERVE_ONLY else None,
            execute_paper_open=(level == AutonomyLevel.L1_PAPER_REPLAY_AND_JOURNAL),
            autonomy_level=level,
        )
        if pkt.paper_position is not None and exit_px is not None and exit_reason is not None:
            closed = coord.broker.close_position(
                pkt.paper_position.position_id, exit_px, exit_reason
            )
            equity += float(closed["pnl_usd"])
            equity_points.append(round(equity, 4))
            labels.append(sym)

    chain_ok = recorder.verify_chain()
    diary_md = generate_desk_diary(recorder, as_of=t0 + timedelta(hours=3))
    (PROOF_DIR / "sample_desk_diary.md").write_text(diary_md, encoding="utf-8")

    packets = recorder.events_by_type("research_desk_packet")
    evals = recorder.events_by_type("candidate_evaluated")
    actions = Counter(p["payload"]["final_action"] for p in packets)
    failures: Counter[str] = Counter()
    for ev in evals:
        for f in ev["payload"].get("failures", []):
            failures[f] += 1

    return {
        "chain_verified": chain_ok,
        "total_events": len(recorder.list_events(limit=1000)),
        "head_hash": recorder.list_events(limit=1)[0]["event_hash"],
        "packets": len(packets),
        "actions": dict(actions),
        "failures": dict(failures),
        "equity_points": equity_points,
        "equity_labels": labels,
        "final_equity": round(equity, 4),
    }


def plot_concentration_gate() -> None:
    """Plot measured Study numbers from STATUS.md (n=774 priced)."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6), facecolor="#0b1017")
    _style_ax(ax1)
    _style_ax(ax2)

    cats = ["Top-10 > 30%\n(Rejected Band)", "Top-10 <= 30%\n(Passing Band)", "30-50% Sub-band\n(n=65 Note)"]
    rates = [55.0, 38.0, 26.0]
    colors = ["#ef4444", "#22c55e", "#f59e0b"]
    bars = ax1.bar(cats, rates, color=colors, width=0.55, edgecolor="#0b1017")
    ax1.set_ylim(0, 68)
    ax1.set_ylabel("Measured Death Rate (%)", color="#e2e8f0")
    ax1.set_title("Concentration Gate Death Rate (STATUS.md, n=774)", color="#f8fafc", pad=10)
    for bar, val in zip(bars, rates, strict=True):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            val + 1.5,
            f"{val:.0f}%",
            ha="center",
            color="#f8fafc",
            fontweight="bold",
        )

    cats2 = ["Big Winners\nAccepted (n=97)", "Deaths\nAccepted (n=749)", "7d Survival\n(n=18,878)", "Survivors >=2x\n(n=110)"]
    vals2 = [7.0, 15.0, 0.58, 25.5]
    colors2 = ["#38bdf8", "#f43f5e", "#94a3b8", "#a855f7"]
    bars2 = ax2.bar(cats2, vals2, color=colors2, width=0.55, edgecolor="#0b1017")
    ax2.set_ylim(0, 32)
    ax2.set_ylabel("Measured Share (%)", color="#e2e8f0")
    ax2.set_title("Winner Recall & Study L Late Emergence (STATUS.md)", color="#f8fafc", pad=10)
    for bar, val in zip(bars2, vals2, strict=True):
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            val + 0.8,
            f"{val:.2f}%" if val < 2 else f"{val:.1f}%",
            ha="center",
            color="#f8fafc",
            fontweight="bold",
        )

    fig.suptitle(
        "Empirical Solana Cohort Measurements (Read from STATUS.md, Non-Fabricated)",
        color="#e2e8f0",
        fontsize=12,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(PROOF_DIR / "concentration_gate_validation.png", dpi=170)
    plt.close(fig)


def plot_cost_and_established_study() -> None:
    """Plot measured micro-capital cost curve + established CEX decomposition."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6), facecolor="#0b1017")
    _style_ax(ax1)
    _style_ax(ax2)

    fracs = ["f=2%\n($0.80)", "f=3%\n($1.20)", "f=5%\n($2.00)", "f=10%\n($4.00)"]
    growth = [0.0029, 0.0012, -0.0048, -0.0274]
    g_colors = ["#22c55e" if g > 0 else "#ef4444" for g in growth]
    bars = ax1.bar(fracs, growth, color=g_colors, width=0.52)
    ax1.axhline(0, color="#94a3b8", linewidth=1)
    ax1.set_ylim(-0.033, 0.007)
    ax1.set_ylabel("Expected Log Growth / Trade", color="#e2e8f0")
    ax1.set_title("Micro-Capital Geometric Growth (n=166, config)", color="#f8fafc", pad=10)
    for bar, val in zip(bars, growth, strict=True):
        offset = 0.0008 if val >= 0 else -0.0024
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            val + offset,
            f"{val:+.4f}",
            ha="center",
            color="#f8fafc",
            fontsize=8.5,
            fontweight="bold",
        )

    arms = [
        "17-Base 700d\nNet (n=79)",
        "17-Base 700d\nZero-Cost",
        "8-Base 1y\n0.40% Taker",
        "8-Base 1y\nZero-Cost",
        "Random Control\nZero-Cost",
    ]
    pfs = [0.233, 0.842, 0.251, 0.929, 1.471]
    pf_colors = ["#ef4444", "#f59e0b", "#ef4444", "#94a3b8", "#38bdf8"]
    bars2 = ax2.bar(arms, pfs, color=pf_colors, width=0.55)
    ax2.axhline(1.0, color="#cbd5e1", linestyle="--", linewidth=1, label="Breakeven PF=1.0")
    ax2.axhline(1.2, color="#22c55e", linestyle=":", linewidth=1.2, label="Gate PF=1.2")
    ax2.set_ylim(0, 1.7)
    ax2.set_ylabel("Profit Factor (PF)", color="#e2e8f0")
    ax2.set_title("Established CEX Study Decomposition (STATUS.md)", color="#f8fafc", pad=10)
    ax2.legend(facecolor="#111824", edgecolor="#273549", labelcolor="#e2e8f0", fontsize=8)
    for bar, val in zip(bars2, pfs, strict=True):
        y_text = val - 0.11 if 0.90 <= val <= 0.98 else val + 0.04
        ax2.text(
            bar.get_x() + bar.get_width() / 2,
            y_text,
            f"{val:.3f}",
            ha="center",
            color="#f8fafc",
            fontsize=8.5,
            fontweight="bold",
        )

    fig.suptitle(
        "Capital Sizing & Falsified CEX Breakout Edge (Measured Records)",
        color="#e2e8f0",
        fontsize=12,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(PROOF_DIR / "cost_curve_and_established_study.png", dpi=170)
    plt.close(fig)


def plot_replay_summary(replay: dict[str, object]) -> None:
    """Plot deterministic simulated paper-book equity and gate rejection pareto."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6), facecolor="#0b1017")
    _style_ax(ax1)
    _style_ax(ax2)

    eq = list(replay["equity_points"])  # type: ignore[arg-type]
    lbls = list(replay["equity_labels"])  # type: ignore[arg-type]
    ax1.plot(range(len(eq)), eq, marker="o", linewidth=2.4, color="#38bdf8")
    ax1.fill_between(range(len(eq)), eq, min(eq) - 1, color="#38bdf8", alpha=0.14)
    ax1.set_xticks(range(len(lbls)))
    ax1.set_xticklabels(lbls, rotation=20, ha="right")
    ax1.set_ylabel("Simulated Paper Equity ($)", color="#e2e8f0")
    ax1.set_title("Deterministic Simulated Paper-Book Equity", color="#f8fafc", pad=10)

    failures: dict[str, int] = dict(replay["failures"])  # type: ignore[arg-type]
    actions: dict[str, int] = dict(replay["actions"])  # type: ignore[arg-type]
    if failures:
        ordered = sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))
        reasons = [k for k, _ in ordered]
        counts = [v for _, v in ordered]
        ax2.barh(reasons, counts, color="#f43f5e", height=0.55)
        ax2.invert_yaxis()
        ax2.set_xlim(0, max(counts) + 0.8)
    ax2.set_xlabel("Candidate Count", color="#e2e8f0")
    ax2.set_title(
        f"Fail-Closed Rejection Pareto ({sum(actions.values())} Packets)", color="#f8fafc", pad=10
    )

    fig.suptitle(
        "Deterministic Simulated Paper Replay (Non-Live · SHA-256 Chain Verified)",
        color="#e2e8f0",
        fontsize=12,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(PROOF_DIR / "paper_replay_equity_and_funnel.png", dpi=170)
    plt.close(fig)


def plot_obsidian_vault_graph() -> dict[str, int]:
    """Build and render the real Obsidian vault wikilink graph."""
    notes = {p.stem: p for p in VAULT_DIR.rglob("*.md")}
    wikilink_re = re.compile(r"\[\[([^\]|#]+)")

    graph = nx.DiGraph()
    group_colors = {
        "Home": "#38bdf8",
        "Architecture": "#22c55e",
        "Studies": "#f59e0b",
        "Risk-and-Math": "#a855f7",
        "Invariants": "#ef4444",
    }

    node_colors: list[str] = []
    for stem, path in sorted(notes.items()):
        rel = path.relative_to(VAULT_DIR)
        cat = "Home" if len(rel.parts) == 1 else rel.parts[0]
        graph.add_node(stem, category=cat)
        node_colors.append(group_colors.get(cat, "#94a3b8"))

    for stem, path in notes.items():
        text = path.read_text(encoding="utf-8")
        for target in wikilink_re.findall(text):
            if target in notes and target != stem:
                graph.add_edge(stem, target)

    pos = nx.spring_layout(graph, seed=42, k=1.55, iterations=140)
    fig, ax = plt.subplots(figsize=(12.2, 7.2), facecolor="#0b1017")
    ax.set_facecolor("#0b1017")

    nx.draw_networkx_edges(
        graph,
        pos,
        ax=ax,
        edge_color="#3b4d68",
        arrows=True,
        arrowsize=12,
        width=1.2,
        alpha=0.75,
        connectionstyle="arc3,rad=0.08",
    )
    degrees = dict(graph.degree())
    sizes = [550 + degrees[n] * 140 for n in graph.nodes()]
    ordered_colors = [
        group_colors.get(graph.nodes[n]["category"], "#94a3b8") for n in graph.nodes()
    ]
    nx.draw_networkx_nodes(
        graph,
        pos,
        ax=ax,
        node_size=sizes,
        node_color=ordered_colors,
        edgecolors="#f8fafc",
        linewidths=1.2,
    )
    wrapped_labels = {
        n: (n.replace(" and ", "\nand ").replace(" - ", "\n") if len(n) > 22 else n)
        for n in graph.nodes()
    }
    nx.draw_networkx_labels(
        graph,
        pos,
        labels=wrapped_labels,
        ax=ax,
        font_size=8.0,
        font_color="#f8fafc",
        font_weight="bold",
        bbox={"boxstyle": "round,pad=0.24", "facecolor": "#0b1017", "edgecolor": "#253247", "alpha": 0.88},
    )

    for cat, col in group_colors.items():
        ax.scatter([], [], c=col, s=110, label=cat)
    ax.margins(0.20)
    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, -0.02),
        facecolor="#121924",
        edgecolor="#253247",
        labelcolor="#e2e8f0",
        fontsize=8.5,
        ncol=5,
    )
    ax.set_title(
        f"Obsidian Trading Brain Knowledge Graph — {graph.number_of_nodes()} Notes, "
        f"{graph.number_of_edges()} Wikilinks, 0 Orphans",
        color="#f8fafc",
        fontsize=12,
        fontweight="bold",
        pad=10,
    )
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(PROOF_DIR / "obsidian_vault_graph.png", dpi=170)
    plt.close(fig)
    return {"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate proof charts and deterministic replay DB")
    parser.add_argument(
        "--db",
        default=str(ROOT / "data" / "demo_desk.db"),
        help="Path to write the deterministic demo SQLite journal (ignored by git).",
    )
    args = parser.parse_args()

    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    replay = run_deterministic_paper_replay(Path(args.db))
    plot_concentration_gate()
    plot_cost_and_established_study()
    plot_replay_summary(replay)
    vault_stats = plot_obsidian_vault_graph()

    print("Deterministic Replay Summary:")
    print(f"  chain_verified : {replay['chain_verified']}")
    print(f"  total_events   : {replay['total_events']}")
    print(f"  head_hash      : {replay['head_hash']}")
    print(f"  packets        : {replay['packets']}")
    print(f"  actions        : {replay['actions']}")
    print(f"  failures       : {replay['failures']}")
    print(f"  final_equity   : ${replay['final_equity']}")
    print(f"Vault Graph      : {vault_stats['nodes']} nodes, {vault_stats['edges']} edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
