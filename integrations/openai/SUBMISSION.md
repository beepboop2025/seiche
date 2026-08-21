# Seiche — OpenAI plugin submission worksheet

Status: **code and review-material draft only; not submitted**  
Submission type: **With MCP (MCP-only, no custom UI)**  
MCP URL type: **Universal**

## Listing draft

| Field | Draft value |
|---|---|
| Plugin name | Seiche |
| Short description | Evidence-bounded intelligence across money, foreign-exchange and capital markets. |
| Long description | Seiche helps users inspect money-market funding, 22 public FX reference series and capital-market transmission using official and public evidence. It exposes the live dollar-funding conclusion, a granular chartless USD desk, a licence-aware global money-market atlas, a bounded world-markets context, historical analogs, the published diagnostic record and misses, data freshness, institutional positioning, and oil/FX/material pathways. Every response carries timestamps, canonical citation URLs, status or claim boundaries where applicable. Seiche is research data, not investment advice, and does not promise exhaustive coverage, executable quotes, forecasts or causal conclusions. |
| Website | https://seiche.info |
| Support | https://seiche.info/support |
| Privacy | https://seiche.info/privacy |
| Terms | https://seiche.info/terms |
| Logo candidate | https://seiche.info/icons/pwa-512.png |
| Source | https://github.com/beepboop2025/seiche |
| MCP server | https://api.seiche.info/mcp |
| Authentication | None for the eleven-tool public surface; 200 calls per IP per UTC day |
| Custom UI | None |
| Category | Owner must select the closest current portal category; do not guess outside the portal |
| Country availability | Owner must choose only jurisdictions where they are prepared to offer the service |

The logo URL is a source asset, not proof that the portal's image checks have
passed. Export or upload it in the exact dimensions and format the live portal
requests.

## Starter prompts

1. What does the current dollar-funding board say, and how fresh is the evidence?
2. Give me the repo-segment view from the USD money-market desk, with dates and sources.
3. Which historical funding episodes look most like today, and what are the diagnostic's limits?
4. Is current oil-market cash pressure reaching US dollar funding, or is it still scenario-only?
5. Is FX or physical-material working-capital pressure showing up in money markets?
6. What did Seiche publish today? Preserve the article's exact factual authority and caveats.
7. Give me a money, FX and capital-markets briefing with source clocks, evidence states and canonical citations.

## MCP configuration draft

- Universal MCP Server URL: `https://api.seiche.info/mcp`
- Authentication: no authentication for the submitted public surface.
- Reviewer credentials: none required for the eleven public tools.
- Content security policy: no custom UI is included and no browser component
  fetches external domains. Confirm the portal accepts an empty CSP allowlist.
- Tool scan expectation: exactly eleven tools, each with `inputSchema`,
  `outputSchema`, title, description, and accurate annotations.
- Annotations for every submitted tool:
  - `readOnlyHint: true`
  - `idempotentHint: true`
  - `destructiveHint: false`
  - `openWorldHint: false`

`openWorldHint` is false because calls read Seiche's already-operated evidence
surfaces and do not browse arbitrary user-selected URLs, send messages, or
change outside systems. `latest_article` reads Seiche's own allowlisted feed.

## Test cases

Use `test-cases.json`. It contains seven positive and four negative cases. Run
them against the anonymous public surface so review does not accidentally
depend on subscriber-only tools or credentials.

Suggested release notes:

> Initial MCP-only Seiche plugin submission. Eleven anonymous read-only tools
> provide live dollar-funding conclusions, granular USD and global money-market
> context, a money/FX/capital world-markets view, historical analogs, diagnostic
> evidence, freshness, and bounded cross-market context. This version adds
> declared structured output schemas and typed, privacy-safe failure contracts.

## Owner and portal steps — not completed by this repository

Do not mark any item complete without evidence from the same OpenAI
organization that will publish the plugin.

- [ ] Grant the submitter **Apps Management: Write** in the OpenAI Platform.
- [ ] Complete developer or business **identity verification** and confirm the
      selected publisher name matches the website, support, privacy, and terms.
- [ ] Have the owner or counsel review the public privacy and terms pages. They
      currently label themselves operational drafts pending legal review; do
      not make policy attestations until that review is complete.
- [ ] Create the MCP-only draft in the plugin submission portal and enter the
      universal production endpoint directly, not an existing integration ID.
- [ ] Run **Scan Tools** after the output-schema change is deployed and save the
      portal's validation result.
- [ ] If the portal issues a domain token, serve that exact token alone at
      `https://api.seiche.info/.well-known/openai-apps-challenge` (or an allowed
      parent origin). Never invent, pre-generate, reuse, or commit a token.
- [ ] Re-scan after the `openai-apps-challenge` succeeds.
- [ ] Upload the production logo in the portal-requested format.
- [ ] Choose accurate country availability and category values in the live form.
- [ ] Execute the positive and negative cases and preserve reviewer-visible
      results.
- [ ] Read and answer every portal **policy attestations** item truthfully. This
      repository does not answer legal or publisher attestations on the owner's
      behalf.
- [ ] Submit only after the production MCP endpoint advertises the same tool
      contracts tested in this branch.

## Pre-submission production checks

```bash
curl -fsS https://api.seiche.info/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

curl -fsS https://api.seiche.info/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"funding_stress_now","arguments":{}}}'
```

Pass conditions:

- HTTPS succeeds without reviewer credentials;
- `tools/list` returns exactly eleven anonymous tools;
- every tool has an object `inputSchema`, object `outputSchema`, title,
  description, and the four annotations above;
- each successful structured result conforms to its advertised schema;
- data-health and claim-boundary fields remain attached to analytical results;
- no response contains secrets, raw exception diagnostics, or unnecessary
  personal data;
- `/privacy`, `/terms`, and `/support` are public and consistent with actual
  MCP data handling.
