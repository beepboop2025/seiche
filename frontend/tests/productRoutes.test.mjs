import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/productRoutes.ts", import.meta.url), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const context = { exports: {} };
vm.runInNewContext(output.outputText, context);
const { TERMINAL_TABS, terminalTabFromHash } = context.exports;

test("every existing named desk route retains identity, encoded labels and subroutes", () => {
  assert.equal(TERMINAL_TABS.length, 24);
  for (const tab of TERMINAL_TABS) {
    assert.equal(terminalTabFromHash(`#${tab.toLowerCase()}`), tab);
    assert.equal(terminalTabFromHash(`#${encodeURIComponent(tab.toLowerCase())}/detail`), tab);
  }
  assert.equal(terminalTabFromHash("#time%20machine/2019-09-16"), "TIME MACHINE");
});

test("product anchors and malformed hashes stay on the product entry", () => {
  for (const hash of ["", "#", "#product-content", "#not-a-tool", "#%E0%A4%A", "#%"])
    assert.equal(terminalTabFromHash(hash), null);
});
