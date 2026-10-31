# Funding board availability

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
