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
  ["src/research-ui.css", "953bb6d5315ca93f147dd70fb149edefabe9385c"],
  ["public/research-ui/research.css", "953bb6d5315ca93f147dd70fb149edefabe9385c"],
  ["src/family-tokens.css", "ba1c78e2c2eab22d9435beb5e90013bdbbd0d095"],
  ["public/research-ui/family-tokens.css", "ba1c78e2c2eab22d9435beb5e90013bdbbd0d095"],
  ["public/research-ui/context.js", "177f59d5b8cf9b0e620809f65464b6dbaf12a847"],
];

test("vendored family files are byte for byte the blobs their headers name", () => {
  for (const [path, id] of VENDORED) {
    const text = read(path);
    assert.match(text.split("\n")[0], /^\/\* VENDORED\. Source: beepboop2025\/liquilens-site /, path);
    assert.ok(text.split("\n").slice(0, 5).join("\n").includes(id), `${path} header names ${id}`);
    assert.equal(blob(vendoredBody(text)), id, `${path} drifted from the family blob`);
  }
});

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
  // articles/ and dispatches/ are generated at publish time by backend/seiche/dispatch_pages.py.
  const pages = walk(PUBLIC).filter((p) => p.endsWith(".html") && !/\/(research-ui|articles|dispatches)\//.test(p));
  assert.ok(pages.length >= 20, "static pages found");
  const shell = readFileSync(join(PUBLIC, "research-ui/header.html"), "utf8").trim();
  const mast = shell.slice(0, shell.indexOf('<nav class="research-tabs"'));
  for (const path of pages) {
    const html = readFileSync(path, "utf8");
    const name = relative(PUBLIC, path);
    assert.match(html, /<html\b[^>]*class="[^"]*research-interface[^"]*"[^>]*data-product="seiche"/, `${name} html`);
    for (const href of ["/research-ui/research.css", "/research-ui/family-tokens.css", "/research-ui/seiche-pages.css"]) {
      assert.ok(html.includes(`<link rel="stylesheet" href="${href}">`), `${name} links ${href}`);
    }
    assert.equal(html.split('<header class="research-header">').length - 1, 1, `${name} has one family header`);
    assert.ok(html.includes(mast.replace('<a class="research-skip" href="#main">Skip to content</a>', "")), `${name} mast matches header.html`);
    assert.ok(html.includes('<script type="module" src="/research-ui/context.js"></script>'), `${name} loads context.js`);
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
  assert.ok(shell.includes('<a class="research-api" href="/developers">API &amp; MCP</a>'));
  assert.ok(spa.includes('aria-current="page">Seiche</a>'), "desk switcher marks Seiche");
  assert.doesNotMatch(spa, /<small>/, "the wordmark carries no subtitle");
});
