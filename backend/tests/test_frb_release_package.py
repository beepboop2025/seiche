"""The Board's own release packages (PRATES, H.4.1, H.15, H.10, G.17): pinned, bounded, cited.

Fixtures in ``fixtures/frb`` are trimmed copies of the packages published on
2026-11-02 (see its README); the H.15 commercial paper values are synthetic.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import Request, Response

from seiche import api, store
from seiche.config import ALL_SERIES
from seiche.domain.observation import (
    RATE_ROLES,
    CanonicalUnit,
    ConnectorClassification,
    QualityState,
    RedistributionStatus,
    SemanticRole,
)
from seiche.markets.atlas import build_global_money_market_atlas, market_source_reference
from seiche.markets.base import PublicationClockPrecision, SourceAdapterSpec
from seiche.markets.reading_rights import reading_dependencies, reading_restricted
from seiche.markets.registry import MarketRegistry, default_registry
from seiche.markets.us_usd.pack import PACK
from seiche.repository import SQLiteMarketRepository
from seiche.sources import fed_h41, fed_prates
from seiche.sources import frb_release_package as frb
from seiche.sources.canonical import FetchedDocument, PublicationTimePolicy
from seiche.sources.official import PRODUCTION_ADAPTER_KEYS, build_official_adapters

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "frb"
LEDGER = json.loads((ROOT / "docs" / "data-source-expansion.json").read_text())
RECORDS = {item["id"]: item for item in LEDGER["candidates"]}
SERVED = tuple(package for package in frb.PACKAGES if package.rights_hold is None)
NEW_ROLES = {
    SemanticRole.CENTRAL_BANK_BALANCE_SHEET_ITEM,
    SemanticRole.RETIRED_ADMINISTERED_RATE,
    SemanticRole.TBILL_DISCOUNT_RATE,
    SemanticRole.EXCHANGE_RATE,
    SemanticRole.CURRENCY_INDEX,
    SemanticRole.INDUSTRIAL_PRODUCTION,
}


def fixture(package: frb.ReleasePackage) -> str:
    return (FIXTURES / package.member).read_text()


def document(package: frb.ReleasePackage, xml: str | None = None, *,
             members: dict[str, str] | None = None, url: str | None = None) -> FetchedDocument:
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in (members or {package.member: fixture(package) if xml is None else xml}).items():
            archive.writestr(name, body)
    return FetchedDocument(url or package.source_url, "application/zip", raw.getvalue(), package.adapter_id)


def parse(package: frb.ReleasePackage, xml: str | None = None, **kwargs):
    return frb.parse_package(document(package, xml), package, **kwargs)


def latest(points, instrument_id: str):
    rows = [point for point in points if point.instrument_id == instrument_id]
    return rows[-1].event_time, rows[-1].raw_value


def series_block(xml: str, name: str) -> str:
    """The first published <kf:Series> element named ``name``."""
    return next(block for block in re.findall(r"<kf:Series [^>]*>.*?</kf:Series>", xml)
                if f'SERIES_NAME="{name}"' in block)


def pin_digest(points) -> tuple[str, int]:
    lines = [
        f"{p.instrument_id}|{p.event_time.isoformat()}|{p.raw_value}|{p.row_evidence.decode()}|"
        f"{p.revision_id}|{p.publication_time_policy.value}|{p.source_publication_time}"
        for p in sorted(points, key=lambda p: (p.instrument_id, p.event_time))
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), len(lines)


# --------------------------------------------------------------------------
# Identity: rows already stored keep their exact evidence and revision IDs.
# --------------------------------------------------------------------------

def test_refactor_keeps_the_stored_h41_and_iorb_revision_identity():
    """Digests recorded with the 0.16.12 fed_h41 and fed_prates parsers on these fixtures."""
    h41 = [p for p in fed_h41.parse_document(document(frb.H41))
           if p.instrument_id in {fed_h41.INSTRUMENT_ID, fed_h41.TOTAL_ASSETS_INSTRUMENT_ID}]
    assert pin_digest(h41) == ("a2737c33f960536206f476ad69c7199795bfb17afea6dde42b6985226119837b", 8)
    iorb = [p for p in fed_prates.parse_document(document(frb.PRATES))
            if p.instrument_id == fed_prates.INSTRUMENT_ID]
    assert pin_digest(iorb) == ("b382256072efaa06ab324d5da007b5db4157763fd73d7dbde7813ab97b3036fd", 8)
    assert fed_h41.PACKAGE is frb.H41 and fed_prates.PACKAGE is frb.PRATES


# --------------------------------------------------------------------------
# Parsing per package, from the published shape.
# --------------------------------------------------------------------------

def test_h41_reads_all_nine_lines_and_no_neighbouring_series():
    points = parse(frb.H41)
    assert {p.instrument_id for p in points} == set(frb.H41.instrument_ids)
    day = date(2026, 10, 28)
    assert {p.instrument_id: p.raw_value for p in points if p.event_time == day} == {
        "US.FED.RESERVE_BALANCES_DIRECT": Decimal("2893770"),
        "US.FED.TOTAL_ASSETS_DIRECT": Decimal("6745635"),
        "US.FED.TGA_WEEK_AVERAGE_DIRECT": Decimal("999727"),
        "US.FED.CURRENCY_IN_CIRCULATION_DIRECT": Decimal("2484501"),
        "US.FED.PRIMARY_CREDIT_DIRECT": Decimal("9538"),
        "US.FED.LIQUIDITY_SWAPS_DIRECT": Decimal("156"),
        "US.FED.FOREIGN_OFFICIAL_RRP_DIRECT": Decimal("325661"),
        "US.FED.CUSTODY_TREASURIES_DIRECT": Decimal("2568597"),
        "US.FED.FOREIGN_OFFICIAL_REPO_DIRECT": Decimal("0"),
    }
    assert {p.event_time for p in points} == {date(2026, 10, d) for d in (7, 14, 21, 28)}
    # Wednesday levels and week averages of the same lines are never read.
    decoys = {"2895236", "1015632", "2485878", "9501"}
    assert not decoys & {str(p.raw_value) for p in points if p.event_time == day}
    tga = next(p for p in points if p.instrument_id == "US.FED.TGA_WEEK_AVERAGE_DIRECT" and p.event_time == day)
    evidence = json.loads(tga.row_evidence)
    assert evidence["series"] == "RESPPLLDT_XAW_N.WW"
    # The new lines keep the H.4.1 evidence layout stored since 0.16.12.
    assert set(evidence) == {"source_url", "member", "series", "field", "declared", "observation"}
    assert evidence["declared"] == fed_h41.SERIES_ATTRIBUTES
    assert tga.revision_id.startswith("fed-h41:RESPPLLDT_XAW_N.WW:2026-10-28:")
    assert all(p.publication_time_policy is PublicationTimePolicy.INFER for p in points)


def test_h15_reads_constant_maturities_and_bills_but_never_commercial_paper():
    points = parse(frb.H15)
    assert {p.instrument_id for p in points} == set(frb.H15.instrument_ids)
    day = date(2026, 10, 29)
    assert {p.instrument_id: p.raw_value for p in points if p.event_time == day} == {
        "US.FED.H15_CMT_3M": Decimal("4.18"), "US.FED.H15_CMT_2Y": Decimal("4.61"),
        "US.FED.H15_CMT_10Y": Decimal("4.98"), "US.FED.H15_CMT_30Y": Decimal("5.32"),
        "US.FED.H15_TBILL_4W": Decimal("3.91"), "US.FED.H15_TBILL_3M": Decimal("4.00"),
    }
    # Neither the synthetic commercial paper values nor the 1-month, 5-year or
    # 6-month neighbours are read by the served package.
    assert not {Decimal(v) for v in ("1.11", "1.14", "2.24", "3.34", "4.06", "4.78", "4.15")} & {
        p.raw_value for p in points if p.event_time == day}
    evidence = json.loads(next(p for p in points if p.instrument_id == "US.FED.H15_CMT_10Y"
                               and p.event_time == day).row_evidence)
    assert evidence["dataset"] == "H15" and evidence["member"] == "H15_data.xml"
    assert evidence["declared"] == {"CURRENCY": "NA", "FREQ": "9", "INSTRUMENT": "TCMNOM",
                                    "MATURITY": "Y10", "UNIT": "Percent:_Per_Year", "UNIT_MULT": "1"}
    assert evidence["observation"] == {"OBS_STATUS": "A", "OBS_VALUE": "4.98", "TIME_PERIOD": "2026-10-29"}


def test_h15_commercial_paper_parses_but_its_rights_hold_keeps_it_off():
    points = parse(frb.H15_COMMERCIAL_PAPER)
    nonfinancial = [(p.event_time.isoformat(), str(p.raw_value)) for p in points
                    if p.instrument_id == "US.FED.H15_CP_AA_NONFINANCIAL_90D"]
    # NA rows ("n.a." on the release) stay absent, never zero. Values are synthetic.
    assert nonfinancial == [("2026-10-23", "1.11"), ("2026-10-27", "1.12"),
                            ("2026-10-28", "1.13"), ("2026-10-29", "1.14")]
    package = frb.H15_COMMERCIAL_PAPER
    assert package.rights_hold and "Depository Trust" in package.rights_hold
    assert "Depository Trust Company" in package.attribution
    # Off today: the US pack declares nothing for it and nothing registers.
    assert package.adapter_id not in PACK.adapter_map
    assert set(package.instrument_ids).isdisjoint(PACK.instrument_map)
    assert frb.rights_gate_open(PACK, package) is False
    assert (PACK.market_id, package.adapter_id) not in PRODUCTION_ADAPTER_KEYS
    # Off even if a pack declared an allowed record: the hold is in code.
    declared = replace(PACK, source_adapters=(*PACK.source_adapters, SourceAdapterSpec(
        package.adapter_id, ConnectorClassification.OFFICIAL_OPEN, "P1D",
        PACK.adapter_map["fed_h15"].publication_clock, RedistributionStatus.ALLOWED)),
        instruments=(*PACK.instruments, *(replace(PACK.instrument_map["US.FED.H15_TBILL_3M"],
                                                  instrument_id=instrument, mnemonic=instrument.split(".")[-1],
                                                  semantic_role=SemanticRole.CP_3M,
                                                  source_adapter_id=package.adapter_id)
                                          for instrument in package.instrument_ids)))
    assert frb.declared_instruments(declared, package) == set(package.instrument_ids)
    assert frb.rights_gate_open(declared, package) is False
    registry = MarketRegistry(tuple(declared if item.market_id == "US-USD" else item
                                    for item in default_registry().list()))
    adapters = build_official_adapters(registry=registry, repository=SQLiteMarketRepository())
    assert package.adapter_id not in {item.adapter_id for item in adapters}


def test_h10_reads_rates_in_the_releases_own_direction_and_the_dollar_indexes():
    points = parse(frb.H10)
    assert len(frb.H10.series) == 25
    assert {p.instrument_id for p in points} == set(frb.H10.instrument_ids)
    day = date(2026, 10, 23)
    values = {p.instrument_id: p.raw_value for p in points if p.event_time == day}
    assert values["US.FED.H10_USD_PER_EUR"] == Decimal("1.1199")
    assert values["US.FED.H10_USD_PER_GBP"] == Decimal("1.3375")
    assert values["US.FED.H10_USD_PER_AUD"] == Decimal("0.7037")
    assert values["US.FED.H10_USD_PER_NZD"] == Decimal("0.5705")
    assert values["US.FED.H10_CAD_PER_USD"] == Decimal("1.3993")
    assert values["US.FED.H10_JPY_PER_USD"] == Decimal("156.8700")
    assert values["US.FED.H10_INR_PER_USD"] == Decimal("94.9400")
    assert values["US.FED.H10_ZAR_PER_USD"] == Decimal("16.5868")
    assert values["US.FED.H10_DOLLAR_INDEX_BROAD"] == Decimal("121.3202")
    assert values["US.FED.H10_DOLLAR_INDEX_AFE"] == Decimal("114.2969")
    assert values["US.FED.H10_DOLLAR_INDEX_EME"] == Decimal("130.2140")
    assert Decimal("884.0115") not in values.values()  # the unmapped bolivar
    assert {p.event_time for p in points} == {date(2026, 10, d) for d in range(19, 24)}


def test_g17_pins_one_data_set_of_the_three_that_repeat_the_total_index():
    points = parse(frb.G17)
    assert [(p.event_time.isoformat(), str(p.raw_value)) for p in points] == [
        ("2026-06-30", "102.9610"), ("2026-07-31", "103.2006"),
        ("2026-08-31", "103.2664"), ("2026-09-30", "103.9506"),
    ]
    assert {p.instrument_id for p in points} == {"US.FED.G17_INDUSTRIAL_PRODUCTION"}
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)
    evidence = json.loads(points[-1].row_evidence)
    assert evidence["dataset"] == "IP_MAJOR_INDUSTRY_GROUPS"
    assert evidence["declared"]["SA"] == "SA" and evidence["declared"]["UNIT"] == "Index:_2017_100"
    # The copies in the other two data sets are not a duplicate of the pinned one.
    xml = fixture(frb.G17)
    other_only = xml.replace('id="IP_MAJOR_INDUSTRY_GROUPS"', 'id="IP_RENAMED"')
    with pytest.raises(ValueError, match="missing"):
        parse(frb.G17, other_only)


def test_prates_serves_ioer_beside_iorb_under_its_own_identity():
    points = parse(frb.PRATES)
    ioer = [(p.event_time.isoformat(), str(p.raw_value)) for p in points if p.instrument_id == "US.FED.IOER_DIRECT"]
    iorb = [(p.event_time.isoformat(), str(p.raw_value)) for p in points if p.instrument_id == "US.FED.IORB_DIRECT"]
    assert ioer == [(f"2021-07-{d}", "0.15") for d in range(24, 29)]
    assert iorb[:2] == [("2021-07-29", "0.15"), ("2021-07-30", "0.15")]
    assert iorb[-1] == ("2026-11-02", "3.90")
    assert all(p.publication_time_policy is PublicationTimePolicy.UNKNOWN for p in points)
    first = next(p for p in points if p.instrument_id == "US.FED.IOER_DIRECT")
    assert first.revision_id.startswith("fed-prates:RESBME_N.D:2021-07-24:")
    assert set(json.loads(first.row_evidence)) == {"source_url", "series", "field", "unit", "unit_mult", "observation"}


@pytest.mark.parametrize("package", frb.PACKAGES, ids=lambda item: item.adapter_id)
def test_window_and_instrument_requests_never_skip_validation(package):
    every = parse(package)
    one = package.instrument_ids[-1]
    picked = parse(package, instruments=[one])
    assert {p.instrument_id for p in picked} == {one}
    newest = max(p.event_time for p in every)
    windowed = parse(package, start=newest, end=newest)
    assert {p.event_time for p in windowed} == {newest}
    # A broken series that is not requested still fails the whole package.
    broken_name = package.series[0].series_name
    block = series_block(fixture(package), broken_name)
    broken = fixture(package).replace(block, block.replace('UNIT_MULT="', 'UNIT_MULT="9', 1))
    with pytest.raises(ValueError):
        parse(package, broken, instruments=[one])
    with pytest.raises(ValueError, match="no series"):
        parse(package, instruments=["US.FED.NOT_IN_PACKAGE"])


# --------------------------------------------------------------------------
# A changed package shape fails closed.
# --------------------------------------------------------------------------

def _mutate_first_target(package, transform):
    xml = fixture(package)
    block = series_block(xml, package.series[0].series_name)
    return xml.replace(block, transform(block))


def _first_obs(block: str) -> str:
    return re.search(r"<frb:Obs [^>]*/>", block)[0]


def _set_obs(block: str, **changes) -> str:
    obs = _first_obs(block)
    changed = obs
    for key, value in changes.items():
        changed = re.sub(rf'{key}="[^"]*"', f'{key}="{value}"', changed)
    return block.replace(obs, changed, 1)


def _off_calendar_after(day: date, rule: frb.DateRule) -> date | None:
    """A date after ``day`` that the rule forbids, so row order stays valid."""
    if rule is frb.DateRule.WEDNESDAY:
        return date.fromordinal(day.toordinal() + 1)  # Thursday
    if rule is frb.DateRule.WEEKDAY:
        return date.fromordinal(day.toordinal() + ((5 - day.weekday()) % 7 or 7))  # next Saturday
    if rule is frb.DateRule.MONTH_END:
        return date.fromordinal(day.toordinal() + 15)  # mid-month
    return None  # PRATES is calendar daily: every date is valid


def _move_first_row_last(block: str) -> str:
    first = _first_obs(block)
    return block.replace(first, "", 1).replace("</kf:Series>", first + "</kf:Series>")


SHAPE_CHANGES = {
    "unit changed": lambda p, b: re.sub(r'UNIT="[^"]*"', 'UNIT="Other"', b, count=1),
    "multiplier changed": lambda p, b: b.replace('UNIT_MULT="', 'UNIT_MULT="1', 1),
    "frequency changed": lambda p, b: re.sub(r'FREQ="[^"]*"', 'FREQ="0"', b, count=1),
    "identity dimension dropped": lambda p, b: re.sub(
        r' (SERIESTYPE|MATURITY|FX|SA|INT_RATES_PAID)="[^"]*"', "", b, count=1),
    "series renamed": lambda p, b: b.replace(p.series[0].series_name, "RENAMED_N.B", 1),
    "series duplicated": lambda p, b: b + b,
    "observation status unknown": lambda p, b: _set_obs(b, OBS_STATUS="P"),
    "date not ISO": lambda p, b: _set_obs(b, TIME_PERIOD="2026/06/30"),
    "date missing": lambda p, b: _set_obs(b, TIME_PERIOD=""),
    "dates out of order": lambda p, b: _move_first_row_last(b),
    "value with separator": lambda p, b: _set_obs(b, OBS_VALUE="1,000", OBS_STATUS="A"),
    "value in exponent form": lambda p, b: _set_obs(b, OBS_VALUE="1E2", OBS_STATUS="A"),
    "value not a number": lambda p, b: _set_obs(b, OBS_VALUE="NaN", OBS_STATUS="A"),
    "value beyond bound": lambda p, b: _set_obs(b, OBS_VALUE="1000000000000", OBS_STATUS="A"),
    "no available observation": lambda p, b: re.sub(r'OBS_STATUS="A"', 'OBS_STATUS="ND"', b),
}


@pytest.mark.parametrize("change", sorted(SHAPE_CHANGES))
@pytest.mark.parametrize("package", frb.PACKAGES, ids=lambda item: item.adapter_id)
def test_a_changed_series_shape_fails_the_package_closed(package, change):
    transform = SHAPE_CHANGES[change]
    xml = _mutate_first_target(package, lambda block: transform(package, block))
    assert xml != fixture(package)
    with pytest.raises(ValueError):
        parse(package, xml)


@pytest.mark.parametrize("package", frb.PACKAGES, ids=lambda item: item.adapter_id)
def test_duplicate_dates_and_off_calendar_dates_fail_closed(package):
    xml = fixture(package)
    block = series_block(xml, package.series[0].series_name)
    dates = re.findall(r'TIME_PERIOD="([^"]+)"', block)
    duplicate = block.replace(f'TIME_PERIOD="{dates[1]}"', f'TIME_PERIOD="{dates[0]}"', 1)
    with pytest.raises(ValueError, match="duplicated or out of order"):
        parse(package, xml.replace(block, duplicate))
    off = _off_calendar_after(date.fromisoformat(dates[-1]), package.date_rule)
    if off is None:
        assert package.date_rule is frb.DateRule.ANY_DAY
        weekend = block.replace(f'TIME_PERIOD="{dates[-1]}"', 'TIME_PERIOD="2026-11-07"', 1)
        assert parse(package, xml.replace(block, weekend))  # a Saturday is a PRATES date
        return
    moved = block.replace(f'TIME_PERIOD="{dates[-1]}"', f'TIME_PERIOD="{off.isoformat()}"', 1)
    with pytest.raises(ValueError, match="calendar"):
        parse(package, xml.replace(block, moved))


@pytest.mark.parametrize("package", frb.PACKAGES, ids=lambda item: item.adapter_id)
def test_unavailable_rows_stay_absent_never_zero(package):
    xml = fixture(package)
    name = package.series[-1].series_name
    block = series_block(xml, name)
    first = _first_obs(block)
    day = re.search(r'TIME_PERIOD="([^"]+)"', first)[1]
    marked = xml.replace(block, block.replace(first, f'<frb:Obs OBS_STATUS="ND" OBS_VALUE="-9999" TIME_PERIOD="{day}" />', 1))
    points = parse(package, marked)
    assert date.fromisoformat(day) not in {
        p.event_time for p in points if p.instrument_id == package.series[-1].instrument_id}


@pytest.mark.parametrize("package", frb.PACKAGES, ids=lambda item: item.adapter_id)
@pytest.mark.parametrize("change", ["doctype", "entity", "truncated", "data set renamed"])
def test_unsafe_or_broken_xml_fails_closed(package, change):
    xml = fixture(package)
    if change == "doctype":
        xml = xml.replace("?>\n", '?>\n<!DOCTYPE m [<!ELEMENT m ANY>]>\n', 1)
    elif change == "entity":
        xml = xml.replace("?>\n", '?>\n<!DOCTYPE m [<!ENTITY x "y">]>\n', 1)
    elif change == "truncated":
        xml = xml[: len(xml) // 2]
    else:
        xml = xml.replace(f'id="{package.dataset_id}"', 'id="RENAMED"')
    with pytest.raises(ValueError):
        parse(package, xml)


# --------------------------------------------------------------------------
# Size bounds.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("package", frb.PACKAGES, ids=lambda item: item.adapter_id)
def test_package_identity_member_and_size_bounds(package):
    good = document(package)
    with pytest.raises(ValueError, match="unrecognized or oversized"):
        frb.parse_package(FetchedDocument("https://example.invalid/FRB_xml.zip", "application/zip",
                                          good.payload, package.adapter_id), package)
    with pytest.raises(ValueError, match="unrecognized or oversized"):
        frb.parse_package(good, replace(package, max_archive_bytes=len(good.payload) - 1))
    with pytest.raises(ValueError, match="member changed"):
        frb.parse_package(document(package, members={"OTHER_data.xml": fixture(package)}), package)
    crowded = {f"schema{i}.xsd": "x" for i in range(package.max_members)} | {package.member: fixture(package)}
    with pytest.raises(ValueError, match="member changed"):
        frb.parse_package(document(package, members=crowded), package)
    size = len(fixture(package).encode())
    with pytest.raises(ValueError, match="size or encryption changed"):
        frb.parse_package(good, replace(package, max_xml_bytes=size - 1))
    with pytest.raises(ValueError, match="row bound"):
        frb.parse_package(good, replace(package, max_rows_per_series=1))
    assert frb.parse_package(good, replace(package, max_rows_per_series=10_000))


def test_the_streamed_byte_count_is_bounded_independently_of_the_declared_size():
    """A member that understates its size still cannot stream past the bound."""
    from seiche.sources import frb_release

    body = fixture(frb.H10).encode()
    with pytest.raises(ValueError, match="XML exceeds bound"):
        frb_release._stream(io.BytesIO(body), lambda dataset, attributes: None,
                            label="Federal Reserve H.10", max_xml_bytes=len(body) - 1)
    seen = []
    frb_release._stream(io.BytesIO(body), lambda dataset, attributes: seen.append(dataset),
                        label="Federal Reserve H.10", max_xml_bytes=len(body))
    assert set(seen) == {"H10"} and len(seen) == 26


@pytest.mark.asyncio
@pytest.mark.parametrize("package", frb.PACKAGES, ids=lambda item: item.adapter_id)
async def test_bounded_fetch_requests_only_the_publisher_package(package):
    doc = document(package)
    requests = []

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield doc.payload

    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await frb.fetch_package(client, package) == (doc,)
    assert [str(item.url) for item in requests] == [package.source_url]
    assert requests[0].headers["accept-encoding"] == "identity"
    assert requests[0].headers["user-agent"] == frb.USER_AGENT

    async def expect(response_factory, match):
        async with httpx.AsyncClient(transport=httpx.MockTransport(response_factory)) as client:
            with pytest.raises(ValueError, match=match):
                await frb.fetch_package(client, package)

    await expect(lambda request: httpx.Response(302, headers={"location": "https://example.invalid/"}), "HTTP 302")
    await expect(lambda request: httpx.Response(404), "HTTP 404")
    await expect(lambda request: httpx.Response(200, content=b"small", headers={
        "content-length": str(package.max_archive_bytes + 1)}), "archive exceeds bound")
    await expect(lambda request: httpx.Response(200, content=b"x", headers={"content-encoding": "gzip"}),
                 "transport encoding changed")

    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b"\0" * 1024

    # No length header: the streamed byte count alone trips the bound.
    small = replace(package, max_archive_bytes=4096)
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Endless()))) as client:
        with pytest.raises(ValueError, match="archive exceeds bound"):
            await frb.fetch_package(client, small)


# --------------------------------------------------------------------------
# Pack contract: visible as direct series, never wired into an engine.
# --------------------------------------------------------------------------

def test_the_us_pack_declares_every_served_package_and_nothing_else_from_the_board():
    pack = default_registry().get("US-USD")
    board_ids = {package.adapter_id for package in frb.PACKAGES}
    assert {adapter.adapter_id for adapter in pack.source_adapters} & board_ids == {
        package.adapter_id for package in SERVED}
    for package in SERVED:
        adapter = pack.adapter_map[package.adapter_id]
        assert adapter.classification is ConnectorClassification.OFFICIAL_OPEN
        assert adapter.redistribution_status is RedistributionStatus.ALLOWED
        on_adapter = {item.instrument_id for item in pack.instruments if item.source_adapter_id == package.adapter_id}
        assert on_adapter == set(package.instrument_ids)
        assert frb.rights_gate_open(pack, package) is True
        assert (pack.market_id, package.adapter_id) in PRODUCTION_ADAPTER_KEYS
    registered = {item.adapter_id for item in build_official_adapters(repository=SQLiteMarketRepository())}
    assert {package.adapter_id for package in SERVED} <= registered
    assert frb.H15_COMMERCIAL_PAPER.adapter_id not in registered


def test_instrument_units_roles_and_clocks():
    pack = default_registry().get("US-USD")
    spec = pack.instrument_map
    for package in SERVED:
        for series in package.series:
            instrument = spec[series.instrument_id]
            if instrument.semantic_role in RATE_ROLES:
                assert instrument.canonical_unit is CanonicalUnit.BASIS_POINTS
                assert instrument.normalize(Decimal("4.18")) == Decimal("418")
            else:
                assert instrument.normalize(Decimal("1.1199")) == Decimal("1.1199")
    assert spec["US.FED.IOER_DIRECT"].semantic_role is SemanticRole.RETIRED_ADMINISTERED_RATE
    assert {spec[f"US.FED.H15_CMT_{t}"].semantic_role for t in ("3M", "2Y", "10Y", "30Y")} == {
        SemanticRole.SOVEREIGN_YIELD}
    assert {spec[f"US.FED.H15_TBILL_{t}"].semantic_role for t in ("4W", "3M")} == {
        SemanticRole.TBILL_DISCOUNT_RATE}
    assert spec["US.FED.H10_JPY_PER_USD"].canonical_unit is CanonicalUnit.RATIO
    assert spec["US.FED.H10_JPY_PER_USD"].source_unit == "JPY per USD"
    assert spec["US.FED.H10_USD_PER_EUR"].source_unit == "USD per EUR"
    assert spec["US.FED.H10_DOLLAR_INDEX_BROAD"].semantic_role is SemanticRole.CURRENCY_INDEX
    assert spec["US.FED.G17_INDUSTRIAL_PRODUCTION"].canonical_unit is CanonicalUnit.INDEX_POINTS
    # Clocks: H.15 next business day 16:15; H.10 posts weekly, so its
    # currentness clock is five business days; G.17 has no carried calendar.
    h15 = pack.adapter_map["fed_h15"].publication_clock
    assert (h15.local_time.isoformat(), h15.business_day_lag, h15.precision) == (
        "16:15:00", 1, PublicationClockPrecision.SCHEDULED)
    assert pack.adapter_map["fed_h10"].publication_clock.business_day_lag == 1
    assert {spec[i].freshness_clock.business_day_lag for i in frb.H10.instrument_ids} == {5}
    assert pack.adapter_map["fed_g17"].publication_clock.precision is PublicationClockPrecision.UNKNOWN
    assert pack.adapter_map["fed_g17"].expected_cadence == "P1M"


def test_no_capability_event_engine_or_v1_board_reads_the_new_series():
    registry = default_registry()
    for pack in registry.list():
        for capability in pack.capabilities:
            assert not set(capability.required_roles) & NEW_ROLES, (pack.market_id, capability.capability_id)
        for event in pack.events:
            assert not event.affected_roles & NEW_ROLES
    pack = registry.get("US-USD")
    board = {item.instrument_id for package in frb.PACKAGES for item in package.series}
    new = [item for item in pack.instruments if item.instrument_id in board
           and item.instrument_id not in {"US.FED.RESERVE_BALANCES_DIRECT", "US.FED.TOTAL_ASSETS_DIRECT",
                                          "US.FED.IORB_DIRECT"}]
    assert len(new) == 7 + 1 + 6 + 25 + 1
    assert all(item.semantic_role in NEW_ROLES | {SemanticRole.SOVEREIGN_YIELD} for item in new)
    # The v1 bridge maps board mnemonics to pack instruments; none of these
    # collide, so the bridged reading's inputs and restriction do not move.
    assert not {item.mnemonic for item in new} & set(ALL_SERIES)
    without = replace(pack, source_adapters=tuple(a for a in pack.source_adapters
                                                  if a.adapter_id not in {"fed_h15", "fed_h10", "fed_g17"}),
                      instruments=tuple(i for i in pack.instruments if i not in new))
    assert reading_dependencies(pack) == reading_dependencies(without)
    assert reading_restricted(pack) is True


# --------------------------------------------------------------------------
# Citations and the rights registry.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("package", SERVED, ids=lambda item: item.adapter_id)
def test_every_served_package_is_cited_to_the_board_wherever_shown(package):
    assert package.attribution.startswith("Source: Board of Governors of the Federal Reserve System, ")
    assert package.terms_url == "https://www.federalreserve.gov/disclaimer.htm"
    assert market_source_reference(package.adapter_id) == {
        "publisher": "Board of Governors of the Federal Reserve System",
        "source_url": package.source_url,
        "attribution": package.attribution,
        "terms_url": package.terms_url,
    }
    atlas = build_global_money_market_atlas([default_registry().get("US-USD")], {"US-USD": []},
                                            as_of=datetime(2026, 11, 2, 21, tzinfo=UTC))
    notices = [item for item in atlas["legal_notices"] if item.get("notice", "").startswith(package.attribution + ".")]
    assert len(notices) == 1 and notices[0]["terms_url"] == package.terms_url
    for series in package.series:
        assert series.series_name in notices[0]["source"] or package.release in notices[0]["source"]


@pytest.mark.parametrize("package", frb.PACKAGES, ids=lambda item: item.adapter_id)
def test_every_package_has_a_registry_record_quoting_the_board_notice(package):
    record = RECORDS[package.registry_record]
    assert record["publisher"] == "Board of Governors of the Federal Reserve System"
    assert record["access_url"] == package.source_url
    assert record["publisher_url"] == package.release_url
    assert record["access_format"] == ["SDMX-ML release package (ZIP)"]
    assert frb.BOARD_NOTICE in record["blocker"]
    assert package.attribution in record["blocker"]
    for series in package.series:
        assert series.series_name in record["blocker"]
        assert series.fred_id in record["blocker"]
    if package.rights_hold is None:
        assert record["rights_status"] == "explicit_public_domain_with_exclusions"
        assert record["rights_evidence_url"] == frb.TERMS_URL
        assert record["implementation_status"] == "implemented"
        pack = default_registry().get("US-USD")
        assert set(record["semantic_roles"]) == {
            item.semantic_role.value for item in pack.instruments if item.source_adapter_id == package.adapter_id}
    else:
        # Unknown or unclear rights fail closed: metadata-only, blocked.
        assert record["rights_status"] == "metadata_only"
        assert record["implementation_status"] == "policy_blocked"
        assert "Depository Trust" in record["blocker"]


def test_the_board_notice_is_quoted_verbatim():
    assert frb.BOARD_NOTICE == (
        "Unless otherwise indicated, information on Board's website is in the public domain and may "
        "be copied and distributed without permission. Please cite to the Board as the source of the "
        "information."
    )
    assert {package.registry_record for package in frb.PACKAGES} <= set(RECORDS)
    assert len({package.adapter_id for package in frb.PACKAGES}) == len(frb.PACKAGES)


# --------------------------------------------------------------------------
# Collection, clocks and serving.
# --------------------------------------------------------------------------

async def collect(package: frb.ReleasePackage, captured: datetime, xml: str | None = None):
    adapter = next(item for item in build_official_adapters(
        repository=SQLiteMarketRepository(), clock=lambda: captured) if item.adapter_id == package.adapter_id)

    async def fetcher(client):
        return (document(package, xml),)

    adapter.fetcher = fetcher
    return await adapter.collect()


@pytest.mark.asyncio
async def test_h15_rows_become_knowable_only_after_the_16_15_posting(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "h15.sqlite")
    before = await collect(frb.H15, datetime(2026, 10, 30, 18, tzinfo=UTC))  # 14:00 New York
    assert max(row.event_time.date() for row in before.observations) == date(2026, 10, 28)
    after = await collect(frb.H15, datetime(2026, 10, 30, 21, tzinfo=UTC))  # 17:00 New York
    rows = [row for row in after.observations if row.event_time.date() == date(2026, 10, 29)]
    assert {row.instrument_id: row.value for row in rows}["US.FED.H15_CMT_10Y"] == Decimal("498")
    assert all(row.source_publication_time == datetime(2026, 10, 30, 20, 15, tzinfo=UTC) for row in rows)
    assert all(row.redistribution_status is RedistributionStatus.ALLOWED for row in after.observations)
    assert all(row.quality is QualityState.VERIFIED for row in after.observations)


@pytest.mark.asyncio
async def test_h10_currentness_follows_the_weekly_monday_posting(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "h10.sqlite")
    captured = datetime(2026, 10, 26, 21, tzinfo=UTC)  # Monday 17:00 New York, after the posting
    batch = await collect(frb.H10, captured)
    assert max(row.event_time.date() for row in batch.observations) == date(2026, 10, 23)
    friday = next(row for row in batch.observations if row.event_time.date() == date(2026, 10, 23))
    assert friday.source_publication_time == datetime(2026, 10, 26, 20, 15, tzinfo=UTC)
    pack = default_registry().get("US-USD")

    def status(moment: datetime) -> str:
        atlas = build_global_money_market_atlas([pack], {"US-USD": batch.observations}, as_of=moment)
        metrics = {item["id"]: item for item in atlas["markets"][0]["metrics"]}
        return metrics["US.FED.H10_USD_PER_EUR"]["status"]

    # New York leaves daylight time on 2026-11-01: 16:15 is 21:15 UTC from then.
    assert status(datetime(2026, 10, 29, 16, tzinfo=UTC)) == "FRESH"  # midweek, nothing due
    assert status(datetime(2026, 11, 2, 21, 10, tzinfo=UTC)) == "FRESH"  # Monday 16:10, not yet due
    assert status(datetime(2026, 11, 2, 21, 30, tzinfo=UTC)) == "AGING"  # the posting is out, uncollected
    assert status(datetime(2026, 11, 3, 21, 30, tzinfo=UTC)) == "STALE"  # a day overdue


@pytest.mark.asyncio
async def test_g17_publication_time_stays_unknown_and_routine_runs_read_400_days(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "g17.sqlite")
    captured = datetime(2026, 11, 2, 18, tzinfo=UTC)
    batch = await collect(frb.G17, captured)
    assert [row.event_time.date().isoformat() for row in batch.observations] == [
        "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-30"]
    assert all(row.source_publication_time is None for row in batch.observations)
    assert all(row.knowledge_time == captured for row in batch.observations)
    atlas = build_global_money_market_atlas([default_registry().get("US-USD")],
                                            {"US-USD": batch.observations}, as_of=captured)
    metric = next(item for item in atlas["markets"][0]["metrics"] if item["id"] == "US.FED.G17_INDUSTRIAL_PRODUCTION")
    assert metric["value"] == 103.9506 and metric["unit"] == "index_points"
    assert metric["status"] == "UNKNOWN" and metric["published_at"] is None
    assert metric["attribution"] == frb.G17.attribution


@pytest.mark.asyncio
async def test_ioer_never_becomes_the_policy_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "prates.sqlite")
    captured = datetime(2026, 11, 2, 18, tzinfo=UTC)
    adapter = next(item for item in build_official_adapters(
        repository=SQLiteMarketRepository(), clock=lambda: captured, backfill=True)
        if item.adapter_id == frb.PRATES.adapter_id)

    async def fetcher(client):
        return (document(frb.PRATES),)

    adapter.fetcher = fetcher
    batch = await adapter.collect()
    ioer = [row for row in batch.observations if row.instrument_id == "US.FED.IOER_DIRECT"]
    assert [row.value for row in ioer] == [Decimal("15")] * 5
    pack = default_registry().get("US-USD")
    atlas = build_global_money_market_atlas([pack], {"US-USD": ioer}, as_of=captured)
    market = atlas["markets"][0]
    assert market["policy_anchor"] is None
    metric = next(item for item in market["metrics"] if item["id"] == "US.FED.IOER_DIRECT")
    assert metric["semantic_role"] == "RETIRED_ADMINISTERED_RATE" and metric["value"] == 0.15
    assert metric["attribution"] == frb.PRATES.attribution


@pytest.mark.asyncio
async def test_atlas_and_series_page_serve_the_new_rows_cited(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "frb-series.sqlite")
    captured = datetime(2026, 10, 30, 21, tzinfo=UTC)
    rows = []
    for package in (frb.H41, frb.H15):
        rows.extend((await collect(package, captured)).observations)
    rows.extend((await collect(frb.H10, captured)).observations)
    store.save_observations(rows)
    pack = default_registry().get("US-USD")
    atlas = build_global_money_market_atlas([pack], {"US-USD": rows}, as_of=captured)
    metrics = {item["id"]: item for item in atlas["markets"][0]["metrics"]}
    tga = metrics["US.FED.TGA_WEEK_AVERAGE_DIRECT"]
    assert (tga["value"], tga["unit"], tga["asof"]) == (999727.0, "local_currency_millions", "2026-10-28")
    assert tga["attribution"] == frb.H41.attribution and tga["redistribution_status"] == "allowed"
    cmt = metrics["US.FED.H15_CMT_30Y"]
    assert (cmt["value"], cmt["unit"], cmt["asof"]) == (5.32, "%", "2026-10-29")
    assert cmt["attribution"] == frb.H15.attribution and cmt["terms_url"] == frb.TERMS_URL
    eur = metrics["US.FED.H10_USD_PER_EUR"]
    assert (eur["value"], eur["unit"], eur["semantic_role"]) == (1.1199, "ratio", "EXCHANGE_RATE")
    assert eur["attribution"] == frb.H10.attribution

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return captured if tz is None else captured.astimezone(tz)

    monkeypatch.setattr(api, "datetime", Frozen)
    request = Request({"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
                       "path": "/api/v2/markets/US-USD/series", "raw_path": b"/api/v2/markets/US-USD/series",
                       "query_string": b"", "headers": [], "client": ("127.0.0.2", 1), "server": ("t", 80)})
    payload = api.market_series_v2("US-USD", request, Response(), n=500)
    instruments = {item["instrument_id"]: item for item in payload["instruments"]}
    for package in (frb.H41, frb.H15, frb.H10, frb.G17, frb.PRATES):
        for instrument_id in package.instrument_ids:
            item = instruments[instrument_id]
            assert item["attribution"] == package.attribution and item["terms_url"] == frb.TERMS_URL
            assert item["publisher"] == "Board of Governors of the Federal Reserve System"
            assert item["redistribution_status"] == "allowed"
    assert instruments["US.FED.H15_CMT_10Y"]["availability"] == "READY"
    assert instruments["US.FED.H10_JPY_PER_USD"]["availability"] == "READY"
    # Held commercial paper rates are not on the page at all.
    assert not set(frb.H15_COMMERCIAL_PAPER.instrument_ids) & set(instruments)
    records = {(item["instrument_id"], item["event_time"][:10]): item for item in payload["observations"]}
    assert Decimal(records[("US.FED.H15_TBILL_3M", "2026-10-29")]["value"]) == Decimal("400")
    assert Decimal(records[("US.FED.H10_USD_PER_EUR", "2026-10-23")]["value"]) == Decimal("1.1199")
