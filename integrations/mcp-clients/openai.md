# OpenAI surfaces

## Codex CLI and IDE extension

Use [`codex.config.toml`](codex.config.toml). Codex CLI and the Codex IDE
extension use Codex's MCP configuration, so the same anonymous Streamable HTTP
entry works across them. The configuration does not install Seiche into
ChatGPT or publish a public OpenAI listing.

## ChatGPT workspace draft

OpenAI currently documents custom MCP app setup for eligible Business,
Enterprise, and Edu workspaces on ChatGPT web. An eligible workspace admin or
authorized developer can test Seiche without waiting for a public directory
listing:

1. Enable developer mode under the workspace's Apps permissions.
2. Open **Settings → Apps → Create**.
3. Name the draft `Seiche`, enter `https://api.seiche.info/mcp`, and select no
   authentication.
4. Run **Scan Tools**, review the discovered read-only tools, and create the
   draft.

This creates a developer/workspace draft. It is not evidence of OpenAI review,
approval, or app-directory publication. Plan availability, admin roles, and
workspace controls are determined by the live OpenAI interface.

The public submission worksheet and reviewer cases live in
[`integrations/openai`](../openai/). They remain prepared only; do not label
Seiche as submitted or listed until a portal receipt and a public listing are
recorded in [`distribution/submissions.csv`](../../distribution/submissions.csv).

References: [Codex MCP documentation](https://developers.openai.com/codex/extend/mcp),
[OpenAI developer mode and MCP apps](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt),
and [Apps in ChatGPT](https://help.openai.com/en/articles/11487775-connectors-in).
