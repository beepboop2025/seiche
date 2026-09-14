import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/apiBase.ts", import.meta.url), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
function from(hostname) {
  const context = { exports: {}, window: { location: { hostname } } };
  vm.runInNewContext(output.outputText, context);
  return context.exports;
}

test("all supported static origins route live application reads to the canonical API", () => {
  for (const hostname of ["seiche.info", "www.seiche.info", "seiche.pages.dev", "beepboop2025.github.io"]) {
    const { API_BASE, CORPUS_API_BASE } = from(hostname);
    for (const path of ["/api/overview", "/api/v2/money-markets", "/api/v2/market-workbench"])
      assert.equal(`${API_BASE}${path}`, `https://api.seiche.info${path}`, hostname);
    assert.equal(CORPUS_API_BASE, "https://api.seiche.info/api/v2/corpus", hostname);
  }
});

test("local and self-hosted application routes stay on their own origin", () => {
  for (const hostname of ["localhost", "127.0.0.1", "desk.example.org", "seiche.pages.dev.example.org"]) {
    const { API_BASE, CORPUS_API_BASE } = from(hostname);
    assert.equal(API_BASE, "", hostname);
    assert.equal(CORPUS_API_BASE, "https://api.seiche.info/api/v2/corpus", hostname);
  }
});
