"""The OECD SDMX adapter: fixed origin, bounded bytes, pinned identity, OECD rights."""
import asyncio
import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from seiche.domain.observation import ConnectorClassification, RedistributionStatus
from seiche.markets.funding_reference import (
    COUNTRY_MAP, OECD_ADAPTER, OECD_DATA_URL, OECD_SERIES, OECD_TERMS_URL, RETIRED_SOURCES,
    SOURCE_REFERENCES,
)
from seiche.markets.registry import default_registry
from seiche.sources import oecd_sdmx
from seiche.sources.canonical import FetchedDocument, FunctionalCanonicalAdapter, PublicationTimePolicy
from seiche.sources.country_funding import build_country_adapters

FIXTURE = Path(__file__).parent / "fixtures/country_funding/oecd-finmark-kor-2025-10-to-2026-10.csv"
FIXTURE_SHA256 = "038f8b1102d333079ae6082f60cbe16856449d4ce7105f8c11da14f61dc53302"
FIRST_ROW = b"OECD.SDD.STES:DSD_STES@DF_FINMARK(4.0),KOR,M,IRLT,PA,_Z,_Z,_Z,_Z,N,2025-10,2.933,A,0,2,"
MEDIA = "application/vnd.sdmx.data+csv; charset=utf-8"
KOREA = tuple(item for item in OECD_SERIES if item.country == "KR")
TODAY = date(2026, 11, 9)
URL = oecd_sdmx.request_url(KOREA, start=date(2025, 10, 5))
# Retired FRED mirror id -> unchanged instrument id -> OECD series key.
FRED_MIRRORS_REPLACED = {
    "IRLTLT01AUM156N": ("AU.OECD.LONG_TERM_10Y_MONTHLY", "AUS.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IRLTLT01KRM156N": ("KR.OECD.LONG_TERM_10Y_MONTHLY", "KOR.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IRLTLT01CHM156N": ("CH.OECD.LONG_TERM_10Y_MONTHLY", "CHE.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IRLTLT01SEM156N": ("SE.OECD.LONG_TERM_10Y_MONTHLY", "SWE.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IRLTLT01NOM156N": ("NO.OECD.LONG_TERM_10Y_MONTHLY", "NOR.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IRLTLT01DKM156N": ("DK.OECD.LONG_TERM_10Y_MONTHLY", "DNK.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IRLTLT01PLM156N": ("PL.OECD.LONG_TERM_10Y_MONTHLY", "POL.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IRLTLT01CZM156N": ("CZ.OECD.LONG_TERM_10Y_MONTHLY", "CZE.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IRLTLT01HUM156N": ("HU.OECD.LONG_TERM_10Y_MONTHLY", "HUN.M.IRLT.PA._Z._Z._Z._Z.N"),
    "IR3TIB01CNM156N": ("CN.OECD.INTERBANK_3M_MONTHLY", "CHN.M.IR3TIB.PA._Z._Z._Z._Z.N"),
    "IR3TIB01KRM156N": ("KR.OECD.INTERBANK_3M_MONTHLY", "KOR.M.IR3TIB.PA._Z._Z._Z._Z.N"),
}


def document(payload=None, *, uri=URL, media_type=MEDIA):
    return FetchedDocument(uri, media_type, FIXTURE.read_bytes() if payload is None else payload, "oecd_sdmx:KR")


def parse(payload=None, **kwargs):
    return oecd_sdmx.parse_document(document(payload, **kwargs), series=KOREA, today=TODAY)


def with_first_row(row: bytes) -> bytes:
    payload = FIXTURE.read_bytes()
    assert payload.count(FIRST_ROW) == 1
    return payload.replace(FIRST_ROW, row)


def test_every_retired_fred_mirror_keeps_its_instrument_id_under_one_oecd_key():
    assert {item.instrument_id: item.key for item in OECD_SERIES} == dict(FRED_MIRRORS_REPLACED.values())
    assert all(item.url == OECD_DATA_URL + item.key for item in OECD_SERIES)


def test_real_oecd_response_parses_both_korean_series_at_month_end():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    points = parse()
    assert len(points) == 26
    rates = {(p.instrument_id, p.event_time): p for p in points}
    long_term = rates[("KR.OECD.LONG_TERM_10Y_MONTHLY", date(2026, 10, 31))]
    assert long_term.raw_value == Decimal("4.21")
    assert rates[("KR.OECD.INTERBANK_3M_MONTHLY", date(2026, 10, 31))].raw_value == Decimal("3.25")
    assert rates[("KR.OECD.LONG_TERM_10Y_MONTHLY", date(2026, 2, 28))].raw_value == Decimal("3.612")
    assert all(p.source_publication_time is None for p in points)
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)
    assert b'"series_key":"KOR.M.IRLT.PA._Z._Z._Z._Z.N"' in long_term.row_evidence
    assert b'"time_period":"2026-10"' in long_term.row_evidence and b'"obs_status":"A"' in long_term.row_evidence
    assert [(p.event_time, p.instrument_id) for p in points] == sorted((p.event_time, p.instrument_id) for p in points)


def test_window_start_keeps_only_month_end_labels_on_or_after_it():
    points = oecd_sdmx.parse_document(document(), series=KOREA, today=TODAY, start=date(2026, 9, 1))
    assert {p.event_time for p in points} == {date(2026, 9, 30), date(2026, 10, 31)} and len(points) == 4


def test_flagged_missing_month_is_a_gap_not_a_zero():
    points = parse(with_first_row(FIRST_ROW.replace(b",2.933,A,", b",,M,")))
    assert len(points) == 25
    assert ("KR.OECD.LONG_TERM_10Y_MONTHLY", date(2025, 10, 31)) not in {(p.instrument_id, p.event_time) for p in points}


@pytest.mark.parametrize("row", [
    FIRST_ROW.replace(b",KOR,", b",CHN,"),                       # another reference area
    FIRST_ROW.replace(b",IRLT,", b",IRSTCI,"),                    # another measure
    FIRST_ROW.replace(b",M,IRLT,", b",Q,IRLT,"),                  # another frequency
    FIRST_ROW.replace(b",PA,", b",PC,"),                          # another unit
    FIRST_ROW.replace(b",A,0,2,", b",A,2,2,"),                    # unit multiplier
    FIRST_ROW.replace(b",_Z,N,", b",_Z,H,"),                      # methodology
    FIRST_ROW.replace(b",PA,_Z,_Z,", b",PA,_Z,S,"),               # seasonal adjustment
    FIRST_ROW.replace(b"(4.0)", b"(4.1)"),                        # structure version
    FIRST_ROW.replace(b",2.933,", b",NaN,"),                      # non-finite, unflagged
    FIRST_ROW.replace(b",2.933,", b",Infinity,"),
    FIRST_ROW.replace(b",2.933,", b",2.933e0,"),
    FIRST_ROW.replace(b",2.933,", b",293.3,"),                    # basis points, not percent
    FIRST_ROW.replace(b",2.933,A,", b",2.933,F,"),                # forecast, not observed
    FIRST_ROW.replace(b",2.933,A,", b",2.933,M,"),                # missing flag with a value
    FIRST_ROW.replace(b",2025-10,", b",2026-11,"),                # month not ended by capture
    FIRST_ROW.replace(b",2025-10,", b",2027-01,"),                # future month
    FIRST_ROW.replace(b",2025-10,", b",2025-13,"),                # invalid period
    FIRST_ROW.removesuffix(b","),                                 # truncated row
    FIRST_ROW + b",extra",                                        # shifted row
])
def test_changed_identity_units_values_or_dates_fail_closed(row):
    with pytest.raises(ValueError):
        parse(with_first_row(row))


@pytest.mark.parametrize("payload", [
    FIXTURE.read_bytes() + FIRST_ROW + b"\r\n",                   # duplicate observation
    FIXTURE.read_bytes().replace(b"OBS_VALUE", b"VALUE", 1),      # changed column
    FIXTURE.read_bytes().replace(b",", b";"),                     # localised separator
    b"\r\n".join(line for line in FIXTURE.read_bytes().split(b"\r\n") if b",IR3TIB," not in line),
    FIXTURE.read_bytes() + b"x" * (oecd_sdmx.MAX_BODY_BYTES + 1),  # oversize
    b"",
    FIXTURE.read_bytes().replace(b"2.933", b"2.9\x0033"),
])
def test_malformed_partial_or_oversized_documents_fail_closed(payload):
    with pytest.raises(ValueError):
        parse(payload)


def test_row_limit_applies_before_any_row_is_read():
    with pytest.raises(ValueError, match="row limit"):
        parse(FIXTURE.read_bytes() + (FIRST_ROW + b"\r\n") * oecd_sdmx.MAX_ROWS)


def test_broken_csv_quoting_is_reported_as_malformed():
    with pytest.raises(ValueError, match="malformed"):
        parse(FIXTURE.read_bytes().replace(b",2.933,", b',"2.933,', 1))


@pytest.mark.parametrize("changes", [
    {"media_type": "text/html; charset=utf-8"},                   # an interstitial page
    {"uri": OECD_DATA_URL + "CHN.M.IR3TIB.PA._Z._Z._Z._Z.N?format=csvfile"},
    {"uri": "https://fred.stlouisfed.org/data/IRLTLT01KRM156N"},
])
def test_documents_from_another_request_or_media_type_fail(changes):
    with pytest.raises(ValueError):
        parse(**changes)


def test_canonical_capture_hashes_the_raw_oecd_bytes_and_never_backdates():
    now = datetime(2026, 11, 9, 4, tzinfo=UTC)

    async def fetch(_client):
        return (document(),)

    class Repository:
        def load_observations_as_of(self, *args, **kwargs):
            assert kwargs["sources"] == (OECD_ADAPTER,)
            assert kwargs["instrument_ids"] == ("KR.OECD.INTERBANK_3M_MONTHLY", "KR.OECD.LONG_TERM_10Y_MONTHLY")
            return []

    adapter = FunctionalCanonicalAdapter(
        pack=default_registry().get("KR-KRW"), adapter_id=OECD_ADAPTER, source=OECD_ADAPTER,
        fetcher=fetch, parser=lambda item: oecd_sdmx.parse_document(item, series=KOREA, today=now.date()),
        repository=Repository(), clock=lambda: now)
    batch = asyncio.run(adapter.collect())
    assert batch.raw_capture.payload == FIXTURE.read_bytes()
    assert batch.raw_capture.evidence_hash == FIXTURE_SHA256 and batch.raw_capture.source_uri == URL
    assert {r.knowledge_time for r in batch.observations} == {now}
    assert all(r.source_publication_time is None and r.source == OECD_ADAPTER for r in batch.observations)
    latest = {r.instrument_id: r.value for r in batch.observations if r.event_time == datetime(2026, 10, 31, tzinfo=UTC)}
    assert latest == {"KR.OECD.LONG_TERM_10Y_MONTHLY": Decimal("421"), "KR.OECD.INTERBANK_3M_MONTHLY": Decimal("325")}


def fetch_with(handler, url=URL):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            return await oecd_sdmx.fetch_documents(client, (("oecd_sdmx:KR", url),))
    return asyncio.run(run())


def test_fetch_returns_the_exact_bytes_of_the_fixed_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=FIXTURE.read_bytes(), headers={"Content-Type": MEDIA})

    item, = fetch_with(handler)
    assert item.payload == FIXTURE.read_bytes() and item.source_uri == URL and item.media_type == MEDIA
    assert str(seen[0].url) == URL and seen[0].headers["Accept-Language"] == "en"


async def _oversized_stream():
    for _ in range(oecd_sdmx.MAX_BODY_BYTES // 65536 + 2):
        yield b"x" * 65536


def test_fetch_does_not_follow_a_redirect_off_the_fixed_origin():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host != "sdmx.oecd.org":
            return httpx.Response(200, content=FIXTURE.read_bytes(), headers={"Content-Type": MEDIA})
        return httpx.Response(302, headers={"Location": "https://fred.stlouisfed.org/data/IRLTLT01KRM156N"})

    with pytest.raises(httpx.HTTPStatusError):
        fetch_with(handler)
    assert seen == [URL]


@pytest.mark.parametrize("respond", [
    lambda: httpx.Response(403, content=b"blocked", headers={"Content-Type": "text/html"}),
    lambda: httpx.Response(200, content=b"<html>challenge</html>", headers={"Content-Type": "text/html"}),
    lambda: httpx.Response(204, headers={"Content-Type": MEDIA}),
    lambda: httpx.Response(200, content=b"x", headers={"Content-Type": MEDIA,
                                                       "Content-Length": str(oecd_sdmx.MAX_BODY_BYTES + 1)}),
    lambda: httpx.Response(200, content=_oversized_stream(), headers={"Content-Type": MEDIA}),
])
def test_fetch_refuses_blocks_interstitials_and_oversized_bodies(respond):
    with pytest.raises((httpx.HTTPError, ValueError)):
        fetch_with(lambda request: respond())


def test_fetch_never_leaves_the_fixed_oecd_origin():
    def handler(request):
        pytest.fail("a request left the fixed OECD origin")
    with pytest.raises(ValueError, match="fixed data origin"):
        fetch_with(handler, url="https://fred.stlouisfed.org/data/IRLTLT01KRM156N")


def test_production_collectors_request_only_their_own_oecd_series():
    now = datetime(2026, 11, 9, tzinfo=UTC)
    adapters = [item for item in build_country_adapters(
        registry=default_registry(), repository=object(), clock=lambda: now) if item.adapter_id == OECD_ADAPTER]
    requested = {}

    def handler(request):
        requested.setdefault(market, []).append(str(request.url))
        return httpx.Response(200, content=FIXTURE.read_bytes(), headers={"Content-Type": MEDIA})

    async def run(adapter):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await adapter.fetcher(client)

    for adapter in adapters:
        market = adapter.market_id
        asyncio.run(run(adapter))
    expected = {}
    for item in OECD_SERIES:
        expected.setdefault(COUNTRY_MAP[item.country].market_id, []).append(item)
    assert set(requested) == set(expected) and len(adapters) == 10
    for market, urls in requested.items():
        assert urls == [oecd_sdmx.request_url(tuple(expected[market]), start=date(2025, 10, 5))]
        assert urls[0].startswith(OECD_DATA_URL) and "stlouisfed" not in urls[0]
    assert "KOR.M.IR3TIB+IRLT.PA._Z._Z._Z._Z.N?startPeriod=2025-10&format=csvfile" in requested["KR-KRW"][0]


def test_redistribution_rests_on_the_oecd_terms_not_fred():
    reference = SOURCE_REFERENCES[OECD_ADAPTER]
    assert reference["rights_url"] == OECD_TERMS_URL
    assert "including commercial use" in reference["terms"] and "credit to the OECD" in reference["terms"]
    assert "even for commercial use" in reference["terms_quote"]
    assert "You must give appropriate credit to the OECD" in reference["terms_quote"]
    assert not any("fred" in str(value).lower() for value in reference.values())
    registry = default_registry()
    for item in OECD_SERIES:
        pack = registry.get(COUNTRY_MAP[item.country].market_id)
        adapter = pack.adapter_map[pack.instrument_map[item.instrument_id].source_adapter_id]
        assert adapter.adapter_id == OECD_ADAPTER
        assert adapter.redistribution_status is RedistributionStatus.ALLOWED
        assert adapter.classification is ConnectorClassification.OFFICIAL_OPEN
    assert not any(adapter.adapter_id in RETIRED_SOURCES
                   for pack in registry.list() for adapter in pack.source_adapters)
