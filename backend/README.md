# Seiche

Money-, foreign-exchange and capital-market evidence intelligence, with deepest
live competence in US dollar funding. Seiche joins free/keyless public data from
the Fed, NY Fed, OFR, Treasury, CFTC and other official sources into an 11-pack
money-market atlas, 22 H.10 currency reference series and a bounded
capital-market transmission layer. Source clocks, rights, explicit gaps and
construction-PIT eligibility flags travel with every output.

Full project, the terminal UI, and deployment: https://github.com/beepboop2025/seiche
Live: https://seiche.info

## As an agent tool (MCP)

Seiche is a Model Context Protocol server. Any MCP-capable agent can read the
live board as tools — the current stress regime, forward event odds, historical
analogs, the status-bound historical diagnostic, and a chartless world-markets
context spanning money, forex and capital markets.

```bash
pip install seiche
seiche-mcp                 # stdio MCP server
```

Or connect to the hosted, metered endpoint at `https://api.seiche.info/mcp`.
See [docs/MCP.md](https://github.com/beepboop2025/seiche/blob/main/docs/MCP.md).

mcp-name: io.github.beepboop2025/seiche
