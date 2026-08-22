# Seiche MCP directory inventory

Verification is per record: use each row's `checked_utc` value in the
machine-readable [`submissions.csv`](submissions.csv), and do not infer that an
older row was rechecked because this document changed. A page being live does
not imply that it is current, owner-claimed, independently verified, official,
or approved by the directory operator.

## Live records

| Directory | Ownership | Freshness | Owner action |
|---|---|---|---|
| [Official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.beepboop2025%2Fseiche/versions/latest) | Owner-published | Current `0.10.1` | Publish the signed next version through the release workflow |
| [Glama remote connector](https://glama.ai/mcp/connectors/io.github.beepboop2025/seiche) | Owner-verified | Current; healthy with 11 live tools | Create/claim the separate repository-score entry |
| [Smithery](https://smithery.ai/servers/mrinallovesbhature/seiche) | Owner-published | Stale; 10 tools | Republish/rescan the existing namespace after release |
| [mcp.so](https://mcp.so/servers/seiche) | Unclaimed | Stale; 9-tool inventory despite a recent sync | Claim the existing entry, then request or trigger a rescan; do not duplicate |
| [LobeHub](https://market.lobehub.com/s/plugins/beepboop2025-seiche) | Unclaimed | Stale `1.0.0` local placeholder; 0 tools | Claim, then correct and validate the remote transport; do not duplicate |
| [MCP Index](https://mcpindex.ai/server/io-github-beepboop2025-seiche) | Unclaimed | Current Registry version `0.10.1` | Use the listing's claim contact and complete its review; do not call it claimed before the page changes |
| [Lulu](https://getlulu.dev/mcps/seiche) | Unclaimed aggregate | Current sources | Optional browser claim; no new submission |
| [MCPBeat](https://mcpbeat.com/mcp-servers/beepboop2025/seiche/) | Third-party runtime index | Stale `0.10.0`/9 tools | Request a correction from the maintainer |
| [ConnectorZone](https://connector.zone/connectors/beepboop2025-seiche/) | Registry mirror | Stale `0.8.0` | Wait for automatic registry refresh |
| [ZBS Index](https://index.zbs.gg/en/mcp/io-github-beepboop2025-seiche/) | Registry/probe index | Stale `0.8.0`/8 tools | Wait for automatic refresh |
| [CorpusIQ Hermes](https://www.corpusiq.io/docs/hermes/mcp/servers/external/index.html#seiche-us-money-market-stress-testing-mcp-new-july-12) | Curated third-party docs | Stale product copy | Submit a docs correction for review |

## Action and receipt order

1. Update the Official MCP Registry only through the signed release workflow.
   Keep `0.10.1` in this inventory until the Registry's public latest-version
   response returns the signed next version.
2. After the released endpoint exposes the intended tool contract, use owner
   authentication to rescan Smithery and to claim/correct the existing mcp.so,
   LobeHub, and MCP Index records. A login, draft, or submitted correction is
   not a live-listing receipt.
3. Recheck automatic mirrors only after the Official Registry update. Do not
   create duplicate records to force ConnectorZone, ZBS Index, Lulu, or other
   aggregators to refresh.
4. Record a status or freshness change in `submissions.csv` only after checking
   the served record itself. Preserve the prior value when a page cannot be
   independently inspected.

## Not yet listed

- [PulseMCP](https://www.pulsemcp.com/submit): wait for Official Registry
  ingestion or use its curated form.
- [Docker MCP Catalog](https://github.com/docker/mcp-registry/blob/main/CONTRIBUTING.md):
  submit a reviewed remote-server entry; no image redistribution is required.
- [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers/blob/main/CONTRIBUTING.md):
  blocked until the separate Glama repository-score badge exists.
- [mcpservers.org](https://mcpservers.org/submit): use its reviewed web form;
  the backing awesome list does not accept pull requests.
- [MCPub](https://mcpub.dev/mcp): the same-origin discovery route exists in the
  release tree, but code is not a deployment receipt. Require an anonymous
  `200` response from `https://api.seiche.info/.well-known/mcp.json`, validate
  its exact remote URL and version, then invoke MCPub's public submission tool
  and preserve the resulting live-directory receipt.

The archived `appcypher/awesome-mcp-servers` repository is intentionally not an
action target. No external submission or approval is implied by this inventory.
