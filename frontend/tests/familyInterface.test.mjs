// The family interface (record edition) on every Seiche surface: vendored family
// files stay byte for byte, one accent for every product, and every static page
// carries the same family header as the desk.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";

const FRONTEND = new URL("..", import.meta.url).pathname;
const PUBLIC = join(FRONTEND, "public");
const read = (path) => readFileSync(join(FRONTEND, path), "utf8");

// git's blob id: sha1("blob <bytes>\0" + content)
const blob = (text) => {
  const bytes = Buffer.from(text, "utf8");
  return createHash("sha1").update(Buffer.concat([Buffer.from(`blob ${bytes.length}\0`), bytes])).digest("hex");
};
const vendoredBody = (text) => text.split("\n").slice(5).join("\n");

const VENDORED = [
  ["src/research-ui.css", "bb10f8141177510140ae8dbd7fc5c8a1e504e446"],
  ["public/research-ui/research.css", "bb10f8141177510140ae8dbd7fc5c8a1e504e446"],
  ["src/family-tokens.css", "ba1c78e2c2eab22d9435beb5e90013bdbbd0d095"],
  ["public/research-ui/family-tokens.css", "ba1c78e2c2eab22d9435beb5e90013bdbbd0d095"],
  ["public/research-ui/context.js", "ee3bca05a61e5fbdce0b60cab07d9c59adef3c28"],
];

test("vendored family files are byte for byte the blobs their headers name", () => {
  for (const [path, id] of VENDORED) {
    const text = read(path);
    assert.match(text.split("\n")[0], /^\/\* VENDORED\. Source: beepboop2025\/liquilens-site /, path);
    assert.ok(text.split("\n").slice(0, 5).join("\n").includes(id), `${path} header names ${id}`);
    assert.equal(blob(vendoredBody(text)), id, `${path} drifted from the family blob`);
  }
});

// header.html groups pages with data-owns and data-for; neither attribute reaches a page, and the
// mast's own link (API & MCP) is current on its pages. The product switcher keeps its state.
const unattributed = (html) => html.replace(/ data-(?:owns|for)="[^"]*"/g, "");
const plainMast = (html) => unattributed(html).replace(/(<a class="research-api"[^>]*?) aria-current="page"/, "$1");
const currentLinks = (header) => [...header.matchAll(/<a[^>]*aria-current="page"[^>]*>([^<]*)<\/a>/g)].map((match) => match[1]);

const walk = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  return statSync(path).isDirectory() ? walk(path) : [path];
});

