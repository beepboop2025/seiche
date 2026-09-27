# Standard API discovery and citation guidance

The public `/.well-known/api-catalog` uses RFC 9727 Linkset JSON to advertise
the REST, Seiche MCP and independent corpus MCP interfaces separately. The
existing `ai-catalog.json` remains the source of interface identity. The
corpus descriptor is owner-hosted; it does not imply a listing in the public
MCP Registry. The main Seiche entry links its existing versioned registry
record.

Run `python backend/scripts/build_api_catalog.py` after changing that inventory.
Use `--check` to detect drift. The existing AI-discovery CI test module verifies
the generated catalog, separate transports, corpus descriptor and required
Cloudflare response headers. The developer page explains the citation fields
needed to preserve evidence clocks, source definitions, missingness and limits.

These public files and header changes are outside the frontend-only publication
contract. Publish through the normal signed application/catalog release in
`docs/PUBLISHING.md`; do not expand the frontend allowlist or reuse an immutable
release receipt to deploy them. Publication must preserve the sealed site
mirror's generated evidence. After the normal release, verify GET and HEAD on
the catalog, its Link and Content-Type headers, the JSON descriptor, linked
OpenAPI/registry resources and the visible developer guide.

IndexNow's existing publication helper includes the catalog and direct OFR
dataset route. A submission receipt is only notification, never proof of
indexing, search position or citation by an AI service.
