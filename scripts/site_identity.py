"""Seiche's search-facing identity on every hand-maintained static page in frontend/public.

Two pieces of page chrome, kept here as one source like scripts/research_shell.py:
- the icon trio (favicon.svg, apple-touch-icon.png, manifest.webmanifest);
- the operator line inside the page footer: LIQUILENS PRIVATE LIMITED, its CIN, a contact
  address and the privacy and terms links. Seiche is free: the line never carries access terms.

apply() is idempotent. scripts/build_question_pages.py applies it to the Seiche question pages it
renders; sync() refreshes the other static pages. Pages written at publish time (articles/,
dispatches/), the 404 page and the three Google-Extended pages held to reviewed bytes by
docs/google-extended-reviewed-pages.json are left alone.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend/public"

ICONS_START, ICONS_END = "<!-- SITE-ICONS:START -->", "<!-- SITE-ICONS:END -->"
ICONS = (
    ICONS_START
    + '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
    + '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
    + '<link rel="manifest" href="/manifest.webmanifest">'
    + ICONS_END
)
COMPANY_START, COMPANY_END = "<!-- COMPANY-LINE:START -->", "<!-- COMPANY-LINE:END -->"
COMPANY = (
    COMPANY_START
    + '<p class="company-line">Seiche · LIQUILENS PRIVATE LIMITED · CIN U62011RJ2026PTC116792 · '
    + 'Jaipur, Rajasthan, India · <a href="mailto:mrinal@liquilens.in">mrinal@liquilens.in</a> · '
    + '<a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></p>'
    + COMPANY_END
)
ICON_LINK = re.compile(
    r'[ \t]*<link\b[^>]*\brel=["\'](?:shortcut icon|icon|apple-touch-icon|manifest)["\'][^>]*>[ \t]*\n?', re.I
)
MARKED_ICONS = re.compile(re.escape(ICONS_START) + r".*?" + re.escape(ICONS_END), re.S)
MARKED_COMPANY = re.compile(re.escape(COMPANY_START) + r".*?" + re.escape(COMPANY_END), re.S)
OPAQUE = re.compile(r"<(script|style|template)\b.*?</\1\s*>|<!--.*?-->", re.S | re.I)
GENERATED = ("research-ui/", "articles/", "dispatches/")


def _last_markup(source: str, tag: str, end: int | None = None) -> int:
    end = len(source) if end is None else end
    opaque = [match.span() for match in OPAQUE.finditer(source, 0, end)]
    position = source.rfind(tag, 0, end)
    while position >= 0 and any(start <= position < stop for start, stop in opaque):
        position = source.rfind(tag, 0, position)
    return position


def apply(source: str) -> str:
    head_end = source.find("</head>")
    if head_end < 0 or re.search(r'http-equiv=["\']refresh', source, re.I):
        return source
    head, rest = source[:head_end], source[head_end:]
    if ICONS_START in head:
        head = MARKED_ICONS.sub(lambda _: ICONS, head, count=1)
        before, marked, after = head.partition(ICONS)
        head = ICON_LINK.sub("", before) + marked + ICON_LINK.sub("", after)
    else:
        head = ICON_LINK.sub("", head)
        anchor = re.search(r'<meta\b[^>]*\bname=["\']viewport["\'][^>]*>', head, re.I)
        if anchor:
            head = head[: anchor.end()] + ICONS + head[anchor.end():]
    source = head + rest
    if COMPANY_START in source:
        return MARKED_COMPANY.sub(lambda _: COMPANY, source, count=1)
    body_end = _last_markup(source, "</body>")
    if body_end < 0:
        return source
    footer_end = _last_markup(source, "</footer>", body_end)
    if footer_end >= 0:
        return source[:footer_end] + COMPANY + source[footer_end:]
    return source[:body_end] + '<footer class="company-footer">' + COMPANY + "</footer>\n" + source[body_end:]


def reviewed_pages() -> set[str]:
    record = json.loads((ROOT / "docs/google-extended-reviewed-pages.json").read_text(encoding="utf-8"))
    return {page["source_file"].removeprefix("frontend/public/") for page in record["pages"]}


def static_pages() -> list[str]:
    reviewed = reviewed_pages()
    names = sorted(path.relative_to(PUBLIC).as_posix() for path in PUBLIC.rglob("*.html"))
    return [name for name in names
            if name != "404.html" and not name.startswith(GENERATED) and name not in reviewed]


def sync(check: bool = False) -> list[str]:
    changed = []
    for name in static_pages():
        path = PUBLIC / name
        source = path.read_text(encoding="utf-8")
        rendered = apply(source)
        if rendered != source:
            changed.append(name)
            if not check:
                path.write_text(rendered, encoding="utf-8")
    return changed


if __name__ == "__main__":
    check = "--check" in sys.argv[1:]
    changed = sync(check=check)
    if check and changed:
        raise SystemExit("Static pages without the current icon trio or operator line: " + ", ".join(changed))
    print(("Checked" if check else "Updated") + f" {len(changed)} static pages")
