# Public discovery transport

`seiche-discovery` serves GET and HEAD for exactly `/sitemap.xml`, `/robots.txt`,
`/llms.txt`, and `/.well-known/ai-catalog.json` from the current production Pages
alias, `https://seiche.pages.dev`. The signed native publisher remains the
authority for those files. There are no embedded copies, new content generators,
credential forwarding, arbitrary proxy targets, or data freshness claims.

On 2026-10-05, plain Python clients received Cloudflare 403/error 1010 from both
the public domain and direct Pages URLs. Identified clients received the correct
files. A zone BIC exception matched in events and a configuration rule matched
in the simulator, but neither alone resolved the live response. The Worker uses
its own descriptive user agent for the public origin read. This observation does
not prove the cause of Google's separate sitemap-processing error; Google's
live URL fetch succeeded before this change.

The four Worker route patterns have trailing wildcards to include query strings.
The handler checks exact paths and passes other paths through untouched. Public
discovery query parameters have no content semantics and are not forwarded.
Conditional HTTP reads and origin cache headers are preserved. Redirects or an
HTML challenge masquerading as a successful discovery file return an uncached
502. Other origin errors preserve their status and are not cached. A successful
response is never manufactured from an unavailable origin.

## Required zone setting

One Cloudflare Configuration Rule turns **Browser Integrity Check off** for:

```text
(http.host eq "seiche.info" and http.request.method in {"GET" "HEAD"} and http.request.uri.path in {"/sitemap.xml" "/robots.txt" "/llms.txt" "/.well-known/ai-catalog.json"})
```

All other security settings remain unchanged. Do not enable a redundant WAF skip
rule, exempt other paths, disable zone-wide security, or change crawler rights.

## Validation and deployment

Use an isolated, clean checkout and the repository's reviewed signed source.
This is an independent edge transport deployment. It does not authorize a
backend, dataset, catalog-content, or static-site publication, and it does not
change any held publisher's apply switch.

```sh
node --test ops/cloudflare/discovery-worker.test.mjs
npx --yes wrangler@4.130.0 deploy --config ops/cloudflare/wrangler.discovery.jsonc --dry-run
npx --yes wrangler@4.130.0 deploy --config ops/cloudflare/wrangler.discovery.jsonc
```

The deploy account must own `seiche.info` and have Workers script/route write
permissions. No service secret or storage binding is needed. Keep the deployment
version, source SHA, config readback, and live checks in retained evidence. Verify
all four GET/HEAD responses using plain Python and identified crawler clients;
compare GET SHA-256 with Pages, parse the XML, and confirm
`X-Seiche-Discovery-Version` matches the deployed version. Check an unrelated
path to prove the exception stayed narrow. These operator probes are not usage
or adoption metrics. Google processing must separately report success before the
sitemap issue is closed.

For rollback, remove only the four routes assigned to `seiche-discovery` in
Cloudflare Workers Routes; requests then return directly to the existing Pages
deployment. Keep the Worker version available for audit. No DNS, Pages deploy,
backend deployment, published file, or publisher apply setting needs to change.
