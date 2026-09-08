# Publishing a frontend without activating backend code

Use this contract only for a reviewed UI compatible with an already released
Seiche backend and corpus. It adds an independent frontend publication receipt;
it does not refresh, move or reinterpret the existing application/corpus tags.
The ordinary application and desk-content gates remain unchanged.

## What the frontend receipt asserts

An immutable annotated SSH-signed tag named
`frontend-publication-<full source SHA>` authenticates that exact source, even
when GitHub signed its merge commit. Its canonical JSON annotation has schema
`seiche.frontend-publication.v1` and purpose
`frontend_only_no_runtime_activation`. It binds the actual backend version-tag
subject, actual corpus-receipt subject, catalog bytes and the complete classified
change history. It makes no new data, rights, freshness or trading assertion.

The independent repository variable `RELEASE_SIGNING_KEY_FINGERPRINT` remains
the trust root. Neither a GitHub merge signature, a lightweight tag, an unsigned
annotation, another SSH key nor a PGP signature satisfies the frontend receipt.
The verifier accepts only its deterministic tag name and canonical payload.

Every commit after the signed backend subject is checked, including side
branches, merges and reverted edits. This bounded contract permits:

- regular TypeScript/TSX/CSS under `frontend/src`, plus `frontend/index.html`;
- frontend test fixtures and direct-child Markdown documentation;
- the exact frontend publication controller/test files named by the verifier;
- existing narrowly defined desk content, only with the existing desk identity
  and daily/weekly subject rules; repository content is excluded from this build;
- five exact monitoring paths listed in `EXCLUDED_MONITOR_PATHS`, reported as
  excluded and never used as renderer or runtime build input.

Backend runtime/package code, frontend dependencies and build configuration,
catalog metadata, public data, other workflows, trust files, symlinks,
executables, submodules and unrelated ancestry fail closed. Reverting an
intervening forbidden edit does not restore eligibility. Such changes require
the normal application release, not a larger frontend exception.

The original catalog verifier is byte-identical to the backend subject. Its
existing package, fault-free runtime/discovery and deep corpus checks still run.
The old signed corpus receipt must target that actual backend release; no source
SHA is substituted into it. The receipt asserts UI compatibility, not that the
backend has activated the UI merge SHA.

The same responses used for runtime health, discovery and corpus validation
must identify that exact backend SHA, production authority and one consistent
Railway deployment UUID. A same-version replacement, missing identity, candidate
or deployment change during verification stops publication.

## Review, receipt and publication

1. Review the change and run the contract tests plus frontend tests/build. Merge
   through normal checks. Independently confirm the exact source and release
   compatibility before invoking the production signer. Receipt preparation is
   separate from review and does not itself prove that tests passed.

   ```sh
   PYTHONPATH=backend python -m pytest -q \
     backend/tests/test_catalog_publication_gate.py backend/tests/test_publish_scripts.py
   npm --prefix frontend ci
   npm --prefix frontend test
   npm --prefix frontend run build
   ```

2. Use a clean checkout of exact current main, with the existing backend version,
   corpus version and corpus receipt tags fetched. Use the independently pinned
   fingerprint, not an unreviewed candidate-provided replacement. Generate new
   unsigned review inputs:

   ```sh
   publication_sha="$(git rev-parse HEAD)"
   test "$publication_sha" = "$(git ls-remote --exit-code origin refs/heads/main | awk 'NR == 1 {print $1}')"
   python -I -S ops/release/verify_frontend_publication.py \
     --root . --expected-sha "$publication_sha" \
     --signer-fingerprint "$RELEASE_SIGNING_KEY_FINGERPRINT" \
     --prepare --output "$NEW_RECEIPT_DIRECTORY"
   ```

   The output directory must be new. Inspect `receipt.json` and the full
   `compatibility.json`, including excluded monitor/content paths. Preparation
   refuses any existing local or remote tag and any unverified remote absence.
   It does not sign, deploy or claim fresh live validation.

3. After independent approval of the exact source and receipt inputs, use the
   existing approved SSH key to create one new immutable receipt. Preserve the
   canonical annotation exactly; do not add prose or reuse an old tag.

   ```sh
   frontend_tag="frontend-publication-$publication_sha"
   git -c gpg.format=ssh tag -s --cleanup=verbatim \
     -F "$NEW_RECEIPT_DIRECTORY/receipt.json" "$frontend_tag" "$publication_sha"
   python -I -S ops/release/verify_frontend_publication.py \
     --root . --expected-sha "$publication_sha" \
     --signer-fingerprint "$RELEASE_SIGNING_KEY_FINGERPRINT" \
     --receipt-tag "$frontend_tag"
   git push origin "refs/tags/$frontend_tag"
   gh workflow run publish-static.yml --repo beepboop2025/seiche --ref main \
     -f frontend_receipt_tag="$frontend_tag"
   ```

   The validation command checks the exact frontend receipt and all existing
   live package/runtime/corpus gates. Never use `--force`, move `r7`, issue a
   replacement backend receipt, or claim this is a backend activation. If main
   advances, this receipt cannot publish that other source: prepare/review a
   new exact-source receipt, retaining the old tag unchanged.

## Sealed data, recovery and completion

After tests, frontend mode builds a fresh Git archive containing only the exact
source's `frontend` subtree; excluded monitors and backend code are absent from
that build context. The frontend mode skips all repository `frontend/public`
passthrough. It reuses
the mirror's existing evidence and runs prerender/root-card generation from a
Git archive of the authenticated backend subject. The ignored monitoring source
and new controller checkout cannot become backend renderer imports.

Before the first public write, the publisher captures the previous mirror SHA,
a full SHA-256 file manifest and a Git archive. It reads that archive back and
matches every file to the mirror, then retains it as a required Actions artifact.
The candidate may replace only the root shell and add new assets or the bounded,
content-addressed root image. Existing data, catalog, dispatches, articles,
views, cards and assets must remain byte-identical. Missing assets, changed
evidence or unsafe paths stop publication before a push.

Current-main checks and the ordinary non-force mirror push provide the source
and mirror compare-and-swap checks; both are repeated before Cloudflare deploy.
The existing dataset/DCAT proof stays mandatory. Additional public probes compare
the exact root shell, referenced/new assets, catalog and sealed overview with
the staged hashes. A successful upload alone is not completion.

Keep the `frontend-recovery-<SHA>-<attempt>` artifact and workflow result. Recovery
must verify its archive digest and current mirror/source before a write. If no
newer publication exists, the reviewed prior site can be restored through the
normal mirror/Cloudflare route. If newer evidence has arrived, rebuild the prior
UI over that current sealed evidence instead of restoring an old data cut. This
contract neither rolls back backend state nor enables a retired writer.
