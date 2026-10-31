"""Capture paper-ready UI screenshots (dashboard/login/admin) as PNG.

Goal: produce reproducible, paper-embeddable images of the actual HTML pages.

Outputs:
- paper/figures/dashboard_main.png
- paper/figures/dashboard_login.png
- paper/figures/dashboard_admin.png

This script prefers Playwright + the installed Microsoft Edge (no browser downloads).

Usage:
  python paper/capture_ui_screenshots.py

Optional:
  python paper/capture_ui_screenshots.py --port 8080 --start-server

Notes:
- Screenshots are taken from the web UI served by `crowdcount serve` / `crowdcount run`.
- The pages will render even without the detector running; values may show 0.
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8080


def _wait_for_url(url: str, timeout_s: float = 20.0) -> bool:
    try:
        import requests  # type: ignore
    except Exception:
        return False

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _capture(url: str, out_png: Path, viewport_w: int = 1440, viewport_h: int = 900) -> None:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Playwright is required to capture screenshots. Install with: pip install playwright\n"
            "Then run again. (No browser download is required if Edge is installed.)"
        ) from e

    _ensure_dir(out_png.parent)

    with sync_playwright() as p:
        browser = None
        last_err: Exception | None = None

        # Prefer installed Edge to avoid Playwright browser downloads.
        for launch_kwargs in (
            {"channel": "msedge"},
            {},
        ):
            try:
                browser = p.chromium.launch(headless=True, **launch_kwargs)
                break
            except Exception as ex:  # pragma: no cover
                last_err = ex

        if browser is None:
            raise RuntimeError(
                "Unable to launch a Chromium browser via Playwright. "
                "If Playwright is installed, you may need: playwright install chromium"
            ) from last_err

        try:
            page = browser.new_page(viewport={"width": viewport_w, "height": viewport_h})
            page.goto(url, wait_until="networkidle", timeout=30_000)
            # Give charts a moment to initialize.
            page.wait_for_timeout(1200)
            page.screenshot(path=str(out_png), full_page=False)
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture UI screenshots for the paper")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--start-server",
        action="store_true",
        help="Start FastAPI server-only temporarily for screenshotting",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(REPO_ROOT / "paper" / "figures"),
        help="Output directory for PNGs (default: paper/figures)",
    )

    args = parser.parse_args()

    port = int(args.port)
    base_candidates = [
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    ]
    out_dir = Path(args.out_dir)

    server_proc: subprocess.Popen[str] | None = None
    try:
        if args.start_server:
            # Run the server only; avoids OpenCV UI and detector startup.
            # Bind to loopback only for reliable local screenshotting.
            cmd = [
                sys.executable,
                "-m",
                "crowdcount",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
            server_proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))

        # Wait for the web UI to respond (try localhost first, then 127.0.0.1)
        base = None
        for candidate in base_candidates:
            if _wait_for_url(f"{candidate}/index.html", timeout_s=45.0):
                base = candidate
                break

        if base is None:
            raise RuntimeError(
                "Server not reachable at any of:\n"
                + "\n".join(f"- {c}/index.html" for c in base_candidates)
                + "\n\nRun `crowdcount run` or `crowdcount serve --port 8080` (or pass --start-server)."
            )

        _capture(f"{base}/index.html", out_dir / "dashboard_main.png")
        _capture(f"{base}/login.html", out_dir / "dashboard_login.png")
        _capture(f"{base}/admin.html", out_dir / "dashboard_admin.png")

        print(f"Wrote UI screenshots to: {out_dir}")
        return 0

    finally:
        if server_proc is not None:
            with contextlib.suppress(Exception):
                server_proc.terminate()
            with contextlib.suppress(Exception):
                server_proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
