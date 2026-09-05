import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  AGENT_SCENARIOS,
  formatHealthReceipt,
} from "../public/agent-network.js";

const publicFile = (name) => new URL(`../public/${name}`, import.meta.url);

test("agent routes map only to shipped Seiche MCP tools", () => {
  assert.deepEqual(Object.keys(AGENT_SCENARIOS), ["context", "negotiate", "audit"]);
  assert.equal(AGENT_SCENARIOS.context.packet.tool, "trade_safety_risk_context");
  assert.equal(AGENT_SCENARIOS.context.packet.response_contract.cache_only, true);
  assert.equal(AGENT_SCENARIOS.context.packet.response_contract.executable, false);
  assert.equal(AGENT_SCENARIOS.negotiate.packet.tool, "agent_room_append_event");
  assert.equal(AGENT_SCENARIOS.negotiate.packet.event.executable, false);
  assert.equal(AGENT_SCENARIOS.audit.packet.tool, "agent_room_verify");
  assert.match(AGENT_SCENARIOS.audit.packet.claim_boundary, /not_legal_compliance$/);
});

test("health receipt exposes release, evidence count, faults, and exact clock", () => {
  assert.equal(
    formatHealthReceipt({
      version: "0.12.2 estuary",
      generated_at: "2026-09-02T22:04:12+00:00",
      provenance: [{}, {}],
      faults: [],
    }),
    "0.12.2 estuary · 2026-09-02T22:04:12.000Z · 2 provenance · 0 faults",
  );
});

test("use-case page keeps execution, compliance, and payment claims bounded", async () => {
  const html = await readFile(publicFile("use-cases.html"), "utf8");

  assert.match(html, /role="tablist"/);
  assert.match(html, /type="module" src="\/agent-network\.js"/);
  assert.match(html, /connect-src[^\"]*https:\/\/api\.seiche\.info/);
  assert.match(html, /Execution authority<\/strong><span>None\./);
  assert.match(html, /legal sufficiency requires independent assessment/);
  assert.match(html, /Micropayments<\/strong><span>Dormant\./);
  assert.doesNotMatch(html, /satisf(?:y|ies) (?:the )?SEC|CFTC compliance/i);
});
