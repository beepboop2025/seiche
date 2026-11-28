"""The family research shell for the pages the backend renders at publish time.

The publish pipeline writes methodology.html, skeptic.html, ampleness.html and
referee.html into frontend/public just before the frontend tests run, so each of
them must carry the same shell as the committed static pages
(frontend/tests/familyInterface.test.mjs): the research-interface root, the three
family stylesheets, exactly one family header, a skip link with a target, and the
context script. Until 0.16.15 none of the four did, and the first one the test
reached (ampleness.html) stopped the 0.16.14 site publication.

scripts/research_shell.py keeps the committed static pages on this shell. These
four are never committed, so their shell belongs here, in the generators.

TEMPLATE is a byte copy of frontend/public/research-ui/header.html. The backend
package ships without the frontend tree, so the copy is held as a constant and
backend/tests/test_family_shell.py fails when the two drift. header(path) applies
the family's grouping rule to it, exactly as scripts/research_shell.py does for the
committed pages: the page's group is marked current, only that group's sub-tabs
are kept, and the data-owns and data-for attributes never reach a page. All of
these pages sit under Research.
"""

from __future__ import annotations

import re

HTML_OPEN = '<html lang="en" class="research-interface" data-product="seiche">'

# The family first, so each page's own inline rules still lay out its content.
STYLE = (
    '<link rel="stylesheet" href="/research-ui/research.css">'
    '<link rel="stylesheet" href="/research-ui/family-tokens.css">'
    '<link rel="stylesheet" href="/research-ui/seiche-pages.css">'
)

CONTEXT = '<script type="module" src="/research-ui/context.js"></script>'

# The skip link in the header points here; each page puts this id on its first heading.
SKIP_TARGET = "main"

TEMPLATE = (
    '<a class="research-skip" href="#main">Skip to content</a><header class="research-header"><div class="research-mast"><a class="research-identity" href="/"><svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="14"/><path d="M7 17c5-8 9 8 14 0s7-5 8-2M7 23c5-8 9 8 14 0s7-5 8-2"/></svg><span><strong>Seiche</strong></span></a><nav class="research-network" aria-label="Products"><a href="https://liquilens.in/">LiquiLens</a><a href="/" aria-current="page">Seiche</a><a href="https://liquilens-undertow.com/">Undertow</a></nav><a class="research-api" href="/developers" data-owns="/developers /agents/ /datasets/">API &amp; MCP</a></div><nav class="research-tabs" aria-label="Seiche sections"><a href="/">Overview</a><a href="/money-markets/">Money markets</a><a href="/markets/">Markets</a><a href="/gift-city/">India</a><a href="/articles/" data-owns="/articles/ /investigations/ /dispatches/ /methodology /skeptic /ampleness /referee /use-cases /use-cases/ /questions/ /guide /weekly/">Research</a><a href="https://liquilens.in/about/">Company</a></nav><nav class="research-subtabs" aria-label="Money markets" data-for="/money-markets/"><a href="/money-markets/">Evidence map</a><a href="/#today">Funding desk</a><a href="/#money%20markets">Money-market desk</a><a href="/#board">Funding board</a></nav><nav class="research-subtabs" aria-label="Markets" data-for="/markets/"><a href="/markets/">World markets</a><a href="/markets/forex/">Forex</a><a href="/markets/capital-markets/">Capital markets</a><a href="/markets/china-macro/">China macro</a><a href="/#corpus">Market atlas</a><a href="/#workbench">Workbench</a></nav><nav class="research-subtabs" aria-label="India" data-for="/gift-city/"><a href="/gift-city/">GIFT City guide</a><a href="/#gift%20city">GIFT City desk</a><a href="/#geography">India geography</a></nav><nav class="research-subtabs" aria-label="Research" data-for="/articles/"><a href="/articles/">Published research</a><a href="/investigations/">Investigations</a><a href="/dispatches/">Dispatches</a><a href="/methodology">Methodology</a><a href="/skeptic">Skeptic pack</a><a href="/use-cases">Use cases</a><a href="/questions/">Questions</a><a href="/guide">Guide</a><a href="/#research">Research catalog</a></nav><nav class="research-subtabs" aria-label="API and MCP" data-for="/developers"><a href="/developers">Developer guide</a><a href="/agents/quant/">Quant workflows</a><a href="/datasets/direct-ofr/">OFR dataset</a></nav></header>'
)


def _covers(path: str, prefix: str) -> bool:
    return path == prefix or (prefix != "/" and prefix.endswith("/") and path.startswith(prefix))


def header(path: str) -> str:
    """The family header for one page, as scripts/research_shell.py renders it for that path."""
    path = path.removesuffix(".html") or "/"
    mast, rest = TEMPLATE.split('<nav class="research-tabs"', 1)
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


# A generated page's own palette, as family tokens. One accent for every product:
# no page sets a Seiche colour of its own.
#
# research.css declares most of these names again on html.research-interface, and
# that selector outranks :root, so for those names the family's mapping is the one
# a reader sees. Each one here therefore points at the same family token the
# family points it at (backend/tests/test_family_shell.py holds the two together);
# the literal after the comma is used only when the family stylesheets fail to
# load. In the family, --mono is the reading font: numerals and labels are set in
# it with tabular figures.
#
# --edge and --code are this page's own names. The family does not declare them,
# so they hold. --code is the real monospace, for the blocks a reader copies:
# commands and citations.
ROOT_TOKENS = (
    ":root { --bg:var(--research-bg,#000); --panel:var(--research-surface,#0b0d15); "
    "--edge:var(--research-line,#1c1f2b); --text:var(--research-text,#edeef4);\n"
    "        --dim:var(--research-muted,#9aa0b6); --faint:var(--research-muted,#787f95); "
    "--accent:var(--research-accent,#abc7ff);\n"
    "        --accent-soft:var(--research-accent,#abc7ff); --accent-bright:var(--research-accent,#abc7ff);\n"
    "        --mono:var(--research-font,-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif);\n"
    "        --code:var(--research-code,ui-monospace,'SF Mono',Menlo,Consolas,monospace);\n"
    "        color-scheme:dark; }"
)


def heading(text: str) -> str:
    """The page's first heading, carrying the skip link's target."""
    return f'<h1 id="{SKIP_TARGET}">{text}</h1>'
