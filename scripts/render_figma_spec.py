"""Generate a Figma-style design token & layout specification sheet for Meme Flight Recorder Desk UI."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def _fonts():
    try:
        return {
            "h1": ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 25),
            "h2": ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 15),
            "body": ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 13),
            "mono": ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 13),
            "xs": ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 11),
        }
    except OSError:
        d = ImageFont.load_default()
        return {"h1": d, "h2": d, "body": d, "mono": d, "xs": d}


def main() -> None:
    out = ROOT / "docs" / "proof" / "figma-design-spec.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    f = _fonts()

    img = Image.new("RGB", (1400, 760), (9, 12, 16))
    d = ImageDraw.Draw(img)

    d.text((40, 26), "Meme Flight Recorder — Figma-Grade Paper Research Terminal Spec", fill=(237, 242, 247), font=f["h1"])
    d.text(
        (40, 62),
        "Self-contained workstation tokens derived from src/meme_flight_recorder/desk_ui.py (Zero external CDN · Paper-only fail-closed UI)",
        fill=(163, 179, 198),
        font=f["body"],
    )

    # Panel 1: Color & Semantic Tokens
    d.rounded_rectangle([40, 102, 460, 480], radius=10, fill=(16, 21, 29), outline=(30, 40, 55), width=2)
    d.text((60, 122), "01  SURFACE & SEMANTIC TOKENS", fill=(56, 189, 248), font=f["h2"])
    swatches = [
        ("--canvas", "#090c10", (9, 12, 16), "Terminal canvas"),
        ("--surface", "#10151d", (16, 21, 29), "Primary card / rail"),
        ("--well", "#161d28", (22, 29, 40), "Control / draft well"),
        ("--border", "#1e2837", (30, 40, 55), "1px structural hairline"),
        ("--accent", "#38bdf8", (56, 189, 248), "Paper equity & active tab"),
        ("--good", "#10b981", (16, 185, 129), "CHAIN: VERIFIED / OPEN"),
        ("--warn", "#f59e0b", (245, 158, 11), "L2 HUMAN_APPROVAL_DRAFT"),
        ("--bad", "#ef4444", (239, 68, 68), "FAIL-CLOSED REJECTED"),
        ("--violet", "#a78bfa", (167, 139, 250), "Rejection Pareto fill"),
    ]
    for i, (var, hx, rgb, role) in enumerate(swatches):
        y = 158 + i * 33
        d.rounded_rectangle([60, y, 84, y + 22], radius=4, fill=rgb, outline=(70, 90, 120), width=1)
        d.text((96, y + 4), f"{var:<10} {hx}", fill=(237, 242, 247), font=f["mono"])
        d.text((262, y + 4), role, fill=(163, 179, 198), font=f["xs"])

    # Panel 2: Empirical Calibration Spec
    d.rounded_rectangle([40, 500, 460, 720], radius=10, fill=(16, 21, 29), outline=(30, 40, 55), width=2)
    d.text((60, 518), "02  GATE MATRIX & CALIBRATION SPEC", fill=(245, 158, 11), font=f["h2"])
    gates = [
        ("AUTH", "Mint & Freeze Authority Disabled", (16, 185, 129)),
        ("CONC", "Top-10 Private Holder <= 30%", (16, 185, 129)),
        ("IDEN", "Dev-Sell & Sybil Cluster Audit", (245, 158, 11)),
        ("LIQU", "Liq >= $50k & Exit Route Sim", (239, 68, 68)),
    ]
    for idx, (tag, desc, col) in enumerate(gates):
        y = 554 + idx * 30
        d.rounded_rectangle([60, y, 124, y + 22], radius=4, fill=(22, 29, 40), outline=col, width=1)
        d.text((68, y + 4), tag, fill=col, font=f["xs"])
        d.text((138, y + 4), desc, fill=(203, 213, 225), font=f["xs"])
    d.text((60, 684), "Study L 0.58% survival | Study C 63.4% coverage | PF 0.233 halt", fill=(139, 155, 181), font=f["xs"])

    # Panel 3: Desktop Anatomy
    d.rounded_rectangle([484, 102, 1360, 720], radius=10, fill=(16, 21, 29), outline=(30, 40, 55), width=2)
    d.text((506, 122), "03  TERMINAL GRID ANATOMY (1400PX WORKSTATION / 390PX MOBILE)", fill=(16, 185, 129), font=f["h2"])

    d.rounded_rectangle([506, 156, 1338, 198], radius=6, fill=(22, 29, 40), outline=(43, 58, 79), width=1)
    d.text((520, 170), "Sticky Topbar: [MFR] 5-Agent Desk | PAPER ONLY · CHAIN: VERIFIED · BRIDGE: READ-ONLY · HEAD SHA-256", fill=(226, 232, 240), font=f["xs"])

    labels_5 = ["01 Discovery", "02 Virality", "03 Adversarial", "04 Risk Judge", "05 Auditor"]
    for idx, lb in enumerate(labels_5):
        x0 = 506 + idx * 168
        d.rounded_rectangle([x0, 210, x0 + 160, 250], radius=6, fill=(22, 29, 40), outline=(56, 189, 248) if idx in (2, 3) else (30, 40, 55), width=1)
        d.text((x0 + 10, 224), lb, fill=(226, 232, 240), font=f["xs"])

    labels_6 = ["Packets: 16", "Open: 0", "Closed: 5/30", "PnL: +$11.99", "PF: 2.44", "Eq: $61.99"]
    for idx, lb in enumerate(labels_6):
        x0 = 506 + idx * 140
        d.rounded_rectangle([x0, 262, x0 + 132, 298], radius=6, fill=(22, 29, 40), outline=(30, 40, 55), width=1)
        d.text((x0 + 8, 274), lb, fill=(163, 179, 198), font=f["xs"])

    # Main 1.42fr : 0.58fr
    d.rounded_rectangle([506, 312, 1068, 560], radius=8, fill=(9, 12, 16), outline=(56, 189, 248), width=1)
    d.text((522, 326), "LEFT WORKSPACE (1.42fr): FILTERED PACKETS TABLE", fill=(56, 189, 248), font=f["mono"])
    d.text((522, 352), "Segmented Tabs: [ALL] [DRAFTS] [OPEN] [REJECTED] [MONITOR] + Instant Search", fill=(163, 179, 198), font=f["xs"])
    d.text((522, 380), "Single-line tabular-nums rows | AUTH · CONC · IDEN · LIQU chips | sticky thead", fill=(203, 213, 225), font=f["xs"])

    d.rounded_rectangle([1082, 312, 1338, 560], radius=8, fill=(9, 12, 16), outline=(16, 185, 129), width=1)
    d.text((1096, 326), "RIGHT RAIL (0.58fr)", fill=(16, 185, 129), font=f["mono"])
    for idx, txt in enumerate([
        "L2 Human-Approval Queue",
        "SVG Equity Curve + $50 Ref",
        "Gate Rejection Pareto Bars",
        "Study L/C Guardrails Grid",
    ]):
        y = 358 + idx * 44
        d.rounded_rectangle([1096, y, 1324, y + 34], radius=5, fill=(22, 29, 40), outline=(30, 40, 55), width=1)
        d.text((1106, y + 10), txt, fill=(226, 232, 240), font=f["xs"])

    # Bottom 1fr : 1fr
    d.rounded_rectangle([506, 574, 916, 702], radius=8, fill=(9, 12, 16), outline=(30, 40, 55), width=1)
    d.text((522, 588), "BOTTOM-LEFT (1fr): READ-ONLY BRIDGE + CLOSURES", fill=(203, 213, 225), font=f["xs"])
    d.text((522, 616), "12 read-only sources (write_capable=false)", fill=(139, 155, 181), font=f["xs"])
    d.text((522, 636), "Closed paper trade R-multiple ledger table", fill=(139, 155, 181), font=f["xs"])

    d.rounded_rectangle([928, 574, 1338, 702], radius=8, fill=(9, 12, 16), outline=(30, 40, 55), width=1)
    d.text((944, 588), "BOTTOM-RIGHT (1fr): FORMATTED DESK DIARY", fill=(203, 213, 225), font=f["xs"])
    d.text((944, 616), "Markdown -> semantic headings, bullets,", fill=(139, 155, 181), font=f["xs"])
    d.text((944, 636), "inline SHA hashes & HTML Pareto table", fill=(139, 155, 181), font=f["xs"])

    img.save(out)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
