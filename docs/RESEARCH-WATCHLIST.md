# Money-market research watchlist

The native Money Markets tab lets a reader watch up to 12 market IDs, filter
the World map and Market lab selectors, and reopen a watched market's existing
analysis. The watch panel retains the current atlas's source-reported states
and benchmark clocks. Stale, derived-only, missing and unavailable evidence
remain distinct. A market absent from a live response stays listed without a
previous value; an unavailable atlas or USD fallback does not establish global
absence. No new score, alert delivery, request, polling or analytical model is added.

Choices begin in the current tab. The explicit remembering checkbox stores only
`{"schema":"seiche.money-market-watch.v1","ids":["US-USD"]}` in local storage.
It stores no rates, timestamps, source documents, account details or telemetry.
Unchecking forgets persisted choices while retaining the current tab's list.
Clear removes both. A storage deletion failure still clears the tab and turns
off saving, with an explanation that browser settings are needed to remove the
remaining stored choices. Other preferences are untouched.

Storage events refresh other tabs. Before a persisted add/remove action the
latest IDs are read again, preserving another tab's changes; revoked consent
is never automatically recreated. This is a local preference, not synchronized
account storage or a guarantee against simultaneous browser writes.

## Verification and release

From `frontend`, run `npm ci`, `npm test`, and `npm run build`. The focused tests
are `node --test tests/moneyMarketWatchlist.test.mjs`. Confirm the native tab at
desktop and mobile widths, including absent markets, storage denial, and two-tab
revocation. Fixtures establish UI behavior, not live data freshness or adoption.

The reproducible browser check mounts the real component in React Strict Mode
and intercepts the existing atlas route with synthetic responses. With Vite
running on port 8198, run `node tests/moneyMarketWatchlist.browser.mjs` from
`frontend`. Set `PLAYWRIGHT_MODULE` to an installed Playwright module if needed;
`PLAYWRIGHT_CHANNEL=chrome` uses a fresh headless Chrome context. The default
artifact directory is `artifacts/research-watchlist-browser` at repository root.

The frontend-only publication route is `.github/workflows/publish-static.yml`.
After review and exact-main integration, its ordinary trigger is
`gh workflow run publish-static.yml -R beepboop2025/seiche --ref main`. It tests
and builds the UI, checks signed catalog/runtime/PyPI receipts, overlays the
current sealed `seiche-site` mirror while preserving generated evidence, checks
source/mirror identity, deploys `/tmp/cloudflare-site` to Cloudflare Pages project
`seiche`, then verifies public dataset bytes. It needs no backend rollout.

The existing catalog gate currently blocks this path. A read-only check on
2026-09-08 reproduced `PublicationGateError: generated-content commit is not a
single-parent daily/weekly desk commit`. Under
`ops/release/verify_catalog_publication.py:verify_market_corpus_release`, a corpus
receipt that targets the signed Seiche release permits only the narrow generated
desk/controller descendant classes. Main already contains `31eaf81` (Railway
public monitoring) and `d5b17ba` (discovery probes) outside those classes, before
this feature; arbitrary frontend changes are outside them too.

Normal resolution requires the release owner to prepare fresh signed publication
evidence through the repository's reviewed release/receipt process and reconcile
the exact source/catalog identity before the static workflow can pass. Existing
tags, receipts and package versions must not be retargeted or relabeled, and the
gate must not be weakened or skipped by a direct Pages upload. A backend change,
if independently needed, follows `ops/deploy/RAILWAY-APPLICATION-UPDATES.md` and
its current-state recovery and writer-grant requirements. Local tests and a commit
are not deployment proof. This feature changes no release controller, collector,
backend, database or API contract.
