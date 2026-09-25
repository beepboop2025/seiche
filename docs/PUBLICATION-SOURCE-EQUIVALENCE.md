# Signed publication source equivalence

The source-equivalence receipt repairs a publication hold caused by root product
README changes without issuing a new engine release. It is a separate contract
from `seiche.frontend-publication.v1`. The existing frontend receipt schema,
purpose, finite controller paths, and rejection of README-only releases remain
unchanged. The original catalog gate and generated-content fallback are unchanged.

## Four subjects, three clean checkouts

- **R**: the exact existing signed backend release, also the existing corpus
  receipt subject. All execution, dependency, build and rendering inputs for a
  full publication come from this immutable tree.
- **C**: the reviewed publication controller. Its signed `frontend-publication-C`
  tag is authenticated by the verifier stored in **R**, not C's new rules. Only
  the original verifier's isolated operations, controller, review-only and
  already validated desk classes are accepted. Frontend changes, asset
  retirements, CSP changes and new runtime exceptions are not an equivalence
  bootstrap.
- **D**: the exact source authorized by a distinct signed
  `publication-source-equivalence-D` annotated tag. D may equal C for the first
  receipt; no artificial documentation commit is needed.
- **H**: exact current main selected for publication. H must equal D or follow it
  through the unchanged single-parent daily/weekly desk contract, with no
  controller exception. Current-main compare and swap checks still compare
  against H immediately before each write.

Native static and full publishers do not currently share a publication lock.
Activation and recurring publication therefore require exactly one enabled
publication writer across native static/full controllers and GitHub Actions,
until separately reviewed shared serialization exists. H/mirror checks and
non-force Git pushes do not eliminate a Cloudflare last-writer race after the
last check. This operating prerequisite is not a guarantee made by this receipt.

Use distinct clean checkouts for R, C and H even when C, D and H identify the same
commit. The API verifies both tracked bytes and index flags. It checks immutable
trust-file and catalog-gate bytes across roots before loading the authenticated
original verifier. A new private temporary directory under R's parent prevents
ignored Python bytecode caches from substituting for original source bytes.

This receipt does **not** bind new backend, package, corpus, activation or recovery
subjects. It contains no assertion of deployment, live health or new traction.

## Canonical signed receipt

The original v1 preparation remains the default and continues to reject UI
changes in C. A separately reviewed UI uses v2, described below; an existing v1
signature never grants that authority.

Schema: `seiche.publication-source-equivalence.v1`.
Purpose: `unchanged_signed_engine_with_validated_desk_overlay`.
The exact required key set is:

```text
schema
purpose
sourceSha
controllerSourceSha
controllerReceiptTag
controllerReceiptObjectId
backendReleaseTag
backendReleaseSha
corpusReceiptTag
corpusReceiptSha
catalogSha256
equivalentInputManifestSha256
classifiedHistorySha256
```

The tag targets D directly as a commit. Its header must name the deterministic
full-SHA tag, and its annotation must be the exact generated canonical JSON:
UTF-8, sorted keys, compact separators, no nonfinite values, one trailing newline.
Extra/missing keys, alternate formatting, subject substitutions, lightweight tags,
PGP signatures and signatures from any other key fail. The caller supplies the
SSH fingerprint independently; the repository trust file must contain exactly
that fingerprint. The exact signed annotated C tag object ID is included, so
moving or replacing it cannot preserve D's receipt.

The input manifest binds the full R tree's paths, Git object IDs and file modes,
except root `README.md`, the finite paths actually classified in the original
signed C receipt, and the strict generated desk paths. Its nonexcluded entries
must match C, D and H exactly. The excluded controller paths cannot change after
C: history admission independently rejects them even though they are not engine
build inputs. Packaged `backend/README.md` is never the root README exception.

History classification examines every DAG edge, including side branches and
reverted changes. Between C and D, only regular-file modifications to root
`README.md` and strict desk changes are admitted. A desk commit must use the
existing author, subject and file rules; it cannot also modify README. A merge
may inherit a complete validated desk snapshot only when that parent carries all
desk origins. Merge-authored evidence, selecting an older parent snapshot and
combining divergent desk histories fail. Regular direct desk corrections retain
the original contract; this is not a semantic validator of their content.
Unrelated ancestry, executable/symlink/gitlink changes, renames, empty commits,
workflow changes and arbitrary later operations edits fail.

## Review and signing commands

These are operator steps, **not automatic signing instructions**. First authenticate
R's signed release and its original verifier bytes with the independently pinned
release trust root. Set `R`, `C`, `D`, `H`, `R_ROOT`, `C_ROOT`, `H_ROOT`,
`SIGNER_FINGERPRINT` and review directories to reviewed exact values; use permanent
SSD paths on the Mac. Do not derive the fingerprint from an untrusted candidate.

Prepare C with **R's original** verifier and inspect the complete compatibility
report. This must succeed before C's new verifier is used:

```sh
python3 -B "$R_ROOT/ops/release/verify_frontend_publication.py" \
  --root "$C_ROOT" --expected-sha "$C" \
  --signer-fingerprint "$SIGNER_FINGERPRINT" \
  --prepare --output "$C_REVIEW"
```

After exact review, create the new immutable C tag with the already configured
release signing key. Never reuse/move a tag or suppress a signature failure:

```sh
git -C "$C_ROOT" tag -s --cleanup=verbatim \
  -F "$C_REVIEW/receipt.json" "frontend-publication-$C" "$C"
```

Make the new tag available to all three local checkouts (shared worktrees already
share it). Independently run R's original verifier against C. Its normal verify
CLI also performs the existing live backend/corpus receipt checks:

