# Verified research release scope

On 28 October 2026 the owner approved releasing verified research tools while marking unqualified legacy features unavailable. This is an availability policy, not a claim of publisher permission.

MCP discovery exposes the available research tools and source_rights_status. Older calls to restricted tools return explicit source-rights errors before loading a snapshot or executing a model. Paid preflight refuses these calls before payment. The associated legacy HTTP routes return 403 without running their handlers; authority and authentication middleware remain outer guards. All FRED series JSON and CSV exports use the same current restriction policy.

The USD desk retains independently sourced rows and dated metadata for withheld rows. The workbench defaults to directly acquired ECB reference FX. The canonical USD atlas uses the existing NY Fed SOFR median and a separately identified Federal Reserve Board IORB adapter. No historical row is relabelled, no publication timestamp is invented, and absent reference comparisons cannot make a funding review ready.

Private retained snapshots, source collection state and internal release-health controls are unchanged. Remaining static/editorial publication surfaces are separate rights gates and are not advertised through the released MCP tools. This release does not claim all historical material is rights-qualified.
