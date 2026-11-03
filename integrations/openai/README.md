# Seiche OpenAI plugin submission pack

## Release scope: 3 November 2026

The legacy funding board and the legacy tools are visible again for free research
use while source permissions are sought (policy
`seiche.public-source-rights.2026-11-03`). Source owners can request removal at
any time.
Anonymous public tools: `source_rights_status`, `money_market_context`, `market_workbench`, `world_markets_context`, `gift_city_context`, `gold_inventory_carry`, `research_network`, `data_health`, and the legacy `latest_article`, `funding_stress_now`, `historical_analogs`, `proof_backtest`, `crypto_stress_record`, `institutional_flows`, `oil_funding_context`, `fx_materials_passage`, `trade_safety_risk_context`.
Start with `source_rights_status`. The legacy `funding_stress_forecast`,
`replay_asof`, `positioning_book`, `desk_brief` and `ask_desk` keep the bearer
token requirement they had before 28 October; no payment unlocks a legacy tool.
Legacy forecasts, analogs, backtests and replays are research diagnostics, not
validated predictions. The Book (`/api/book` and its hash-chained ledger) stays
withheld until its published chain is restored. Do not reconstruct withheld
observations from metadata, cached history or prior responses. Direct publisher
observations keep their own dates; restricted inputs remain explicitly
metadata-only. Authenticated callers can additionally discover five private,
non-executable Agent Room tools. Earlier feature descriptions below are
historical context where they conflict with this release scope; they do not
advertise availability.


This directory is a review-ready draft for submitting Seiche as an **MCP-only
plugin** to ChatGPT and Codex. It is not evidence that a submission has been
created, verified, reviewed, or approved.

Seiche's universal production endpoint is:

```text
https://api.seiche.info/mcp
```

An anonymous scan sees the eight read-only public research tools `source_rights_status`, `money_market_context`, `market_workbench`, `world_markets_context`, `gift_city_context`, `gold_inventory_carry`, `research_network`, `data_health` and, under the 3 November research release, nine legacy tools: `latest_article`, `funding_stress_now`, `historical_analogs`, `proof_backtest`, `crypto_stress_record`, `institutional_flows`, `oil_funding_context`, `fx_materials_passage`, `trade_safety_risk_context`. No account or API key is needed. Five legacy tools and five private Agent Room tools require bearer identity and are outside this plugin. The review cases in `test-cases.json` and `SUBMISSION.md` still cover only the eight research tools; the reopened legacy tools need their own cases before any submission.

## What the code now supplies

- action-oriented tool names, titles, descriptions, and explicit input schemas;
- an `outputSchema` for every tool whose successful response includes
  `structuredContent`;
- accurate read-only, idempotent, non-destructive, closed-world annotations;
- text `content` alongside structured data for compatibility;
- privacy-safe typed failure envelopes covered by the same output contracts;
- eight positive and four negative review cases in
  `test-cases.json`.

`desk_brief` intentionally has no output schema because its successful payload
is Markdown text, not `structuredContent`. This pack intentionally does **not**
contain a legacy `ai-plugin.json` manifest: the current publication path scans
the submitted MCP server directly.

## Local verification

From the repository root:

```bash
cd backend
pytest -q tests/test_mcp_server.py tests/test_mcp_http.py \
  tests/test_openai_plugin_contract.py
```

The contract suite always validates the JSON Schema subset Seiche publishes.
If the optional `jsonschema` package is already installed, the same live
handler witnesses are also checked with its Draft 2020-12 validator. Seiche
does not add that package to production merely for review metadata.

## Current official requirements used

- [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [Submit plugins](https://developers.openai.com/plugins/deploy/submission)

Those pages require structured tools to declare output schemas and public
submissions to supply listing details, accurate annotations, starter prompts,
five positive tests, three negative tests, domain verification, an eligible
verified publisher, and policy attestations. The owner-only and portal-only
steps are recorded honestly in `SUBMISSION.md`.

The public `market_workbench` tool accepts `provider` (`h10` or `ecb`), `base`, `quote`, `days` (30–3650), and optional `china_series`. It reads cached, dated reference FX and owner-accepted annual Palimpsest China evidence; the WORKBENCH tab and `/api/v2/market-workbench` use the same contract. It never collects during a request or creates executable quotes.


`gift_city_context` returns dated India–UAE funding, separate ECB and CBUAE VAT
FX references, and weekly COMEX gold positioning. Preserve source dates and
unavailable inputs. `gold_inventory_carry` computes a non-persistent financing
scenario from explicit decimal-string prices, rates, FX, quantity, fineness and
fees. Neither tool verifies a quote, determines import eligibility or executes
a transaction.
