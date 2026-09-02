# Seiche — OpenAI plugin submission worksheet

Status: **code and review-material draft only; not submitted**  
Submission type: **With MCP (MCP-only, no custom UI)**  
MCP URL type: **Universal**

## Listing draft

| Field | Draft value |
|---|---|
| Plugin name | Seiche |
| Short description | Evidence-bounded intelligence across money, foreign-exchange and capital markets. |
| Long description | Seiche helps users inspect money-market funding, 22 public FX reference series and capital-market transmission using official and public evidence. It exposes the live dollar-funding conclusion, a granular chartless USD desk, a licence-aware global money-market atlas, a bounded world-markets context, historical analogs, the published diagnostic record and misses, data freshness, institutional positioning, and oil/FX/material pathways. Every response carries timestamps, canonical citation URLs, status or claim boundaries where applicable. Seiche is research data, not investment advice, and does not promise exhaustive coverage, executable quotes, forecasts or causal conclusions. |
| Website | https://seiche.info |
| Support | https://seiche.info/support |
| Privacy | https://seiche.info/privacy |
| Terms | https://seiche.info/terms |
| Logo candidate | https://seiche.info/icons/pwa-512.png |
| Source | https://github.com/beepboop2025/seiche |
| MCP server | https://api.seiche.info/mcp |
| Authentication | None for the twelve-tool public surface; 200 calls per IP per UTC day |
| Custom UI | None |
| Category | Owner must select the closest current portal category; do not guess outside the portal |
| Country availability | Owner must choose only jurisdictions where they are prepared to offer the service |

The logo URL is a source asset, not proof that the portal's image checks have
passed. Export or upload it in the exact dimensions and format the live portal
requests.

## Starter prompts

1. What does the current dollar-funding board say, and how fresh is the evidence?
2. Give me the repo-segment view from the USD money-market desk, with dates and sources.
3. Which historical funding episodes look most like today, and what are the diagnostic's limits?
4. Is current oil-market cash pressure reaching US dollar funding, or is it still scenario-only?
5. Is FX or physical-material working-capital pressure showing up in money markets?
6. What did Seiche publish today? Preserve the article's exact factual authority and caveats.
7. Give me a money, FX and capital-markets briefing with source clocks, evidence states and canonical citations.

## MCP configuration draft

- Universal MCP Server URL: `https://api.seiche.info/mcp`
- Authentication: no authentication for the submitted public surface.
- Reviewer credentials: none required for the twelve public tools.
- Content security policy: no custom UI is included and no browser component
  fetches external domains. Confirm the portal accepts an empty CSP allowlist.
- Tool scan expectation: exactly twelve tools, each with `inputSchema`,
  `outputSchema`, title, description, and accurate annotations.
- Annotations for every submitted tool:
  - `readOnlyHint: true`
  - `idempotentHint: true`
  - `destructiveHint: false`
  - `openWorldHint: false`

`openWorldHint` is false because calls read Seiche's already-operated evidence
surfaces and do not browse arbitrary user-selected URLs, send messages, or
change outside systems. `latest_article` reads Seiche's own allowlisted feed.

## Test cases

Use `test-cases.json`. It contains seven positive and four negative cases. Run
them against the anonymous public surface so review does not accidentally
depend on subscriber-only tools or credentials.

Suggested release notes:

> Initial MCP-only Seiche plugin submission. Twelve anonymous read-only tools
> provide live dollar-funding conclusions, granular USD and global money-market
> context, a money/FX/capital world-markets view, historical analogs, diagnostic
> evidence, freshness, and bounded cross-market context. This version adds
> declared structured output schemas and typed, privacy-safe failure contracts.

## Owner and portal steps — not completed by this repository

Do not mark any item complete without evidence from the same OpenAI
organization that will publish the plugin.

- [ ] Grant the submitter **Apps Management: Write** in the OpenAI Platform.
- [ ] Complete developer or business **identity verification** and confirm the
      selected publisher name matches the website, support, privacy, and terms.
