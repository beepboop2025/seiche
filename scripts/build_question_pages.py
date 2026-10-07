#!/usr/bin/env python3
"""Render reviewed question pages. No network, data refresh or clock substitution.

Run --check in release gates. Content and observation dates are authored in
questions/pages.json; changing a market reading never changes an editorial date.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend/public" if (ROOT / "backend/seiche").is_dir() else ROOT
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def esc(value):
    return html.escape(str(value), quote=True)


def link(pair):
    label, url = pair
    if not url.startswith(("https://", "/")) or url.startswith("//"):
        raise ValueError("Expected a public HTTPS or root-relative link")
    return f'<a href="{esc(url)}">{esc(label)}</a>'


def validate(catalog):
    paths = set()
    dt.date.fromisoformat(catalog["reviewed_on"])
    if not re.fullmatch(r"https://[a-z0-9.-]+", catalog["origin"]):
        raise ValueError("Canonical origin must be a bare HTTPS origin")
    for page in catalog["pages"]:
        slug = page["slug"]
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or slug in paths:
            raise ValueError("Invalid or duplicate stable question path")
        paths.add(slug)
        if len(page["answer"]) < 100 or not page["tables"] or not page["sources"]:
            raise ValueError("A question needs an answer, evidence and sources")
        for table in page["tables"]:
            if any(len(row) != len(table["columns"]) for row in table["rows"]):
                raise ValueError("Evidence table column mismatch")
        for source in page["sources"]:
            link(source)
        for observation in page["observations"]:
            dt.date.fromisoformat(observation)
            if observation > catalog["reviewed_on"]:
                raise ValueError("Observation cannot come after the review date")
        if not page["limits"] or not page["letter"]["date"]:
            raise ValueError("Limits and the dated letter are required")
        dt.date.fromisoformat(page["letter"]["date"])
    return catalog


def render(catalog, page):
    brand, origin = catalog["brand"], catalog["origin"]
    canonical = origin + "/questions/" + page["slug"] + "/"
    card = PUBLIC / "questions" / page["slug"] / "share.png"
    image_url = canonical + "share.png?v=" + hashlib.sha256(card.read_bytes()).hexdigest()[:16]
    sections = []
    for table in page["tables"]:
        head = "".join(f'<th scope="col">{esc(c)}</th>' for c in table["columns"])
        rows = "".join("<tr>" + "".join(f'<{("th scope=\"row\"" if i == 0 else "td")}>{esc(c)}</{("th" if i == 0 else "td")}>' for i, c in enumerate(row)) + "</tr>" for row in table["rows"])
        sections.append(f'<section><h2>{esc(table["title"])}</h2><p class="clock">{esc(table["clock"])}</p><div class="table-scroll" tabindex="0" role="region" aria-label="{esc(table["title"])}"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div><p class="table-note">{esc(table["note"])}</p></section>')
    for section in page["sections"]:
        paragraphs = "".join(f"<p>{esc(p)}</p>" for p in section["paragraphs"])
        sections.append(f'<section><h2>{esc(section["title"])}</h2>{paragraphs}</section>')
    sources = "".join(f"<li>{link(pair)}</li>" for pair in page["sources"])
    related = "".join(f"<li>{link(pair)}</li>" for pair in page["related"])
    limits = "".join(f"<li>{esc(item)}</li>" for item in page["limits"])
    letter = page["letter"]
    schema = {"@context": "https://schema.org", "@type": "Article", "headline": page["title"], "description": page["description"], "url": canonical, "mainEntityOfPage": canonical, "datePublished": catalog["published_on"], "dateModified": catalog["reviewed_on"], "author": {"@type": "Organization", "name": brand, "url": origin + "/"}, "publisher": {"@type": "Organization", "name": "LIQUILENS PRIVATE LIMITED", "url": "https://liquilens.in/about/"}, "image": image_url}
    live_script = '<script src="/questions/exit-research.js" defer></script>' if brand == "Undertow" else ""
    return f'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(page["title"])} | {esc(brand)}</title>
<meta name="description" content="{esc(page["description"])}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="article"><meta property="og:site_name" content="{esc(brand)}">
<meta property="og:title" content="{esc(page["title"])}"><meta property="og:description" content="{esc(page["description"])}"><meta property="og:url" content="{canonical}">
<meta property="og:image" content="{image_url}"><meta property="og:image:secure_url" content="{image_url}"><meta property="og:image:type" content="image/png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta property="og:image:alt" content="{esc(page["card_title"])}; dated research, not a live quote">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{esc(page["title"])}"><meta name="twitter:description" content="{esc(page["description"])}"><meta name="twitter:image" content="{image_url}"><meta name="twitter:image:alt" content="{esc(page["card_title"])}; dated research, not a live quote">
<link rel="stylesheet" href="/questions/questions.css">
<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False).replace('<', chr(92) + 'u003c')}</script>
{live_script}<script src="/questions/questions.js" defer></script>
</head><body data-product="{esc(page.get("current_product", brand.lower()))}" data-size="{page.get("size_usd", "")}">
<a class="skip" href="#main">Skip to answer</a>
<header><a class="brand" href="/">{esc(brand)}<small>{esc(catalog["category"])}</small></a><nav aria-label="Research"><a href="/questions/">Questions</a><a href="{esc(catalog["board"])}">Current board</a><a href="/articles/">Daily letters</a><a href="/developers{('/' if brand != 'Seiche' else '')}">API &amp; MCP</a></nav></header>
<main id="main"><article>
<div class="eyebrow">{esc(catalog["category"])} / evidence explained</div>
<h1>{esc(page["title"])}</h1>
<p class="answer">{esc(page["answer"])}</p>
<p class="identity">{esc(catalog["definition"])}</p>
<div class="review"><span>Explanation reviewed <time datetime="{catalog["reviewed_on"]}">{catalog["reviewed_on"]}</time></span><span>Observations: {esc(', '.join(page["observations"]))}</span></div>
<p class="boundary">{esc(page["boundary"])}</p>
{''.join(sections)}
<section class="current" aria-labelledby="current-title"><h2 id="current-title">Check the latest published evidence</h2><p>The tables above are retained, dated examples. Loading newer evidence below does not change their dates or this explanation.</p><button type="button" id="load-current" hidden>{esc(page.get("current_label", "Load latest published readings"))}</button><div id="current-result" aria-live="polite"></div><p>{link(["Open the current research board", catalog["board"]])} · {link(["Browse all daily letters", "/articles/"])}</p></section>
<section><h2>What this evidence cannot establish</h2><ul>{limits}</ul></section>
<section class="letter"><h2>The dated letter</h2><p>{link([letter["title"], letter["url"]])} · <time datetime="{letter["date"]}">{letter["date"]}</time></p><p>{esc(letter["note"])}</p></section>
<section><h2>Sources and method</h2><ol class="sources">{sources}</ol><p><a href="/questions/evidence-2026-10-06.json">Retained facts and source fingerprints</a>. Observation dates describe the source data; retrieval and publication dates describe our handling of it.</p></section>
<section><h2>Related questions</h2><ul>{related}</ul></section>
</article></main><footer><p>LIQUILENS PRIVATE LIMITED · Research with dates, sources and limits.</p><nav aria-label="Products"><a href="https://liquilens.in/">LiquiLens: institutions</a><a href="https://seiche.info/">Seiche: funding</a><a href="https://liquilens-undertow.com/">Undertow: exit costs</a></nav><p>This page keeps its address. Daily conditions belong to the dated evidence and letters.</p></footer>
</body></html>
'''


def index(catalog):
    items = "".join(f'<li><h2>{link([p["title"], "/questions/" + p["slug"] + "/"])}</h2><p>{esc(p["description"])}</p></li>' for p in catalog["pages"])
    origin, brand = catalog["origin"], catalog["brand"]
    card = PUBLIC / "questions/share.png"
    image_url = origin + "/questions/share.png?v=" + hashlib.sha256(card.read_bytes()).hexdigest()[:16]
    title = catalog["category"] + " explained"
    pairs = {"og:type":"website", "og:site_name":brand, "og:url":origin+"/questions/", "og:title":title, "og:description":catalog["definition"], "og:image":image_url, "og:image:secure_url":image_url, "og:image:type":"image/png", "og:image:width":"1200", "og:image:height":"630", "og:image:alt":title, "twitter:card":"summary_large_image", "twitter:title":title, "twitter:description":catalog["definition"], "twitter:image":image_url, "twitter:image:alt":title}
    metadata = "".join(f'<meta {("name" if k.startswith("twitter:") else "property")}="{k}" content="{esc(v)}">' for k,v in pairs.items())
    schema = json.dumps({"@context":"https://schema.org", "@type":"CollectionPage", "name":title, "url":origin+"/questions/", "image":image_url})

    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(catalog["category"])} questions | {brand}</title><meta name="description" content="{esc(catalog["definition"])}"><link rel="canonical" href="{origin}/questions/">{metadata}<script type="application/ld+json">{schema}</script><link rel="stylesheet" href="/questions/questions.css"></head><body><a class="skip" href="#main">Skip to questions</a><header><a class="brand" href="/">{brand}<small>{esc(catalog["category"])}</small></a><nav><a href="{esc(catalog["board"])}">Current board</a><a href="/articles/">Daily letters</a></nav></header><main id="main"><div class="eyebrow">Questions with dated answers</div><h1>{esc(catalog["category"])} explained</h1><p class="answer">{esc(catalog["definition"])}</p><p>Stable explanations, retained numbers, primary sources and explicit limits. Reviewed {catalog["reviewed_on"]}.</p><ul class="question-list">{items}</ul></main><footer>LIQUILENS PRIVATE LIMITED · Public research</footer></body></html>\n'''


def outputs(catalog):
    result = {PUBLIC / "questions/index.html": index(catalog)}
    result.update({PUBLIC / "questions" / p["slug"] / "index.html": render(catalog, p) for p in catalog["pages"]})
    if catalog["brand"] == "Undertow":
        from research_shell import unify_html
        result = {path: unify_html(content.replace("<header>", '<header class="family-header">', 1), "/" + str(path.relative_to(PUBLIC)).removesuffix("index.html")) for path, content in result.items()}
        result[PUBLIC / "questions/exit-research.js"] = (ROOT / "app/exit-research.js").read_text()
    return result


def check_sitemap(catalog):
    root = ET.fromstring((PUBLIC / "sitemap.xml").read_text())
    pairs = [(n.findtext("s:loc", namespaces=NS), n.findtext("s:lastmod", namespaces=NS)) for n in root.findall("s:url", NS)]
    for path in ["/questions/"] + ["/questions/" + p["slug"] + "/" for p in catalog["pages"]]:
        matches = [date for url, date in pairs if url == catalog["origin"] + path]
        if matches != [catalog["reviewed_on"]]:
            raise ValueError(f"Sitemap must retain exactly one editorial date for {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = validate(json.loads((PUBLIC / "questions/pages.json").read_text()))
    changed = []
    for path, content in outputs(catalog).items():
        if not path.exists() or path.read_text() != content:
            changed.append(str(path.relative_to(PUBLIC)))
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
    if args.check:
        check_sitemap(catalog)
        if changed:
            print("Question pages differ from reviewed source: " + ", ".join(changed), file=sys.stderr)
            return 1
        for p in catalog["pages"]:
            card = PUBLIC / "questions" / p["slug"] / "share.png"
            if not card.is_file() or card.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError(f"Missing contextual share card: {p['slug']}")
    print(f"{catalog['brand']}: {len(catalog['pages'])} reviewed question pages " + ("verified" if args.check else "rendered"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