```sh
python3 -B "$R_ROOT/ops/release/verify_frontend_publication.py" \
  --root "$C_ROOT" --expected-sha "$C" \
  --signer-fingerprint "$SIGNER_FINGERPRINT" \
  --receipt-tag "frontend-publication-$C"
```

For D preparation, H_ROOT must temporarily be the exact clean D checkout. The
prepare command requires proven local and remote absence of the new D tag and a
new output directory. It creates no tag or signature:

```sh
python3 -B "$C_ROOT/ops/release/verify_frontend_publication.py" \
  --source-equivalence --root "$H_ROOT" --expected-sha "$D" \
  --controller-root "$C_ROOT" --backend-root "$R_ROOT" \
  --signer-fingerprint "$SIGNER_FINGERPRINT" \
  --prepare --output "$D_REVIEW"
```

Review all three generated files: `receipt.json`, `input-manifest.json` and
`classified-history.json`. Then sign the exact receipt and subject:

```sh
git -C "$H_ROOT" tag -s --cleanup=verbatim \
  -F "$D_REVIEW/receipt.json" "publication-source-equivalence-$D" "$D"
```

With H_ROOT at the exact selected current H, admission is:

```sh
python3 -B "$C_ROOT/ops/release/verify_frontend_publication.py" \
  --source-equivalence --root "$H_ROOT" --expected-sha "$H" \
  --controller-root "$C_ROOT" --backend-root "$R_ROOT" \
  --signer-fingerprint "$SIGNER_FINGERPRINT" \
  --receipt-tag "publication-source-equivalence-$D"
```

The equivalence CLI is **offline source admission only**. Successful output does
not replace live package/corpus/runtime gates, current-main guards, immutable
controller-image checks, the single-writer prerequisite, acceptance or rollback evidence.
The two new signed tags can be published only after the independent operator
review and normal exact-release checks; never force-push them.

## Publisher API and projection

### Separately built frontend (v2)

Use `--source-equivalence --prepare --include-signed-frontend` only when C also
contains a reviewed interface change. The canonical signed D annotation then
uses schema `seiche.publication-source-equivalence.v2` and purpose
`unchanged_signed_engine_with_separately_built_frontend`. Its remaining fields
are identical to v1 and still bind the exact independently signed C receipt.

R's original verifier must authenticate C and classify every change before
this path is eligible. Only its existing `frontend`, `retired_public_funding`
and `editorial_connect_origin` classes are added to the v1 bootstrap classes.
There must be an actual admitted frontend change. Dependencies, build settings,
backend code, catalog data and neighboring public assets remain forbidden.
No frontend or controller changes after signed D are admitted.

The full publisher first builds and seals R's engine output with the validated
desk overlay. It separately tests and builds C's frontend from bounded,
nonexecutable Git archive inputs, under an unprivileged identity and independent
test/build directories. R's original frontend proof seals the compiled shell
over the generated evidence. Only the root shell, new compiled assets, the
bounded root image and original receipt's exact retirement/CSP exceptions may
change. Publication verifies those bytes on the public origin after the
unchanged dataset gate. Recovery retains the UI source, seal and prior mirror.
Thus v2 signs the UI explicitly while keeping the engine and corpus subjects R.

Verification selects the format from a bounded annotation, then recomputes the
entire canonical payload and verifies its pinned SSH signature. An unsigned,
malformed, downgraded or mismatched annotation grants no authority.

```python
admission = verify_source_equivalence(
    H_ROOT,
    expected_sha=H,
    receipt_tag="publication-source-equivalence-" + D,
    controller_root=C_ROOT,
    backend_root=R_ROOT,
    signer_fingerprint=SIGNER_FINGERPRINT,
)
```

The return schema is `seiche.publication-source-admission.v1` with:

- `sourceEquivalence`: the authenticated exact D payload above.
- `currentSourceSha`: H, still subject to the external current-main guards.
- `equivalentInputManifest`: `entries` (`path`, `mode`, `type`, `object`),
  `excludedPaths`, and `sha256`.
- `deskOverlay`: the complete H desk `entries` (`path`, `mode`, `object`),
  `deletePaths` for R desk files absent at H, and `sha256`.

The overlay digest is SHA-256 of canonical JSON containing only `entries` and
`deletePaths`; entries retain Git tree order and deletions are sorted. Only mode
`100644` blob files pass. Limits are 20,000 files, 8 MiB per file and 128 MiB total.
This is permission to render validated desk data, never to execute its contents.
The publisher must independently match the manifest to exact Git objects and
materialize it safely without following symlinks.

The full publisher runs the unchanged catalog gate in pristine R, retains live
backend/corpus subject checks, and builds in a distinct R projection containing
only the admitted H desk overlay. Dependencies, Python imports, frontend build
inputs, shell scripts, prerenderers and controller helpers must not come from H.
Static publication keeps its existing sealed-evidence path. Neither path may
reinterpret C's frontend receipt as a fresh engine release or suppress a failed
live receipt. Existing callers without explicit equivalence admission retain
the original strict behavior.

## Regression coverage

Synthetic temporary SSH keys and isolated SSD Git fixtures cover D=C, README DAGs,
strict H desk overlays, exact object/digest binding, finite original bootstrap,
canonical signatures, side-branch/reverted drift, forbidden packaged/workflow
inputs, executable/symlink/gitlink/rename changes, dirty roots, invalid desk
merges, divergent and rollback snapshots, and bounded overlays. Original frontend
README-only rejection and strict application-history tests remain in place.
