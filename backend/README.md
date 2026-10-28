# Seiche: funding and FX evidence for Python and AI agents

Research US-dollar funding, money markets, foreign exchange, and capital-market
context with source dates, provenance, coverage, and known gaps attached.

[Open the hosted terminal](https://seiche.info/) ·
[Connect an agent](https://seiche.info/developers) ·
[Read dated research answers](https://seiche.info/questions/)

[![PyPI](https://img.shields.io/pypi/v/seiche)](https://pypi.org/project/seiche/)
[![Python](https://img.shields.io/pypi/pyversions/seiche)](https://pypi.org/project/seiche/)
[![MCP Registry](https://img.shields.io/badge/MCP-registry-6f42c1)](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.beepboop2025%2Fseiche)
[![AGPL-3.0-or-later](https://img.shields.io/badge/code-AGPL--3.0--or--later-blue)](https://github.com/beepboop2025/seiche/blob/main/LICENSE)

Seiche is an open-source money-, foreign-exchange, and capital-market evidence
terminal, with its deepest live coverage in US-dollar funding. It joins public
and official sources from the Federal Reserve, New York Fed, OFR, US Treasury,
CFTC, and other authorities into a source-clocked research surface.

Outputs retain their as-of dates, provenance, evidence status, known gaps, and
point-in-time eligibility. Use them for financial research, agent context, and
reviewable evidence. Seiche does not execute trades or provide real-time quotes
or investment advice.

- Live terminal: [seiche.info](https://seiche.info)
- Developer guide and tool runner: [seiche.info/developers](https://seiche.info/developers)
- Source and full documentation: [github.com/beepboop2025/seiche](https://github.com/beepboop2025/seiche)
- Hosted MCP server: `https://api.seiche.info/mcp`
- Cache-only Trade Safety context: `https://api.seiche.info/api/trade-safety/risk-context`

Country funding references are available through `money_market_context` with
`section="countries"` (catalog) or `section="countries", country="JP"` (country
observations). The REST equivalent is `/api/v2/country-funding/JP`. Coverage is
partial: monthly references, daily curves, shared euro-area policy and national
sovereign issuers retain their own identities and dates. See the
[country methodology](https://github.com/beepboop2025/seiche/blob/main/docs/COUNTRY_FUNDING.md).

## Connect an AI agent without installing Seiche

Connect an MCP-capable client to the public Streamable HTTP endpoint:

```text
https://api.seiche.info/mcp
```

For clients that accept `mcpServers` JSON configuration:

```json
{
  "mcpServers": {
    "seiche": {
      "url": "https://api.seiche.info/mcp"
    }
  }
}
```

Public evidence tools can be used without an account, subject to service limits.
Start with `tools/list` to discover the tools available to your caller, then use:

| Research task | MCP tool and arguments |
| --- | --- |
| Read the funding-stress conclusion | `funding_stress_now` with `{}` |
| Get compact USD funding evidence | `money_market_context` with `{"section":"summary"}` |
| Check source freshness and faults | `data_health` with `{}` |
| Review a country's funding references | `money_market_context` with `{"section":"countries","country":"JP"}` |
| Discover connected evidence products | `research_network` with `{}` |

For example, this anonymous read returns a bounded funding summary:

```bash
curl --fail-with-body https://api.seiche.info/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"money_market_context","arguments":{"section":"summary"}}}'
```

Keep returned dates, coverage, caveats, and unavailable or restricted states in
your agent's answer. A successful request does not make every source current.
See the [MCP integration guide](https://github.com/beepboop2025/seiche/blob/main/docs/MCP.md)
for Claude, Codex, Cursor, VS Code, and raw JSON-RPC examples.

## Install the Python package for local research

Seiche requires Python 3.12 or newer. On macOS or Linux:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade seiche
.venv/bin/seiche --help
```

The package installs the `seiche` research CLI and `seiche-mcp` stdio server.
To connect an agent to your local installation's public tool surface:

```bash
SEICHE_MCP_PUBLIC=1 .venv/bin/seiche-mcp
```

Local tools use local source and cache state. For an optional local collection
and brief:

```bash
.venv/bin/seiche pull
.venv/bin/seiche brief
```

The first pull can be slow while public histories populate the local cache.
Later reads reuse the cadence-aware cache; stale and unavailable inputs remain
explicit. Local collection depends on upstream availability and source access.

Start the local REST API at `http://127.0.0.1:8787` with:

```bash
.venv/bin/seiche serve
```

**The PyPI wheel contains the Python backend, not the browser terminal.** Use
[seiche.info](https://seiche.info/) for the hosted terminal. To serve a local
browser UI, use a [source checkout](https://github.com/beepboop2025/seiche#run-it),
install the backend there, and build the frontend with `npm ci` followed by
`npm run build` in `frontend/`. The source checkout's API serves the resulting
`frontend/dist` directory when it is present.

## Connect the product family

Seiche provides funding and macro context. Follow a research question to the
product responsible for the next evidence type:

| Need | Product |
| --- | --- |
| Bank disclosures and named-institution evidence | [LiquiLens](https://liquilens.in/) |
| Indicative BTC exit cost and market-depth research | [Undertow](https://liquilens-undertow.com/) |
| China and regional public-record research | [Palimpsest](https://palimpsest.info/) |
| Saved reviews and Python agent workflows across the family | [Financial Evidence](https://pypi.org/project/financial-evidence/) |

Each product retains its own coverage, access rules, source clocks, and data
rights. The [shared connection guide](https://beepboop2025.github.io/financial-evidence-skills/connect/)
lists the available hosted MCP endpoints.

## Access and evidence boundaries

Eight public research tools are anonymous: `source_rights_status`,
`money_market_context`, `market_workbench`, `world_markets_context`,
`gift_city_context`, `gold_inventory_carry`, `research_network`, and
`data_health`. Start with `source_rights_status` to inspect the release scope.
Legacy forecasts, backtests, stress conclusions and Trade Safety context are
explicitly unavailable for every caller while their source rights remain
unverified. Authentication does not unlock these legacy features.

Authenticated hosted MCP adds five private Agent Room preview tools for
identity-to-key binding, room creation, signed append, verified listing, and
full-chain verification. The same protected operations have REST twins under
`/api/agent-room/`. They are discussion and evidence primitives only: every
record is non-executable, x402 cannot unlock them, and local stdio has no bearer
principal with which to expose them. See the
[Agent Room guide](https://github.com/beepboop2025/seiche/blob/main/docs/AGENT-ROOM.md).

The canonical registry ownership marker is:

```text
mcp-name: io.github.beepboop2025/seiche
```

## Data and licensing boundary

The source code is licensed under AGPL-3.0-or-later. Upstream data keeps its own
terms: Seiche's software license does not grant redistribution rights over data
from third parties. Public outputs distinguish redistributable observations,
bounded derived context, metadata-only sources, restricted inputs, and declared
gaps. Review the source citation and rights fields before republishing data.

For research citation, use
[CITATION.cff](https://github.com/beepboop2025/seiche/blob/main/CITATION.cff); for
defects or data questions, open a
[GitHub issue](https://github.com/beepboop2025/seiche/issues).
