"""Original Federal Reserve Board IORB and IOER observations, distinct from FRED copies.

This module keeps the ``fed_prates`` names that callers and tests use; the
package itself is declared in ``seiche.sources.frb_release_package``
(``PRATES``). Series ``RESBM_N.D`` is the interest rate on reserve balances
(IORB) from 2021-07-29. Series ``RESBME_N.D`` is the interest rate on excess
reserves (IOER), 2008-10-09 to 2021-07-28, the splice leg for pre-2021 history,
served beside IORB and never as its substitute.

The current PRATES file has effective dates, not per-observation publication
receipts. Acquisition time is the knowledge boundary; no historical PIT claim
is inferred from these dates.
"""
from __future__ import annotations

from seiche.sources import frb_release_package as _frb
from seiche.sources.canonical import FetchedDocument, ParsedPoint

PACKAGE = _frb.PRATES
SOURCE_URL = PACKAGE.source_url
TERMS_URL = PACKAGE.terms_url
ATTRIBUTION = PACKAGE.attribution
ADAPTER_ID = PACKAGE.adapter_id
INSTRUMENT_ID = "US.FED.IORB_DIRECT"
SERIES_NAME = "RESBM_N.D"
IOER_INSTRUMENT_ID = "US.FED.IOER_DIRECT"
IOER_SERIES_NAME = "RESBME_N.D"
MAX_ARCHIVE_BYTES = PACKAGE.max_archive_bytes
MAX_XML_BYTES = PACKAGE.max_xml_bytes


async def fetch_document(client) -> tuple[FetchedDocument, ...]:
    """Download only the fixed publisher endpoint with byte/time bounds."""

    return await _frb.fetch_package(client, PACKAGE)


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    return _frb.parse_package(document, PACKAGE)
