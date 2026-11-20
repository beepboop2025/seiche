// House rule and family contract ("Plain copy: sentence case, active voice, no en or em dashes"):
// no en dash (U+2013) or em dash (U+2014) ships in Seiche's own frontend copy.
//
// What this reads, because it is what reaches a user:
// - every string literal, template literal part, JSX text and JSX attribute string compiled from
//   frontend/src (.ts, .tsx, .js, .jsx, .mjs), read through the TypeScript parser so escapes such as
//   "\u2014" and HTML entities in JSX are caught as the characters they become;
// - CSS in frontend/src outside comments (a `content:` string is drawn on screen);
// - frontend/index.html, the shell every page load serves.
//
// What it leaves out, on purpose:
// - comments (the build strips them; they never reach a user);
// - regular-expression literals (code, not copy: a pattern that normalises producer text may name a dash);
// - text the API supplies at view time (producer text; the family evidence contract normalises it when rendered);
// - frontend/public (static pages; some are held to reviewed bytes by docs/google-extended-reviewed-pages.json,
//   so they need their own reviewed sweep).
//
// A missing value prints a state word ("Unavailable"), never a dash: see the family evidence contract.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import ts from "typescript";

const FRONTEND = new URL("..", import.meta.url).pathname;
const SRC = join(FRONTEND, "src");
const DASH = /[\u2013\u2014]/;
const ENTITY = /&(?:[mn]dash|#821[12]|#x201[34]);/i;

const walk = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  return statSync(path).isDirectory() ? walk(path) : [path];
});

export function shippedDashes(name, source) {
  const kind = /\.(tsx|jsx)$/.test(name) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true, kind);
  const found = [];
  const visit = (node) => {
    const k = node.kind;
    const text = ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isJsxText(node)
      || k === ts.SyntaxKind.TemplateHead || k === ts.SyntaxKind.TemplateMiddle || k === ts.SyntaxKind.TemplateTail;
    const jsx = ts.isJsxText(node) || (ts.isStringLiteral(node) && node.parent && ts.isJsxAttribute(node.parent));
    if (text && (DASH.test(node.text) || (jsx && ENTITY.test(node.text)))) {
      const { line } = file.getLineAndCharacterOfPosition(node.getStart(file));
      found.push(`${name}:${line + 1}: ${JSON.stringify(node.text.trim()).slice(0, 120)}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

export function cssDashes(name, css) {
  // Blank comments, keep strings and line numbers, then report any dash left.
  let out = "";
  for (let i = 0; i < css.length;) {
    if (css.startsWith("/*", i)) {
      const end = css.indexOf("*/", i + 2);
      const stop = end < 0 ? css.length : end + 2;
      out += css.slice(i, stop).replace(/[^\n]/g, " ");
      i = stop;
    } else if (css[i] === '"' || css[i] === "'") {
      let j = i + 1;
      while (j < css.length && css[j] !== css[i]) j += css[j] === "\\" ? 2 : 1;
      out += css.slice(i, j + 1);
      i = j + 1;
    } else {
      out += css[i++];
    }
  }
  return out.split("\n").flatMap((line, n) => (DASH.test(line) || /\\201[34]/i.test(line) ? [`${name}:${n + 1}: ${line.trim().slice(0, 120)}`] : []));
}

test("no en or em dash in any string, template or JSX text compiled from frontend/src", () => {
  const files = walk(SRC).filter((p) => /\.(tsx?|jsx?|mjs)$/.test(p));
  assert.ok(files.length > 100, "frontend sources found");
  const found = files.flatMap((p) => shippedDashes(relative(FRONTEND, p), readFileSync(p, "utf8")));
  assert.deepEqual(found, [], `use a colon, comma, semicolon, parentheses or "to" instead:\n${found.join("\n")}`);
});

test("no en or em dash in frontend/src CSS outside comments, or in the index.html shell", () => {
  const sheets = walk(SRC).filter((p) => p.endsWith(".css"));
  assert.ok(sheets.length > 5, "stylesheets found");
  const found = sheets.flatMap((p) => cssDashes(relative(FRONTEND, p), readFileSync(p, "utf8")));
  const shell = readFileSync(join(FRONTEND, "index.html"), "utf8");
  if (DASH.test(shell) || ENTITY.test(shell)) found.push("index.html");
  assert.deepEqual(found, []);
});

test("the guard reads what ships and only that", () => {
  const sample = [
    'const a = "\\u2014";',                                   // escaped em dash in a string: ships
    "const b = <p>one\n two \u2013 three</p>;",                // en dash inside multi-line JSX text: ships
    "const c = <p title=\"x &mdash; y\">z</p>;",               // entity in a JSX attribute: ships
    "const d = `${a} \u2014 ${b}`;",                           // template part: ships
    "// a comment \u2014 never ships",
    "/* a block comment \u2013 never ships */",
    "const e = /[\u2013\u2014]/g;",                            // regular-expression literal: code, not copy
    'const f = "plain: copy, (fine) 1 to 9";',
  ].join("\n");
  assert.deepEqual(shippedDashes("sample.tsx", sample).map((line) => line.split(":")[1]), ["1", "2", "4", "5"]);
  assert.deepEqual(cssDashes("sample.css", '/* a \u2014 comment */\n.a::after { content: "\u2014"; }\n.b { color: red; }')
    .map((line) => line.split(":")[1]), ["2"]);
});
