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
    """Return the responsive, zero-dependency HTML/CSS/JS Desk UI."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Meme Flight Recorder — 5-Agent Paper Research Desk</title>
  <style>
    :root {
      --bg: #0b1017;
      --panel: #121924;
      --panel-alt: #172131;
      --border: #253247;
      --text: #e6edf7;
      --muted: #8ea0b8;
      --accent: #38bdf8;
      --good: #22c55e;
      --warn: #f59e0b;
      --bad: #ef4444;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      --sans: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--sans);
      background: radial-gradient(circle at top right, #162235, var(--bg) 52%);
      color: var(--text);
      line-height: 1.45;
    }
    .wrap {
      max-width: 1360px;
      margin: 0 auto;
      padding: 20px 16px 40px;
    }
    .topbar {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      padding: 16px 18px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      margin-bottom: 14px;
    }
    .title h1 { margin: 0; font-size: 1.25rem; letter-spacing: -0.01em; }
    .title p { margin: 4px 0 0; color: var(--muted); font-size: 0.84rem; }
    .badges { display: flex; flex-wrap: wrap; gap: 8px; }
    .badge {
      font-family: var(--mono);
      font-size: 0.73rem;
      padding: 5px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--panel-alt);
      white-space: nowrap;
    }
    .badge.good { border-color: rgba(34, 197, 94, 0.45); color: #86efac; }
    .badge.bad { border-color: rgba(239, 68, 68, 0.45); color: #fca5a5; }
    .badge.accent { border-color: rgba(56, 189, 248, 0.45); color: #7dd3fc; }
    .pipeline {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .agent-card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 13px;
    }
    .agent-card .step {
      font-family: var(--mono);
      font-size: 0.7rem;
      color: var(--accent);
      text-transform: uppercase;
    }
    .agent-card h2 { margin: 3px 0 4px; font-size: 0.92rem; }
    .agent-card p { margin: 0; color: var(--muted); font-size: 0.77rem; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .kpi {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 11px 13px;
    }
    .kpi .label {
      color: var(--muted);
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .kpi .val {
      margin-top: 4px;
      font-family: var(--mono);
      font-size: 1.15rem;
      font-weight: 700;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1.35fr 0.65fr;
      gap: 14px;
      margin-bottom: 14px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 15px;
      overflow: hidden;
    }
    .panel h3 {
      margin: 0 0 11px;
      font-size: 0.96rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }
    .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    table { width: 100%; border-collapse: collapse; font-size: 0.81rem; }
    th, td {
      text-align: left;
      padding: 8px 7px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-weight: 600;
      font-size: 0.73rem;
      text-transform: uppercase;
    }
    .pill {
      display: inline-block;
      font-family: var(--mono);
      font-size: 0.7rem;
      padding: 2px 7px;
      border-radius: 6px;
      background: var(--panel-alt);
      border: 1px solid var(--border);
      white-space: nowrap;
    }
    .pill.pass, .pill.PAPER_OPEN { color: #86efac; border-color: rgba(34, 197, 94, 0.45); }
    .pill.warn, .pill.MONITOR_ONLY, .pill.HUMAN_APPROVAL_DRAFT {
      color: #fcd34d;
      border-color: rgba(245, 158, 11, 0.45);
    }
    .pill.fail, .pill.REJECTED, .pill.RISK_REJECTED {
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.45);
    }
    .pill.unknown { color: #cbd5e1; border-color: rgba(148, 163, 184, 0.45); }
    .draft-card {
      background: var(--panel-alt);
      border: 1px solid rgba(245, 158, 11, 0.45);
      border-radius: 9px;
      padding: 11px 12px;
      margin-bottom: 10px;
    }
    .draft-actions {
      display: flex;
      gap: 8px;
      margin-top: 8px;
      flex-wrap: wrap;
    }
    button.btn {
      font-family: var(--mono);
      font-size: 0.74rem;
      padding: 6px 11px;
      border-radius: 7px;
      border: 1px solid var(--border);
      cursor: pointer;
      color: var(--text);
      background: var(--panel);
    }
    button.btn.approve {
      border-color: rgba(34, 197, 94, 0.55);
      background: rgba(34, 197, 94, 0.14);
      color: #86efac;
    }
    button.btn.reject {
      border-color: rgba(239, 68, 68, 0.55);
      background: rgba(239, 68, 68, 0.14);
      color: #fca5a5;
    }
    .bar-row { margin-bottom: 8px; }
    .bar-meta {
      display: flex;
      justify-content: space-between;
      font-family: var(--mono);
      font-size: 0.74rem;
      margin-bottom: 3px;
    }
    .bar-track {
      height: 8px;
      border-radius: 999px;
      background: var(--panel-alt);
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--accent), #818cf8);
    }
    .bridge-grid {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 12px;
    }
    pre.diary {
      margin: 0;
      padding: 12px;
      border-radius: 8px;
      background: #080c12;
      border: 1px solid var(--border);
      color: #cbd5e1;
      font-family: var(--mono);
      font-size: 0.74rem;
      max-height: 280px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .gates-mini { display: flex; flex-wrap: wrap; gap: 4px; }
    .mono { font-family: var(--mono); }
    .muted { color: var(--muted); }
    svg.eq { width: 100%; height: 120px; display: block; }
    @media (max-width: 1080px) {
      .pipeline { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .grid-2 { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .wrap { padding: 12px 10px 28px; }
      .pipeline { grid-template-columns: 1fr; }
      .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .topbar { padding: 12px; }
      .title h1 { font-size: 1.06rem; }
      .panel { padding: 11px; }
      table { font-size: 0.71rem; }
      th, td { padding: 6px 5px; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header class="topbar">
      <div class="title">
        <h1>Meme Flight Recorder — 5-Agent Paper Research Desk</h1>
        <p>One Front Door · 5-Agent Decision Layer · Read-Only Bridge · Autonomy L0 / L1 / L2 (Nothing Moves Real Money).</p>
      </div>
      <div class="badges">
        <span class="badge accent" id="mode-badge">PAPER ONLY · NO SIGNING</span>
        <span class="badge good" id="chain-badge">CHAIN: VERIFYING...</span>
        <span class="badge" id="bridge-badge">BRIDGE: READ-ONLY</span>
        <span class="badge" id="head-badge">HEAD: ...</span>
      </div>
    </header>

    <section class="pipeline" aria-label="Five Specialized Agents">
      <div class="agent-card">
        <div class="step">Agent 1 · Discovery</div>
        <h2>Discovery &amp; Trend Scout</h2>
        <p>Tags read-only provenance, pool tier (&gt;=$50k), and base58 mint identity.</p>
      </div>
      <div class="agent-card">
        <div class="step">Agent 2 · Advisory</div>
        <h2>Virality &amp; Narrative Analyst</h2>
        <p>Scores Reddit/TG/News velocity, credible authors &amp; spam share. Cannot waive gates.</p>
      </div>
      <div class="agent-card">
        <div class="step">Agent 3 · Fail-Closed</div>
        <h2>Adversarial On-Chain Investigator</h2>
        <p>Audits authority, top-10 concentration, vendor dev-sell, liquidity &amp; exit simulation.</p>
      </div>
      <div class="agent-card">
        <div class="step">Agent 4 · Decision Judge</div>
        <h2>Risk &amp; Decision Judge</h2>
        <p>Enforces equity-tier sizing, slot caps, RT cost limit, &amp; L0/L1/L2 autonomy rules.</p>
      </div>
      <div class="agent-card">
        <div class="step">Agent 5 · Immutable</div>
        <h2>Flight Recorder Auditor</h2>
        <p>Appends SHA-256 linked packets &amp; renders Desk Diary for promotion review.</p>
      </div>
    </section>

    <section class="kpis" id="kpi-grid"></section>

    <section class="grid-2">
      <div class="panel">
        <h3>
          <span>5-Agent Research Packets</span>
          <span class="muted mono" id="packet-count">0 packets</span>
        </h3>
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
        <h3>
          <span>L2 Human-Approval Drafts (Paper-Only)</span>
          <span class="muted mono" id="draft-count">0 pending</span>
        </h3>
        <div id="drafts-box"></div>

        <h3 style="margin-top:14px;">
          <span>Paper Equity Curve</span>
          <span class="muted mono" id="eq-summary">$50.00</span>
        </h3>
        <div id="eq-chart"></div>

        <h3 style="margin-top:14px;">
          <span>Gate Rejection Pareto</span>
          <span class="muted mono">Fail-Closed</span>
        </h3>
        <div id="pareto-list"></div>
      </div>
    </section>

    <section class="grid-2">
      <div class="panel">
        <h3>
          <span>Read-Only Bridge (Internal + Ecosystem Reference Patterns)</span>
          <span class="muted mono">write_capable=false</span>
        </h3>
        <div class="bridge-grid" id="bridge-list"></div>
        <h3>
          <span>Paper Book Closures</span>
          <span class="muted mono" id="closed-count">0 closed</span>
        </h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Asset</th><th>Reason</th><th>PnL</th><th>R</th></tr>
            </thead>
            <tbody id="closed-body"></tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <h3>
          <span>Nightly / Weekly Desk Diary</span>
          <span class="muted mono">/api/desk/diary</span>
        </h3>
        <pre class="diary" id="diary-box">Loading desk diary...</pre>
      </div>
    </section>
  </div>

  <script>
    function esc(v) {
      return String(v ?? "").replace(/[&<>"]/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"
      }[c]));
    }
    function renderEquitySvg(curve) {
      if (!curve || curve.length < 2) {
        return '<p class="muted" style="font-size:0.8rem">No closed paper trades yet.</p>';
      }
      const vals = curve.map((d) => Number(d.equity_usd));
      const minV = Math.min(...vals);
      const maxV = Math.max(...vals);
      const span = Math.max(0.01, maxV - minV);
      const w = 360, h = 110, pad = 12;
      const pts = curve.map((d, i) => {
        const x = pad + (i * (w - 2 * pad)) / Math.max(1, curve.length - 1);
        const y = h - pad - ((Number(d.equity_usd) - minV) * (h - 2 * pad)) / span;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(" ");
      return `<svg class="eq" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polyline fill="none" stroke="#38bdf8" stroke-width="2.4" points="${pts}" />
      </svg>`;
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
      chainBadge.textContent = data.chain_verified ? "CHAIN: VERIFIED" : "CHAIN: CORRUPTED";
      chainBadge.className = "badge " + (data.chain_verified ? "good" : "bad");
      document.getElementById("head-badge").textContent =
        "HEAD: " + String(data.head_hash || "GENESIS").slice(0, 12);

      const kpis = [
        ["Packets", data.counts.packets],
        ["Paper Open", data.counts.open_positions],
        ["Closed Trades", `${data.counts.closed_positions} / ${data.performance.promotion_target_trades}`],
        ["Realized PnL", `$${Number(data.performance.realized_pnl_usd).toFixed(2)}`],
        ["Profit Factor", Number(data.performance.profit_factor).toFixed(2)],
        ["Paper Equity", `$${Number(data.current_equity_usd).toFixed(2)}`]
      ];
      document.getElementById("kpi-grid").innerHTML = kpis.map(([k, v]) =>
        `<div class="kpi"><div class="label">${esc(k)}</div><div class="val">${esc(v)}</div></div>`
      ).join("");

      document.getElementById("packet-count").textContent = `${data.packets.length} shown`;
      document.getElementById("packets-body").innerHTML = data.packets.map((p) => {
        const vir = p.virality || {};
        const inv = p.investigation || {};
        const rsk = p.risk || {};
        const gates = Object.entries(inv.gate_breakdown || {}).map(([k, st]) =>
          `<span class="pill ${esc(st)}">${esc(k.split("_")[0])}:${esc(st)}</span>`
        ).join(" ");
        const riskTxt = rsk.approved
          ? `APPROVED ($${Number(rsk.position_value_usd).toFixed(2)}, ${Number(rsk.estimated_round_trip_cost_pct || 0).toFixed(1)}% cost)`
          : esc(rsk.reason || "blocked");
        return `<tr>
          <td><strong>${esc(p.symbol || "?")}</strong><br><span class="muted mono" style="font-size:0.7rem">${esc(p.autonomy_level)}</span></td>
          <td><span class="pill ${esc(p.final_action)}">${esc(p.final_action)}</span></td>
          <td><span class="mono">${Number(vir.momentum_score || 0).toFixed(1)}</span><br><span class="muted" style="font-size:0.71rem">${esc(vir.advisory_stance)}</span></td>
          <td><div class="gates-mini">${gates}</div></td>
          <td class="mono" style="font-size:0.74rem">${riskTxt}</td>
          <td class="mono muted" style="font-size:0.72rem">${esc(String(p.event_hash || "").slice(0, 10))}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="6" class="muted">No packets recorded yet.</td></tr>`;

      const drafts = data.pending_drafts || [];
      document.getElementById("draft-count").textContent = `${drafts.length} pending`;
      document.getElementById("drafts-box").innerHTML = drafts.map((d) => {
        const dr = d.human_approval_draft || {};
        return `<div class="draft-card">
          <div class="mono" style="font-size:0.8rem"><strong>${esc(dr.symbol)}</strong> · $${Number(dr.proposed_position_usd || 0).toFixed(2)} paper stake · RT cost ${Number(dr.estimated_round_trip_cost_pct || 0).toFixed(2)}%</div>
          <div class="muted" style="font-size:0.74rem">Requires human tap (paper-only, zero live signing)</div>
          <div class="draft-actions">
            <button class="btn approve" data-action="approve" onclick="decideDraft('${esc(d.event_hash)}', 'approve')">Tap: Paper Approve</button>
            <button class="btn reject" data-action="reject" onclick="decideDraft('${esc(d.event_hash)}', 'reject')">Reject Draft</button>
          </div>
        </div>`;
      }).join("") || `<p class="muted" style="font-size:0.8rem">No pending L2 drafts.</p>`;

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
      `).join("") || `<p class="muted" style="font-size:0.82rem">No rejections recorded.</p>`;

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
          <td class="mono" style="color:${pnl >= 0 ? "#86efac" : "#fca5a5"}">${pnl >= 0 ? "+" : ""}$${pnl.toFixed(3)}</td>
          <td class="mono">${Number(c.r_multiple || 0).toFixed(2)}R</td>
        </tr>`;
      }).join("") || `<tr><td colspan="4" class="muted">No closed trades yet.</td></tr>`;

      document.getElementById("diary-box").textContent = data.diary_markdown || "";
    }
    loadDesk().catch((err) => {
      document.getElementById("diary-box").textContent = "Failed to load desk state: " + err;
    });
  </script>
</body>
</html>
"""
