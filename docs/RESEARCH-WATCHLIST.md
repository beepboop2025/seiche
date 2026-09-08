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

Publish through the separately signed [frontend publication process](FRONTEND-PUBLICATION.md).
After review and exact-main integration, prepare and approve a new immutable
`frontend-publication-<full source SHA>` receipt. Its explicit workflow trigger is
`gh workflow run publish-static.yml -R beepboop2025/seiche --ref main -f frontend_receipt_tag="$frontend_tag"`.
The frontend builds in an exact-source archive while the original backend and
corpus receipts retain their actual subjects and live semantic checks. The
publisher preserves the sealed site evidence, retains verified recovery before
writes, checks source/mirror identity, deploys to Cloudflare Pages project
`seiche`, and verifies the exact public shell, assets, catalog and dataset bytes.
This compatible frontend route requires no backend rollout.

Historically, the initial 2026-09-08 publication attempt was blocked because
frontend and monitoring commits do not qualify for the legacy generated-desk
receipt fallback. The separate frontend contract addresses that source boundary;
the old application/corpus tags and fallback remain unchanged.

Follow the linked runbook rather than retargeting receipts or uploading directly
to Pages. Backend changes, if independently needed, follow
`ops/deploy/RAILWAY-APPLICATION-UPDATES.md` and its recovery and writer-grant
requirements. Local tests and a commit are not deployment proof. The watchlist
itself adds no collector, backend, database or API contract.
