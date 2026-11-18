"""The four pages rendered at publish time carry the family shell.

The publish pipeline renders methodology, skeptic, ampleness and referee into
frontend/public and only then runs the frontend tests, where
frontend/tests/familyInterface.test.mjs requires the family shell on every
static page. CI never sees those four files, because they are never committed,
so in 0.16.14 the defect surfaced for the first time inside the site
publication and stopped it. These tests hold the same contract here, where CI
does run, and hold the backend's copy of the header to the frontend's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from seiche import ampleness, family_shell, methodology, referee, skeptic

REPO = Path(__file__).resolve().parents[2]
PUBLIC = REPO / "frontend" / "public"
HEADER_FILE = PUBLIC / "research-ui" / "header.html"
STYLESHEETS = (
    "/research-ui/research.css",
    "/research-ui/family-tokens.css",
    "/research-ui/seiche-pages.css",
)


def _pages() -> dict[str, str]:
    return {
        "methodology.html": methodology.render_methodology_html(),
        "skeptic.html": skeptic.render_skeptic_html({}),
        "ampleness.html": ampleness.render_ampleness_html({}),
        # With no engine block the referee publishes its "inputs are dark" notice.
        "referee.html": referee.render_referee_html({}),
    }


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    return _pages()


def test_the_backend_header_is_the_frontend_header_byte_for_byte():
    if not HEADER_FILE.is_file():
        pytest.skip("the frontend tree is not part of this install")
    assert family_shell.HEADER == HEADER_FILE.read_text(encoding="utf-8").strip()


def test_the_shell_constants_match_the_static_page_shell_script():
    script = REPO / "scripts" / "research_shell.py"
    if not script.is_file():
        pytest.skip("the repository scripts are not part of this install")
    source = script.read_text(encoding="utf-8")
    for href in STYLESHEETS:
        assert f'<link rel="stylesheet" href="{href}">' in source
        assert f'<link rel="stylesheet" href="{href}">' in family_shell.STYLE
    assert f"CONTEXT = '{family_shell.CONTEXT}'" in source


@pytest.mark.parametrize("name", ["methodology.html", "skeptic.html", "ampleness.html", "referee.html"])
def test_each_generated_page_meets_the_frontend_family_contract(pages, name):
    """The assertions of familyInterface.test.mjs, one for one."""
    page = pages[name]
    assert re.search(r'<html\b[^>]*class="[^"]*research-interface[^"]*"[^>]*data-product="seiche"', page), name
    for href in STYLESHEETS:
        assert f'<link rel="stylesheet" href="{href}">' in page, f"{name} links {href}"
    assert page.count('<header class="research-header">') == 1, f"{name} has one family header"
    mast = family_shell.HEADER[: family_shell.HEADER.index('<nav class="research-tabs"')]
    assert mast.replace('<a class="research-skip" href="#main">Skip to content</a>', "") in page, name
    assert family_shell.CONTEXT in page, f"{name} context.js"
    skip = re.search(r'class="research-skip" href="#([^"]+)"', page)
    assert skip and re.search(rf'\bid="{re.escape(skip.group(1))}"', page), f"{name} skip link has a target"
    assert not re.search(r"fonts\.googleapis\.com|fonts\.gstatic\.com", page), f"{name} loads a web font"


@pytest.mark.parametrize("name", ["methodology.html", "skeptic.html", "ampleness.html", "referee.html"])
def test_each_generated_page_keeps_one_heading_and_the_family_accent(pages, name):
    page = pages[name]
    assert len(re.findall(r"<h1\b", page)) == 1, f"{name} has one page heading"
    assert f'<h1 id="{family_shell.SKIP_TARGET}">' in page
    # The family stylesheets come before the page's own rules, and the page sets no accent of its own.
    assert page.index(family_shell.STYLE) < page.index("<style>")
    assert "--accent:var(--research-accent" in page
    for retired in ("#9c8fe8", "#bbaffe", "#d9d4ff"):
        assert retired not in page, f"{name} still sets the retired Seiche accent {retired}"
    assert page.count("</body>") == 1 and page.rstrip().endswith("</html>")


def test_the_pages_survive_the_real_pipeline_order(tmp_path, pages):
    """Written where the publish pipeline writes them, each page is what the renderer returned."""
    snapshot: dict = {}
    written = {
        "ampleness.html": ampleness.write_ampleness(snapshot, tmp_path / "ampleness.html"),
        "methodology.html": methodology.write_methodology(tmp_path / "methodology.html"),
    }
    for name, path in written.items():
        text = Path(path).read_text(encoding="utf-8")
        assert text.count('<header class="research-header">') == 1, name
        assert family_shell.HTML_OPEN in text, name


def test_no_generated_page_is_committed_under_the_frontend():
    """They are build outputs. A committed copy would hide the generator from the frontend test."""
    if not PUBLIC.is_dir():
        pytest.skip("the frontend tree is not part of this install")
    for name in ("methodology.html", "skeptic.html", "ampleness.html", "referee.html"):
        tracked = (PUBLIC / name).is_file() and _is_tracked(PUBLIC / name)
        assert not tracked, f"{name} is committed; it must stay a build output"


def _is_tracked(path: Path) -> bool:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--error-unmatch", str(path.relative_to(REPO))],
            capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0
