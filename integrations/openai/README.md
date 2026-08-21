# Seiche OpenAI plugin submission pack

This directory is a review-ready draft for submitting Seiche as an **MCP-only
plugin** to ChatGPT and Codex. It is not evidence that a submission has been
created, verified, reviewed, or approved.

Seiche's universal production endpoint is:

```text
https://api.seiche.info/mcp
```

An anonymous scan sees eleven read-only public tools, including the bounded
`world_markets_context` projection. The endpoint requires no account or API key
for that surface and permits 200 tool calls per IP per UTC day. Five additional
tools remain bearer-token gated and are not part of the anonymous plugin draft.

## What the code now supplies

- action-oriented tool names, titles, descriptions, and explicit input schemas;
- an `outputSchema` for every tool whose successful response includes
  `structuredContent`;
- accurate read-only, idempotent, non-destructive, closed-world annotations;
- text `content` alongside structured data for compatibility;
- privacy-safe typed failure envelopes covered by the same output contracts;
- seven positive and four negative review cases in
  `test-cases.json`.

`desk_brief` intentionally has no output schema because its successful payload
is Markdown text, not `structuredContent`. This pack intentionally does **not**
contain a legacy `ai-plugin.json` manifest: the current publication path scans
the submitted MCP server directly.

## Local verification

From the repository root:

```bash
cd backend
pytest -q tests/test_mcp_server.py tests/test_mcp_http.py \
  tests/test_openai_plugin_contract.py
```

The contract suite always validates the JSON Schema subset Seiche publishes.
If the optional `jsonschema` package is already installed, the same live
handler witnesses are also checked with its Draft 2020-12 validator. Seiche
does not add that package to production merely for review metadata.

## Current official requirements used

- [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [Submit plugins](https://developers.openai.com/plugins/deploy/submission)

Those pages require structured tools to declare output schemas and public
submissions to supply listing details, accurate annotations, starter prompts,
five positive tests, three negative tests, domain verification, an eligible
verified publisher, and policy attestations. The owner-only and portal-only
steps are recorded honestly in `SUBMISSION.md`.
