# Public institutional-flow delivery

The Wake engine runs on Hetzner while Seiche's API runs on Railway. This
bridge publishes only the established public institutional-flow fields;
private engine versions, model parameters and unrelated collector errors
remain in `/var/lib/wake/wake_seiche.json` and are never served by this route.

`publish.py` validates the envelope and source clocks, atomically writes the
allowlisted projection, and retains the old file if validation fails. The
public JSON includes `generated_at`, `evaluated_at`, each source's observation,
release and retrieval dates, and per-section freshness. The API reader
re-evaluates freshness at request time, omits stale/unavailable sections,
rejects envelopes older than two days, rejects redirects and responses over
64 KiB, and has an eight-second timeout. A today-dated generation never changes
an observation date. The source limits are ten days for weekly CFTC/custody
prints and four days for the daily funding-spread input.

## Installation and verification

The API release must use the normal qualified application release procedure.
Do not replace its image while a protected native recovery is being qualified.
`WAKE_PACK_PATH` intentionally disables the network fallback when explicitly
set: an unavailable operator-selected local file must stay unavailable.

For the independent Hetzner publisher, select and record the reviewed source
commit. Copy these four source files, retaining their relative paths, under a
new root-owned `/opt/seiche-wake-publication/releases/<commit>/` directory:

- `backend/seiche/__init__.py`
- `backend/seiche/wakeflows.py`
- `backend/seiche/wake_public.py`
- `ops/wake-publication/publish.py`

The publisher uses only Python's standard library. Record file hashes against
the selected commit before execution. Create `/var/lib/seiche-wake-public`
root-owned, mode 0755; no raw/private pack is copied into that directory.
Run the selected `publish.py` once, then validate the output and compare the
public field projection with the current private pack. Add a new drop-in for
`wake.service` containing:

```ini
[Service]
ExecStartPost=/usr/bin/python3 -B /opt/seiche-wake-publication/releases/<commit>/ops/wake-publication/publish.py
```

Appending this command preserves the existing Undertow post-success copy.
Use `systemctl daemon-reload`; do not run the entire collector just to test
the bridge. Save the prior unit definition and current Caddyfile as receipts.
Insert `caddy-route.conf` inside `api.seiche.info` before generic API handling,
using the just-read file as the expected input. If that file changed during
preparation, rebase the small insertion instead of overwriting another change.
Validate the complete Caddy configuration before reload.

Acceptance checks:

1. GET and HEAD the exact HTTPS route; JSON matches the locally validated
   public projection, with no private keys. POST returns 405.
2. A neighboring route cannot read `wake_seiche.json` or list the public
   directory. Existing Seiche API and Undertow MCP health remain available.
3. After the separately qualified API release, a synthetic public
   `institutional_flows` tool call succeeds and carries per-section freshness
   plus CFTC/custody/spread source clocks.
4. Observe the next normal Wake timer run refreshing the projection. A manual
   projection does not prove the recurring schedule ran.

## Rollback

Record installed hashes and Caddy/unit before-state before changing anything.
Remove only this bridge's drop-in and exact Caddy handle, preserving later
unrelated changes. Validate Caddy and reload both configurations. Retain the
published projection and receipts for investigation. The API then fails closed
if neither its local pack nor the exact public endpoint is available; no
private engine or guessed stale reading is substituted. Application rollback
uses Seiche's existing accepted-identity procedure.
