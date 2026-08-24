# Signed NBS browser-export intake

Seiche's China macro path is an offline evidence intake, not a scraper and not a
market pack. A trusted owner exports a CSV from the official National Bureau of
Statistics of China monthly-data browser, builds a canonical manifest, signs a
domain-separated claim with an already trusted Ed25519 key, and hands the three
files to the operator intake. The server stores the raw envelope under a
root-only directory and materializes a separate metadata-only public revision.

The public contract intentionally contains no NBS observations. Schema v1 fixes
`values_published=false`; neither an environment variable, signed input nor API
request can relax it. A later value-publishing change would require a reviewed
source release and rights decision, not an operator toggle.

## Public and restricted boundaries

| Layer | Default path | Reader | Contents |
|---|---|---|---|
| restricted evidence | `/var/lib/seiche-nbs/restricted` | root operator only | raw CSV object, canonical manifest, detached signature, committed-head receipt |
| public revisions | `/var/lib/seiche-nbs/public/revisions` | root writes; `seiche` reads | immutable metadata-only projection and append-only continuity receipts |
| REST / MCP | `section=china_macro` | anonymous public | four source identities, semantics, revision provenance and evidence status |

The API service receives `SEICHE_NBS_PUBLIC_DIR` and a read-only systemd grant
for the public directory. Its unit explicitly makes the restricted path
inaccessible. The request path never constructs a restricted path, reads a raw
file, starts collection or accepts a signer supplied by the request.

## Release-reviewed series

The only accepted identities are defined in `backend/seiche/nbs_intake.py`:

- `CN.NBS.CPI_INDEX` — CPI index, previous-year same month = 100. The browser's
  literal `%` unit label is retained as source metadata but is not treated as a
  percent-change semantic.
- `CN.NBS.PPI_INDEX` — PPI index, previous-year same month = 100. The browser's
  literal `无` unit label is retained but is not semantically authoritative.
- `CN.NBS.MANUFACTURING_PMI` — diffusion index in percentage points with a
  threshold of 50.
- `CN.NBS.INDUSTRIAL_VALUE_ADDED_YOY` — real year-over-year percent change;
  blank source cells stay missing.

Each binding pins the browser catalog ID, row ID, indicator ID, export key,
dimension code/name, exact English label, semantic contract and an official NBS
release-reference URL. An intake that drifts on any field is rejected.

## Prepare an export

