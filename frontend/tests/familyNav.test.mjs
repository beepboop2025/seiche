// The desk's menu (src/familyNav.ts) is the static header's menu (public/research-ui/header.html):
// the same tabs and sub-tabs, every desk view sits under a family tab, and every view is one
// click from its group, in a sub-tab or in the desk's own menu.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const load = async (path) => {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const context = { exports: {} };
  vm.runInNewContext(output.outputText, context);
  return context.exports;
};
const { FAMILY_TABS, FAMILY_ROWS, DESK_GROUP, deskGroup, deskRow } = await load("../src/familyNav.ts");
const { TERMINAL_TABS, terminalTabFromHash } = await load("../src/productRoutes.ts");
const header = await readFile(new URL("../public/research-ui/header.html", import.meta.url), "utf8");
const navigation = await readFile(new URL("../src/WorkspaceNavigation.tsx", import.meta.url), "utf8");

const links = (html) => [...html.matchAll(/<a\b[^>]*?href="([^"]+)"[^>]*>([^<]*)<\/a>/g)].map(([, href, label]) => ({ href, label }));
const plain = (rows) => JSON.parse(JSON.stringify(rows));

test("the desk shows header.html's tabs and sub-tabs, label for label", () => {
  const tabs = header.match(/<nav class="research-tabs"[^>]*>([\s\S]*?)<\/nav>/)[1];
  assert.deepEqual(plain(FAMILY_TABS), links(tabs).map(({ href, label }) => ({ label, href })));
  const rows = [...header.matchAll(/<nav class="research-subtabs" aria-label="([^"]+)" data-for="([^"]+)">([\s\S]*?)<\/nav>/g)]
    .map(([, label, group, body]) => ({ label, group, links: links(body).map(({ href, label: text }) => ({ label: text, href })) }));
  const desk = plain(FAMILY_ROWS).map((row) => ({ ...row, links: row.links.map(({ label, href }) => ({ label, href })) }));
  assert.deepEqual(desk, rows);
  assert.ok(FAMILY_TABS.length <= 6);
});

test("every desk link names the view its address opens", () => {
  for (const row of FAMILY_ROWS) {
    for (const link of row.links) {
      const view = link.href.startsWith("/#") ? terminalTabFromHash(link.href.slice(1)) : undefined;
      assert.equal(link.tab, view ?? undefined, `${row.label}: ${link.label}`);
    }
  }
});

test("every desk view sits under a family tab and is one click from its group", () => {
  const tabs = new Set(FAMILY_TABS.map((tab) => tab.href));
  assert.deepEqual(Object.keys(DESK_GROUP).sort(), [...TERMINAL_TABS].sort());
  for (const view of TERMINAL_TABS) {
    assert.ok(tabs.has(DESK_GROUP[view]), `${view} sits under a family tab`);
    const row = deskRow(view);
    if (row) assert.equal(row.group, deskGroup(view));
  }
  assert.equal(deskGroup(null), "/", "the bare front page is the Overview");
  // A view opens from its group's sub-tabs or from the desk's own menu, never from both.
  const inRows = new Set(FAMILY_ROWS.flatMap((row) => row.links.map((link) => link.tab).filter(Boolean)));
  const menu = navigation.slice(navigation.indexOf("const groups"), navigation.indexOf("export default"));
  const inMenu = new Set([...menu.matchAll(/"([A-Z][A-Z× ]+)"/g)].map((match) => match[1]).filter((name) => TERMINAL_TABS.includes(name)));
  for (const view of TERMINAL_TABS) {
    // The desk's letter view reads the same dated letters as the Dispatches archive sub-tab.
    if (view === "DISPATCHES") continue;
    assert.ok(inRows.has(view) !== inMenu.has(view), `${view} is in exactly one of the sub-tabs and the desk menu`);
  }
  for (const row of FAMILY_ROWS) {
    for (const link of row.links) {
      if (link.tab) assert.equal(deskGroup(link.tab), row.group, `${link.label} sits under its own row`);
    }
  }
});
