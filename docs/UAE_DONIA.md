# UAE overnight funding reference

The GIFT City AED card reads DONIA from the Central Bank of the UAE's
[official interest-rate chart](https://centralbank.ae/en/our-operations/monetary-policy-and-domestic-markets/).
The hourly collector captures the publisher's JSON chart without executing page
scripts and retains the raw response, content hash, source link and collection
time. The card and `gift_city_context` read this completed cache; user requests
do not fetch the central bank.

The source uses Python's standard HTTP transport in a dedicated worker, with a
transparent Seiche user agent and an English language preference. It does not
use authentication, browser impersonation, challenge solving or a proxy fallback.
Redirects, partial responses, access denials, oversized bodies and expired
download deadlines fail without publishing a new observation.

DONIA combines secured and unsecured overnight funding transactions. It is not
SOFR, an unsecured-only fixing, EIBOR, or the UAE policy rate. The
[CBUAE term sheet](https://centralbank.ae/media/kuqd0q5o/attachment-7_donia-term-sheet.pdf)
specifies ACT/360 and describes publication on UAE business days by 09:30 local
time. The source may revise historical values, and its contingency methodology
can apply when no eligible transactions occur. A published observation does not
prove which underlying calculation route was used on that date.

Dates in the chart remain the observation-date basis. The response does not
establish each observation's actual publication timestamp or historical
availability; those timestamps remain unknown. A same-Dubai-day chart date is
fresh, one day old is aging and older is stale. This conservative calendar-age
policy does not infer holidays or count missed publications. Re-fetching an old
observation cannot make its date current.

The four bounded chart ranges (month, six months, year to date and one year)
must agree. Only the one-year history is admitted: the publisher's separate
all-history chart has unequal date and DONIA-value counts and is excluded.
Duplicate, unordered, future,
malformed or nonfinite observations fail validation. Missing historical points
are omitted; a missing newest value is never forward-filled. The series and its
capture metadata commit atomically. Refresh failures retain the last validated
generation and its original clocks, while reporting a source fault.

Reuse was reviewed on 6 October 2026 against the
[CBUAE Open Data Policy](https://centralbank.ae/en/open-data-landing/open-data-policy/).
Public evidence and CSV downloads attribute the Central Bank of the UAE and link
to this dataset. This dataset-specific admission grants no rights to unrelated
CBUAE or third-party material. The reference does not enter a funding stress
score or establish an executable borrowing quote.
