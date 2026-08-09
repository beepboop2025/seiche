# Fleet usage digest

The daily digest deliberately separates two different signals:

- **Edge traffic** comes from Caddy and includes discovery, liveness checks,
  directory crawlers, scanners, and possible users. Explicit automation is
  labeled; everything else remains **unclassified**, never “human” or “organic.”
- **MCP tool calls** come from bounded `mcp_activation` journal events emitted
  only after dispatch. They contain product, surface, an allowlisted tool name,
  success/error, and a coarse `edge|direct|unknown` origin—never arguments,
  IPs, user agents, tokens, or identities. Direct loopback work (for example,
  Conn assembling the fleet board) remains visible but separate from edge use.

Payment offers and quota rejections are funnel events, not activations. An
unknown tool is reported separately as an invalid probe. LiquiLens runs on
Railway and is explicitly outside this host-local digest.

Run the tests and preview locally on the host:

```bash
python3 -m pytest ops/fleet-usage/test_usage_digest.py -q
python3 ops/fleet-usage/usage_digest.py --print-only
```

After all hosted activation hooks are deployed, install as root:

```bash
bash ops/fleet-usage/install.sh
```

The installer atomically replaces the script, retains a timestamped backup,
versions the systemd service/timer, and records when telemetry became complete.
Until a full 24-hour observation window has elapsed, the digest prints coverage
duration rather than implying that a partial-window zero is conclusive.
