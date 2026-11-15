// The MCP Registry card (server.json) and the AI catalog copy of it are what GitHub's
// MCP Registry, VS Code and other directories show for Seiche. This pins three things:
// - the title is the repository's canonical title (CITATION.cff), with no en or em dash;
// - every icon is a PNG this site already serves from frontend/public at the declared size,
//   listed in the web app manifest, so renaming the file breaks a test, not a registry
//   record that can no longer be edited;
// - the catalog entry stays byte-for-byte the same object as server.json.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url));
const json = (path) => JSON.parse(read(path).toString("utf8"));
const server = json("../../server.json");
const catalog = json("../public/.well-known/ai-catalog.json");
const webManifest = json("../public/manifest.webmanifest");

// Unicode dash punctuation (category Pd) other than the ASCII hyphen-minus.
const dashes = (text) => [...new Set([...text].filter((c) => c !== "-" && /\p{Pd}/u.test(c)))]
  .map((c) => "U+" + c.codePointAt(0).toString(16).toUpperCase().padStart(4, "0"));

export function pngSize(bytes) {
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (bytes.length < 24 || signature.some((b, i) => bytes[i] !== b)) return null;
  if (bytes.toString("latin1", 12, 16) !== "IHDR") return null;
  return `${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`;
}

test("registry title is the canonical title, without en or em dashes", () => {
  const citation = read("../../CITATION.cff").toString("utf8").match(/^title: "([^"]+)"$/m);
  assert.ok(citation, "CITATION.cff has a quoted title");
  assert.equal(server.title, citation[1]);
  for (const [field, text] of [["title", server.title], ["description", server.description]]) {
    assert.deepEqual(dashes(text), [], `server.json ${field}`);
  }
  for (const entry of catalog.entries) {
    for (const text of [entry.displayName, entry.data?.title, entry.data?.description]) {
      if (typeof text === "string") assert.deepEqual(dashes(text), [], `${entry.identifier}: ${text}`);
    }
  }
});

test("registry icons are PNGs this site serves at the declared size", () => {
  assert.ok(Array.isArray(server.icons) && server.icons.length > 0, "server.json declares icons");
  const manifestIcons = new Set(webManifest.icons.map((icon) => icon.src));
  for (const icon of server.icons) {
    const url = new URL(icon.src);
    assert.equal(url.protocol, "https:");
    assert.equal(url.host, "seiche.info", "icons come from the server's own domain");
    assert.equal(url.search + url.hash, "");
    assert.ok(icon.src.length <= 255);
    assert.equal(icon.mimeType, "image/png");
    assert.equal(icon.sizes.length, 1);
    assert.ok(manifestIcons.has(url.pathname), `${url.pathname} is listed in manifest.webmanifest`);
    const bytes = read(`../public${url.pathname}`);
    assert.equal(pngSize(bytes), icon.sizes[0], `${url.pathname} pixel size`);
  }
});

test("the AI catalog carries server.json unchanged", () => {
  const entry = catalog.entries.find((e) => e.identifier === "urn:air:seiche.info:mcp:funding-stress");
  assert.ok(entry, "canonical MCP entry");
  assert.deepEqual(entry.data, server);
});

test("pngSize reads the IHDR size and refuses other bytes", () => {
  const png = Buffer.alloc(24);
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).copy(png);
  png.write("IHDR", 12, "latin1");
  png.writeUInt32BE(512, 16);
  png.writeUInt32BE(256, 20);
  assert.equal(pngSize(png), "512x256");
  assert.equal(pngSize(Buffer.from("GIF89a")), null);
  const notIhdr = Buffer.from(png);
  notIhdr.write("IDAT", 12, "latin1");
  assert.equal(pngSize(notIhdr), null);
});
