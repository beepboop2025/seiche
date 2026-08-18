# Publishing Seiche to MCP registries

Everything in the repo is ready (`server.json`, PyPI ownership proof in
`backend/README.md`). What remains needs credentials or a live deploy, so it's
listed here as a runbook rather than automated blind.

## Prerequisites (once)

1. **Deploy the hosted endpoint.** Merge `feat/mcp-agent-endpoint` to `main`
   (auto-deploys to Hetzner). Confirm `https://api.seiche.info/mcp` answers:
   ```bash
   curl -sX POST https://api.seiche.info/mcp -H 'content-type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
   ```
2. **Publish the package to PyPI when the stdio implementation changes.** The
   registry may advertise a newer hosted server while keeping its optional
   package entry pinned to the latest version that actually exists on PyPI.
   Seiche currently pins `seiche==0.10.0` to match the hosted estuary product
   and the nine public tools (including `latest_article`). That package is not
   on PyPI until a human cuts a GitHub release or publishes the artifact; do
   not advertise a newer package version than the next intended release.
   ```bash
   cd backend
   python -m pip install build twine
   python -m build                 # builds sdist + wheel from backend/
   twine upload dist/*             # needs your PyPI API token
   ```
   The `mcp-name: io.github.beepboop2025/seiche` line in `backend/README.md`
   ships in the package description — that's how the registry proves you own it.

## 1. Official MCP registry (registry.modelcontextprotocol.io)

The one that matters — GitHub, Anthropic, Microsoft back it, and every other
catalogue (below) aggregates from it.

```bash
# install the publisher CLI
brew install mcp-publisher            # or the prebuilt binary from the registry releases

# validate the manifest without publishing
curl --fail --show-error --silent \
  -H 'content-type: application/json' \
  --data-binary @server.json \
  https://registry.modelcontextprotocol.io/v0.1/validate

# authenticate to the io.github.beepboop2025/* namespace (opens a browser)
mcp-publisher login github

# publish
mcp-publisher publish
```

`server.json` already declares both a **PyPI package** (stdio) and the **remote
HTTP endpoint**. If the remote entry is rejected for the GitHub namespace, the
alternative is the DNS-verified `info.seiche` namespace (you own seiche.info and
have the Cloudflare API token): add a TXT record and
`mcp-publisher login dns --domain seiche.info …`. See the runbook comments.

The normal path is already automated: `.github/workflows/registry-publish.yml`
uses GitHub OIDC and a checksum-pinned publisher, so it needs no stored registry
credential. The manual commands above are a recovery path.

## 2. Aggregator registries (submit after the official listing)

Most of these pull from the official registry automatically; a few take direct
submissions. Submitting to all of them is the distribution surface.

| Registry | How |
|----------|-----|
| **PulseMCP** (pulsemcp.com) | Auto-indexes from the official registry + GitHub; submit at pulsemcp.com/submit to speed it up |
| **Smithery** (smithery.ai) | Connect the GitHub repo; it builds from `server.json` |
| **MCP.so** (mcp.so) | "Submit" form; links the repo |
| **Glama** (glama.ai/mcp/servers) | Auto-crawls public GitHub; add the `mcp` topic to the repo |
| **mcpservers.org** | PR to their list repo |
| **GitHub MCP Registry** | Inherits from the official registry |
| **Docker MCP Catalog** | Optional; needs a Docker image |

Add the topics `mcp`, `model-context-protocol`, `mcp-server` to the GitHub repo
so the crawlers find it.

## 3. Client-native distribution

- **Claude Code plugin** — a one-line `claude mcp add` in the README already
  works; a plugin entry makes it one click.
- **ChatGPT / Codex connector** — the remote `https://api.seiche.info/mcp`
  endpoint is the connector URL.

## Operations that still need an owner

1. Publish a new PyPI artifact when the stdio implementation changes.
2. Merge and deploy hosted runtime changes before advertising their version.
3. Use browser OAuth only if the OIDC workflow is unavailable.
4. Submit to any aggregator that does not ingest the official registry.
