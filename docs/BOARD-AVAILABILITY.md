# Funding board availability

## Research visibility restored (3 November 2026)

On 3 November 2026 the owner decided to show the legacy funding board and the
legacy tools again for free research use while source permissions are sought.
Seiche is free, research grade and not commercial. Source owners can request
removal at any time.

`seiche.public_rights.ACTIVE_POLICY` selects the release policy. It is now
`seiche.public-source-rights.2026-11-03`, with this scope:

- `/api/overview`, `/api/public` and the other legacy routes and prefixes in
  `LEGACY_PATHS` and `LEGACY_PREFIXES` run their existing handlers again,
  behind their existing authentication settings. The publisher writes the board
  it computed to `/data/overview.json` and `/data/public.json` unchanged.
- Thirteen legacy MCP tools return to discovery. Ten are anonymous, including
  `funding_stress_forecast`, which the owner opened on 3 November because the
  board already shows the forecast probabilities
  (`public_rights.RESEARCH_PUBLIC_TOOLS`); its research labels and honesty
  fields are unchanged, and its REST twin `/api/deep` is gated like the board
  with the Book's fields still withheld. `replay_asof`, `desk_brief` and
  `ask_desk` keep the bearer identity requirement they had before 28 October,
  and so do their REST routes (`/api/asof/{date}`, `/api/brief`, `/api/ask`,
  and `/api/book` with `positioning_book` once the Book reopens), which answer
  an anonymous caller with 401 whatever `SEICHE_BOARD_AUTH` says
  (`public_rights.ACCOUNT_ROUTES`, `api.account_twin`). `/api/engines/{name}`
  serves only snapshot engines and none of the forecast tool's deep blocks.
  `positioning_book` is withheld with the Book. No legacy tool is priced: x402
  never charges for one, and a payment header offered for one is refused like
  any other unpriced call.
- `source_rights_status` reports `status: research_release`, the new policy,
  the anonymous `available_tools`, `account_tools`, `account_routes`,
  `legacy_tools`, `unavailable_tools` (`positioning_book`), the
  `withheld_surfaces` and the `withheld_fields`.

What does not change:

- The board's honesty features. Source clocks, missing values, faults and the
  rule that a value is never reconstructed stay as they are. Nothing is
  recomputed, backfilled or relabelled; each route serves what the existing
  pipelines compute.
- The Book stays withheld. `/api/book` and `/data/book_history.json` keep the
  28 October restriction payload byte for byte. That static file is the
  hash-chained ledger, and `append_book_record.py` extends the chain it
  downloads from the live site. The live file is now the restriction payload,
  so an append would stop with "restore the published chain before appending".
  Reopening the Book needs the last published chain restored from the site
  mirror's history and verified first. That is an owner step, not a code
  change, and no chain record is forged or rewritten here. While the Book is
  withheld its content is withheld wherever it appears, as a field-level
  projection (`public_rights.project_book`) carrying the same restriction
  payload as `/api/book`: `deep.book` in `/api/overview`, `/api/deep`, the
  desk assistant's context and the static `/data/overview.json`; the Book
  positions in `/api/pit` records; Book alerts in `/api/alerts` and the board's
  alert log; and `positioning_book`. No stale or partial Book value is served,
  and no new Book alert is raised or emailed. The stored board, the
  as-published record and the private build snapshot keep the Book, so the
  chain and its inputs stay intact.
- Field-level restrictions outside the legacy board. The money desk projection
  in `money_market_context` and `world_markets_context`, H.10 values in
  `market_workbench`, and FRED series JSON and CSV downloads keep their
  28 October rules.
- The restriction machinery. Setting `ACTIVE_POLICY` back to
  `seiche.public-source-rights.2026-10-28` withholds every legacy route, tool
  and static file again with the 0.16.11 payloads, byte for byte. The website
  still renders the withheld state whenever a published file carries the
  restriction.

The release identity files and the signed AI catalog still describe the
published 0.16.11 package and its eight anonymous tools. They change with the
next release candidate. This is a runtime, static publication and discovery
change, so it follows the application release procedure in
[PUBLISHING.md](PUBLISHING.md#release-prerequisites); a frontend-only
publication is not enough.

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