1. Open the [official monthly-data browser](https://data.stats.gov.cn/dg/website/page.html#/pc/national/en/monthData).
2. Select one or more release-reviewed rows and export CSV. Do not open and
   resave it in spreadsheet software: intake binds the exact UTF-8 BOM, NBS
   `TAB + comma` delimiter, human month headers, row labels, notes and source
   footer.
3. Record a UTC `knowledge_time` no earlier than the official release page and
   no later than capture. This is capture/knowability time, not observation time.
4. Build a canonical manifest using schema `seiche.nbs-owner-export.v1`. Use
   `NBS_SERIES_BINDINGS[series_id].manifest_dict(release_url=...)` rather than
   retyping source identifiers. Generate `commitment_nonce` independently for
   every manifest with a cryptographically secure random source, for example
   `python3 -c 'import secrets; print(secrets.token_hex(32))'`. Never reuse or
   derive this nonce from the export contents.
5. Canonicalize JSON as UTF-8 with sorted keys and separators `(',', ':')`.
   Extra whitespace, duplicate keys and unknown fields are rejected.

The manifest binds:

- dataset and export identity;
- predecessor export ID and predecessor manifest digest;
- a fresh restricted 32-byte random commitment nonce;
- exact source metadata and official release URL;
- source periods and decimal strings;
- the raw filename, format, media type, exact human month-header map, SHA-256
  and byte length;
- the code-owned metadata-only publication policy.

Raw limits are 32 MiB and 10,000 rows; the manifest is capped at 256 KiB and
4,096 records. These are security bounds, not claims about upstream coverage.

## Sign the claim

Use `build_signature_claim()` to derive the exact claim and
`encode_signature_claim()` to produce the bytes for Ed25519 signing. The claim
is domain-separated as `seiche-nbs-owner-export-v1` and commits to the manifest,
the metadata-only public projection, and—transitively through the salted
manifest—the restricted raw object. Neither the raw digest, its byte length nor
the nonce appears in the public projection or detached public claim. The
operator CLI performs the same strict canonical-manifest read and emits the
exact bytes without a trailing newline:

```bash
seiche nbs-intake claim manifest.json \
  --signed-at 2026-08-22T10:05:00Z \
  --signer-key-id "$TRUSTED_ED25519_PUBLIC_KEY_HEX" > claim.json
```

Run that command and the external Ed25519 signing operation on the owner-held
signing workstation. Do not copy the private key to the Seiche application host.
Sign `claim.json` byte for byte; tools that silently add a newline produce a
different message and intake will reject the sidecar.

The sidecar's `signer_key_id` is the 32-byte public key encoded as 64 lowercase
hex characters. It is an identifier, not a trust grant. Intake accepts it only
when the same key is already present in Seiche's release-pinned hosted allowlist
or explicit protected trust policy. Ambient environment overrides and key
material carried beside the export do not bootstrap authority.

Removing a key from the trust policy makes revisions signed by that key fail
verification; leaving an old key trusted leaves its historical signatures
valid. Plan key rotation as an explicit operator event with an archived trust
decision. The revision chain does not itself revoke a previously trusted key.

Write the detached signature as 128 lowercase hex characters in the canonical
signature sidecar. Keep the private key outside the application host and never
place it in the repository, manifest, sidecar, logs or shell history.

## Ingest and verify

Copy only `manifest.json`, `signature.json`, and the exact browser CSV to an
operator-controlled intake directory on the host. Then run:

```bash
sudo /etc/seiche/libexec/seiche-nbs-intake.py \
  manifest.json signature.json nbs-export.csv
```

The production launcher has the exact `#!/usr/bin/python3 -I` shebang and no
root override, so direct execution through `sudo` starts the system interpreter
in isolated mode even when the caller supplies a hostile `PYTHONPATH`. It
requires effective UID 0, resolves the named `seiche` group, takes the
root-owned `0600` single-link market-backup lock with a fixed 300-second wait,
safely opens and retains the canonical root:`seiche` mode `0750`
`/var/lib/seiche-nbs` directory, and runs the complete fixed-path storage-volume
v2 preflight. A missing root is rejected rather than created on the host root
disk.

The launcher selects a root-owned, read-only package from
`/opt/seiche-nbs-intake/releases/<40-hex-sha>/seiche` through the protected
single-link `/opt/seiche-nbs-intake/current-sha` pointer. It accepts exactly
`__init__.py`, `nbs_intake.py`, and `nbs_trust.py`, runs the child under
`/usr/bin/python3 -I -B`, and proves both security-sensitive modules resolved
from that same release. Only then does it give the child a one-use inherited
directory descriptor and random pipe-token capability. It revalidates the
runtime pointer, storage proof, production descriptor/path identity, named
group, and backup-lock identity after every child outcome before unlocking.

The command prints only the metadata-only public projection. It never prints
the raw CSV or private manifest records. Direct
`seiche nbs-intake ingest --root /var/lib/seiche-nbs`, direct
`NBSIntakeStore.ingest()`, and `ingest_signed_export()` calls against the
production tree are rejected because they do not hold that launcher
capability. Path aliases, an exact-root bind-mount alias, and lexical or
resolved descendants of the production root cannot be presented as custom
stores.

The library entry point remains available for isolated development and tests
using a custom root outside the production tree:

```python
from seiche.nbs_intake import ingest_signed_export

context = ingest_signed_export(
    "manifest.json",
    "signature.json",
    "nbs-export.csv",
    root="/tmp/seiche-nbs-development-store",
)
print(context.revision_id)
```

Intake completes only after all of these pass:

1. bounded, canonical JSON and exact-field validation;
2. raw digest/size and exact NBS CSV label/month/footer binding;
3. official release URL, series semantics and value-range checks;
4. trusted Ed25519 signature verification;
5. immutable export-ID and object collision checks;
6. unique predecessor, digest continuity, signed chronology and no-fork checks;
7. rollback protection against the committed head receipt;
8. atomic publication of the restricted envelope and metadata-only revision.

An exact retry is idempotent. A reused export ID with different bytes, a
non-adjacent per-series period, chain splice, suffix deletion, fork, rollback or
untrusted signer fails closed. A failed intake must not change the committed
head. Public projection files and the public head are assigned the reviewed
`seiche` reader group on their staged inode before rename, then verified as
single-link mode `0640`; setgid inheritance alone is not treated as proof of
API readability.

After ingest, verify through the least-privileged reader:

```python
from seiche.nbs_intake import load_public_context_from_public_dir

result = load_public_context_from_public_dir("/var/lib/seiche-nbs/public")
print(result.to_dict())
```

Then check the public projection:

```bash
seiche nbs-intake status --public-dir /var/lib/seiche-nbs/public
curl --fail --silent --show-error \
  'https://api.seiche.info/api/v2/world-markets?section=china_macro'
```

`status` exits 0 for a verified head, 2 when onboarding remains unavailable,
and 1 for a hard integrity or I/O failure. `catalog` prints the code-owned four
series identities without reading any evidence directory.

The response may be `restricted` only when a fully verified public head is
present. It must always say `values_published=false`,
`raw_evidence_included=false`, `history_included=false`,
`scoring_eligible=false` and `cn_cny_gauge_eligible=false`. `knowledge_time`
must never appear as `as_of` or advance World Markets coverage/freshness clocks.

## Backup and recovery

The NBS tree is deliberately separate from service-writable `/var/lib/seiche`:
root-only evidence must not sit below an ancestor the API or collectors can
rename. The normal market-platform state archive includes both
`/var/lib/seiche` and `/var/lib/seiche-nbs`, so the restricted raw and numeric
evidence, metadata-only public projections, and both head receipts stay in one
checksummed disaster-recovery snapshot. This local snapshot is deliberately
unencrypted at the archive layer, but its directory is root-only mode `0700`
and every committed member is mode `0600`. Host access controls are therefore
part of its custody boundary.

The closed snapshot manifest is `seiche.market-backup.v4`. It binds
`nbs_state_root=/var/lib/seiche-nbs`,
`nbs_full_store_audit_contract=seiche.nbs-full-store-audit.v1`, and
`nbs_full_store_audit_result=required_at_restore`, while separately binding the
root-controlled `/var/lib/seiche-palimpsest-china` archive and its canonical
activation-state audit. A v2 snapshot predates the NBS recovery contract and
cannot satisfy disaster-recovery readiness merely by containing a public
revisions directory. A legacy v3 snapshot can be restored only with an empty,
inactive Palimpsest China state.

The optional offsite lane encrypts the complete closed snapshot before upload;
only ciphertext and a metadata-only receipt leave the host. Restricted raw and
numeric evidence is intentionally present inside the protected local snapshot
and encrypted offsite object so it can be recovered, but it is excluded from
public projections, REST, MCP, frontend payloads, and every unencrypted offsite
surface. A restore is not accepted merely because files exist: run the sealed
full-store audit and confirm every restricted manifest and Ed25519 signature,
every referenced raw object's size and SHA-256 commitment, the exact raw-object
set, the restricted chain and head, the public chain and head, and the exact
public bytes committed by each corresponding restricted pair. Missing or
orphaned raw objects, missing or corrupt manifests/signatures/heads, extra
topology, and restricted/public divergence all fail closed before a scratch
database is created.

Restore extraction intentionally discards archived ownership and permissions.
The restore checker first rejects symlinks, hard links, unknown members, and an
inexact tree, then normalizes only the code-owned modes in its isolated scratch
directory. It imports `NBSIntakeStore.audit_store_strict()` and the trust policy
only from the root-owned sealed runtime. That audit never takes the intake lock,
creates a layout, reconciles staging, changes a mode, or removes a file. The v4
restore receipt records `nbs_full_store_audit_result=not_onboarded` for the one
safely empty provisioned state or `verified_head` for a complete signed history.
Only those two results satisfy recovery readiness.

The local head receipt detects a missing suffix or pointer while that receipt is
present. It cannot distinguish a whole-store rollback in which an older,
internally consistent revisions directory and older receipt are restored
together. The append-only offsite receipt for the encrypted archive is therefore
the external witness: restore the exact immutable ciphertext object version
named by the latest accepted remote receipt, verify its ciphertext and closed
source-content digests, then run the sealed full-store audit against the
restored public and restricted chains before activation. The offsite metadata
retains `nbs_full_store_audit_result=required_at_restore`; encryption and object-store
round-trip checks do not substitute for the local sealed semantic audit. Do not
describe local validation alone as complete rollback detection.

Never recover by deleting a head receipt, renaming a revision or editing JSON.
Restore the exact signed objects and receipts from the offsite copy. If that is
impossible, leave the public selector structurally unavailable and investigate;
absence is safer than an unverifiable China-data claim.

## Rights and citation

The official browser permits export, but export permission does not by itself
settle every downstream redistribution use. Seiche therefore publishes source
identity and provenance only while retaining the raw and values as restricted
evidence. Upstream [NBS terms](https://www.stats.gov.cn/english/nbs/200701/t20070104_59236.html)
remain controlling.

Owner attestation is not an NBS digital signature. Cite the canonical
[China macro evidence page](https://seiche.info/markets/china-macro/), public
schema, revision ID and `knowledge_time` when available, and link the official
release page for the source identity. Do not cite a withheld value or describe
capture time as the economic observation date.
