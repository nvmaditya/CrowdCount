import re

import pytest

from crowdcount.server.app import WEB_DIR

ALLOWED_URLS = ("https://github.com/nvmaditya/CrowdCount", "http://www.w3.org/2000/svg")
PAGES = sorted(WEB_DIR.glob("*.html"))
OWN_ASSETS = sorted(p for p in WEB_DIR.rglob("*") if p.suffix in {".css", ".js"} and "vendor" not in p.parts)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_pages_reference_only_bundled_files(page):
    for ref in re.findall(r'(?:src|href)="([^"]+)"', page.read_text(encoding="utf-8")):
        if ref.startswith(("#", "/api/", *ALLOWED_URLS)) or ref in {"./", "/docs"}:
            continue
        assert not re.match(r"[a-z]+:", ref), f"{page.name} loads an external URL: {ref}"
        local = re.split(r"[?#]", ref)[0]  # "admin.html#zones" is still admin.html
        assert (WEB_DIR / local).is_file(), f"{page.name} references a missing file: {ref}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_pages_have_no_inline_scripts_or_handlers(page):
    html = page.read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert not re.search(r"\son[a-z]+=", html)


@pytest.mark.parametrize("asset", OWN_ASSETS, ids=lambda p: str(p.relative_to(WEB_DIR)))
def test_own_assets_load_nothing_external(asset):
    for url in re.findall(r"https?://[^\s\"')`]+", asset.read_text(encoding="utf-8")):
        assert url.startswith(ALLOWED_URLS), f"{asset.name}: {url}"


@pytest.mark.parametrize(
    "script", [p for p in OWN_ASSETS if p.suffix == ".js"], ids=lambda p: str(p.relative_to(WEB_DIR))
)
def test_own_scripts_never_parse_html_strings(script):
    """The DOM is built with CC.el / createElementNS only, so user data is never parsed as HTML."""
    text = script.read_text(encoding="utf-8")
    for api in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert api not in text, f"{script.name} mentions {api}"


def test_fonts_are_self_hosted():
    css = (WEB_DIR / "assets/css/tokens.css").read_text(encoding="utf-8")
    for name in ("Geist-Variable.woff2", "GeistMono-Variable.woff2"):
        assert f"../../vendor/geist/{name}" in css
        assert (WEB_DIR / "vendor/geist" / name).is_file()
