# Funding board availability

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
