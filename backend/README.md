# Seiche

[![PyPI](https://img.shields.io/pypi/v/seiche)](https://pypi.org/project/seiche/)
[![Python](https://img.shields.io/pypi/pyversions/seiche)](https://pypi.org/project/seiche/)
[![MCP Registry](https://img.shields.io/badge/MCP-registry-6f42c1)](https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.beepboop2025%2Fseiche)
[![AGPL-3.0-or-later](https://img.shields.io/badge/code-AGPL--3.0--or--later-blue)](https://github.com/beepboop2025/seiche/blob/main/LICENSE)

Seiche is an open-source money-, foreign-exchange, and capital-market evidence
terminal, with its deepest live coverage in US-dollar funding. It joins public
and official sources from the Federal Reserve, New York Fed, OFR, US Treasury,
CFTC, and other authorities into a source-clocked research surface.

Every output keeps its as-of date, provenance, evidence status, known gaps, and
point-in-time eligibility attached. Seiche is a research and evidence tool—not
a real-time quote service, execution venue, or investment adviser.

- Live terminal: [seiche.info](https://seiche.info)
- Developer guide and tool runner: [seiche.info/developers](https://seiche.info/developers)
- Source and full documentation: [github.com/beepboop2025/seiche](https://github.com/beepboop2025/seiche)
- Hosted MCP server: `https://api.seiche.info/mcp`

## Install and run

Seiche requires Python 3.12 or newer.

```bash
python3.12 -m venv .venv
.venv/bin/pip install seiche
.venv/bin/seiche pull
.venv/bin/seiche brief
```

Start the local REST API and terminal UI with:

```bash
.venv/bin/seiche serve
```

The first pull can be slower while the local cadence-aware cache is populated.
Subsequent reads reuse that cache and continue to expose stale or unavailable
inputs explicitly instead of silently dropping them.

## Use Seiche from an AI agent

Seiche is a [Model Context Protocol](https://modelcontextprotocol.io) server.
Run it locally over stdio:

```bash
SEICHE_MCP_PUBLIC=1 .venv/bin/seiche-mcp
```

Or connect an MCP-capable client directly to the public Streamable HTTP server:

```text
https://api.seiche.info/mcp
```

Eleven evidence/context tools are anonymous: the current funding-stress read,
historical analogs, public backtest, data health, crypto record, institutional
flows, oil/funding, FX/materials, US money markets, world markets, and the latest
article. Five higher-cost forecast, replay, positioning, prose, and LLM tools
require an account and are omitted from anonymous `tools/list` responses.

The canonical registry identifier is:

```text
io.github.beepboop2025/seiche
```

See the [MCP integration guide](https://github.com/beepboop2025/seiche/blob/main/docs/MCP.md)
for Claude, Codex, Cursor, VS Code, and raw JSON-RPC examples.

## Data and licensing boundary

The source code is licensed under AGPL-3.0-or-later. Upstream data keeps its own
terms: Seiche's software license does not grant redistribution rights over data
from third parties. Public outputs distinguish redistributable observations,
bounded derived context, metadata-only sources, restricted inputs, and declared
gaps. Review the source citation and rights fields before republishing data.

For research citation, use the repository's `CITATION.cff`; for defects or data
questions, open a [GitHub issue](https://github.com/beepboop2025/seiche/issues).
