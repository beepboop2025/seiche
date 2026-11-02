"""Board commercial paper rates: implemented, but off until a rights record allows them."""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from seiche.domain.observation import (
    CanonicalUnit,
    ConnectorClassification,
    DayCountConvention,
    RateCompounding,
    RedistributionStatus,
    SemanticRole,
)
from seiche.markets.base import InstrumentSpec, PublicationClock, PublicationClockPrecision, SourceAdapterSpec
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.markets.us_usd.pack import PACK
from seiche.repository import SQLiteMarketRepository
from seiche.sources import fed_cp
from seiche.sources.canonical import FetchedDocument
from seiche.sources.official import PRODUCTION_ADAPTER_KEYS, build_official_adapters

HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
    '<message:MessageGroup xmlns:message="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/message" '
    'xmlns:common="http://www.SDMX.org/resources/SDMXML/schemas/v1_0/common" '
    'xmlns:frb="http://www.federalreserve.gov/structure/compact/common">'
    '<frb:DataSet id="RATES" xmlns:kf="http://www.federalreserve.gov/structure/compact/CP_RATES">'
)
FOOTER = "</frb:DataSet></message:MessageGroup>"
# Published 90-day AA rates for 2026-10-23 to 2026-10-29 (release of 2026-10-30).
FINANCIAL = (
    '<kf:Series CP_TERM="90" CP_TYPE="FAA" CURRENCY="NA" FREQ="9" SERIES_NAME="RIFSPPFAAD90_N.B" '
    'UNIT="Percent" UNIT_MULT="1">'
    '<frb:Obs OBS_STATUS="ND" OBS_VALUE="-9999" TIME_PERIOD="2026-10-23" />'
    '<frb:Obs OBS_STATUS="A" OBS_VALUE="4.12" TIME_PERIOD="2026-10-26" />'
    '<frb:Obs OBS_STATUS="ND" OBS_VALUE="-9999" TIME_PERIOD="2026-10-27" />'
    '<frb:Obs OBS_STATUS="A" OBS_VALUE="4.08" TIME_PERIOD="2026-10-28" />'
    '<frb:Obs OBS_STATUS="A" OBS_VALUE="4.05" TIME_PERIOD="2026-10-29" />'
    "</kf:Series>"
)
NONFINANCIAL = (
    '<kf:Series CP_TERM="90" CP_TYPE="NAA" CURRENCY="NA" FREQ="9" SERIES_NAME="RIFSPPNAAD90_N.B" '
    'UNIT="Percent" UNIT_MULT="1">'
    '<frb:Obs OBS_STATUS="ND" OBS_VALUE="-9999" TIME_PERIOD="2026-10-28" />'
    '<frb:Obs OBS_STATUS="A" OBS_VALUE="4.11" TIME_PERIOD="2026-10-29" />'
    "</kf:Series>"
)
# The 60-day series must never be read as the 90-day rate.
DECOY = (
    '<kf:Series CP_TERM="60" CP_TYPE="FAA" CURRENCY="NA" FREQ="9" SERIES_NAME="RIFSPPFAAD60_N.B" '
    'UNIT="Percent" UNIT_MULT="1"><frb:Obs OBS_STATUS="A" OBS_VALUE="3.98" TIME_PERIOD="2026-10-29" /></kf:Series>'
)
FULL = HEADER + DECOY + FINANCIAL + NONFINANCIAL + FOOTER


def document(xml: str = FULL) -> FetchedDocument:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(fed_cp.MEMBER, xml)
    return FetchedDocument(fed_cp.SOURCE_URL, "application/zip", raw.getvalue(), fed_cp.ADAPTER_ID)


def declared(status: RedistributionStatus):
    """A hypothetical pack that declares the CP record, as an owner decision would."""
    clock = PublicationClock("America/New_York", time(13, 0), 1, PublicationClockPrecision.SCHEDULED,
                             PACK.settlement_calendar.calendar_id)
    adapter = SourceAdapterSpec(fed_cp.ADAPTER_ID, ConnectorClassification.OFFICIAL_OPEN, "P1D", clock, status)
    instruments = tuple(
        InstrumentSpec(instrument_id, instrument_id.rsplit(".", 1)[1], SemanticRole.CP_3M, fed_cp.ADAPTER_ID,
                       "percent", CanonicalUnit.BASIS_POINTS, 100, RateCompounding.SIMPLE, DayCountConvention.ACT_360)
        for instrument_id in fed_cp.SERIES.values())
    return replace(PACK, source_adapters=(*PACK.source_adapters, adapter),
                   instruments=(*PACK.instruments, *instruments))


def registry_with(pack) -> MarketRegistry:
    return MarketRegistry(tuple(pack if item.market_id == "US-USD" else item
                                for item in default_registry().list()))


def test_the_registered_pack_declares_no_cp_record_so_nothing_runs():
    pack = default_registry().get("US-USD")
    assert fed_cp.ADAPTER_ID not in pack.adapter_map
    assert fed_cp.declared_instruments(pack) == frozenset()
    assert fed_cp.rights_gate_open(pack) is False
    adapters = build_official_adapters(repository=SQLiteMarketRepository())
    assert fed_cp.ADAPTER_ID not in {item.adapter_id for item in adapters}
    assert ("US-USD", fed_cp.ADAPTER_ID) not in PRODUCTION_ADAPTER_KEYS
    # The FRED copy of the nonfinancial rate keeps its own restriction.
    copy = pack.instrument_map["US.CP.NONFINANCIAL_3M"]
    assert copy.source_adapter_id == "fred_daily"
    assert pack.adapter_map["fred_daily"].redistribution_status is RedistributionStatus.METADATA_ONLY
    assert [item.instrument_id for item in pack.instruments if item.semantic_role is SemanticRole.CP_3M] == [
        "US.CP.NONFINANCIAL_3M"]


