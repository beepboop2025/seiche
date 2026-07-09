# Seiche

Funding-stress early warning for US money markets, built entirely from free,
keyless public data (Fed H.4.1, NY Fed operations, OFR repo, Treasury cash). It
reads the plumbing so you don't have to: one stress board, honest backtests,
published misses, updated twice a day.

Full project, the terminal UI, and deployment: https://github.com/beepboop2025/seiche
Live: https://seiche.info

## As an agent tool (MCP)

Seiche is a Model Context Protocol server. Any MCP-capable agent can read the
live board as tools — the current stress regime, forward event odds, historical
analogs, and the honest backtest.

```bash
pip install seiche
seiche-mcp                 # stdio MCP server
```

Or connect to the hosted, metered endpoint at `https://api.seiche.info/mcp`.
See [docs/MCP.md](https://github.com/beepboop2025/seiche/blob/main/docs/MCP.md).

mcp-name: io.github.beepboop2025/seiche