- [ ] Have the owner or counsel review the public privacy and terms pages. They
      currently label themselves operational drafts pending legal review; do
      not make policy attestations until that review is complete.
- [ ] Create the MCP-only draft in the plugin submission portal and enter the
      universal production endpoint directly, not an existing integration ID.
- [ ] Run **Scan Tools** after the output-schema change is deployed and save the
      portal's validation result.
- [ ] If the portal issues a domain token, serve that exact token alone at
      `https://api.seiche.info/.well-known/openai-apps-challenge` (or an allowed
      parent origin), using the activation runbook below. Never invent,
      pre-generate, reuse, or commit a token.
- [ ] Re-scan after the `openai-apps-challenge` succeeds.
- [ ] Upload the production logo in the portal-requested format.
- [ ] Choose accurate country availability and category values in the live form.
- [ ] Execute the positive and negative cases and preserve reviewer-visible
      results.
- [ ] Read and answer every portal **policy attestations** item truthfully. This
      repository does not answer legal or publisher attestations on the owner's
      behalf.
- [ ] Submit only after the production MCP endpoint advertises the same tool
      contracts tested in this branch.

## Pre-submission production checks

```bash
curl -fsS https://api.seiche.info/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

curl -fsS https://api.seiche.info/mcp \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"funding_stress_now","arguments":{}}}'
```

Pass conditions:

- HTTPS succeeds without reviewer credentials;
- `tools/list` returns exactly twelve anonymous tools;
- every tool has an object `inputSchema`, object `outputSchema`, title,
  description, and the four annotations above;
- each successful structured result conforms to its advertised schema;
- data-health and claim-boundary fields remain attached to analytical results;
- no response contains secrets, raw exception diagnostics, or unnecessary
  personal data;
- `/privacy`, `/terms`, and `/support` are public and consistent with actual
  MCP data handling.

## Domain-verification activation, rotation, and teardown

The committed Caddy route is fail-closed. It returns `404` unless the running
Caddy process has an `OPENAI_APPS_CHALLENGE_TOKEN` that is 16–512 characters,
starts with an ASCII letter or digit, and otherwise contains only ASCII
letters, digits, `.`, `_`, `~`, `=`, or `-`. The enabled response is the exact
runtime value with no newline and is available only to `GET` and `HEAD` at the
single challenge path. A missing or malformed value reaches an explicit `404`;
it is never interpolated into the Caddyfile parser and cannot become arbitrary
configuration or a generic static-file route.

If the portal supplies a value outside that grammar, stop. Do not transform the
portal value or broaden the matcher on the host. Review and test a repository
change first, because OpenAI requires the response to match its issued value
exactly.

### Activate from the production host

First deploy the signed release containing the challenge route. Before storing
any token, prove the default state is dark:

```bash
challenge_url='https://api.seiche.info/.well-known/openai-apps-challenge'
status=$(curl --proto '=https' --tlsv1.2 --silent --show-error \
  --max-time 15 --max-redirs 0 --output /dev/null \
  --write-out '%{http_code}' "$challenge_url")
test "$status" = 404
```

Then run the following in Bash on the production host. Paste only the exact
token displayed by the OpenAI portal. Silent input keeps it out of shell
history, the allowlist rejects whitespace and shell syntax, and both files are
created from root-owned staging files before Caddy is restarted:

