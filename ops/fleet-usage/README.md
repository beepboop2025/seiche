# Fleet usage digest

The daily digest deliberately separates two different signals:

- **Edge traffic** comes from Caddy and includes discovery, liveness checks,
  directory crawlers, scanners, and possible users. Explicit automation is
  labeled; everything else remains **unclassified**, never “human” or “organic.”
- **MCP tool events** come from bounded `mcp_activation` journal events emitted
  by the server request-handling path. They contain product, surface, an allowlisted tool name,
  success/error, and a coarse `edge|direct|unknown` origin—never arguments,
  IPs, user agents, tokens, or identities. Direct loopback work (for example,
  Conn assembling the fleet board) remains visible but separate from edge use.
  A recorded error can be a refusal before invocation, including quota denial;
  a recognized tool name does not prove that useful evidence was returned.

Raw known-tool totals and all existing dimensions are retained for continuity.
Each raw event also belongs to exactly one separate attribution bucket:
`operator_verification` for the exact authenticated-service log suffix, or
`unclassified` for other events, including old logs. Known/success/error/invalid
counts and tool/surface/origin dimensions remain separate in both buckets. The
operator count is excluded from the unclassified count; neither count is a
verified customer or adoption metric. Missing, unsupported, malformed or
conflicting classification suffixes stay unclassified rather than being guessed.

Payment offers remain funnel events, not verified payment or adoption. An
unknown tool is reported separately as an invalid probe. Customer identity,
retention and payment attribution remain unknown. LiquiLens runs on Railway and
is outside this host-local digest; Seiche's host telemetry is retired and does
not establish Railway completion coverage.

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

For a script-only attribution repair, verify the deployed script and unit hashes,
retain a private backup, qualify the exact replacement script, and publish it
atomically without replacing units or resetting telemetry arming state. Preview
with a bounded `--hours` window and `--print-only`; invoking the script without
`--print-only` sends the digest. Do not start the digest service to verify a repair.
