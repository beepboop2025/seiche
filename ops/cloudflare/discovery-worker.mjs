// Public discovery transport only. Pages remains the publication authority.
const TYPES = new Map([
  ["/sitemap.xml", ["application/xml", "text/xml"]],
  ["/robots.txt", ["text/plain"]],
  ["/llms.txt", ["text/plain"]],
  ["/.well-known/ai-catalog.json", ["application/json", "application/ai-catalog+json"]],
]);

function unavailable(method) {
  return new Response(method === "HEAD" ? null : "Discovery source unavailable\n", {
    status: 502,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", "Retry-After": "60" },
  });
}

export async function serveDiscovery(request, fetchSource = fetch, versionId) {
  const url = new URL(request.url);
  const types = TYPES.get(url.pathname);
  // Route patterns end in * to cover query strings. Do not intercept siblings.
  if (url.hostname !== "seiche.info" || !types) return fetchSource(request);
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method not allowed\n", {
      status: 405,
      headers: { Allow: "GET, HEAD", "Cache-Control": "no-store" },
    });
  }

  const headers = new Headers({
    "User-Agent": "Seiche-Discovery-Edge/1.0 (+https://seiche.info/)",
    Accept: types.join(", "),
  });
  for (const key of ["If-None-Match", "If-Modified-Since"]) {
    if (request.headers.has(key)) headers.set(key, request.headers.get(key));
  }
  try {
    // Fixed origin and exact allowlisted path: no user URL, cookies, or auth.
    const upstream = await fetchSource(`https://seiche.pages.dev${url.pathname}`, {
      method: request.method,
      headers,
      redirect: "manual",
      signal: AbortSignal.timeout(15000),
    });
    const mediaType = (upstream.headers.get("Content-Type") || "").split(";", 1)[0].trim().toLowerCase();
    if ((upstream.status >= 300 && upstream.status < 400 && upstream.status !== 304)
        || (upstream.status === 200 && !types.includes(mediaType))) {
      await upstream.body?.cancel();
      return unavailable(request.method);
    }
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.delete("Set-Cookie");
    responseHeaders.set("X-Seiche-Discovery-Transport", "pages-proxy-v1");
    if (versionId) responseHeaders.set("X-Seiche-Discovery-Version", versionId);
    if (!upstream.ok && upstream.status !== 304) responseHeaders.set("Cache-Control", "no-store");
    return new Response(request.method === "HEAD" ? null : upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return unavailable(request.method);
  }
}

export default { fetch: (request, env) => serveDiscovery(request, fetch, env?.CF_VERSION_METADATA?.id) };