test("one accent for every product: no Seiche stylesheet overrides the family accent", () => {
  const owned = [
    ...walk(join(FRONTEND, "src")).filter((p) => p.endsWith(".css")),
    ...walk(PUBLIC).filter((p) => p.endsWith(".css")),
  ].filter((p) => !/(?:research-ui\.css|family-tokens\.css|research-ui\/research\.css)$/.test(p));
  for (const path of owned) {
    const css = readFileSync(path, "utf8");
    assert.doesNotMatch(css, /--research-accent\s*:/, `${relative(FRONTEND, path)} sets its own accent`);
    assert.doesNotMatch(css, /\[data-product=["']?seiche/, `${relative(FRONTEND, path)} styles by product`);
  }
});

test("every static page carries the family header, tokens and context script", () => {
  // articles/ and dispatches/ are generated at publish time by backend/seiche/dispatch_pages.py;
  // the Google-Extended pages are held to their reviewed bytes (docs/google-extended-reviewed-pages.json).
  const reviewed = new Set(JSON.parse(readFileSync(join(FRONTEND, "../docs/google-extended-reviewed-pages.json"), "utf8"))
    .pages.map((page) => join(FRONTEND, "..", page.source_file)));
  const pages = walk(PUBLIC).filter((p) => p.endsWith(".html") && !/\/(research-ui|articles|dispatches)\//.test(p) && !reviewed.has(p));
  assert.ok(pages.length >= 20, "static pages found");
  const shell = readFileSync(join(PUBLIC, "research-ui/header.html"), "utf8").trim();
  const mast = unattributed(shell.slice(0, shell.indexOf('<nav class="research-tabs"')));
  for (const path of pages) {
    const html = readFileSync(path, "utf8");
    const name = relative(PUBLIC, path);
    assert.match(html, /<html\b[^>]*class="[^"]*research-interface[^"]*"[^>]*data-product="seiche"/, `${name} html`);
    for (const href of ["/research-ui/research.css", "/research-ui/family-tokens.css", "/research-ui/seiche-pages.css"]) {
      assert.ok(html.includes(`<link rel="stylesheet" href="${href}">`), `${name} links ${href}`);
    }
    assert.equal(html.split('<header class="research-header">').length - 1, 1, `${name} has one family header`);
    assert.ok(plainMast(html).includes(mast.replace('<a class="research-skip" href="#main">Skip to content</a>', "")), `${name} mast matches header.html`);
    assert.doesNotMatch(html.match(/<header class="research-header">[\s\S]*?<\/header>/)[0], /data-(?:owns|for)=/, `${name} keeps the grouping attributes out`);
    assert.ok((html.match(/<nav class="research-subtabs"/g) || []).length <= 1, `${name} shows at most one sub-tab row`);
    // The no-JS canonical money-markets answer keeps exactly its one ld+json script.
    const scriptFree = name === "money-markets/index.html";
    assert.equal(html.includes('<script type="module" src="/research-ui/context.js"></script>'), !scriptFree, `${name} context.js`);
    const skip = html.match(/class="research-skip" href="#([^"]+)"/);
    assert.ok(skip && new RegExp(`\\bid="${skip[1]}"`).test(html), `${name} skip link has a target`);
    assert.doesNotMatch(html, /fonts\.googleapis\.com|fonts\.gstatic\.com/, `${name} loads a web font`);
  }
});

test("the desk header and the static header share one mast", () => {
  const spa = read("src/ResearchHeader.tsx");
  const shell = readFileSync(join(PUBLIC, "research-ui/header.html"), "utf8");
  for (const part of ['className="research-identity"', 'className="research-network" aria-label="Products"', "API &amp; MCP"]) {
    assert.ok(spa.includes(part), `desk header: ${part}`);
  }
  assert.ok(shell.includes('class="research-network" aria-label="Products"><a href="https://liquilens.in/">LiquiLens</a><a href="/" aria-current="page">Seiche</a><a href="https://liquilens-undertow.com/">Undertow</a></nav>'));
  assert.ok(unattributed(shell).includes('<a class="research-api" href="/developers">API &amp; MCP</a>'));
  // Seiche is free: the family mast carries API & MCP and no Pricing link.
  assert.doesNotMatch(shell, /research-pricing|Pricing/);
  assert.doesNotMatch(spa, /research-pricing|Pricing/);
  assert.ok(spa.includes('aria-current="page">Seiche</a>'), "desk switcher marks Seiche");
  assert.doesNotMatch(spa, /<small>/, "the wordmark carries no subtitle");
});

test("a page shows its group, only that group's sub-tabs and its own sub-tab", () => {
  const header = (name) => readFileSync(join(PUBLIC, name), "utf8").match(/<header class="research-header">[\s\S]*?<\/header>/)[0];
  const expected = {
    "money-markets/index.html": ["Seiche", "Money markets", "Evidence map"],
    "markets/index.html": ["Seiche", "Markets", "World markets"],
    "markets/forex/index.html": ["Seiche", "Markets", "Forex"],
    "gift-city/index.html": ["Seiche", "India", "GIFT City guide"],
    "questions/why-sofr-above-iorb/index.html": ["Seiche", "Research", "Questions"],
    "investigations/index.html": ["Seiche", "Research", "Investigations"],
    "guide.html": ["Seiche", "Research", "Guide"],
    "use-cases.html": ["Seiche", "Research", "Use cases"],
    "developers.html": ["Seiche", "API &amp; MCP", "Developer guide"],
    "agents/quant/index.html": ["Seiche", "API &amp; MCP", "Quant workflows"],
    "datasets/direct-ofr/index.html": ["Seiche", "API &amp; MCP", "OFR dataset"],
    "privacy.html": ["Seiche"],
  };
  for (const [name, current] of Object.entries(expected)) {
    assert.deepEqual(currentLinks(header(name)), current, name);
  }
  assert.equal((header("privacy.html").match(/research-subtabs/g) || []).length, 0, "an ungrouped page shows no sub-tab row");
  // Six tabs at most, and every tab label names one destination.
  const shell = readFileSync(join(PUBLIC, "research-ui/header.html"), "utf8");
  const tabs = [...shell.match(/<nav class="research-tabs"[\s\S]*?<\/nav>/)[0].matchAll(/<a\b[^>]*>([^<]*)<\/a>/g)];
  assert.ok(tabs.length <= 6, "six tabs at most");
  const destinations = new Map();
  for (const [, href, label] of shell.matchAll(/<a\b[^>]*?href="([^"]+)"[^>]*>([^<]*)<\/a>/g)) {
    if (destinations.has(label)) assert.equal(destinations.get(label), href, `${label} opens one destination`);
    destinations.set(label, href);
  }
});

test("the desk front page shows the same header before the app loads", () => {
  const shell = read("index.html");
  const root = shell.slice(shell.indexOf('<div id="root">'));
  const header = root.match(/<header class="research-header">[\s\S]*?<\/header>/)[0];
  assert.deepEqual(currentLinks(header), ["Seiche", "Overview"]);
  assert.doesNotMatch(header, /data-(?:owns|for)=|research-subtabs/);
  assert.doesNotMatch(shell, /product-nav|For developers/, "the old pre-load menu is gone");
  assert.match(root, /<main id="main"/, "the pre-load skip link has a target");
});
