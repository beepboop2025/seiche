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
