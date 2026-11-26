// The homepage links into the published record with plain anchors the page renders
// itself (not inside <noscript>), and every listed piece comes from an archive index.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";
import * as React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");
const transpile = async (path, context, jsx = false) => {
  const compilerOptions = { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 };
  if (jsx) compilerOptions.jsx = ts.JsxEmit.ReactJSX;
  vm.runInNewContext(ts.transpileModule(await read(path), { compilerOptions }).outputText, context);
  return context.exports;
};
const record = await transpile("../src/recordLinks.ts", { exports: {} });
// Values built inside the vm realm carry its own prototypes; compare them as plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));
const archives = {
  "/articles/index.json": JSON.parse(await read("../public/articles/index.json")),
  "/dispatches/index.json": JSON.parse(await read("../public/dispatches/index.json")),
  "/investigations/index.json": JSON.parse(await read("../public/investigations/index.json")),
};
const newestFirst = (rows, key) => [...rows].sort((a, b) => (a[key] < b[key] ? 1 : a[key] > b[key] ? -1 : 0));
const DASH = /[\u2013\u2014]/;

test("the newest pieces come from each archive index, newest first, at their clean URLs", () => {
  const articles = plain(record.latestArticles(archives["/articles/index.json"]));
  const letters = plain(record.latestLetters(archives["/dispatches/index.json"]));
  const investigations = plain(record.latestInvestigations(archives["/investigations/index.json"]));
  const expectedArticles = newestFirst(archives["/articles/index.json"], "date").slice(0, 3);
  const expectedLetters = newestFirst(archives["/dispatches/index.json"], "date").slice(0, 3);
  assert.deepEqual(articles.map((row) => row.href), expectedArticles.map((row) => `/articles/${row.slug}/`));
  assert.deepEqual(articles.map((row) => row.date), expectedArticles.map((row) => row.date));
  assert.deepEqual(letters.map((row) => row.href), expectedLetters.map((row) => `/dispatches/${row.slug}`));
  assert.deepEqual(investigations.map((row) => row.href),
    archives["/investigations/index.json"].articles.map((row) => `/investigations/${row.slug}/`));
  for (const row of [...articles, ...letters, ...investigations]) {
    assert.doesNotMatch(row.href, /\.html$/);
    assert.doesNotMatch(row.title, DASH, row.title);
    assert.match(row.date, /^\d{4}-\d{2}-\d{2}$/);
  }
});

test("rows that do not validate are left out, and unreadable indexes yield nothing", () => {
  assert.deepEqual(plain(record.latestArticles(null)), []);
  assert.deepEqual(plain(record.latestLetters({ not: "a list" })), []);
  assert.deepEqual(plain(record.latestInvestigations([])), []);
  const rows = [
    { slug: "../escape", date: "2026-09-24", headline: "Bad slug" },
    { slug: "no-date", headline: "No date" },
    { slug: "blank", date: "2026-09-23", headline: "  " },
    { slug: "kept", date: "2026-09-22", headline: "Kept\u2014with plain punctuation" },
  ];
  assert.deepEqual(plain(record.latestArticles(rows)), [{ href: "/articles/kept/", title: "Kept, with plain punctuation", date: "2026-09-22" }]);
  assert.deepEqual(plain(record.latestInvestigations({ articles: [{ slug: "draft", headline: "Draft", published_at: "2026-08-01T00:00:00Z", publication_status: "DRAFT" }] })), []);
});

async function renderRecord(fetchIndex) {
  const state = [];
  const effects = [];
  let cursor = 0;
  const hooks = {
    ...React,
    useState(initial) {
      const index = cursor++;
      if (!(index in state)) state[index] = initial;
      return [state[index], (next) => { state[index] = next; }];
    },
    useEffect(effect) { effects.push(effect); },
  };
  const requests = [];
  const component = await transpile("../src/PublishedRecord.tsx", {
    exports: {}, AbortController, Promise, Object,
    window: { setTimeout, clearTimeout },
    fetch: (url, init) => { requests.push({ url, init }); return fetchIndex(url); },
    require(name) {
      if (name === "react") return hooks;
      if (name === "react/jsx-runtime") return jsxRuntime;
      if (name === "./recordLinks") return record;
      throw new Error("Unexpected PublishedRecord dependency: " + name);
    },
  }, true);
  const render = () => { cursor = 0; return renderToStaticMarkup(React.createElement(component.default)); };
  render();
  effects.splice(0).forEach((effect) => effect());
  for (let tick = 0; tick < 8; tick += 1) await new Promise((resolve) => setImmediate(resolve));
  return { html: render(), requests };
}

const hrefs = (html) => [...html.matchAll(/<a href="([^"]+)"/g)].map((match) => match[1]);

test("the rendered section links the archives, the evidence maps and the newest pieces", async () => {
  const { html, requests } = await renderRecord(async (url) => ({
    ok: true, headers: { get: () => "application/json" }, json: async () => archives[url],
  }));
  assert.deepEqual(requests.map((request) => request.url).sort(), Object.keys(archives).sort());
  for (const request of requests) assert.equal(request.init.credentials, "omit");
  const links = hrefs(html);
  for (const hub of ["/articles/", "/dispatches/", "/investigations/", "/money-markets/", "/markets/"]) {
    assert.ok(links.includes(hub), hub);
  }
  const newestArticle = newestFirst(archives["/articles/index.json"], "date")[0];
  const newestLetter = newestFirst(archives["/dispatches/index.json"], "date")[0];
  assert.ok(links.includes(`/articles/${newestArticle.slug}/`));
  assert.ok(links.includes(`/dispatches/${newestLetter.slug}`));
  assert.ok(html.includes(`<time dateTime="${newestArticle.date}">`) || html.includes(`<time datetime="${newestArticle.date}">`));
  assert.equal(links.filter((href) => href.endsWith(".html")).length, 0);
  assert.doesNotMatch(html, /<noscript/);
  assert.doesNotMatch(html, DASH);
});

test("an unreadable archive keeps the hub links and lists nothing invented", async () => {
  const { html } = await renderRecord(async () => { throw new TypeError("network down"); });
  const links = hrefs(html);
  assert.deepEqual(links, ["/articles/", "/dispatches/", "/investigations/", "/money-markets/", "/markets/"]);
  assert.doesNotMatch(html, /<li>/);
});

test("the homepage entry renders the record section outside any noscript block", async () => {
  const site = await read("../src/Site.tsx");
  assert.match(site, /import PublishedRecord from "\.\/PublishedRecord";/);
  assert.equal(site.split("<PublishedRecord/>").length - 1, 1);
  const entry = site.slice(site.indexOf("{entry && <>"), site.indexOf("</>}<Suspense"));
  assert.ok(entry.includes("<PublishedRecord/>"), "rendered on the homepage entry only");
  assert.doesNotMatch(site, /<noscript/i);
});
