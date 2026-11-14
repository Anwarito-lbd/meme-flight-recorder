"""Self-contained local Research Desk UI & state serializer (strictly paper-only).

Implements the **1 App** front door of the 5 Agents + 1 App + 1 Prompt desk
architecture. Renders an inspectable HTML dashboard for:
  - 5 Specialized Agents (Discovery, Virality, Adversarial, Risk Judge, Auditor)
  - Read-Only Bridge & Autonomy Levels (L0 / L1 / L2)
  - Single-Tap Paper Approve / Reject Draft controls (no live signing)
  - Live Paper Equity Curve SVG, Gate Rejection Pareto, & Desk Diary export
Uses zero external CDNs so offline verification and headless screenshots/videos
are 100% deterministic and self-contained.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .config import Settings
from .journal import FlightRecorder
from .paper import PaperBroker
from .research_team import AutonomyLevel, ReadOnlyBridge, generate_desk_diary


def build_desk_state(
    recorder: FlightRecorder,
    broker: PaperBroker,
    settings: Settings,
) -> dict[str, Any]:
    """Assemble the live JSON payload consumed by the Desk UI."""
    chain_ok = recorder.verify_chain()
    packets_raw = recorder.events_by_type("research_desk_packet")
    evals_raw = recorder.events_by_type("candidate_evaluated")
    opened_raw = recorder.events_by_type("paper_position_opened")
    closed_raw = recorder.events_by_type("paper_position_closed")
    draft_decisions_raw = recorder.events_by_type("human_draft_decision")
    recent_events = recorder.list_events(limit=25)
    head_hash = recent_events[0]["event_hash"] if recent_events else "GENESIS"

    decided_draft_hashes = {
        row["payload"].get("packet_event_hash") for row in draft_decisions_raw
    }

    action_counts = Counter(
        row["payload"].get("final_action", "UNKNOWN") for row in packets_raw
    )
    failure_counts: Counter[str] = Counter()
    for ev in evals_raw:
        for reason in ev["payload"].get("failures", []):
            failure_counts[reason] += 1

    closed_pnls = [float(c["payload"].get("pnl_usd", 0.0)) for c in closed_raw]
    realized_pnl = round(sum(closed_pnls), 4)
    gross_wins = sum(p for p in closed_pnls if p > 0)
    gross_losses = abs(sum(p for p in closed_pnls if p < 0))
    profit_factor = (
        round(gross_wins / gross_losses, 3)
        if gross_losses > 0
        else (round(gross_wins, 3) if gross_wins > 0 else 0.0)
    )
    wins = sum(1 for p in closed_pnls if p > 0)
    expectancy = round(realized_pnl / len(closed_pnls), 4) if closed_pnls else 0.0

    # Build chronological equity curve for inline SVG rendering
    equity_curve: list[dict[str, Any]] = [
        {"step": 0, "label": "Start", "equity_usd": round(settings.starting_equity_usd, 4)}
    ]
    running_eq = settings.starting_equity_usd
    for idx, c in enumerate(closed_raw, start=1):
        running_eq += float(c["payload"].get("pnl_usd", 0.0))
        equity_curve.append(
            {
                "step": idx,
                "label": str(c["payload"].get("asset_id", ""))[:8],
                "equity_usd": round(running_eq, 4),
            }
        )

    packets = []
    pending_drafts = []
    for row in reversed(packets_raw[-30:]):
        p = row["payload"]
        item = {
            "observed_at": row["observed_at"],
            "entity_id": p.get("entity_id"),
            "symbol": p.get("symbol"),
            "autonomy_level": p.get("autonomy_level", AutonomyLevel.L0_OBSERVE_ONLY.value),
            "final_action": p.get("final_action"),
            "discovery": p.get("discovery", {}),
            "virality": p.get("virality", {}),
            "investigation": p.get("investigation", {}),
            "risk": p.get("risk", {}),
            "paper_position_id": p.get("paper_position_id"),
            "human_approval_draft": p.get("human_approval_draft"),
            "event_hash": row["event_hash"],
        }
        packets.append(item)
        if (
            p.get("final_action") == "HUMAN_APPROVAL_DRAFT"
            and p.get("human_approval_draft") is not None
            and row["event_hash"] not in decided_draft_hashes
        ):
            pending_drafts.append(item)

    open_positions = [
        {
            "position_id": pos.position_id,
            "asset_id": pos.asset_id,
            "universe": pos.universe.value,
            "entry_price": pos.entry_price,
            "quantity": round(pos.quantity, 4),
            "position_value_usd": round(pos.entry_price * pos.quantity, 2),
            "risk_amount_usd": pos.risk_amount_usd,
            "fees_paid_usd": pos.fees_paid_usd,
            "opened_at": pos.opened_at,
        }
        for pos in broker.positions.values()
    ]

    closed_positions = [
        {
            "position_id": c["payload"].get("position_id"),
            "asset_id": c["payload"].get("asset_id"),
            "exit_price": c["payload"].get("exit_price"),
            "pnl_usd": c["payload"].get("pnl_usd"),
            "r_multiple": c["payload"].get("r_multiple"),
            "reason": c["payload"].get("reason"),
            "observed_at": c["observed_at"],
            "event_hash": c["event_hash"],
        }
        for c in reversed(closed_raw[-25:])
    ]

    return {
        "execution_mode": "paper",
        "autonomy_levels": [level.value for level in AutonomyLevel],
        "read_only_bridge": ReadOnlyBridge.manifest(),
        "chain_verified": chain_ok,
        "head_hash": head_hash,
        "starting_equity_usd": settings.starting_equity_usd,
        "current_equity_usd": round(settings.starting_equity_usd + realized_pnl, 4),
        "counts": {
            "packets": len(packets_raw),
            "evaluations": len(evals_raw),
            "paper_open_events": len(opened_raw),
            "open_positions": len(open_positions),
            "closed_positions": len(closed_raw),
            "pending_drafts": len(pending_drafts),
            "wins": wins,
            "actions": dict(action_counts),
        },
        "performance": {
            "realized_pnl_usd": realized_pnl,
            "expectancy_usd": expectancy,
            "profit_factor": profit_factor,
            "promotion_target_trades": 30,
        },
        "equity_curve": equity_curve,
        "rejection_pareto": [
            {"reason": reason, "count": count}
            for reason, count in failure_counts.most_common(10)
        ],
        "pending_drafts": pending_drafts,
        "packets": packets,
        "open_positions": open_positions,
        "closed_positions": closed_positions,
        "diary_markdown": generate_desk_diary(recorder),
    }


def render_desk_html() -> str:
    """Return the responsive, zero-dependency Figma-grade Desk UI."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Meme Flight Recorder — 5-Agent Paper Research Desk</title>
  <style>
    :root {
      --canvas: #090c10;
      --surface: #10151d;
      --well: #161d28;
      --row-hover: #1a2330;
      --border: #1e2837;
      --border-strong: #2b3a4f;
      --text: #edf2f7;
      --secondary: #a3b3c6;
      --muted: #6f8197;
      --accent: #38bdf8;
      --accent-soft: rgba(56, 189, 248, 0.12);
      --good: #10b981;
      --good-soft: rgba(16, 185, 129, 0.12);
      --warn: #f59e0b;
      --warn-soft: rgba(245, 158, 11, 0.12);
      --bad: #ef4444;
      --bad-soft: rgba(239, 68, 68, 0.12);
      --violet: #a78bfa;
      --mono: ui-monospace, "JetBrains Mono", SFMono-Regular, Menlo, Consolas, monospace;
      --sans: "Inter", system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      background: var(--canvas);
      color: var(--text);
      font-family: var(--sans);
      font-feature-settings: "cv02", "cv03", "cv04", "cv11";
      line-height: 1.45;
      -webkit-font-smoothing: antialiased;
    }
    .tnum { font-variant-numeric: tabular-nums; }
    .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
    .muted { color: var(--muted); }
    .wrap {
      max-width: 1400px;
      margin: 0 auto;
      padding: 16px 18px 36px;
    }
    /* Sticky Command Bar */
    .topbar {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      padding: 13px 16px;
      background: rgba(16, 21, 29, 0.94);
      backdrop-filter: blur(8px);
      border: 1px solid var(--border);
      border-radius: 12px;
      margin-bottom: 12px;
    }
    .brand-row {
      display: flex;
      align-items: center;
      gap: 11px;
    }
    .brand-mark {
      width: 32px;
      height: 32px;
      border-radius: 8px;
      background: linear-gradient(135deg, rgba(56,189,248,0.22), rgba(16,185,129,0.18));
      border: 1px solid var(--border-strong);
      display: grid;
      place-items: center;
      font-family: var(--mono);
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--accent);
      flex-shrink: 0;
    }
    .title h1 {
      margin: 0;
      font-size: 1.05rem;
      font-weight: 650;
      letter-spacing: -0.015em;
    }
    .title p {
      margin: 2px 0 0;
      color: var(--secondary);
      font-size: 0.77rem;
    }
    .badges { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-family: var(--mono);
      font-size: 0.7rem;
      font-weight: 500;
      padding: 4px 9px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--well);
      color: var(--secondary);
      white-space: nowrap;
    }
    .badge .dot {
      width: 6px;
      height: 6px;
      border-radius: 999px;
      background: var(--muted);
    }
    .badge.good { border-color: rgba(16, 185, 129, 0.35); background: var(--good-soft); color: #6ee7b7; }
    .badge.good .dot { background: var(--good); }
    .badge.bad { border-color: rgba(239, 68, 68, 0.4); background: var(--bad-soft); color: #fca5a5; }
    .badge.bad .dot { background: var(--bad); }
    .badge.accent { border-color: rgba(56, 189, 248, 0.38); background: var(--accent-soft); color: #7dd3fc; }
    .badge.accent .dot { background: var(--accent); }

    /* 5-Agent Pipeline Strip */
    .pipeline {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 12px;
    }
    .agent-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 12px;
      position: relative;
      transition: border-color 0.15s ease;
    }
    .agent-card:hover { border-color: var(--border-strong); }
    .agent-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      margin-bottom: 4px;
    }
    .agent-card .step {
      font-family: var(--mono);
      font-size: 0.66rem;
      font-weight: 600;
      color: var(--accent);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .agent-role {
      font-family: var(--mono);
      font-size: 0.63rem;
      padding: 1px 6px;
      border-radius: 4px;
      background: var(--well);
      border: 1px solid var(--border);
      color: var(--muted);
    }
    .agent-card h2 {
      margin: 0 0 3px;
      font-size: 0.84rem;
      font-weight: 600;
      color: var(--text);
    }
    .agent-card p {
      margin: 0;
      color: var(--secondary);
      font-size: 0.72rem;
      line-height: 1.38;
    }

    /* Compact KPI Telemetry Strip */
    .kpis {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 8px;
      margin-bottom: 12px;
    }
    .kpi {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 12px;
    }
    .kpi .label {
      color: var(--muted);
      font-family: var(--mono);
      font-size: 0.67rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .kpi .val {
      margin-top: 3px;
      font-family: var(--mono);
      font-size: 1.12rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      font-variant-numeric: tabular-nums;
    }
    .kpi .sub {
      margin-top: 1px;
      font-size: 0.69rem;
      color: var(--secondary);
    }

    /* Main Workstation 2-Column Split */
    .grid-main {
      display: grid;
      grid-template-columns: 1.42fr 0.58fr;
      gap: 12px;
      margin-bottom: 12px;
      align-items: start;
    }
    .grid-bottom {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      align-items: start;
    }
    .panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
      overflow: hidden;
    }
    .panel-head {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }
    .panel h3 {
      margin: 0;
      font-size: 0.88rem;
      font-weight: 600;
      color: var(--text);
      letter-spacing: -0.01em;
    }
    .subpanel-title {
      margin: 14px 0 8px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      font-size: 0.84rem;
      font-weight: 600;
    }

    /* Filter Toolbar */
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 10px;
    }
    .seg-group {
      display: inline-flex;
      flex-wrap: wrap;
      gap: 4px;
      background: var(--canvas);
      border: 1px solid var(--border);
      padding: 3px;
      border-radius: 8px;
    }
    .seg-btn {
      font-family: var(--mono);
      font-size: 0.69rem;
      padding: 4px 8px;
      border-radius: 5px;
      border: 0;
      background: transparent;
      color: var(--secondary);
      cursor: pointer;
      white-space: nowrap;
    }
    .seg-btn.active {
      background: var(--well);
      color: var(--text);
      box-shadow: inset 0 0 0 1px var(--border-strong);
    }
    .search-input {
      background: var(--canvas);
      border: 1px solid var(--border);
      border-radius: 7px;
      color: var(--text);
      font-family: var(--mono);
      font-size: 0.72rem;
      padding: 5px 9px;
      min-width: 170px;
      outline: none;
    }
    .search-input:focus { border-color: var(--accent); }

    /* Compact Workstation Table */
    .table-wrap {
      overflow-x: auto;
      max-height: 540px;
      overflow-y: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--canvas);
    }
    .table-wrap-sm {
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--canvas);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.77rem;
    }
    th, td {
      text-align: left;
      padding: 7px 9px;
      border-bottom: 1px solid var(--border);
      vertical-align: middle;
    }
    thead th {
      position: sticky;
      top: 0;
      z-index: 2;
      background: var(--well);
      color: var(--muted);
      font-family: var(--mono);
      font-weight: 600;
      font-size: 0.66rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    tbody tr:hover { background: var(--row-hover); }
    tbody tr:last-child td { border-bottom: 0; }

    /* Semantic Pills */
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-family: var(--mono);
      font-size: 0.67rem;
      font-weight: 500;
      padding: 2px 7px;
      border-radius: 5px;
      background: var(--well);
      border: 1px solid var(--border);
      white-space: nowrap;
    }
    .pill.pass, .pill.PAPER_OPEN {
      color: #6ee7b7;
      background: var(--good-soft);
      border-color: rgba(16, 185, 129, 0.35);
    }
    .pill.warn, .pill.MONITOR_ONLY, .pill.HUMAN_APPROVAL_DRAFT {
      color: #fcd34d;
      background: var(--warn-soft);
      border-color: rgba(245, 158, 11, 0.38);
    }
    .pill.fail, .pill.REJECTED, .pill.RISK_REJECTED {
      color: #fca5a5;
      background: var(--bad-soft);
      border-color: rgba(239, 68, 68, 0.35);
    }
    .pill.unknown { color: #cbd5e1; border-color: var(--border-strong); }

    /* Compact Inline Gate Matrix (AUTH CONC ID LIQ) */
    .gate-strip {
      display: inline-flex;
      gap: 4px;
      align-items: center;
    }
    .gchip {
      font-family: var(--mono);
      font-size: 0.64rem;
      padding: 2px 5px;
      border-radius: 4px;
      border: 1px solid var(--border);
      background: var(--well);
    }
    .gchip.pass { color: #6ee7b7; border-color: rgba(16, 185, 129, 0.32); }
    .gchip.warn { color: #fcd34d; border-color: rgba(245, 158, 11, 0.35); }
    .gchip.fail { color: #fca5a5; border-color: rgba(239, 68, 68, 0.45); background: var(--bad-soft); }

    /* Draft Card */
    .draft-card {
      background: var(--well);
      border: 1px solid rgba(245, 158, 11, 0.42);
      border-radius: 9px;
      padding: 10px 12px;
      margin-bottom: 8px;
    }
    .draft-actions {
      display: flex;
      gap: 7px;
      margin-top: 8px;
      flex-wrap: wrap;
    }
    button.btn {
      font-family: var(--mono);
      font-size: 0.71rem;
      font-weight: 600;
      padding: 6px 10px;
      border-radius: 6px;
      border: 1px solid var(--border-strong);
      cursor: pointer;
      color: var(--text);
      background: var(--surface);
    }
    button.btn.approve {
      border-color: rgba(16, 185, 129, 0.5);
      background: var(--good-soft);
      color: #6ee7b7;
    }
    button.btn.reject {
      border-color: rgba(239, 68, 68, 0.45);
      background: var(--bad-soft);
      color: #fca5a5;
    }

    /* Bars & Equity SVG */
    .bar-row { margin-bottom: 7px; }
    .bar-meta {
      display: flex;
      justify-content: space-between;
      font-family: var(--mono);
      font-size: 0.7rem;
      color: var(--secondary);
      margin-bottom: 3px;
    }
    .bar-track {
      height: 6px;
      border-radius: 999px;
      background: var(--well);
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--accent), var(--violet));
    }
    .chart-box {
      border: 1px solid var(--border);
      background: var(--canvas);
      border-radius: 8px;
      padding: 8px;
    }
    svg.eq { width: 100%; height: 145px; display: block; }

    /* Empirical Calibration Strip */
    .calib-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 7px;
      margin-top: 8px;
    }
    .calib-item {
      border: 1px solid var(--border);
      background: var(--canvas);
      border-radius: 7px;
      padding: 7px 9px;
    }
    .calib-item .ck {
      font-family: var(--mono);
      font-size: 0.65rem;
      color: var(--muted);
      text-transform: uppercase;
    }
    .calib-item .cv {
      font-family: var(--mono);
      font-size: 0.8rem;
      font-weight: 600;
      margin-top: 2px;
      color: var(--text);
    }

    .bridge-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-bottom: 12px;
    }
    /* Formatted Desk Diary */
    .diary-view {
      border: 1px solid var(--border);
      background: var(--canvas);
      border-radius: 8px;
      padding: 12px 14px;
      max-height: 315px;
      overflow-y: auto;
      font-size: 0.77rem;
      color: var(--secondary);
    }
    .diary-view h4 {
      margin: 0 0 8px;
      font-size: 0.84rem;
      color: var(--text);
      border-bottom: 1px solid var(--border);
      padding-bottom: 5px;
    }
    .diary-view ul { margin: 4px 0 8px 16px; padding: 0; }
    .diary-view li { margin-bottom: 3px; }
    .diary-view code {
      font-family: var(--mono);
      font-size: 0.71rem;
      background: var(--well);
      padding: 1px 4px;
      border-radius: 4px;
      color: var(--accent);
    }

    @media (max-width: 1120px) {
      .pipeline { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .grid-main, .grid-bottom { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .wrap { padding: 10px 10px 26px; }
      .pipeline, .calib-grid { grid-template-columns: 1fr; }
      .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .topbar { padding: 11px; }
      .title h1 { font-size: 0.96rem; }
      .panel { padding: 11px; }
      table { font-size: 0.7rem; }
      th, td { padding: 6px; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header class="topbar">
      <div class="brand-row">
        <div class="brand-mark">MFR</div>
        <div class="title">
          <h1>Meme Flight Recorder — 5-Agent Paper Research Desk</h1>
          <p>One Front Door · 5-Agent Decision Layer · Read-Only Bridge · Autonomy L0 / L1 / L2 (Zero Live Signing)</p>
        </div>
      </div>
      <div class="badges">
        <span class="badge accent" id="mode-badge"><span class="dot"></span>PAPER ONLY · NO SIGNING</span>
        <span class="badge good" id="chain-badge"><span class="dot"></span>CHAIN: VERIFYING...</span>
        <span class="badge" id="bridge-badge"><span class="dot"></span>BRIDGE: READ-ONLY</span>
        <span class="badge" id="head-badge">HEAD: ...</span>
      </div>
    </header>

    <section class="pipeline" aria-label="Five Specialized Agents">
      <div class="agent-card">
        <div class="agent-head"><span class="step">Agent 01</span><span class="agent-role">DISCOVERY</span></div>
        <h2>Discovery &amp; Trend Scout</h2>
        <p>Tags read-only provenance, pool tier (&ge;$50k), and base58 mint identity.</p>
      </div>
      <div class="agent-card">
        <div class="agent-head"><span class="step">Agent 02</span><span class="agent-role">ADVISORY</span></div>
        <h2>Virality &amp; Narrative Analyst</h2>
        <p>Scores Reddit/TG/News velocity &amp; spam share. Advisory only; cannot waive gates.</p>
      </div>
      <div class="agent-card">
        <div class="agent-head"><span class="step">Agent 03</span><span class="agent-role">FAIL-CLOSED</span></div>
        <h2>Adversarial On-Chain Investigator</h2>
        <p>Audits mint/freeze authority, top-10 concentration, dev-sell &amp; exit simulation.</p>
      </div>
      <div class="agent-card">
        <div class="agent-head"><span class="step">Agent 04</span><span class="agent-role">RISK JUDGE</span></div>
        <h2>Risk &amp; Decision Judge</h2>
        <p>Enforces equity-tier sizing, slot caps, RT cost ceiling &amp; L0/L1/L2 rules.</p>
      </div>
      <div class="agent-card">
        <div class="agent-head"><span class="step">Agent 05</span><span class="agent-role">IMMUTABLE</span></div>
        <h2>Flight Recorder Auditor</h2>
        <p>Appends SHA-256 hash-chained packets &amp; compiles the Nightly Desk Diary.</p>
      </div>
    </section>

    <section class="kpis" id="kpi-grid"></section>

    <section class="grid-main">
      <div class="panel">
        <div class="panel-head">
          <h3>5-Agent Research Packets</h3>
          <span class="muted mono" id="packet-count" style="font-size:0.74rem">0 packets</span>
        </div>
        <div class="toolbar">
          <div class="seg-group" id="action-filters" role="tablist" aria-label="Filter packets"></div>
          <input id="packet-search" class="search-input" type="search" placeholder="Filter token or reason..." aria-label="Filter packets" />
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Token</th>
                <th>Action</th>
                <th>Virality</th>
                <th>Adversarial Gates</th>
                <th>Risk Judge</th>
                <th>SHA-256</th>
              </tr>
            </thead>
            <tbody id="packets-body"></tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>L2 Human-Approval Drafts</h3>
          <span class="muted mono" id="draft-count" style="font-size:0.74rem">0 pending</span>
        </div>
        <div id="drafts-box"></div>

        <div class="subpanel-title">
          <span>Paper Equity Curve</span>
          <span class="muted mono" id="eq-summary" style="font-size:0.74rem">$50.00</span>
        </div>
        <div class="chart-box" id="eq-chart"></div>

        <div class="subpanel-title">
          <span>Gate Rejection Pareto</span>
          <span class="muted mono" style="font-size:0.72rem">Fail-Closed</span>
        </div>
        <div id="pareto-list"></div>

        <div class="subpanel-title">
          <span>Empirical Guardrails (Study L &amp; C)</span>
          <span class="muted mono" style="font-size:0.72rem">Calibrated</span>
        </div>
        <div class="calib-grid">
          <div class="calib-item">
            <div class="ck">Study L Survival</div>
            <div class="cv" style="color:#fcd34d">0.58% <span class="muted" style="font-size:0.68rem">(25.5% 2x tail)</span></div>
          </div>
          <div class="calib-item">
            <div class="ck">Study C Coverage</div>
            <div class="cv" style="color:#6ee7b7">63.4% <span class="muted" style="font-size:0.68rem">matched panel</span></div>
          </div>
          <div class="calib-item">
            <div class="ck">Holder Gate Split</div>
            <div class="cv">55% <span class="muted" style="font-size:0.68rem">obs</span> vs 38% <span class="muted" style="font-size:0.68rem">pass</span></div>
          </div>
          <div class="calib-item">
            <div class="ck">Established Baseline</div>
            <div class="cv" style="color:#fca5a5">PF 0.233 <span class="muted" style="font-size:0.68rem">(halted)</span></div>
          </div>
        </div>
      </div>
    </section>

    <section class="grid-bottom">
      <div class="panel">
        <div class="panel-head">
          <h3>Read-Only Bridge (Adapters &amp; Reference Patterns)</h3>
          <span class="muted mono" style="font-size:0.72rem">write_capable=false</span>
        </div>
        <div class="bridge-grid" id="bridge-list"></div>

        <div class="subpanel-title">
          <span>Paper Book Closures</span>
          <span class="muted mono" id="closed-count" style="font-size:0.74rem">0 closed</span>
        </div>
        <div class="table-wrap-sm">
          <table>
            <thead>
              <tr><th>Asset</th><th>Reason</th><th>PnL</th><th>R-Mult</th></tr>
            </thead>
            <tbody id="closed-body"></tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>Nightly / Weekly Desk Diary</h3>
          <span class="muted mono" style="font-size:0.72rem">/api/desk/diary</span>
        </div>
        <div class="diary-view" id="diary-box">Loading desk diary...</div>
      </div>
    </section>
  </div>

  <script>
    let allPackets = [];
    let activeFilter = "ALL";
    let searchQuery = "";

    function esc(v) {
      return String(v ?? "").replace(/[&<>"]/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"
      }[c]));
    }

    function renderEquitySvg(curve) {
      if (!curve || curve.length < 2) {
        return '<p class="muted" style="font-size:0.78rem;margin:8px">No closed paper trades yet.</p>';
      }
      const vals = curve.map((d) => Number(d.equity_usd));
      const startEq = vals[0];
      const rawMin = Math.min(...vals);
      const rawMax = Math.max(...vals);
      const padV = Math.max(0.5, (rawMax - rawMin) * 0.14);
      const minV = rawMin - padV;
      const maxV = rawMax + padV;
      const span = Math.max(0.01, maxV - minV);
      const w = 390, h = 145, padL = 44, padR = 12, padT = 12, padB = 22;
      const plotW = w - padL - padR;
      const plotH = h - padT - padB;
      const coords = curve.map((d, i) => {
        const x = padL + (i * plotW) / Math.max(1, curve.length - 1);
        const y = padT + plotH - ((Number(d.equity_usd) - minV) * plotH) / span;
        return { x, y, step: d.step, label: d.label, eq: Number(d.equity_usd) };
      });
      const poly = coords.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
      const area = `M ${coords[0].x.toFixed(1)},${(padT + plotH).toFixed(1)} ` +
        coords.map((p) => `L ${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ") +
        ` L ${coords[coords.length - 1].x.toFixed(1)},${(padT + plotH).toFixed(1)} Z`;
      const baseY = padT + plotH - ((startEq - minV) * plotH) / span;
      const midV = (minV + maxV) / 2;
      const dots = coords.map((p) =>
        `<circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="3" fill="#38bdf8" stroke="#090c10" stroke-width="1.2"><title>Step ${p.step} (${esc(p.label)}): $${p.eq.toFixed(2)}</title></circle>`
      ).join("");
      return `<svg class="eq" viewBox="0 0 ${w} ${h}" role="img" aria-label="Paper equity curve">
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.28" />
            <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.01" />
          </linearGradient>
        </defs>
        <line x1="${padL}" y1="${padT}" x2="${w - padR}" y2="${padT}" stroke="#1e2837" stroke-dasharray="2 3" />
        <line x1="${padL}" y1="${(padT + plotH / 2).toFixed(1)}" x2="${w - padR}" y2="${(padT + plotH / 2).toFixed(1)}" stroke="#1e2837" stroke-dasharray="2 3" />
        <line x1="${padL}" y1="${baseY.toFixed(1)}" x2="${w - padR}" y2="${baseY.toFixed(1)}" stroke="#6f8197" stroke-dasharray="4 3" stroke-width="1" />
        <path d="${area}" fill="url(#eqGrad)" />
        <polyline fill="none" stroke="#38bdf8" stroke-width="2.2" stroke-linejoin="round" points="${poly}" />
        ${dots}
        <text x="${padL - 5}" y="${padT + 4}" text-anchor="end" font-size="9" fill="#6f8197" font-family="monospace">$${maxV.toFixed(1)}</text>
        <text x="${padL - 5}" y="${(padT + plotH / 2 + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="#6f8197" font-family="monospace">$${midV.toFixed(1)}</text>
        <text x="${padL - 5}" y="${padT + plotH}" text-anchor="end" font-size="9" fill="#6f8197" font-family="monospace">$${minV.toFixed(1)}</text>
        <text x="${padL}" y="${h - 5}" font-size="9" fill="#6f8197" font-family="monospace">Start $${startEq.toFixed(0)}</text>
        <text x="${w - padR}" y="${h - 5}" text-anchor="end" font-size="9" fill="#38bdf8" font-family="monospace">Now $${vals[vals.length - 1].toFixed(2)}</text>
      </svg>`;
    }

    function formatDiaryHtml(md) {
      const lines = String(md || "").split(/\\r?\\n/);
      let out = [];
      let inList = false;
      let inTable = false;
      for (const raw of lines) {
        const line = raw.trim();
        if (!line) {
          if (inList) { out.push("</ul>"); inList = false; }
          if (inTable) { out.push("</tbody></table></div>"); inTable = false; }
          continue;
        }
        const fmt = esc(line)
          .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>")
          .replace(/`([^`]+)`/g, "<code>$1</code>");
        if (line.startsWith("|") && line.endsWith("|")) {
          if (inList) { out.push("</ul>"); inList = false; }
          const cells = line.slice(1, -1).split("|").map((c) => c.trim());
          if (cells.every((c) => /^-+$/.test(c.replace(/:/g, "")))) {
            continue;
          }
          if (!inTable) {
            out.push('<div class="table-wrap-sm" style="margin:6px 0"><table><thead><tr>' +
              cells.map((c) => `<th>${esc(c)}</th>`).join("") +
              "</tr></thead><tbody>");
            inTable = true;
          } else {
            out.push("<tr>" + cells.map((c) => `<td class="mono">${esc(c)}</td>`).join("") + "</tr>");
          }
          continue;
        }
        if (inTable) { out.push("</tbody></table></div>"); inTable = false; }
        if (line.startsWith("# ") || line.startsWith("## ")) {
          if (inList) { out.push("</ul>"); inList = false; }
          out.push(`<h4>${fmt.replace(/^#+\\s*/, "")}</h4>`);
        } else if (line.startsWith("- ")) {
          if (!inList) { out.push("<ul>"); inList = true; }
          out.push(`<li>${fmt.slice(2)}</li>`);
        } else {
          if (inList) { out.push("</ul>"); inList = false; }
          out.push(`<p style="margin:3px 0">${fmt}</p>`);
        }
      }
      if (inList) out.push("</ul>");
      if (inTable) out.push("</tbody></table></div>");
      return out.join("") || '<p class="muted">No diary entries recorded.</p>';
    }

    function renderPacketsTable() {
      const q = searchQuery.trim().toLowerCase();
      const filtered = allPackets.filter((p) => {
        if (activeFilter !== "ALL" && p.final_action !== activeFilter) return false;
        if (!q) return true;
        const rsk = p.risk || {};
        return String(p.symbol || "").toLowerCase().includes(q) ||
               String(p.final_action || "").toLowerCase().includes(q) ||
               String(rsk.reason || "").toLowerCase().includes(q);
      });
      document.getElementById("packet-count").textContent = `${filtered.length} / ${allPackets.length} packets`;
      const gateTag = { authority: "AUTH", concentration: "CONC", identity: "ID", liquidity: "LIQ" };
      document.getElementById("packets-body").innerHTML = filtered.map((p) => {
        const vir = p.virality || {};
        const inv = p.investigation || {};
        const rsk = p.risk || {};
        const gates = Object.entries(inv.gate_breakdown || {}).map(([k, st]) => {
          const shortK = gateTag[k] || k.split("_")[0].slice(0, 4).toUpperCase();
          const sym = st === "pass" ? "✓" : st === "warn" ? "!" : "✕";
          return `<span class="gchip ${esc(st)}" style="white-space:nowrap" title="${esc(k)}: ${esc(st)}">${esc(shortK)} ${sym}</span>`;
        }).join("");
        const tier = String(p.autonomy_level || "").split("_")[0] || "L0";
        const riskTxt = rsk.approved
          ? `APPROVED $${Number(rsk.position_value_usd).toFixed(2)} (${Number(rsk.estimated_round_trip_cost_pct || 0).toFixed(1)}%)`
          : esc(rsk.reason || "blocked");
        return `<tr>
          <td><strong>${esc(p.symbol || "?")}</strong> <span class="pill" style="padding:1px 5px;font-size:0.62rem">${esc(tier)}</span></td>
          <td><span class="pill ${esc(p.final_action)}">${esc(p.final_action)}</span></td>
          <td><span class="mono">${Number(vir.momentum_score || 0).toFixed(1)}</span> <span class="muted" style="font-size:0.69rem">${esc(vir.advisory_stance)}</span></td>
          <td><div class="gate-strip">${gates}</div></td>
          <td class="mono" style="font-size:0.71rem;color:${rsk.approved ? '#6ee7b7' : '#a3b3c6'}">${riskTxt}</td>
          <td class="mono muted" style="font-size:0.7rem">${esc(String(p.event_hash || "").slice(0, 8))}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="6" class="muted">No matching packets.</td></tr>`;
    }

    async function decideDraft(eventHash, decision) {
      await fetch("/api/desk/decision", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ packet_event_hash: eventHash, decision })
      });
      await loadDesk();
    }

    async function loadDesk() {
      const res = await fetch("/api/desk/state");
      const data = await res.json();

      const chainBadge = document.getElementById("chain-badge");
      chainBadge.innerHTML = `<span class="dot"></span>` + (data.chain_verified ? "CHAIN: VERIFIED" : "CHAIN: CORRUPTED");
      chainBadge.className = "badge " + (data.chain_verified ? "good" : "bad");
      document.getElementById("head-badge").textContent =
        "HEAD: " + String(data.head_hash || "GENESIS").slice(0, 10);

      const pnlVal = Number(data.performance.realized_pnl_usd);
      const kpis = [
        ["Packets Audited", data.counts.packets, "SHA-256 linked"],
        ["Open Paper", data.counts.open_positions, "L1 autonomous"],
        ["Closed Trades", `${data.counts.closed_positions} / ${data.performance.promotion_target_trades}`, "Promotion bar: 30"],
        ["Realized PnL", `${pnlVal >= 0 ? "+" : ""}$${pnlVal.toFixed(2)}`, "Paper ledger only"],
        ["Profit Factor", Number(data.performance.profit_factor).toFixed(2), "Gate requires >= 1.4"],
        ["Paper Equity", `$${Number(data.current_equity_usd).toFixed(2)}`, `Start $${Number(data.starting_equity_usd).toFixed(0)}`]
      ];
      document.getElementById("kpi-grid").innerHTML = kpis.map(([k, v, sub]) =>
        `<div class="kpi"><div class="label">${esc(k)}</div><div class="val">${esc(v)}</div><div class="sub">${esc(sub)}</div></div>`
      ).join("");

      allPackets = data.packets || [];
      const actions = data.counts.actions || {};
      const tabs = [
        ["ALL", `ALL (${allPackets.length})`],
        ["HUMAN_APPROVAL_DRAFT", `DRAFTS (${actions.HUMAN_APPROVAL_DRAFT || 0})`],
        ["PAPER_OPEN", `OPEN (${actions.PAPER_OPEN || 0})`],
        ["REJECTED", `REJECTED (${(actions.REJECTED || 0) + (actions.RISK_REJECTED || 0)})`],
        ["MONITOR_ONLY", `MONITOR (${actions.MONITOR_ONLY || 0})`]
      ];
      document.getElementById("action-filters").innerHTML = tabs.map(([key, label]) =>
        `<button type="button" class="seg-btn ${activeFilter === key ? "active" : ""}" data-filter="${esc(key)}">${esc(label)}</button>`
      ).join("");
      document.querySelectorAll("#action-filters .seg-btn").forEach((btn) => {
        btn.onclick = () => {
          activeFilter = btn.getAttribute("data-filter") || "ALL";
          document.querySelectorAll("#action-filters .seg-btn").forEach((b) => b.classList.toggle("active", b === btn));
          renderPacketsTable();
        };
      });
      renderPacketsTable();

      const drafts = data.pending_drafts || [];
      document.getElementById("draft-count").textContent = `${drafts.length} pending`;
      document.getElementById("drafts-box").innerHTML = drafts.map((d) => {
        const dr = d.human_approval_draft || {};
        return `<div class="draft-card">
          <div class="mono" style="font-size:0.78rem"><strong>${esc(dr.symbol)}</strong> · $${Number(dr.proposed_position_usd || 0).toFixed(2)} paper stake · RT cost ${Number(dr.estimated_round_trip_cost_pct || 0).toFixed(2)}%</div>
          <div class="muted" style="font-size:0.71rem;margin-top:2px">L2 Draft — requires explicit human tap (paper-only, zero live signing)</div>
          <div class="draft-actions">
            <button class="btn approve" data-action="approve" onclick="decideDraft('${esc(d.event_hash)}', 'approve')">Tap: Paper Approve</button>
            <button class="btn reject" data-action="reject" onclick="decideDraft('${esc(d.event_hash)}', 'reject')">Reject Draft</button>
          </div>
        </div>`;
      }).join("") || `<p class="muted" style="font-size:0.78rem;margin:4px 0">No pending L2 drafts.</p>`;

      document.getElementById("eq-summary").textContent =
        `$${Number(data.starting_equity_usd).toFixed(2)} -> $${Number(data.current_equity_usd).toFixed(2)}`;
      document.getElementById("eq-chart").innerHTML = renderEquitySvg(data.equity_curve);

      const pareto = data.rejection_pareto || [];
      const maxCount = Math.max(1, ...pareto.map((r) => r.count));
      document.getElementById("pareto-list").innerHTML = pareto.map((r) => `
        <div class="bar-row">
          <div class="bar-meta"><span>${esc(r.reason)}</span><span>${r.count}</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:${(100 * r.count / maxCount).toFixed(1)}%"></div></div>
        </div>
      `).join("") || `<p class="muted" style="font-size:0.78rem">No rejections recorded.</p>`;

      const br = data.read_only_bridge || {};
      const adapters = [...(br.internal_adapters || []), ...(br.ecosystem_references || [])];
      document.getElementById("bridge-list").innerHTML = adapters.map((a) =>
        `<span class="pill pass" title="${esc(a.role)}">${esc(a.name)} (RO)</span>`
      ).join("");

      document.getElementById("closed-count").textContent = `${data.closed_positions.length} closed`;
      document.getElementById("closed-body").innerHTML = data.closed_positions.map((c) => {
        const pnl = Number(c.pnl_usd || 0);
        return `<tr>
          <td class="mono">${esc(String(c.asset_id || "").slice(0, 10))}</td>
          <td class="mono">${esc(c.reason)}</td>
          <td class="mono" style="color:${pnl >= 0 ? "#6ee7b7" : "#fca5a5"}">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(3)}</td>
          <td class="mono">${Number(c.r_multiple || 0).toFixed(2)}R</td>
        </tr>`;
      }).join("") || `<tr><td colspan="4" class="muted">No closed trades yet.</td></tr>`;

      document.getElementById("diary-box").innerHTML = formatDiaryHtml(data.diary_markdown);
    }

    document.getElementById("packet-search").addEventListener("input", (e) => {
      searchQuery = e.target.value || "";
      renderPacketsTable();
    });

    loadDesk().catch((err) => {
      document.getElementById("diary-box").textContent = "Failed to load desk state: " + err;
    });
  </script>
</body>
</html>
"""
