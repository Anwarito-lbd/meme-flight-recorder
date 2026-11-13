#!/usr/bin/env python3
"""Launch the Desk UI on a deterministic replay DB and capture screenshots & video.

Produces in docs/proof/:
  - desk_ui_desktop.png (1440x900)
  - desk_ui_mobile.png  (390x844)
  - desk_walkthrough.mp4
  - desk_walkthrough.gif
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_proof_charts import run_deterministic_paper_replay

PROOF_DIR = ROOT / "docs" / "proof"
CHROME_PATHS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)


def _find_browser() -> str | None:
    for candidate in CHROME_PATHS:
        if candidate.exists():
            return str(candidate)
    return None


def _wait_for_server(url: str, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                if resp.status == 200:
                    return
        except Exception:  # noqa: BLE001 - server still booting
            time.sleep(0.25)
    raise RuntimeError(f"Server at {url} did not become healthy within {timeout_s}s")


def main() -> int:
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    browser_exe = _find_browser()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        db_path = tmp_dir / "desk_capture.db"
        video_dir = tmp_dir / "video"
        video_dir.mkdir(parents=True, exist_ok=True)

        run_deterministic_paper_replay(db_path)

        env = dict(os.environ)
        env["MFR_DATABASE_PATH"] = db_path.as_posix()
        env["MFR_STARTING_EQUITY_USD"] = "50.0"
        env["PYTHONPATH"] = str(ROOT / "src")

        port = 8765
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "meme_flight_recorder.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_for_server(f"http://127.0.0.1:{port}/health")

            with sync_playwright() as pw:
                launch_kwargs: dict[str, object] = {"headless": True}
                if browser_exe:
                    launch_kwargs["executable_path"] = browser_exe
                browser = pw.chromium.launch(**launch_kwargs)

                # 1. Desktop screenshot + interactive walkthrough video
                desktop_ctx = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    record_video_dir=str(video_dir),
                    record_video_size={"width": 1280, "height": 800},
                )
                page = desktop_ctx.new_page()
                page.goto(f"http://127.0.0.1:{port}/desk", wait_until="networkidle")
                page.wait_for_function(
                    "document.getElementById('chain-badge')?.textContent?.includes('VERIFIED')"
                )
                page.wait_for_timeout(600)

                page.screenshot(
                    path=str(PROOF_DIR / "desk_ui_desktop.png"),
                    full_page=True,
                )

                # Smooth scroll walkthrough + single-tap L2 Paper Approve demo
                for y in (320, 680, 1080, 320, 0):
                    page.evaluate(f"window.scrollTo({{top: {y}, behavior: 'smooth'}})")
                    page.wait_for_timeout(700)

                approve_btn = page.locator("button[data-action='approve']")
                if approve_btn.count() > 0:
                    approve_btn.first.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    approve_btn.first.click()
                    page.wait_for_timeout(1200)

                page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
                page.wait_for_timeout(800)

                video_path = page.video.path() if page.video else None
                desktop_ctx.close()

                # 2. Mobile 390x844 responsive verification screenshot
                mobile_ctx = browser.new_context(viewport={"width": 390, "height": 844})
                m_page = mobile_ctx.new_page()
                m_page.goto(f"http://127.0.0.1:{port}/desk", wait_until="networkidle")
                m_page.wait_for_function(
                    "document.getElementById('chain-badge')?.textContent?.includes('VERIFIED')"
                )
                m_page.wait_for_timeout(500)
                overflow = m_page.evaluate(
                    "document.documentElement.scrollWidth > window.innerWidth"
                )
                m_page.screenshot(
                    path=str(PROOF_DIR / "desk_ui_mobile.png"),
                    full_page=True,
                )
                mobile_ctx.close()
                browser.close()

                if video_path and Path(video_path).exists():
                    webm_src = Path(video_path)
                    mp4_dst = PROOF_DIR / "desk_walkthrough.mp4"
                    gif_dst = PROOF_DIR / "desk_walkthrough.gif"
                    ffmpeg = shutil.which("ffmpeg")
                    if ffmpeg:
                        subprocess.run(
                            [
                                ffmpeg,
                                "-y",
                                "-i",
                                str(webm_src),
                                "-c:v",
                                "libx264",
                                "-pix_fmt",
                                "yuv420p",
                                "-movflags",
                                "+faststart",
                                "-crf",
                                "26",
                                str(mp4_dst),
                            ],
                            check=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        subprocess.run(
                            [
                                ffmpeg,
                                "-y",
                                "-i",
                                str(webm_src),
                                "-vf",
                                (
                                    "fps=8,scale=840:-1:flags=lanczos,split[s0][s1];"
                                    "[s0]palettegen[p];[s1][p]paletteuse"
                                ),
                                str(gif_dst),
                            ],
                            check=True,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    else:
                        shutil.copyfile(webm_src, PROOF_DIR / "desk_walkthrough.webm")

                print(f"Desktop + Mobile screenshots captured (mobile_overflow={overflow})")
        finally:
            server.terminate()
            server.wait(timeout=5)

    for artifact in (
        "desk_ui_desktop.png",
        "desk_ui_mobile.png",
        "desk_walkthrough.mp4",
        "desk_walkthrough.gif",
    ):
        p = PROOF_DIR / artifact
        if p.exists():
            print(f"  {artifact}: {p.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
