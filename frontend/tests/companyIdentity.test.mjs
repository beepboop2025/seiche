import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// Seiche names its operator, LIQUILENS PRIVATE LIMITED, in the home JSON-LD and
// in the terminal footer that every page renders. DPIIT startup recognition is
// stated there as a verifiable registration status, with the same certificate
// number and Startup India verification page as
// https://liquilens.in/company-profile.json.
const CERTIFICATE = "DIPP290644";
const CIN = "U62011RJ2026PTC116792";
const VERIFY_URL =
  "https://www.startupindia.gov.in/content/sih/en/startupgov/validate-startup-recognition.html";
const OVERCLAIM = /\b(endorsed|accredited|certified|approved by|rbi|sebi)\b/i;
const OPERATOR_LINE = `Seiche · LIQUILENS PRIVATE LIMITED · registered company in India · DPIIT recognised startup, certificate ${CERTIFICATE}`;

const source = (path) => readFile(new URL(path, import.meta.url), "utf8");
const html = await source("../index.html");
const entry = await source("../src/main.tsx");
const site = await source("../src/Site.tsx");
const terminal = await source("../src/App.tsx");

function graph() {
  const blocks = [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)];
  return blocks.flatMap((match) => JSON.parse(match[1])["@graph"] ?? []);
}

test("the operator JSON-LD carries the CIN and the DPIIT certificate with its check page", () => {
  const seiche = graph().find((node) => node["@id"] === "https://seiche.info/#organization");
  assert.ok(seiche, "Seiche organization node is present");
  const operator = seiche.parentOrganization;
  assert.equal(operator.legalName, "LIQUILENS PRIVATE LIMITED");
  const ids = Object.fromEntries(operator.identifier.map((row) => [row.propertyID, row]));
  assert.equal(ids.CIN.value, CIN);
  assert.equal(ids.DPIIT.value, CERTIFICATE);
  assert.equal(ids.DPIIT.url, VERIFY_URL);
  assert.equal(ids.DPIIT["@type"], "PropertyValue");
  assert.doesNotMatch(JSON.stringify(ids.DPIIT), OVERCLAIM);
});

test("the terminal footer names the recognition beside the operator and nothing more", () => {
  const footer = terminal.match(/<div className="footer">[\s\S]*?<\/div>/)?.[0] ?? "";
  const lines = [...footer.matchAll(/<span className="company-line">([^<]*)<\/span>/g)].map((m) => m[1]);
  assert.deepEqual(lines, [OPERATOR_LINE]);
  assert.doesNotMatch(lines[0], OVERCLAIM);
  assert.doesNotMatch(lines[0], /approval|licen[cs]e/i);
});

// ProductHome's own footer is never rendered: Site imports only its named
// ConnectedUpgrades export. Guard the path that does render, so the line cannot
// silently drop out of the page while the source still passes.
test("that footer is on the render path of every page, landing or tab", () => {
  assert.match(entry, /import Site from "\.\/Site"/);
  assert.match(entry, /<Site \/>/);
  assert.match(site, /const Terminal\s*=\s*lazy\(\(\)\s*=>\s*import\("\.\/App"\)\)/);
  const unguarded = site
    .replace(/\{entry && <>[\s\S]*?<\/>\}/g, "")
    .replace(/\{entry && <div[\s\S]*?<\/div>\}/g, "");
  assert.match(unguarded, /<Terminal\s*\/>/);
});
