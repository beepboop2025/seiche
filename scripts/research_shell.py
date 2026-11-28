"""Keep Seiche's static public pages on the family research shell.

Ported from beepboop2025/liquilens-site scripts/research_shell.py at
dfb8f0899344b473c179d48ca08e94220333e868 (the family's one-header refresh), as
undertow-site did, with the grouping rule of the same file at
dc3bdec46de6327bf5243b403fb223f2b0ab71dc (one simple menu, PR #310). Seiche
changes: pages live under frontend/public, the shell also links the family
tokens and Seiche's static-page layer, and a page whose own skip link targets a
named section keeps that target. The SPA renders the same menu in React
(frontend/src/ResearchHeader.tsx, frontend/src/WorkspaceNavigation.tsx and
frontend/src/familyNav.ts); this script refreshes only the copy of the header
that frontend/index.html shows before the app loads. Pages rendered at publish
time take the same header from backend/seiche/family_shell.py.

Run `python3 scripts/research_shell.py` after editing
frontend/public/research-ui/header.html; `--check` fails when a page drifts.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend" / "public"
STYLE = (
    '<link rel="stylesheet" href="/research-ui/research.css">'
    '<link rel="stylesheet" href="/research-ui/family-tokens.css">'
    '<link rel="stylesheet" href="/research-ui/seiche-pages.css">'
)
CONTEXT = '<script type="module" src="/research-ui/context.js"></script>'
# A page's current research chrome: the skip link (when present) and the header.
RESEARCH_HEADER = re.compile(
    r'(?:<a class="research-skip" href="#[^"]*">Skip to content</a>\s*)?<header class="research-header"[^>]*>.*?</header>',
    re.S,
)


def attach_context(source: str) -> str:
    return source if CONTEXT in source else source.replace("</body>", CONTEXT + "</body>", 1)


def _covers(path: str, prefix: str) -> bool:
    return path == prefix or (prefix != "/" and prefix.endswith("/") and path.startswith(prefix))


def research_header(path: str) -> str:
    """The shared header for one page: its group marked current and only that group's sub-tabs.

    Same rule as liquilens-site scripts/research_shell.py: a link's data-owns names the paths it
    groups, the deepest match wins, a research-subtabs nav whose data-for is the group's href is
    the only sub-tab row a page shows, and neither attribute reaches a page. Links into the desk
    (/#today and the like) are never current on a static page; the desk marks them itself.
    backend/seiche/family_shell.py applies the same rule to the pages rendered at publish time.
    """
    header = (PUBLIC / "research-ui/header.html").read_text().strip()
    path = path.removesuffix(".html")
    mast, rest = header.split('<nav class="research-tabs"', 1)
    rest = '<nav class="research-tabs"' + rest
    tabs, subtabs = rest.split("</nav>", 1)
    tabs += "</nav>"
    network_end = mast.index("</nav>") + len("</nav>")  # the product switcher keeps its own state
    head, tail = mast[:network_end], mast[network_end:]
    tabs = tabs.replace(' aria-current="page"', "")
    best = None  # (prefix length, area, href)
    for area, text in (("mast", tail), ("tabs", tabs)):
        for attrs in re.findall(r"<a\b([^>]*)>", text):
            href = re.search(r'\bhref="(/[^"#?]*)"', attrs)
            if not href:
                continue
            owns = re.search(r'\bdata-owns="([^"]+)"', attrs)
            for prefix in (owns.group(1).split() if owns else [href.group(1)]):
                if _covers(path, prefix) and (best is None or len(prefix) > best[0]):
                    best = (len(prefix), area, href.group(1))
    group = None
    if best:
        _, area, group = best
        mark = f'href="{group}"'
        if area == "mast":
            tail = tail.replace(mark, mark + ' aria-current="page"', 1)
        else:
            tabs = tabs.replace(mark, mark + ' aria-current="page"', 1)
    kept = ""
    for nav in re.findall(r'<nav class="research-subtabs"[^>]*>.*?</nav>', subtabs, re.S):
        if group and f'data-for="{group}"' in nav:
            inner = [h for h in re.findall(r'href="(/[^"#?]*)"', nav) if _covers(path, h)]
            if inner:
                here = max(inner, key=len)
                nav = nav.replace(f'href="{here}"', f'href="{here}" aria-current="page"', 1)
            kept = nav
    out = head + tail + tabs + kept + "</header>"
    return re.sub(r' data-(?:owns|for)="[^"]*"', "", out)


def unify_html(source: str, path: str) -> str:
    """Add or refresh only the product chrome; keep content, metadata, scripts and page navigation."""
    if re.search(r'http-equiv=["\']refresh', source, re.I):
        return source
    path = urlsplit(path).path or "/"
    header = research_header(path)
    if RESEARCH_HEADER.search(source):
        source = RESEARCH_HEADER.sub(lambda _: header, source, count=1)
    elif "<body" in source:
        source = re.sub(r"(<body\b[^>]*>)", lambda m: m[0] + header, source, count=1)
    else:
        return source

    def html_class(match: re.Match) -> str:
        opening = match[0]
        if "research-interface" not in opening:
            if re.search(r'\bclass="', opening):
                opening = re.sub(r'class="([^"]*)"', lambda m: f'class="{m[1]} research-interface"', opening, count=1)
            else:
                opening = opening[:-1] + ' class="research-interface">'
        if "data-product=" not in opening:
            opening = opening[:-1] + ' data-product="seiche">'
        return opening

    source = re.sub(r"<html\b[^>]*>", html_class, source, count=1)
    # The family field is black on every page.
    source = re.sub(r'(<meta name="theme-color" content=")#[0-9a-fA-F]{3,8}(")', r"\g<1>#000000\2", source, count=1)
    if STYLE not in source:
        # The family first, so each page's own stylesheet still lays out its content.
        first = re.search(r'<link rel="stylesheet"|<style\b', source)
        head_end = source.find("</head>")
        at = first.start() if first and first.start() < head_end else head_end
        source = source[:at] + STYLE + source[at:]
    # The family header carries the skip link: retire the page's own, keep its target.
    target = None
    own_skip = re.search(r'\s*<a class="skip" href="#([^"]+)">[^<]*</a>', source)
    if own_skip:
        target = own_skip[1]
        source = source[: own_skip.start()] + source[own_skip.end():]
    # Pages without a <main> (the reading pages) skip to their first heading.
    main = re.search(r"<main\b[^>]*>", source) or re.search(r"<h1\b[^>]*>", source)
    if target is None and main:
        identity = re.search(r'\bid="([^"]+)"', main[0])
        if identity:
            target = identity[1]
        else:
            tag = "<main" if main[0].startswith("<main") else "<h1"
            source = source[: main.start()] + main[0].replace(tag, tag + ' id="main"', 1) + source[main.end():]
            target = "main"
    if target and target != "main":
        source = re.sub(r'class="research-skip" href="#[^"]*"', f'class="research-skip" href="#{target}"', source, count=1)
    if path in SCRIPT_FREE:
        return source.replace(CONTEXT, "")
    return attach_context(source)


def page_path(name: str) -> str:
    rel = name.removeprefix("frontend/public/")
    return "/" + rel.removesuffix("index.html") if rel.endswith("index.html") else "/" + rel


# Generated at publish time by backend/seiche/dispatch_pages.py (and committed by
# the daily dispatch workflow); their shell belongs in that generator, not here.
GENERATED = ("frontend/public/articles/", "frontend/public/dispatches/")
# The owner's Google-Extended permission is tied to these pages' reviewed bytes;
# they join the shell only with a content re-review and a new receipt.
REVIEWED = ROOT / "docs" / "google-extended-reviewed-pages.json"
# The no-JS canonical answer page carries exactly one (ld+json) script by contract
# (backend/tests/test_public_money_market_discovery.py): no context.js there.
SCRIPT_FREE = {"/money-markets/"}


def reviewed_pages() -> set[str]:
    review = json.loads(REVIEWED.read_text())
    return {page["source_file"] for page in review["pages"]}


def pages() -> list[str]:
    names = subprocess.check_output(["git", "ls-files", "frontend/public/*.html"], cwd=ROOT, text=True).splitlines()
    held = reviewed_pages()
    return [n for n in names if not n.startswith(("frontend/public/research-ui/", *GENERATED)) and n not in held]


# The app's own page. Before the app loads it shows the header React renders for
# the front page; the app then replaces everything inside <div id="root">.
SPA = ROOT / "frontend" / "index.html"
SPA_HEADER = re.compile(
    r'(?:<a class="research-skip" href="#[^"]*">Skip to content</a>)?'
    r'<header class="(?:research-header|product-nav)"[^>]*>.*?</header>',
    re.S,
)


def unify_spa(source: str) -> str:
    """The pre-load copy of the front page's header, refreshed from header.html."""
    root = source.find('<div id="root">')
    if root < 0:
        return source
    start = root + len('<div id="root">')
    current = SPA_HEADER.search(source, start)
    if not current:
        return source
    return source[: current.start()] + research_header("/") + source[current.end():]


def sync(check: bool = False) -> list[str]:
    changed = []
    for name in pages():
        file = ROOT / name
        source = file.read_text()
        if not re.search(r"<html\b", source, re.I):
            continue
        rendered = unify_html(source, page_path(name))
        if rendered != source:
            changed.append(name)
            if not check:
                file.write_text(rendered)
    if SPA.is_file():
        source = SPA.read_text()
        rendered = unify_spa(source)
        if rendered != source:
            changed.append(str(SPA.relative_to(ROOT)))
            if not check:
                SPA.write_text(rendered)
    return changed


if __name__ == "__main__":
    check = "--check" in sys.argv[1:]
    changed = sync(check)
    if check and changed:
        print("Pages off the family shell: " + ", ".join(changed), file=sys.stderr)
        raise SystemExit(1)
    print(f"{'Checked' if check else 'Unified'} {len(pages())} public page shells; {len(changed)} changed")
