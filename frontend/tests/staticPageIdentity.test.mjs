// Every hand-maintained static page carries the same search-facing identity: the Seiche icon trio,
// a canonical URL, Open Graph and Twitter cards, structured data, and the operator line with the
// CIN, a contact address and the privacy and terms links inside a footer.
//
// Left out on purpose: 404.html; articles/ and dispatches/ (written at publish time by
// backend/seiche/dispatch_pages.py); and the three Google-Extended pages held to reviewed bytes by
// docs/google-extended-reviewed-pages.json, which change only through the owner's dated re-review.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

const FRONTEND = new URL("..", import.meta.url).pathname;
const PUBLIC = join(FRONTEND, "public");
const REVIEWED = JSON.parse(readFileSync(join(FRONTEND, "..", "docs", "google-extended-reviewed-pages.json"), "utf8"))
  .pages.map((page) => page.source_file.replace(/^frontend\/public\//, ""));
const SKIP = (name) => name === "404.html" || /^(research-ui|articles|dispatches)\//.test(name) || REVIEWED.includes(name);

const ICONS = '<!-- SITE-ICONS:START --><link rel="icon" type="image/svg+xml" href="/favicon.svg">'
  + '<link rel="apple-touch-icon" href="/apple-touch-icon.png"><link rel="manifest" href="/manifest.webmanifest">'
  + "<!-- SITE-ICONS:END -->";
const COMPANY = /<!-- COMPANY-LINE:START --><p class="company-line">([\s\S]*?)<\/p><!-- COMPANY-LINE:END -->/g;
const ICON_LINK = /<link\b[^>]*\brel=["'](?:shortcut icon|icon|apple-touch-icon|manifest)["'][^>]*>/gi;
const meta = (head, attr, key) => new RegExp(`<meta\\b(?=[^>]*\\b${attr}=["']${key.replace(/[.:]/g, "\\$&")}["'])[^>]*\\bcontent=["']([^"']+)["']`, "i").exec(head)?.[1];

const walk = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  return statSync(path).isDirectory() ? walk(path) : [path];
});
const pages = walk(PUBLIC)
  .filter((path) => path.endsWith(".html"))
  .map((path) => [relative(PUBLIC, path), readFileSync(path, "utf8")])
  .filter(([name]) => !SKIP(name));

test("the static page set is the one this guard was written for", () => {
  assert.ok(pages.length >= 20, `only ${pages.length} static pages found`);
  assert.equal(REVIEWED.length, 3);
});

for (const [name, source] of pages) {
  test(`${name} carries the icon trio, canonical, social cards and structured data`, () => {
    const head = source.slice(0, source.indexOf("</head>"));
    assert.equal(head.split(ICONS).length - 1, 1, "one icon block");
    assert.deepEqual(head.replace(ICONS, "").match(ICON_LINK) ?? [], [], "no other icon link");
    assert.match(head, /<link\b[^>]*rel=["']canonical["'][^>]*href=["']https:\/\/seiche\.info\//);
    for (const key of ["og:title", "og:description", "og:image", "og:url"]) assert.ok(meta(head, "property", key), key);
    assert.ok(meta(head, "name", "twitter:card"), "twitter:card");
    const blocks = [...source.matchAll(/<script type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/g)];
    assert.ok(blocks.length >= 1, "structured data");
    for (const block of blocks) {
      const text = JSON.stringify(JSON.parse(block[1]));
      assert.doesNotMatch(text, /"logo":"https:\/\/seiche\.info\/og2?\.png"/, "logo is the square mark, not a social card");
    }
  });

  test(`${name} names the operator once, inside a footer`, () => {
    const lines = [...source.matchAll(COMPANY)];
    assert.equal(lines.length, 1);
    const at = lines[0].index;
    assert.ok(source.lastIndexOf("<footer", at) > source.lastIndexOf("</footer>", at), "inside an open footer");
    const line = lines[0][1];
    for (const fact of ["LIQUILENS PRIVATE LIMITED", "CIN U62011RJ2026PTC116792", "mailto:mrinal@liquilens.in", 'href="/privacy"', 'href="/terms"']) {
      assert.ok(line.includes(fact), fact);
    }
    // Seiche is free forever: the company line never carries price or access-tier words.
    assert.doesNotMatch(line, /\b(price|pricing|paid|subscribe|subscription|premium|plan)\b/i);
    assert.doesNotMatch(line, /[\u2013\u2014]/);
  });
}

test("the home shell names a square logo for the Seiche organization", () => {
  const shell = readFileSync(join(FRONTEND, "index.html"), "utf8");
  const graph = [...shell.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)]
    .flatMap((match) => JSON.parse(match[1])["@graph"] ?? []);
  const seiche = graph.find((node) => node["@id"] === "https://seiche.info/#organization");
  assert.deepEqual(seiche.logo, { "@type": "ImageObject", url: "https://seiche.info/icons/pwa-512.png", width: 512, height: 512 });
});
