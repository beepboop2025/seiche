# Seiche static publication controller

Build this isolated controller from reviewed signed contents. It has no GitHub
autodeploy connection and runs as a finite Railway cron service with one replica
and restart NEVER. Configure `PUBLISH_APPLY=0` for full preparation proof before
providing publication credentials.

The controller pins the source workflow and four verifier blobs, verifies the
current main through its existing application or frontend signed
receipt, and runs npm/build/rendering as UID10001 without any credential. The
publisher receives only a checked public file tree after that process group is
terminated. Mirror writes compare the previous source revision; recovery must
be persisted on a mounted `/evidence` volume before a public write. GitHub host
keys are pinned; a dedicated write deploy key is limited to seiche-site.

Required service variables: RELEASE_SIGNING_KEY_FINGERPRINT; for publication
also SITE_DEPLOY_KEY, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID and
PUBLISH_APPLY=1. No shared/environment-wide credentials. The Cloudflare token
needs only Pages edit on the existing account. A changed workflow/verifier
requires a reviewed controller update.

This adapts publish-static only. It does not replace full evidence collection,
daily articles, Telegram announcements, or package/release attestations.

An application publisher without a frontend receipt may follow daily/weekly
desk descendants of its immutable controller source. Source selection uses the
existing linear desk author, subject, path and mode checks without the optional
controller-signature exception. It keeps current main as the publication source
and runs the complete signed application, live runtime, package and corpus gates
before building or publishing. It never substitutes another tag or approves a
controller, UI or runtime change through the desk lane. Its retained source
admission names both the controller revision and the current desk revision.

After a lawful daily/weekly desk commit, the controller may retain its signed
frontend source while admitting current main separately. Its only automatic
ancestor candidate is the immutable controller-source SHA, with an exact signed
`frontend-publication-SHA` tag. Every intervening commit must pass the existing
linear desk author/subject/path/mode gate; merges, controller/UI/runtime changes
and reverted forbidden changes fail. The unchanged exact receipt and live gates
then run at that ancestor. No new desk bytes are published through this path.
Logs and retained evidence separate `source` (signed frontend subject) from
`current_main`; unchanged runs repeat all live gates. An explicit
FRONTEND_RECEIPT_TAG must still name current main exactly. A changed controller
or frontend requires a fresh reviewed controller and exact signed receipt.

## Signed publication-source equivalence

An optional `PUBLICATION_EQUIVALENCE_TAG=publication-source-equivalence-D`
admits current main H through a separate signed source-equivalence receipt D.
The bundle must independently pin controller C and signed engine release R.
The controller authenticates H/D using its exact C verifier, checks C's original
frontend receipt using the verifier from pristine R, and pins all verifier bytes
before importing either checkout. A missing, invalid or stale receipt fails;
this option never falls back to the ordinary source-selection path.

Static publication still builds exact frontend C and re-renders the mirror's
sealed evidence with R. No H desk data is overlaid or generated here. An explicit
`FRONTEND_RECEIPT_TAG`, when supplied, must equal `frontend-publication-C`.
Recovery and state record H as `publicationSourceSha`, C as `controllerSourceSha`
and `buildSourceSha`, R as `engineSourceSha` and `rendererSourceSha`, D, the
admitted data/input digests, and the independently checked live runtime subjects.
Main checks always compare H; mirror compare-and-swap checks run immediately
before each public write and again after public verification.

Assemble static and full contexts independently from exact committed Git blobs:

```sh
python ops/railway-automation/publisher/assemble.py /ssd/static-controller \
  --kind static --source "$C" --engine-source "$R"
python ops/railway-automation/publisher/assemble.py /ssd/full-controller \
  --kind full --source "$C" --engine-source "$R"
```

The default command without these options still assembles the static controller
at HEAD. Dirty working files never enter either context. `--engine-source`
adds the separate engine SHA and verifier hashes to `controller-source.json`;
it does not create a receipt or authorize deployment. First validate C through
the original R frontend contract, obtain the exact reviewed frontend-C receipt
and separate D receipt, and prepare with `PUBLISH_APPLY=0`. Activation follows
operator acceptance of the complete proof; no signer key enters either image.

Allow only one active publication writer across native static, native full and
GitHub publishers. Main/mirror compare-and-swap checks are not a Cloudflare
mutex. Prepare both controllers without writes, then apply serially while all
counterparts are held. After full acceptance, enable only the full publisher's
cron and retire the old GitHub/static schedules with their prior states saved.
