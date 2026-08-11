# API + MCP traction runbook

This is the operating loop for the Liquidity Lab's three public machine
surfaces. It treats traction as repeated useful answers, not endpoint traffic.

| Product | Job | Developer page | First activation tool |
|---|---|---|---|
| Seiche | systemic dollar-funding stress | `https://seiche.info/developers.html` | `funding_stress_now` |
| LiquiLens | institution failure risk | `https://liquilens.in/developers/` | `failure_radar_board` |
| Undertow | market liquidity and exit cost | `https://liquilens-undertow.com/developers/` | `exit_cost` |

## The funnel

1. **Discovery:** a developer-page view in Cloudflare Web Analytics, or the
   product appearing for a representative intent query in an ARD registry.
2. **Intent:** an API-catalog or source-code outbound click. Use aggregate
   Cloudflare referrers; do not add fingerprinting.
3. **Activation:** a successful MCP `tools/call`. This is the primary metric.
4. **Depth:** a second distinct tool called in the same product. Add this only
   if the logs can derive it without retaining prompts, arguments, tokens or
   user identifiers.
5. **Commercial intent:** Undertow `agent_access_status` or an authenticated
   subscriber-tool call; Seiche provisioning/upgrade traffic; LiquiLens demo
   access. Keep these separate from free-product success.

Every backend emits a privacy-safe line with the same shape:

```text
mcp_activation product=<product> surface=<public|subscriber|paid> tool=<tool> outcome=<success|error> origin=<edge|direct|unknown>
```

No prompt, argument, IP, bearer token, institution name or caller key belongs
in this event. `origin` separates Caddy-edge demand from loopback product
integration without retaining a caller identity; SDK transports that cannot
expose this use `unknown`.

## Seiche conversion handoffs

The Seiche letter and agent surfaces use the existing Telegram `/start`
attribution rather than increasing Liquidity Lab channel cadence:

| Source | Telegram ref | Handoff moment |
|---|---|---|
| Liquidity Lab Seiche letter | `lab_letter` | One focused button below the served letter |
| MCP `funding_stress_now` | `agent_mcp` | Machine-readable `delivery` in the first-tool result |
| Public API catalog | `agent_api` | Machine-readable delivery field in `/api` |
| Developer live runner | `agent_developers` | Revealed only after a successful first tool call |

Keep the channel at its existing one-letter cadence. Compare private starts by
ref with successful agent activations; optimize the handoff copy or onboarding
when either source sends traffic without starts, rather than adding posts.

## Weekly scorecard

Report, per product and in total:

- developer-page views;
- successful public tool calls;
- activation success rate: success / (success + error);
- first-tool share and the top five tools;
- API 4xx/5xx and MCP error outcomes;
- subscriber-surface calls, reported separately;
- week-over-week change for each figure.

Machine discovery has its own leading indicators:

- all three `/.well-known/ai-catalog.json` endpoints return a valid ARD 1.0
  envelope with cross-origin access;
- the official MCP Registry carries the same current version as each catalog;
- anonymous `tools/list` returns the advertised inventory and first activation
  tool; this probe deliberately does not call a tool and inflate activation;
- every catalog's OpenAPI reference resolves to a 3.x contract with at least
  one path;
- rank, or absence, for one representative intent query per product in three
  ARD reference implementations: GitHub Agent Finder, Ora, and Hugging Face
  Discover. A product counts as indexed if any registry returns a canonical
  identifier, MCP name, endpoint, or catalog domain for that intent.

Run `python backend/scripts/ard_coverage.py` for the live scorecard. The
`agent-discovery-coverage` workflow preserves a JSON report every Monday and
Thursday; use `--strict-indexing` only after registries have had time to ingest
the catalogs. The pre-launch baseline on 2026-08-06 was 0/3 live ARD catalogs
and 0/3 products returned by any tested intent-search registry, while all three
products were already current in the official MCP Registry. That separates the
distribution gap from the execution layer. Registry operators choose what they
index, so publishing a valid catalog creates eligibility rather than promising
inclusion.

The first 14 live days establish the baseline. After that, optimize for more
successful calls without lowering success rate. A pageview increase with flat
successful calls is an onboarding problem; calls rising with errors is a
contract or reliability problem; both rising is actual traction.

Example server-side checks:

```bash
journalctl -u seiche-api --since today | rg 'mcp_activation'
journalctl -u undertow-mcp --since today | rg 'mcp_activation'
```

LiquiLens runs on Railway; filter its service logs for
`mcp_activation product=liquilens`. Aggregate counts only. Do not export raw
request logs into a marketing tool.

## Release order

1. Deploy the three backends and verify each `initialize`, `tools/list` and
   first activation tool from a clean client.
2. Deploy the three public sites and verify the live runners from a browser,
   including CORS preflight.
3. Confirm the curated API catalogs at `/api`, `/api`, and `/undertow/`, plus
   the ARD catalogs at:
   - `https://liquilens.in/.well-known/ai-catalog.json`
   - `https://seiche.info/.well-known/ai-catalog.json`
   - `https://liquilens-undertow.com/.well-known/ai-catalog.json`
4. Publish the new `server.json` versions through each repository's
   `registry-publish.yml` workflow only after its live server reports that
   version.
5. Check the official MCP Registry record, then the downstream directory
   listings that ingest it.

## Distribution loop

Every launch item should demonstrate one answer, not announce infrastructure:

- **Seiche:** “Ask your agent whether dollar funding stress is building now,
  and make it show the historical analog and the misses.”
- **LiquiLens:** “Ask for one lender's evidence packet; an uncovered name is
  returned as uncovered instead of scored from memory.”
- **Undertow:** “Ask what selling $100k of BTC costs venue by venue right now,
  then inspect how concentrated the depth is.”

Use the live result, its as-of time and its caveat in every post or example.
Cross-link the relevant developer page. One concrete answer per post is more
credible and more reusable than a generic “we now have an API” announcement.

Each week:

1. publish one reproducible example per product;
2. turn the best support question into a copy-paste example on the developer
   page or README;
3. inspect the error tools before adding new tools;
4. compare successful calls with pageviews and change only the weakest stage;
5. keep the registry manifests, READMEs, `llms.txt` files and live tool lists in
   sync.