@pytest.mark.parametrize("status", [RedistributionStatus.METADATA_ONLY, RedistributionStatus.DERIVED_ONLY,
                                    RedistributionStatus.PROHIBITED])
def test_only_an_allowed_declaration_would_open_the_gate(status):
    closed = declared(status)
    assert fed_cp.rights_gate_open(closed) is False
    adapters = build_official_adapters(registry=registry_with(closed), repository=SQLiteMarketRepository())
    assert fed_cp.ADAPTER_ID not in {item.adapter_id for item in adapters}
    allowed = declared(RedistributionStatus.ALLOWED)
    assert fed_cp.rights_gate_open(allowed) is True
    adapters = build_official_adapters(registry=registry_with(allowed), repository=SQLiteMarketRepository())
    assert fed_cp.ADAPTER_ID in {item.adapter_id for item in adapters}


def test_the_registry_records_the_dtcc_provenance_and_the_disabled_state():
    ledger = json.loads((Path(__file__).resolve().parents[2] / "docs" / "data-source-expansion.json").read_text())
    record = next(item for item in ledger["candidates"] if item["id"] == "frb_commercial_paper")
    assert record["access_url"] == fed_cp.SOURCE_URL
    assert record["rights_evidence_url"] == fed_cp.ABOUT_URL
    assert record["rights_status"] == "review_required"
    assert record["implementation_status"] == "policy_blocked"
    assert record["semantic_roles"] == ["CP_3M"]
    assert "Derived from data supplied by The Depository Trust & Clearing Corporation" in record["blocker"]
    assert ("is derived from data supplied by The Depository Trust & Clearing Corporation (DTCC)"
            in record["blocker"])
    assert "Unless otherwise indicated" in record["blocker"]


def test_parser_reads_only_the_two_90_day_aa_series_and_skips_nd_rows():
    points = fed_cp.parse_document(document())
    assert [(p.instrument_id, p.event_time, p.raw_value) for p in points] == [
        (fed_cp.FINANCIAL_INSTRUMENT_ID, date(2026, 10, 26), Decimal("4.12")),
        (fed_cp.FINANCIAL_INSTRUMENT_ID, date(2026, 10, 28), Decimal("4.08")),
        (fed_cp.FINANCIAL_INSTRUMENT_ID, date(2026, 10, 29), Decimal("4.05")),
        (fed_cp.NONFINANCIAL_INSTRUMENT_ID, date(2026, 10, 29), Decimal("4.11")),
    ]
    evidence = json.loads(points[0].row_evidence)
    assert evidence["series"] == "RIFSPPFAAD90_N.B" and evidence["declared"]["CP_TYPE"] == "FAA"
    assert points[0].revision_id.startswith("fed-cp:RIFSPPFAAD90_N.B:2026-10-26:")


@pytest.mark.parametrize("mutation", [
    lambda s: s.replace(NONFINANCIAL, ""),
    lambda s: s.replace(FINANCIAL, FINANCIAL + FINANCIAL),
    lambda s: s.replace('CP_TERM="90" CP_TYPE="FAA"', 'CP_TERM="60" CP_TYPE="FAA"'),
    lambda s: s.replace('CP_TYPE="NAA"', 'CP_TYPE="NA2"'),
    lambda s: s.replace('SERIES_NAME="RIFSPPNAAD90_N.B" UNIT="Percent" UNIT_MULT="1"',
                        'SERIES_NAME="RIFSPPNAAD90_N.B" UNIT="Percent" UNIT_MULT="100"'),
    lambda s: s.replace('FREQ="9" SERIES_NAME="RIFSPPFAAD90_N.B"', 'FREQ="19" SERIES_NAME="RIFSPPFAAD90_N.B"'),
    lambda s: s.replace('OBS_VALUE="4.08" TIME_PERIOD="2026-10-28"', 'OBS_VALUE="4.08" TIME_PERIOD="2026-10-25"'),
    lambda s: s.replace('OBS_VALUE="4.08" TIME_PERIOD="2026-10-28"', 'OBS_VALUE="4.08" TIME_PERIOD="2026-10-26"'),
    lambda s: s.replace('OBS_VALUE="4.08"', 'OBS_VALUE="n.a."'),
    lambda s: s.replace('OBS_VALUE="4.08"', 'OBS_VALUE="408"'),
    lambda s: s.replace(HEADER, HEADER.replace("?>\n", '?>\n<!DOCTYPE m [<!ENTITY x "y">]>')),
    lambda s: s[:-40],
])
def test_changed_identity_units_dates_or_values_fail_closed(mutation):
    with pytest.raises(ValueError):
        fed_cp.parse_document(document(mutation(FULL)))


def test_package_identity_fails_closed():
    good = document()
    with pytest.raises(ValueError):
        fed_cp.parse_document(FetchedDocument("https://example.invalid/FRB_CP_xml.zip",
                                              "application/zip", good.payload, fed_cp.ADAPTER_ID))
