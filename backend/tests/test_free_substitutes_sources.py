"""Free substitute adapters: real trimmed fixtures, fail-closed rejections.

Fixtures live in ``fixtures/free_substitutes``; ``MANIFEST.json`` records each
one's publisher URL, fetch time, original size and hash, and how it was
trimmed. No test touches the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from seiche.sources.substitutes import (
    bis_cbta,
    ecb_ciss,
    frb_cp_bill,
    frb_fcig,
    frb_gz,
    ofr_fsi,
    ust_hqm,
    wb_pink,
)
from seiche.sources.substitutes._common import (
    USER_AGENT,
    Acquisition,
    decimal_from_cell,
    fetch_fixed,
    parse_decimal,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "free_substitutes"
ACQUIRED = datetime(2026, 11, 9, 4, 0, tzinfo=UTC)
PINK_LINK = (
    "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def acquisition(url: str, payload: bytes, at: datetime = ACQUIRED) -> Acquisition:
    return Acquisition(url, "application/octet-stream", payload, at)


def csv_doc(module, name: str, mutate=None, at: datetime = ACQUIRED):
    text = fixture(name).decode("utf-8")
    if mutate is not None:
        changed = mutate(text)
        assert changed != text, "mutation did not apply"
        text = changed
    return (acquisition(module.SOURCE_URL, text.encode("utf-8"), at),)


def dup_last(text: str) -> str:
    lines = text.splitlines()
    return "\n".join([*lines, lines[-1]]) + "\n"


def rezip(name: str, member: str, mutate) -> bytes:
    with zipfile.ZipFile(io.BytesIO(fixture(name))) as archive:
        text = archive.read(member).decode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, mutate(text))
    return out.getvalue()


def cpbill_docs(cp_mutate=None, h15_mutate=None, at: datetime = ACQUIRED):
    cp = rezip("frb_cp_release.zip", "CP_data.xml", cp_mutate) if cp_mutate else fixture("frb_cp_release.zip")
    h15 = rezip("frb_h15_release.zip", "H15_data.xml", h15_mutate) if h15_mutate else fixture("frb_h15_release.zip")
    return (acquisition(frb_cp_bill.CP_URL, cp, at), acquisition(frb_cp_bill.H15_URL, h15, at))


def hqm_docs(xls_mutate=None, h15_mutate=None, at: datetime = ACQUIRED):
    xls = fixture("ust_hqm_pars.xls")
    if xls_mutate is not None:
        old, new = xls_mutate
        assert len(old) == len(new) and xls.count(old) == 1
        xls = xls.replace(old, new)
    h15 = rezip("frb_h15_release.zip", "H15_data.xml", h15_mutate) if h15_mutate else fixture("frb_h15_release.zip")
    return (acquisition(ust_hqm.SOURCE_URL, xls, at), acquisition(ust_hqm.H15_URL, h15, at))


def pink_docs(edit=None, page: bytes | None = None, at: datetime = ACQUIRED, link: str = PINK_LINK):
    book = fixture("wb_pink_monthly.xlsx")
    if edit is not None:
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(book))
        edit(workbook)
        buffer = io.BytesIO()
        workbook.save(buffer)
        book = buffer.getvalue()
    page = fixture("wb_commodity_markets.html") if page is None else page
    return (acquisition(wb_pink.PAGE_URL, page, at), acquisition(link, book, at))


def by_measure(points, measure):
    return {point.period: point for point in points if point.measure == measure}


def assert_provenance(points, module, documents):
    hashes = tuple((doc.source_url, hashlib.sha256(doc.payload).hexdigest()) for doc in documents)
    for point in points:
        assert point.source == module.SOURCE and point.publisher == module.PUBLISHER
        assert point.terms_url == module.TERMS_URL and point.source_url.startswith("https://")
        assert point.knowledge_time == max(doc.acquired_at for doc in documents)
        assert set(point.raw_documents) <= set(hashes) and point.raw_documents
        assert point.row_sha256 == hashlib.sha256(point.row_evidence).hexdigest()
        assert point.value.is_finite()
        assert point.event_date <= ACQUIRED.date()


def test_fixture_manifest_matches_committed_bytes():
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    names = {path.name for path in FIXTURES.iterdir() if path.name != "MANIFEST.json"}
    assert names == set(manifest)
    for name, item in manifest.items():
        data = fixture(name)
        assert hashlib.sha256(data).hexdigest() == item["fixture_sha256"]
        assert len(data) == item["fixture_bytes"] <= 64_000
        assert item["source_url"].startswith("https://") and "stlouisfed" not in item["source_url"]


# ALT-OFRFSI -----------------------------------------------------------------

def test_ofr_fsi_parses_level_and_contributions_from_real_rows():
    documents = csv_doc(ofr_fsi, "ofr_fsi.csv")
    points = ofr_fsi.parse(documents)
    assert len(points) == 7 * len(ofr_fsi.MEASURES)
    assert by_measure(points, "fsi")["2026-11-04"].value == Decimal("-2.162")
    assert by_measure(points, "volatility")["2026-11-04"].value == Decimal("-0.402")
    assert by_measure(points, "funding")["2022-04-29"].value == Decimal("-9.076749291912794e-05")
    assert by_measure(points, "fsi")["2000-01-03"].unit == "index (0 = average stress)"
    assert_provenance(points, ofr_fsi, documents)


@pytest.mark.parametrize("mutate", [
    lambda t: t.replace("Volatility,", "Volatility (bp),"),
    lambda t: t.replace("2026-11-04,-2.162,-0.861", "2026-11-04,-2.162,-8.61"),
    lambda t: t.replace("2.14,0.54", "214000,0.54"),
    dup_last,
    lambda t: t.replace("-0.402", "NaN"),
    lambda t: t.replace("2026-11-03,", "2026-11-3,"),
], ids=["header-unit", "identity-rescaled", "bounds", "duplicate", "nonfinite", "date"])
def test_ofr_fsi_rejects_changed_units_duplicates_and_bad_values(mutate):
    with pytest.raises(ValueError):
        ofr_fsi.parse(csv_doc(ofr_fsi, "ofr_fsi.csv", mutate))


def test_ofr_fsi_excludes_rows_after_acquisition_day():
    at = datetime(2026, 11, 3, 23, 0, tzinfo=UTC)
    points = ofr_fsi.parse(csv_doc(ofr_fsi, "ofr_fsi.csv", at=at))
    assert max(point.event_date for point in points) == date(2026, 11, 3)
    assert all(point.knowledge_time == at for point in points)


# ALT-CISS -------------------------------------------------------------------

def test_ecb_ciss_parses_the_pinned_us_series():
    documents = csv_doc(ecb_ciss, "ecb_ciss_us.csv")
    points = ecb_ciss.parse(documents)
    assert [point.period for point in points] == [
        "1980-01-02", "1980-01-03", "2026-11-03", "2026-11-04", "2026-11-05"]
    assert points[-1].value == Decimal("0.027289623239906226")
    assert json.loads(points[-1].row_evidence)["key"] == ecb_ciss.SERIES_KEY
    assert_provenance(points, ecb_ciss, documents)


@pytest.mark.parametrize("mutate", [
    lambda t: t.replace(",PURE_NUMB,0", ",PCT,0"),
    lambda t: t.replace(",PURE_NUMB,0\r\n", ",PURE_NUMB,2\r\n", 1),
    lambda t: t.replace("CISS.D.US.Z0Z", "CISS.D.U2.Z0Z"),
    dup_last,
    lambda t: t.replace("0.027289623239906226", "NaN"),
    lambda t: t.replace("0.027289623239906226", "1.7"),
    lambda t: t.replace(",A,F,", ",Q,F,", 1),
], ids=["unit", "unit-mult", "key", "duplicate", "nonfinite", "bounds", "status"])
def test_ecb_ciss_rejects_changed_identity_units_and_values(mutate):
    with pytest.raises(ValueError):
        ecb_ciss.parse(csv_doc(ecb_ciss, "ecb_ciss_us.csv", mutate))


def test_ecb_ciss_excludes_future_rows():
    at = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    points = ecb_ciss.parse(csv_doc(ecb_ciss, "ecb_ciss_us.csv", at=at))
    assert points[-1].period == "2026-11-04"


# ALT-GZ ---------------------------------------------------------------------

def test_frb_gz_parses_monthly_spread_at_month_end():
    documents = csv_doc(frb_gz, "frb_gz_ebp.csv")
    points = frb_gz.parse(documents)
    assert len(points) == 12
    october = by_measure(points, "gz_spread")["2026-10"]
    assert october.value == Decimal("1.0045612268832755")
    assert october.event_date == date(2026, 10, 31) and october.unit == "percentage points"
    assert by_measure(points, "ebp")["1990-09"].value == Decimal("2.874778897770103e-4")
    assert_provenance(points, frb_gz, documents)


@pytest.mark.parametrize("mutate", [
    lambda t: t.replace("gz_spread,", "gz_spread_bp,"),
    lambda t: t.replace("1.0045612268832755", "100.45612268832755"),
    dup_last,
    lambda t: t.replace("2026-10-01", "2026-10-15"),
    lambda t: t.replace("1.0045612268832755", "inf"),
], ids=["header", "scale", "duplicate", "not-month-label", "nonfinite"])
def test_frb_gz_rejects_changed_units_dates_and_duplicates(mutate):
    with pytest.raises(ValueError):
        frb_gz.parse(csv_doc(frb_gz, "frb_gz_ebp.csv", mutate))


def test_frb_gz_excludes_a_month_that_has_not_ended():
    at = datetime(2026, 10, 15, tzinfo=UTC)
    points = frb_gz.parse(csv_doc(frb_gz, "frb_gz_ebp.csv", at=at))
    assert max(point.period for point in points) == "2026-09"


# ALT-FCIG -------------------------------------------------------------------

def test_frb_fcig_parses_index_and_factors():
    documents = csv_doc(frb_fcig, "frb_fcig_monthly.csv")
    points = frb_fcig.parse(documents)
    assert len(points) == 6 * 8
    september = by_measure(points, "fci_g")["2026-09"]
    assert september.value == Decimal("-0.77513870557961")
    assert september.event_date == date(2026, 9, 30)
    assert json.loads(september.row_evidence)["date"] == "2026-09-30"
    assert_provenance(points, frb_fcig, documents)


@pytest.mark.parametrize("mutate", [
    lambda t: t.replace("Stock Market,", "Equities,"),
    lambda t: t.replace("-0.652689419606198", "-6.52689419606198"),
    dup_last,
    lambda t: t.replace("-0.77513870557961", "nan"),
], ids=["header", "identity-rescaled", "duplicate", "nonfinite"])
def test_frb_fcig_rejects_changed_units_duplicates_and_bad_values(mutate):
    with pytest.raises(ValueError):
        frb_fcig.parse(csv_doc(frb_fcig, "frb_fcig_monthly.csv", mutate))


def test_frb_fcig_excludes_future_month():
    at = datetime(2026, 9, 29, tzinfo=UTC)
    points = frb_fcig.parse(csv_doc(frb_fcig, "frb_fcig_monthly.csv", at=at))
    assert max(point.period for point in points) == "2026-08"


# ALT-BISCBTA ----------------------------------------------------------------

def test_bis_cbta_parses_japan_total_assets():
    documents = csv_doc(bis_cbta, "bis_cbta_jp.csv")
    points = bis_cbta.parse(documents)
    assert [point.period for point in points][-1] == "2026-07"
    assert points[-1].value == Decimal("644295.7") and points[-1].unit == "JPY billions"
    assert points[-1].event_date == date(2026, 7, 31)
    assert_provenance(points, bis_cbta, documents)


@pytest.mark.parametrize("mutate", [
    lambda t: t.replace("M,JP,B,XDC,JPY,N", "M,JP,B,USD,JPY,N"),
    lambda t: t.replace(",1,9,,-,", ",1,6,,-,"),
    dup_last,
    lambda t: t.replace("644295.7", "NaN"),
    lambda t: t.replace("644295.7", "644295700000"),
], ids=["unit-measure", "unit-mult", "duplicate", "nonfinite", "bounds"])
def test_bis_cbta_rejects_changed_units_duplicates_and_bad_values(mutate):
    text = fixture("bis_cbta_jp.csv").decode()
    changed = mutate(text)
    assert changed != text
    with pytest.raises(ValueError):
        bis_cbta.parse((acquisition(bis_cbta.SOURCE_URL, changed.encode()),))


def test_bis_cbta_excludes_month_after_acquisition():
    at = datetime(2026, 7, 30, tzinfo=UTC)
    points = bis_cbta.parse(csv_doc(bis_cbta, "bis_cbta_jp.csv", at=at))
    assert points[-1].period == "2026-06"


# ALT-PINK -------------------------------------------------------------------

def test_wb_pink_resolves_the_yearly_link_and_parses_prices_and_indices():
    documents = pink_docs()
    assert wb_pink.resolve_monthly_link(documents[0].payload) == PINK_LINK
    points = wb_pink.parse(documents)
    assert len(points) == 43
    october = {point.measure: point for point in points if point.period == "2026-10"}
    assert october["crude_oil_brent"].value == Decimal("125")
    assert october["crude_oil_wti"].value == Decimal("97.7")
    assert october["natural_gas_us"].value == Decimal("2.96")
    assert october["natural_gas_us"].unit == "$/mmbtu"
    assert october["index_total"].value == Decimal("149.2") and october["index_total"].unit == "2010=100"
    assert october["copper"].event_date == date(2026, 10, 31)
    assert "1960-01" not in by_measure(points, "crude_oil_wti")  # the publisher's ellipsis is missing, not zero
    assert all(point.source_url == PINK_LINK for point in points)
    assert_provenance(points, wb_pink, documents)


def _set(sheet, label, column_label, value):
    def edit(workbook):
        ws = workbook[sheet]
        header = next(row for row in ws.iter_rows() if any(c.value == column_label for c in row))
        column = next(c.column for c in header if c.value == column_label)
        target = next(row for row in ws.iter_rows() if row[0].value == label)
        ws.cell(row=target[0].row, column=column, value=value)
    return edit


def _relabel(sheet, old, new):
    def edit(workbook):
        for row in workbook[sheet].iter_rows():
            if row[0].value == old:
                row[0].value = new
    return edit


@pytest.mark.parametrize("edit", [
    _set("Monthly Prices", "2026M10", "Crude oil, Brent", "high"),
    _relabel("Monthly Prices", "2026M09", "2026M10"),
    _set("Monthly Prices", "2026M10", "Copper", 14.508),
    _relabel("Monthly Indices", "monthly indices based on nominal US dollars, 2010=100, 1960 to present",
             "monthly indices based on nominal US dollars, 2020=100, 1960 to present"),
], ids=["text-value", "duplicate-month", "unit-scale", "index-base"])
def test_wb_pink_rejects_bad_values_duplicates_and_scale_changes(edit):
    with pytest.raises(ValueError):
        wb_pink.parse(pink_docs(edit))


def test_wb_pink_rejects_a_changed_unit_row():
    def edit(workbook):
        ws = workbook["Monthly Prices"]
        for row in ws.iter_rows():
            for cell in row:
                if cell.value == "($/bbl)":
                    cell.value = "($/mt)"
    with pytest.raises(ValueError, match="unit"):
        wb_pink.parse(pink_docs(edit))


@pytest.mark.parametrize("page", [
    b"<html><body>no workbook today</body></html>",
    b'<a href="https://example.org/en/doc/abc-1/related/CMO-Historical-Data-Monthly.xlsx">x</a>',
    ('<a href="%s">a</a><a href="%s">b</a>' % (
        PINK_LINK, PINK_LINK.replace("0050012026", "0050012027"))).encode(),
], ids=["missing", "other-host", "ambiguous"])
def test_wb_pink_link_must_be_one_world_bank_workbook(page):
    with pytest.raises(ValueError):
        wb_pink.resolve_monthly_link(page)


def test_wb_pink_refuses_a_workbook_from_another_url():
    with pytest.raises(ValueError, match="unrecognized"):
        wb_pink.parse(pink_docs(link=PINK_LINK.replace("0050012026", "0050012027")))


def test_wb_pink_excludes_the_month_in_progress():
    points = wb_pink.parse(pink_docs(at=datetime(2026, 10, 20, tzinfo=UTC)))
    assert max(point.period for point in points) == "2026-09"


# ALT-HQM --------------------------------------------------------------------

def test_ust_hqm_parses_par_yields_and_matched_treasury_spreads():
    documents = hqm_docs()
    points = ust_hqm.parse(documents)
    par10 = by_measure(points, "par_10y")
    spread10 = by_measure(points, "spread_10y")
    assert par10["1984-01"].value == Decimal("12.58") and par10["2026-10"].value == Decimal("5.92")
    assert spread10["2026-10"].value == Decimal("0.79")  # 5.92 minus H.15 5.13
    assert by_measure(points, "spread_2y")["2026-10"].value == Decimal("0.42")
    assert set(spread10) == {"2026-08", "2026-09", "2026-10"}  # only months with a Treasury leg
    assert spread10["2026-10"].raw_documents == tuple(
        (doc.source_url, hashlib.sha256(doc.payload).hexdigest()) for doc in documents)
    assert par10["2026-10"].event_date == date(2026, 10, 31)
    assert_provenance(points, ust_hqm, documents)


@pytest.mark.parametrize("xls_mutate", [
    (b"Yields, Percent", b"Yields, BasisPt"),
    (b"Oct 2026", b"Sep 2026"),
    (b"10 Years", b"10 Montz"),
], ids=["unit", "duplicate", "maturity"])
def test_ust_hqm_rejects_changed_units_duplicates_and_headers(xls_mutate):
    with pytest.raises(ValueError):
        ust_hqm.parse(hqm_docs(xls_mutate=xls_mutate))


@pytest.mark.parametrize("h15_mutate", [
    lambda t: t.replace('MATURITY="Y10" SERIES_NAME="RIFLGFCY10_N.M" UNIT="Percent:_Per_Year"',
                        'MATURITY="Y10" SERIES_NAME="RIFLGFCY10_N.M" UNIT="Basis_Points"'),
    lambda t: t.replace('OBS_VALUE="5.13" TIME_PERIOD="2026-10-31"', 'OBS_VALUE="5.13" TIME_PERIOD="2026-10-30"'),
], ids=["treasury-unit", "treasury-not-month-end"])
def test_ust_hqm_rejects_a_changed_treasury_leg(h15_mutate):
    with pytest.raises(ValueError):
        ust_hqm.parse(hqm_docs(h15_mutate=h15_mutate))


def test_ust_hqm_rejects_a_non_biff_payload_and_excludes_future_months():
    documents = hqm_docs()
    with pytest.raises(ValueError, match="BIFF"):
        ust_hqm.parse((acquisition(ust_hqm.SOURCE_URL, b"PK\x03\x04not a biff"), documents[1]))
    points = ust_hqm.parse(hqm_docs(at=datetime(2026, 10, 30, tzinfo=UTC)))
    assert max(point.period for point in points) == "2026-09"


# ALT-CPBILL -----------------------------------------------------------------

def test_frb_cp_bill_computes_spread_from_both_board_packages():
    documents = cpbill_docs()
    points = frb_cp_bill.parse(documents)
    spreads = {point.period: point.value for point in points}
    assert list(spreads) == ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02",
                             "2026-11-03", "2026-11-04", "2026-11-05"]  # 10-27 CP is ND
    assert spreads["2026-11-05"] == Decimal("-0.03") and spreads["2026-10-30"] == Decimal("0.39")
    evidence = json.loads(points[-1].row_evidence)
    assert evidence["cp"]["series"] == "RIFSPPFAAD90_N.B" and evidence["cp"]["OBS_VALUE"] == "3.97"
    assert evidence["bill"]["series"] == "RIFSGFSM03_N.B" and evidence["bill"]["OBS_VALUE"] == "4.00"
    assert len(points[-1].raw_documents) == 2
    assert_provenance(points, frb_cp_bill, documents)


@pytest.mark.parametrize("cp_mutate", [
    lambda t: t.replace('CP_TYPE="FAA" CURRENCY="NA" FREQ="9" SERIES_NAME="RIFSPPFAAD90_N.B" UNIT="Percent"',
                        'CP_TYPE="FAA" CURRENCY="NA" FREQ="9" SERIES_NAME="RIFSPPFAAD90_N.B" UNIT="Basis_points"'),
    lambda t: t.replace('SERIES_NAME="RIFSPPFAAD90_N.B" UNIT="Percent" UNIT_MULT="1"',
                        'SERIES_NAME="RIFSPPFAAD90_N.B" UNIT="Percent" UNIT_MULT="100"'),
    lambda t: t.replace('OBS_VALUE="4.11" TIME_PERIOD="2026-11-04"', 'OBS_VALUE="4.11" TIME_PERIOD="2026-11-03"'),
    lambda t: t.replace('OBS_VALUE="3.97"', 'OBS_VALUE="NaN"'),
    lambda t: t.replace('OBS_STATUS="A" OBS_VALUE="3.97"', 'OBS_STATUS="Q" OBS_VALUE="3.97"'),
    lambda t: t.replace('OBS_VALUE="3.97"', 'OBS_VALUE="397"'),
    lambda t: t.replace('<frb:DataSet id="RATES"', '<frb:DataSet id="VOL"'),
    lambda t: t.replace("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>",
                        '<?xml version="1.0"?><!DOCTYPE m [<!ENTITY x "y">]>'),
], ids=["unit", "unit-mult", "duplicate", "nonfinite", "status", "bounds", "dataset", "doctype"])
def test_frb_cp_bill_rejects_changed_declarations_and_bad_rows(cp_mutate):
    with pytest.raises(ValueError):
        frb_cp_bill.parse(cpbill_docs(cp_mutate=cp_mutate))


def test_frb_cp_bill_rejects_changed_bill_units_and_oversize_packages(monkeypatch):
    with pytest.raises(ValueError):
        frb_cp_bill.parse(cpbill_docs(h15_mutate=lambda t: t.replace(
            'MATURITY="M3" SERIES_NAME="RIFSGFSM03_N.B" UNIT="Percent:_Per_Year"',
            'MATURITY="M3" SERIES_NAME="RIFSGFSM03_N.B" UNIT="Percent"')))
    monkeypatch.setattr(frb_cp_bill, "MAX_CP_XML_BYTES", 1_000)
    with pytest.raises(ValueError, match="bound|limit"):
        frb_cp_bill.parse(cpbill_docs())


def test_frb_cp_bill_excludes_future_dates():
    at = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    points = frb_cp_bill.parse(cpbill_docs(at=at))
    assert points[-1].period == "2026-11-04"
    assert all(point.knowledge_time == at for point in points)


# Shared acquisition and value rules ------------------------------------------

@pytest.mark.parametrize("module,documents", [
    (ofr_fsi, lambda: csv_doc(ofr_fsi, "ofr_fsi.csv")),
    (ecb_ciss, lambda: csv_doc(ecb_ciss, "ecb_ciss_us.csv")),
    (frb_gz, lambda: csv_doc(frb_gz, "frb_gz_ebp.csv")),
    (frb_fcig, lambda: csv_doc(frb_fcig, "frb_fcig_monthly.csv")),
    (bis_cbta, lambda: csv_doc(bis_cbta, "bis_cbta_jp.csv")),
])
def test_csv_adapters_refuse_oversize_and_foreign_documents(module, documents):
    document = documents()[0]
    oversize = acquisition(module.SOURCE_URL, document.payload + b"\n" * (module.MAX_BYTES + 1))
    with pytest.raises(ValueError, match="bound"):
        module.parse((oversize,))
    with pytest.raises(ValueError, match="unrecognized"):
        module.parse((acquisition("https://example.org/copy.csv", document.payload),))


def test_value_parsers_reject_nonfinite_and_text():
    for text in ("NaN", "inf", "-Infinity", "1,000", "", "0x10", "1e9999x"):
        with pytest.raises(ValueError):
            parse_decimal(text, label="test")
    assert parse_decimal("2.874778897770103e-4", label="test") == Decimal("0.0002874778897770103")
    for cell in (float("nan"), float("inf"), "1.5", True, None):
        with pytest.raises(ValueError):
            decimal_from_cell(cell, label="test")
    assert decimal_from_cell(83.4, label="test") == Decimal("83.4")


class _Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_fetch_is_fixed_origin_bounded_and_stamps_actual_acquisition():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, stream=_Stream([b"date,x\n", b"2026-01-01,1\n"]),
                              headers={"content-type": "text/csv; charset=utf-8"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        document = await fetch_fixed(client, ofr_fsi.SOURCE_URL, max_bytes=100,
                                     label="t", clock=lambda: ACQUIRED)
    assert document.payload == b"date,x\n2026-01-01,1\n" and document.media_type == "text/csv"
    assert document.acquired_at == ACQUIRED
    assert document.sha256 == hashlib.sha256(document.payload).hexdigest()
    assert str(seen[0].url) == ofr_fsi.SOURCE_URL
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert seen[0].headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("response,match", [
    (lambda: httpx.Response(302, headers={"location": "https://example.org/"}), "HTTP 302"),
    (lambda: httpx.Response(200, content=b"x", headers={"content-length": "101"}), "bound"),
    (lambda: httpx.Response(200, stream=_Stream([b"x" * 60, b"y" * 60])), "bound"),
    (lambda: httpx.Response(200, content=b"x", headers={"content-encoding": "gzip"}), "encoding"),
    (lambda: httpx.Response(404, content=b""), "HTTP 404"),
], ids=["redirect", "declared-oversize", "streamed-oversize", "gzip", "missing"])
async def test_fetch_rejects_redirects_oversize_and_encoded_bodies(response, match):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response())) as client:
        with pytest.raises(ValueError, match=match):
            await fetch_fixed(client, ofr_fsi.SOURCE_URL, max_bytes=100, label="t")


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://www.financialresearch.gov/fsi.csv",
    "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS",
    "https://user:pass@www.financialresearch.gov/fsi.csv",
    "https://www.financialresearch.gov:8443/fsi.csv",
])
async def test_fetch_refuses_non_https_fred_and_credentialed_urls(url):
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"x"))) as client:
        with pytest.raises(ValueError):
            await fetch_fixed(client, url, max_bytes=100, label="t")
