import test from "node:test";
import assert from "node:assert/strict";
import { serveDiscovery } from "./discovery-worker.mjs";

test("serves current origin bytes without copying the sitemap into the Worker", async () => {
  let generation = 1;
  const source = async () => new Response(`<urlset>${generation++}</urlset>`, {
    headers: { "Content-Type": "application/xml", ETag: '"live"', "Cache-Control": "public, max-age=0, must-revalidate" },
  });
  const request = new Request("https://seiche.info/sitemap.xml");
  const first = await serveDiscovery(request, source);
  assert.equal(await first.text(), "<urlset>1</urlset>");
  assert.equal(first.headers.get("ETag"), '"live"');
  assert.match(first.headers.get("Cache-Control"), /must-revalidate/);
  assert.equal(await (await serveDiscovery(request, source)).text(), "<urlset>2</urlset>");
});

test("uses the fixed public origin with an identified client and no caller credentials", async () => {
  const request = new Request("https://seiche.info/robots.txt?url=https://example.com", {
    headers: { "User-Agent": "Python-urllib/3.14", Cookie: "private=value", Authorization: "Bearer private", "If-None-Match": '"old"' },
  });
  const result = await serveDiscovery(request, async (url, options) => {
    assert.equal(url, "https://seiche.pages.dev/robots.txt");
    assert.match(options.headers.get("User-Agent"), /^Seiche-Discovery-Edge\//);
    assert.equal(options.headers.has("Cookie"), false);
    assert.equal(options.headers.has("Authorization"), false);
    assert.equal(options.headers.get("If-None-Match"), '"old"');
    assert.equal(options.redirect, "manual");
    return new Response(null, { status: 304, headers: { ETag: '"old"' } });
  });
  assert.equal(result.status, 304);
  assert.equal(await result.text(), "");
});

test("HEAD preserves headers without returning a body", async () => {
  const response = await serveDiscovery(new Request("https://seiche.info/llms.txt", { method: "HEAD" }), async (_, options) => {
    assert.equal(options.method, "HEAD");
    return new Response(null, { headers: { "Content-Type": "text/plain", "Content-Length": "123" } });
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Content-Length"), "123");
  assert.equal(await response.text(), "");
});

test("preserves AI catalog bytes and removes any upstream cookie", async () => {
  const body = '{"name":"Seiche","status":"source_review_hold"}';
  const response = await serveDiscovery(new Request("https://seiche.info/.well-known/ai-catalog.json"), async () => new Response(body, {
    headers: { "Content-Type": "application/ai-catalog+json; charset=utf-8", "Set-Cookie": "unexpected=value" },
  }));
  assert.equal(await response.text(), body);
  assert.equal(response.headers.has("Set-Cookie"), false);
});

test("unrelated routes and similarly named paths pass through untouched", async () => {
  for (const path of ["/sitemap.xml.bak", "/sitemap.xml/extra", "/data/overview.json"]) {
    const request = new Request(`https://seiche.info${path}`);
    const original = new Response("original", { status: 404 });
    assert.equal(await serveDiscovery(request, async (input, options) => {
      assert.equal(input, request);
      assert.equal(options, undefined);
      return original;
    }), original);
  }
});

test("does not send writes to the discovery origin", async () => {
  const response = await serveDiscovery(new Request("https://seiche.info/sitemap.xml", { method: "POST" }), () => { throw new Error("must not fetch"); });
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("Allow"), "GET, HEAD");
});

test("preserves source failure status without caching or inventing successful content", async () => {
  const response = await serveDiscovery(new Request("https://seiche.info/sitemap.xml"), async () => new Response("origin unavailable", { status: 503 }));
  assert.equal(response.status, 503);
  assert.equal(await response.text(), "origin unavailable");
  assert.equal(response.headers.get("Cache-Control"), "no-store");
});

test("rejects redirects, HTML challenge pages, and failed fetches", async () => {
  for (const source of [
    async () => new Response(null, { status: 302, headers: { Location: "https://example.com" } }),
    async () => new Response("<html>challenge</html>", { headers: { "Content-Type": "text/html" } }),
    async () => { throw new Error("network failure"); },
  ]) {
    const response = await serveDiscovery(new Request("https://seiche.info/sitemap.xml"), source);
    assert.equal(response.status, 502);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.match(await response.text(), /unavailable/);
  }
});