```bash
sudo bash -eu <<'ACTIVATE_OPENAI_CHALLENGE'
umask 077
token_file=/etc/caddy/secrets/openai-apps-challenge.env
dropin=/etc/systemd/system/caddy.service.d/openai-apps-challenge.conf
token_stage=$(mktemp)
dropin_stage=$(mktemp)
cleanup() {
  rm -f -- "$token_stage" "$dropin_stage"
  unset challenge_token
}
trap cleanup EXIT

printf 'Paste the portal-issued OpenAI challenge token: ' >/dev/tty
IFS= read -r -s challenge_token </dev/tty
printf '\n' >/dev/tty
if [[ ! $challenge_token =~ ^[A-Za-z0-9][A-Za-z0-9._~=-]{15,511}$ ]]; then
  printf 'Refusing token: value does not satisfy the committed challenge grammar.\n' >&2
  exit 1
fi

printf 'OPENAI_APPS_CHALLENGE_TOKEN=%s\n' "$challenge_token" >"$token_stage"
printf '%s\n' \
  '[Service]' \
  'EnvironmentFile=-/etc/caddy/secrets/openai-apps-challenge.env' \
  >"$dropin_stage"
install -d -m 0700 -o root -g root /etc/caddy/secrets
install -d -m 0755 -o root -g root /etc/systemd/system/caddy.service.d
install -m 0600 -o root -g root "$token_stage" "$token_file"
install -m 0644 -o root -g root "$dropin_stage" "$dropin"
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl daemon-reload
systemctl restart caddy
systemctl is-active --quiet caddy
ACTIVATE_OPENAI_CHALLENGE
```

A config reload is insufficient after changing an `EnvironmentFile`: systemd
injects the environment when the service process starts, so activation and
rotation intentionally restart Caddy.

### Safe live smoke

This check compares bytes without printing the token and bounds the download.
Run it as root on the host so it can read the protected environment file:

```bash
sudo bash -eu <<'SMOKE_OPENAI_CHALLENGE'
umask 077
challenge_url='https://api.seiche.info/.well-known/openai-apps-challenge'
token_file=/etc/caddy/secrets/openai-apps-challenge.env
body=$(mktemp)
headers=$(mktemp)
trap 'rm -f -- "$body" "$headers"; unset expected' EXIT
expected=$(sed -n 's/^OPENAI_APPS_CHALLENGE_TOKEN=//p' "$token_file")
[[ $expected =~ ^[A-Za-z0-9][A-Za-z0-9._~=-]{15,511}$ ]]
status=$(curl --proto '=https' --tlsv1.2 --silent --show-error \
  --max-time 15 --max-redirs 0 --max-filesize 512 \
  --dump-header "$headers" --output "$body" --write-out '%{http_code}' \
  "$challenge_url")
test "$status" = 200
cmp -s "$body" <(printf '%s' "$expected")
grep -Eiq '^content-type:[[:space:]]*text/plain' "$headers"
grep -Eiq '^cache-control:[[:space:]]*no-store, no-transform' "$headers"
printf 'OpenAI challenge: exact token bytes served with bounded no-store response.\n'
SMOKE_OPENAI_CHALLENGE
```

Save the portal's successful verification receipt, not the token or response
body. Keep the same no-store challenge response active while re-running
**Scan Tools**, throughout submission review, and for any portal-requested
reverification. Do not replace or remove an existing challenge merely because
the first verification succeeded; wait until the portal explicitly says the
challenge is no longer needed or the plugin no longer uses that verification.

### Rotate or tear down

Rotate only when the portal issues and expects a replacement value. This single
exact URL cannot serve two challenges: repeating activation atomically replaces
the environment file, and restarting Caddy stops serving the old value before
the new portal check can run. Immediately run the byte-for-byte smoke, then
complete the portal's new verification. Do not retain or try to serve the old
value in parallel. Never reuse an old value.

Tear down only after the portal explicitly confirms the challenge is no longer
needed, or after the submission/plugin using it has been withdrawn. Remove the
exact secret and service drop-in, restart Caddy to clear the inherited
environment, and prove the route is dark again:

```bash
sudo rm -f -- \
  /etc/caddy/secrets/openai-apps-challenge.env \
  /etc/systemd/system/caddy.service.d/openai-apps-challenge.conf
sudo systemctl daemon-reload
sudo systemctl restart caddy
sudo systemctl is-active --quiet caddy

challenge_url='https://api.seiche.info/.well-known/openai-apps-challenge'
status=$(curl --proto '=https' --tlsv1.2 --silent --show-error \
  --max-time 15 --max-redirs 0 --output /dev/null \
  --write-out '%{http_code}' "$challenge_url")
test "$status" = 404
```
