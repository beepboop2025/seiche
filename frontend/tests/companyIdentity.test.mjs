import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// Seiche already names its operator, LIQUILENS PRIVATE LIMITED, in the home
// JSON-LD and the product footer. DPIIT startup recognition is stated there as
// a verifiable registration status, with the same certificate number and
// Startup India verification page as https://liquilens.in/company-profile.json.
const CERTIFICATE = "DIPP290644";
const CIN = "U62011RJ2026PTC116792";
const VERIFY_URL =
  "https://www.startupindia.gov.in/content/sih/en/startupgov/validate-startup-recognition.html";
const OVERCLAIM = /\b(endorsed|accredited|certified|approved by|rbi|sebi)\b/i;

const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
const home = await readFile(new URL("../src/ProductHome.tsx", import.meta.url), "utf8");

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

test("the footer names the recognition beside the operator and nothing more", () => {
  const footer = home.match(/<footer className="product-footer">[\s\S]*?<\/footer>/)?.[0] ?? "";
  const operatorLine = footer.match(/<span>([^<]*)<\/span>/)?.[1] ?? "";
  assert.equal(
    operatorLine,
    `Seiche · LIQUILENS PRIVATE LIMITED · registered company in India · DPIIT recognised startup, certificate ${CERTIFICATE}`,
  );
  assert.doesNotMatch(operatorLine, OVERCLAIM);
  assert.doesNotMatch(operatorLine, /approval|licen[cs]e/i);
});
