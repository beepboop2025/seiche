"""Keep Seiche's static public pages on the family research shell.

Ported from beepboop2025/liquilens-site scripts/research_shell.py at
dfb8f0899344b473c179d48ca08e94220333e868 (the family's one-header refresh), as
undertow-site did. Seiche changes: pages live under frontend/public, the shell
also links the family tokens and Seiche's static-page layer, and a page whose
own skip link targets a named section keeps that target. The SPA (frontend/index.html) renders the same
header in React (frontend/src/ResearchHeader.tsx) and is never touched here.

Run `python3 scripts/research_shell.py` after editing
frontend/public/research-ui/header.html; `--check` fails when a page drifts.
"""
from __future__ import annotations

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


def research_header(path: str) -> str:
    header = (PUBLIC / "research-ui/header.html").read_text().strip()
    path = path.removesuffix(".html")
    prefix, tabs = header.split('<nav class="research-tabs"', 1)
    tabs = tabs.replace(' aria-current="page"', "")
    destinations = re.findall(r'href="(/[^"#?]*)"', tabs)
    section = next(
        (item for item in sorted(set(destinations), key=len, reverse=True)
         if item != "/" and (path == item or path.startswith(item.rstrip("/") + "/"))),
        None,
    )
    if section:
        tabs = tabs.replace(f'href="{section}"', f'href="{section}" aria-current="page"', 1)
    return prefix + '<nav class="research-tabs"' + tabs


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
    main = re.search(r"<main\b[^>]*>", source)
    if target is None and main:
        identity = re.search(r'\bid="([^"]+)"', main[0])
        if identity:
            target = identity[1]
        else:
            source = source[: main.start()] + main[0].replace("<main", '<main id="main"', 1) + source[main.end():]
            target = "main"
    if target and target != "main":
        source = re.sub(r'class="research-skip" href="#[^"]*"', f'class="research-skip" href="#{target}"', source, count=1)
    return attach_context(source)


def page_path(name: str) -> str:
    rel = name.removeprefix("frontend/public/")
    return "/" + rel.removesuffix("index.html") if rel.endswith("index.html") else "/" + rel


def pages() -> list[str]:
    names = subprocess.check_output(["git", "ls-files", "frontend/public/*.html"], cwd=ROOT, text=True).splitlines()
    return [n for n in names if not n.startswith("frontend/public/research-ui/")]


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
    return changed


if __name__ == "__main__":
    check = "--check" in sys.argv[1:]
    changed = sync(check)
    if check and changed:
        print("Pages off the family shell: " + ", ".join(changed), file=sys.stderr)
        raise SystemExit(1)
    print(f"{'Checked' if check else 'Unified'} {len(pages())} public page shells; {len(changed)} changed")
