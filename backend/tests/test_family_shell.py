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

import importlib.util
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


def _mast() -> str:
    """The mast as a page shows it: header.html's grouping attributes never reach a page."""
    mast = family_shell.TEMPLATE[: family_shell.TEMPLATE.index('<nav class="research-tabs"')]
    return re.sub(r' data-(?:owns|for)="[^"]*"', "", mast).replace(
        '<a class="research-skip" href="#main">Skip to content</a>', "")


def _current(page: str) -> list[str]:
    header = re.search(r'<header class="research-header">.*?</header>', page, re.S).group(0)
    return re.findall(r'<a[^>]*aria-current="page"[^>]*>([^<]*)</a>', header)


def test_the_backend_header_is_the_frontend_header_byte_for_byte():
    if not HEADER_FILE.is_file():
        pytest.skip("the frontend tree is not part of this install")
    assert family_shell.TEMPLATE == HEADER_FILE.read_text(encoding="utf-8").strip()


def test_the_backend_rule_is_the_static_page_rule():
    """family_shell.header(path) is scripts/research_shell.py's research_header(path), path for path."""
    script = REPO / "scripts" / "research_shell.py"
    if not script.is_file() or not HEADER_FILE.is_file():
        pytest.skip("the repository scripts are not part of this install")
    spec = importlib.util.spec_from_file_location("seiche_research_shell", script)
    shell = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shell)
    paths = ["/", "/methodology", "/skeptic", "/ampleness", "/referee", "/weekly/", "/weekly/2026-11-30/",
             "/articles/", "/articles/2026-09-24-a-note/", "/dispatches/", "/dispatches/2026-09-24-daily",
             "/money-markets/", "/markets/forex/", "/gift-city/", "/developers", "/developers.html",
             "/agents/quant/", "/guide", "/privacy", "/no-such-page/"]
    for path in paths:
        assert family_shell.header(path) == shell.research_header(path), path


def test_the_header_groups_each_page_and_keeps_the_attributes_out():
    assert re.findall(r'aria-current="page"[^>]*>([^<]*)<', family_shell.header("/methodology")) == [
        "Seiche", "Research", "Methodology"]
    assert re.findall(r'aria-current="page"[^>]*>([^<]*)<', family_shell.header("/dispatches/2026-09-24-daily")) == [
        "Seiche", "Research", "Dispatches"]
    assert re.findall(r'aria-current="page"[^>]*>([^<]*)<', family_shell.header("/developers")) == [
        "Seiche", "API &amp; MCP", "Developer guide"]
    for path in ("/", "/methodology", "/weekly/", "/privacy"):
        page = family_shell.header(path)
        assert not re.search(r"data-(?:owns|for)=", page), path
        assert page.count('<nav class="research-subtabs"') <= 1, path
    # Seiche is free: no Pricing link in its mast.
    assert "Pricing" not in family_shell.TEMPLATE and "research-pricing" not in family_shell.TEMPLATE


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
    assert _mast() in page, name
    assert not re.search(r"data-(?:owns|for)=", page), f"{name} keeps the grouping attributes out"
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


def _rules(page: str) -> list[tuple[str, str]]:
    """Each rule in the page's own style blocks: (selector, body with all whitespace removed)."""
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", page, re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(selector.strip(), re.sub(r"\s+", "", body)) for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def _declarations(block: str) -> dict[str, str]:
    """Custom properties in one CSS block, name to value."""
    return {name: value.strip() for name, value in re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;}]+)", block)}


def _family_token(value: str) -> str | None:
    """The family token a value points at: var(--research-x, ...) gives --research-x."""
    match = re.match(r"var\((--research-[a-z-]+)", value)
    return match.group(1) if match else None


def test_no_page_token_disagrees_with_the_mapping_that_outranks_it():
    """research.css declares these names again on html.research-interface, which outranks :root.

    A page token that pointed somewhere else would be dead text: the family's value is the one a
    reader sees. That is how commands came to be set in the reading font. Each shared name must
    point at the family token the family points it at, so the page reads the same with the family
    stylesheets and without them.
    """
    if not PUBLIC.is_dir():
        pytest.skip("the frontend tree is not part of this install")
    css = (PUBLIC / "research-ui" / "research.css").read_text(encoding="utf-8")
    family = _declarations(re.search(r"html\.research-interface\s*\{(.*?)\}", css, re.S).group(1))
    page = _declarations(family_shell.ROOT_TOKENS)
    shared = sorted(set(page) & set(family))
    assert {"--bg", "--panel", "--text", "--dim", "--faint", "--accent", "--accent-soft", "--accent-bright",
            "--mono"} <= set(shared)
    for name in shared:
        assert _family_token(page[name]) == _family_token(family[name]), (
            f"{name}: the page points at {page[name]}, the family at {family[name]}")
    assert _family_token(family["--mono"]) == "--research-font"  # the reading font, not a monospace


def test_copyable_blocks_use_a_monospace_token_the_family_cannot_override(pages):
    """Commands and citations are copied by hand, so they are set in a real monospace."""
    page_only = _declarations(family_shell.ROOT_TOKENS)
    assert _family_token(page_only["--code"]) == "--research-code"
    if PUBLIC.is_dir():
        for sheet in STYLESHEETS:
            css = (PUBLIC / sheet.lstrip("/")).read_text(encoding="utf-8")
            for name in ("--code", "--edge"):
                assert not re.search(rf"(?<![a-z-]){re.escape(name)}\s*:", css), f"{sheet} declares {name}"
    rules = {
        "skeptic.html": (r"\.cmd\s*\{[^}]*\}",),
        "methodology.html": (r"\.cite\s*\{[^}]*\}", r"code, \.mono\s*\{[^}]*\}"),
    }
    for name, patterns in rules.items():
        for pattern in patterns:
            rule = re.search(pattern, pages[name])
            assert rule, f"{name}: {pattern} is not styled"
            assert "font-family:var(--code)" in rule.group(0), f"{name}: {rule.group(0)[:60]}"
    for name, page in pages.items():
        assert "--code:var(--research-code," in page, name
        preformatted = [(selector, body) for selector, body in _rules(page)
                        if re.search(r"white-space:pre(?:-wrap|-line)?(?:;|$)", body)]
        for selector, body in preformatted:
            # However the rule is spaced, and whether or not it names a font at all: a block that
            # keeps its line breaks is one a reader copies, so it is set in --code explicitly.
            assert "font-family:var(--code)" in body, f"{name}: {selector} keeps its spacing but is not set in --code"
    assert sum(1 for page in pages.values() for _selector, body in _rules(page) if "white-space:pre" in body) >= 2


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


@pytest.mark.parametrize(("name", "label"), [
    ("methodology.html", "Methodology"), ("skeptic.html", "Skeptic pack"),
    ("ampleness.html", None), ("referee.html", None),
])
def test_each_generated_page_sits_under_research(pages, name, label):
    """Research is current on every page rendered at publish time, with the page's own sub-tab when it has one."""
    current = _current(pages[name])
    assert current[:2] == ["Seiche", "Research"], name
    assert current[2:] == ([label] if label else []), name
    assert pages[name].count('<nav class="research-subtabs" aria-label="Research">') == 1, name
