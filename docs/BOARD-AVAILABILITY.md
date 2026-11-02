# Funding board availability

## The v2 US-USD reading follows the same policy (0.16.12)

Until 0.16.12, `/api/v2/markets/US-USD/gauge`, `/overview` and
`/asof/{timestamp}` served the withheld v1 board through the US legacy parity
bridge: the composite index and regime, Tell, the five-day event probability
and its members, and the ICE BofA high-yield OAS in the overview headline. The
bridge stamped its snapshots `PUBLIC_REDISTRIBUTION_FILTERED` without filtering
anything, and the routes checked only that stamp.

From 0.16.12 three things hold:

- Each of the three routes first asks whether the pack's reading depends on an
  input outside `ALLOWED` or `DERIVED_ONLY`
  (`seiche.markets.reading_rights`). The legacy parity bridge
  (`us-usd-legacy-parity-v1`) copies the v1 board, so it depends on the FRED
  copies the pack declares `metadata_only` and on v1 inputs the pack does not
  declare at all. The routes return the `seiche.source-rights-restriction.v1`
  body that `/api/gauge` returns, with `surface` `/api/v2/markets/US-USD`,
  status 403 and `Cache-Control: no-store`, before reading any snapshot, so a
  snapshot sealed earlier under the wrong stamp cannot reopen the reading.
- The materializers compute the stamp from the actual inputs: the kernel and
  the global tide from the rights of the rows they read, the US bridge from
  the board's provenance (`history_export_allowed`) and the pack's adapter
  policy for each mapped input. The bridge now seals
  `SOURCE_RIGHTS_RESTRICTED`, kept for forward evidence and catalog metadata.
- `/api/v2/markets`, `/api/v2/coverage` and `/api/v2/markets/{id}/series` read
  only catalog metadata (coverage, eligibility, cutoffs, faults and stale
  inputs) from a public or restricted snapshot, so they behave as in 0.16.11.

`tests/test_public_rights_sweep.py` seeds restricted sentinel values, calls
every GET route in `app.routes` and every registered MCP tool with the network
closed, and finds none of them; it fails on the 0.16.11 code at exactly these
three routes.

Operations: on 2 November the fsn1 edge started refusing
`/api/v2/markets/US-USD/*` except `/series` with
`/etc/caddy/seiche-withheld-us-board.caddy` (imported before `@public`; header
`X-Seiche-Withheld-By: edge-2026-11-02`). Leave it in place until these paths
return the application's own 403 from behind it. Do not install
`ops/Caddyfile` from this repository for that change: it is stale against the
box.

## Static files follow the API source-rights policy (0.16.11)

From release 0.16.11 each static board file carries exactly what its API route
may serve. While `/api/overview`, `/api/public` and `/api/book` are withheld
under `seiche.public-source-rights.2026-10-28`, the published
`/data/overview.json`, `/data/public.json` and `/data/book_history.json` carry
that same source-rights restriction payload instead of board values. The
publisher keeps the full board only at an explicit private path outside the
public web roots.

The website reads that restriction and shows the funding board as withheld
pending source-rights qualification. It does not keep earlier values on screen
and does not present a withheld reading as calm, zero or last known. The
prerendered home page, the board, series and tab link cards and the Book state
the same withheld condition. Money Markets, Market Workbench, Research, Market
Atlas, Dispatches and Account load their own evidence and remain available. The
Book's earlier hash-chained records remain in the site mirror's history; it is
not extended while withheld, and reopening it requires restoring that chain.

This supersedes the 30 October restoration below for every legacy board value.
The rest of this document describes how the website behaves when a route's
policy is open.

## 30 October restoration

The owner requested restoration of the interactive legacy board and visibility
of all available Seiche data on 30 October 2026.

The website reads the existing published `/data/overview.json` first. This is the
same dated evidence cut used by the public rendered site and publisher. Every
engine in that payload remains available to its existing board view; the UI
does not regenerate, relabel or fabricate observations.

A live API refresh may upgrade that view if its generation is at least as new.
A denied, failed, malformed or slower response cannot erase a valid published
board. After a source-rights denial, periodic checks read the published snapshot;
an explicit refresh can check the API again. Snapshot dates and individual source
faults remain visible. A successful HTTP response alone does not certify the
observations or their source rights.

Market Atlas, Dispatches and Account obtain their own data and therefore do not
depend on overview availability. Historical replay still requires its separate
API; published diagnostics are not replayed publication vintages.

This frontend change does not change source collection, legacy API or MCP
availability, series redistribution policy, source contracts or model eligibility.
It must use the existing frontend publication receipt and preserve the current
published data and backend release identities.


## Core USD funding visibility

The opening page shows SOFR, IORB, the exact-date SOFR-IORB spread, reserve
balances, EFFR, Treasury opening cash, ON RRP and SRF. Every card retains its
observation date, native cadence and acquisition source. The opening page
checks the public global atlas every minute while visible, retaining dated
observations when a request fails. The Money Markets page reuses its existing
atlas request and opens a dedicated USD funding view when no watchlist exists.

Qualified public atlas values replace only matching cards with an equal or
newer observation date and compatible units. The published USD board remains
a separately labelled evidence source. Null, restricted, malformed or older
atlas rows do not erase its observations. The client never derives a spread
from differently dated rate legs or turns a missing value into zero. Published
board values continue to advance through the existing FULL publication lane.

The opening page's Funding Observatory also receives the completed board
snapshot. Named history columns populate rates, spreads, weekly reserves,
cash, money-fund repo, commercial-paper premiums and the Fed balance sheet
before network requests complete. Qualified direct-publisher atlas histories
can advance the rate charts. Refresh failures retain dated history. Unpublished
Treasury curve tenors remain explicit gaps; they are never reconstructed.
