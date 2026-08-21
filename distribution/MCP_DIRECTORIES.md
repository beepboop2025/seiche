# Seiche MCP directory inventory

Checked at `2026-08-21T21:52:40Z`. The machine-readable source of truth is
[`submissions.csv`](submissions.csv). A page being live does not imply that it
is current, owner-claimed, independently verified, or official.

## Live records

| Directory | Ownership | Freshness | Owner action |
|---|---|---|---|
| [Official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fseiche/versions/latest) | Owner-published | Current `0.10.1` | Publish the signed next version through the release workflow |
| [Glama remote connector](https://glama.ai/mcp/connectors/io.github.beepboop2025/seiche) | Owner-verified | Current; 11 live tools | Create/claim the separate repository-score entry |
| [Smithery](https://smithery.ai/servers/mrinallovesbhature/seiche) | Owner-published | Stale; 10 tools | Republish/rescan the existing namespace after release |
| [mcp.so](https://mcp.so/servers/seiche) | Unclaimed | Recently synced | Claim the existing entry through GitHub |
| [LobeHub](https://market.lobehub.com/s/plugins/beepboop2025-seiche) | Unclaimed | Stale transport/version | Claim, then correct; do not duplicate |
| [MCP Index](https://mcpindex.ai/server/io-github-beepboop2025-seiche) | Unclaimed | Stale `0.10.0` | Complete its challenge and human-review claim flow |
| [Lulu](https://getlulu.dev/mcps/seiche) | Unclaimed aggregate | Current sources | Optional browser claim; no new submission |
| [MCPBeat](https://mcpbeat.com/mcp-servers/beepboop2025/seiche/) | Third-party runtime index | Stale `0.10.0`/9 tools | Request a correction from the maintainer |
| [ConnectorZone](https://connector.zone/connectors/beepboop2025-seiche/) | Registry mirror | Stale `0.8.0` | Wait for automatic registry refresh |
| [ZBS Index](https://index.zbs.gg/en/mcp/io-github-beepboop2025-seiche/) | Registry/probe index | Stale `0.8.0`/8 tools | Wait for automatic refresh |
| [CorpusIQ Hermes](https://www.corpusiq.io/docs/hermes/mcp/servers/external/index.html#seiche-us-money-market-stress-testing-mcp-new-july-12) | Curated third-party docs | Stale product copy | Submit a docs correction for review |

## Not yet listed

- [PulseMCP](https://www.pulsemcp.com/submit): wait for Official Registry
  ingestion or use its curated form.
- [Docker MCP Catalog](https://github.com/docker/mcp-registry/blob/main/CONTRIBUTING.md):
  submit a reviewed remote-server entry; no image redistribution is required.
- [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers/blob/main/CONTRIBUTING.md):
  blocked until the separate Glama repository-score badge exists.
- [mcpservers.org](https://mcpservers.org/submit): use its reviewed web form;
  the backing awesome list does not accept pull requests.
- [MCPub](https://mcpub.dev/mcp): the same-origin discovery route is implemented
  for this release; deploy it, then invoke MCPub's public submission tool and
  preserve the resulting live-directory receipt.

The archived `appcypher/awesome-mcp-servers` repository is intentionally not an
action target.
