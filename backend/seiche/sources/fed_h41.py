"""Federal Reserve Board H.4.1 balance-sheet series, distinct from FRED.

This module keeps the ``fed_h41`` names that callers and tests use; the
package itself is declared in ``seiche.sources.frb_release_package`` (``H41``),
which streams, pins and parses every Board release package the same way.

Rights: the Board's notice (https://www.federalreserve.gov/disclaimer.htm,
"Copyright/trademark") states: "Unless otherwise indicated, information on
Board's website is in the public domain and may be copied and distributed
without permission. Please cite to the Board as the source of the
information." Every served value therefore carries ``ATTRIBUTION``. Board seals
and logos and any identified non-Board material are outside that notice.

Gate: ``build_official_adapters`` registers this adapter only while the US pack
itself declares the official-open ``fed_h41`` adapter with redistribution
allowed and at least one direct instrument on it (``rights_gate_open``), and it
keeps only rows for the instruments the pack declares there. Any other
declaration makes no request and stores or serves nothing. The FRED copies
(``US.FED.RESERVE_BALANCES``/WRESBAL and the legacy WALCL, WTREGEN, WCURCIR,
WLCFLPCL, SWPT, WLRRAFOIAL, WMTSECL1 and H41RESPPALGTRFNWW series) keep their
own metadata-only policy.

Source: the Board's own release package, not FRED and not the Data Download
Program "Build Your Package" tool that the Board is retiring. All nine series
are in millions of US dollars (UNIT=Currency, UNIT_MULT=1000000), dated by the
Wednesday that ends each week. Series ``RESH4R_XAW_N.WW`` is "Reserve balances
with Federal Reserve Banks: week average" (FRED WRESBAL) and ``RESPPMA_N.WW``
is "Total assets (Less eliminations from consolidation): Wednesday level"
(FRED WALCL); ``SERIESTYPE`` A marks a week average and L a Wednesday level,
and both are pinned with the rest of each declaration. The Board releases
H.4.1 on Thursdays at 16:30 New York time, delayed by federal holidays; the
package carries no per-row publication receipt, so the pack's declared clock,
not this parser, supplies the inferred publication time.
"""
from __future__ import annotations

from seiche.sources import frb_release_package as _frb
from seiche.sources.canonical import FetchedDocument, ParsedPoint

PACKAGE = _frb.H41
SOURCE_URL = PACKAGE.source_url
RELEASE_URL = PACKAGE.release_url
TERMS_URL = PACKAGE.terms_url
ATTRIBUTION = PACKAGE.attribution
ADAPTER_ID = PACKAGE.adapter_id
MEMBER = PACKAGE.member
INSTRUMENT_ID = "US.FED.RESERVE_BALANCES_DIRECT"
SERIES_NAME = "RESH4R_XAW_N.WW"
TOTAL_ASSETS_INSTRUMENT_ID = "US.FED.TOTAL_ASSETS_DIRECT"
TOTAL_ASSETS_SERIES_NAME = "RESPPMA_N.WW"
# Publisher series to direct instrument. Every one must be present in every package.
SERIES = {item.series_name: item.instrument_id for item in PACKAGE.series}
# The declarations stored in each row's evidence since 0.16.12. Every series
# pins these, and SERIESTYPE as well; any change fails closed rather than rescaling.
SERIES_ATTRIBUTES = {"FREQ": "19", "UNIT": "Currency", "UNIT_MULT": "1000000", "CURRENCY": "USD"}
MAX_ARCHIVE_BYTES = PACKAGE.max_archive_bytes
MAX_XML_BYTES = PACKAGE.max_xml_bytes
MAX_MEMBERS = PACKAGE.max_members


def declared_instruments(pack) -> frozenset[str]:
    """The direct H.4.1 instruments the pack itself places on this adapter."""

    return _frb.declared_instruments(pack, PACKAGE)


def rights_gate_open(pack) -> bool:
    """True only when the pack itself declares the direct H.4.1 rights record."""

    return _frb.rights_gate_open(pack, PACKAGE)


async def fetch_document(client) -> tuple[FetchedDocument, ...]:
    """Download only the fixed publisher package with byte/time bounds."""

    return await _frb.fetch_package(client, PACKAGE)


def parse_document(document: FetchedDocument) -> tuple[ParsedPoint, ...]:
    return _frb.parse_package(document, PACKAGE)
