# Connect Seiche to MCP clients

Seiche has one hosted, anonymous Streamable HTTP endpoint:

```text
https://api.seiche.info/mcp
```

The examples in this directory connect that endpoint directly. They contain no
API key, bearer token, local command, or package-install step. Seiche's public
tools are read-only evidence surfaces: they retain source clocks, citations,
missing-data states, and the research-not-investment-advice boundary.

These are usable client configurations, not vendor endorsements or directory
publication receipts. The receipt-backed status ledger is
[`distribution/submissions.csv`](../../distribution/submissions.csv).

## Install by client

If a destination file already exists, merge only the `seiche` entry; do not
replace the rest of the file.

### Claude Code

Add Seiche for your user account:

```bash
claude mcp add --transport http seiche --scope user https://api.seiche.info/mcp
```

For a project-scoped setup, merge
[`claude-code.mcp.json`](claude-code.mcp.json) into `.mcp.json` at the project
root. Run `claude mcp list`, then use `/mcp` in an interactive session and
confirm that `seiche` is connected. Claude Code asks users to approve a new
project-scoped server.

### Cursor

Merge [`cursor.mcp.json`](cursor.mcp.json) into `.cursor/mcp.json` for one
project or `~/.cursor/mcp.json` for all projects. Open **Customize → MCP** to
enable or inspect the server. Connection diagnostics appear in the **MCP Logs**
output channel.

### Visual Studio Code

Merge [`vscode.mcp.json`](vscode.mcp.json) into `.vscode/mcp.json`. Run
**MCP: List Servers** from the Command Palette to start, stop, restart, or show
output for `seiche`. VS Code uses Streamable HTTP first for `"type": "http"`.

### Gemini CLI

Merge [`gemini.settings.json`](gemini.settings.json) into
`~/.gemini/settings.json` for user scope or `.gemini/settings.json` for project
scope. Run `/mcp` in Gemini CLI to inspect the discovered tools. The example
keeps `trust` false so tool-call confirmations remain enabled.

### Codex

Merge [`codex.config.toml`](codex.config.toml) into `~/.codex/config.toml` or,
for a trusted project, `.codex/config.toml`. Codex CLI and the Codex IDE
extension use this configuration. Run `codex mcp list` or use `/mcp` to inspect
the connection.

ChatGPT does not use local Codex configuration for MCP apps. See
[`openai.md`](openai.md) for the accurate workspace-draft and public-listing
boundaries.

## Discovery and publication boundary

As checked at 2026-08-21T21:52:40Z:

- the [official MCP Registry record](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fseiche/versions/latest)
  is active for `io.github.beepboop2025/seiche` version `0.10.1`;
- [Lulu MCPs](https://getlulu.dev/mcps/seiche) has a live downstream listing
  for the same repository and hosted endpoint; this says indexed, not
  owner-claimed;
- [Glama](https://glama.ai/mcp/connectors/io.github.beepboop2025/seiche) has an
  owner-verified, healthy remote connector with eleven live tool links;
- [Smithery](https://smithery.ai/servers/mrinallovesbhature/seiche) has an
  owner-published entry, but its 2026-07-27 scan is stale at ten tools and needs
  an authenticated republish/rescan;
- seven additional live indexes and every claim/freshness gap are recorded in
  the [dated directory inventory](../../distribution/MCP_DIRECTORIES.md).

OpenAI, Docker/GHCR, OpenBB, Hugging Face, Kaggle, and Zenodo remain prepared
and gated until a public receipt exists.

Smithery's current flow republishes or scans an anonymous Streamable HTTP URL.
It does not use the obsolete `smithery.yaml` repository descriptor. A static
`/.well-known/mcp/server-card.json` is only a fallback when automatic scanning
fails, and it must be served from the MCP origin (`api.seiche.info`), not merely
committed under an unrelated web root. The existing entry is evidence of
listing, not Smithery verification or official status.

The AI Catalog's ARD entry embeds the official MCP Registry `server.json`
shape and labels that profile explicitly. It is not a claim of conformance to
the separate experimental MCP Server Card extension, whose schema and
recommended discovery path are still changing.

## Current format references

- [Claude Code MCP documentation](https://code.claude.com/docs/en/mcp)
- [Cursor MCP documentation](https://cursor.com/docs/mcp)
- [VS Code MCP configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
- [Gemini CLI MCP documentation](https://geminicli.com/docs/tools/mcp-server/)
- [Codex MCP documentation](https://developers.openai.com/codex/extend/mcp)
- [OpenAI developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt)
- [Smithery publishing documentation](https://smithery.ai/docs/build/publish)
