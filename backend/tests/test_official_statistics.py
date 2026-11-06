"""Independent definition/rights/clock checks for BLS, BEA, ECB and EIA."""
import copy
import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from seiche import methodology, store
from seiche.sources import official_statistics as stats, original_publishers


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "stats.sqlite")
    monkeypatch.setattr(methodology, "DB_PATH", tmp_path / "stats.sqlite")


def ecb_csv(m, *, value="2.5", period=None, change=None):
    row = {**stats.CONTRACTS[m]["dimensions"], "TIME_PERIOD": period or "2020-01-03", "OBS_VALUE": value, "OBS_STATUS": "A"}
    row.update(change or {})
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    return out.getvalue().encode()


def bls_payload():
    return {"status": "REQUEST_SUCCEEDED", "message": [], "Results": {"series": [
        {"seriesID": c["remote_id"], "data": [
            {"year": "2024", "period": "M12", "value": "323.259"},
            {"year": "2024", "period": "M13", "value": "999999"},
        ]} for c in stats.CONTRACTS.values() if c["provider"] == "bls"
    ]}}


def bea_xlsx(*, units="[Millions of dollars] Seasonally adjusted at annual rates", series="A191RC", value="33010036"):
    strings = ["Table 1.1.5. Gross Domestic Product", units, "Data published October 29, 2026", "Gross domestic product", series, "2026Q3"]
    shared = '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + ''.join('<si><t>' + s + '</t></si>' for s in strings) + '</sst>'
    cells = ''.join(f'<c r="{r}" t="s"><v>{v}</v></c>' for r, v in [("A1",0),("A2",1),("A5",2),("B9",3),("C9",4),("D8",5)])
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row>' + cells + f'<c r="A9"><v>1</v></c><c r="D9"><v>{value}</v></c></row></sheetData></worksheet>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="T10105-Q" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", shared)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()


def eia_html(m, *, wrong_title=False, duplicate=False):
    title = "Unrelated vendor spot price" if wrong_title else stats.CONTRACTS[m]["title"]
    groups = {}
    day = date(2020, 1, 3)
    for i in range(52):
        groups.setdefault(day.strftime("%Y-%b"), []).append((day.strftime("%m/%d"), str(10000+i)))
        day += timedelta(days=7)
    rows = []
    for month, values in groups.items():
        cells = [month] + [v for pair in values + [("", "")] * (5-len(values)) for v in pair]
        rows.append('<tr>' + ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>')
    if duplicate:
        rows.append(rows[0])
    return ('<title>' + title + '</title><td>Release Date: 1/6/2021</td><table>' + ''.join(rows) + '</table>').encode()


def save(m, parsed, body=b"fixture"):
    group = stats.CONTRACTS[m]["group"]
    doc = {"source_url": stats.source_url(m), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "request": None}
    cap = stats.make_capture(group, parsed, [doc], datetime.now(UTC).isoformat(timespec="seconds"))
    store.save_blob(stats._key(group), cap)
    return cap


def test_bls_monthly_exact_ids_and_annual_average_exclusion():
    parsed = stats.parse_bls(json.dumps(bls_payload()).encode(), 2015, 2024)
    assert set(parsed) == {"CORE_CPI", "ENERGY_CPI"}
    assert parsed["CORE_CPI"][0] == [["2024-12-01", 323.259]]
    bad = bls_payload()
    bad["Results"]["series"][0]["seriesID"] = "CUUR0000SA0L1E"  # unadjusted is a different series
    with pytest.raises(ValueError, match="identity"):
        stats.parse_bls(json.dumps(bad).encode(), 2015, 2024)
    bad = bls_payload()
    bad["message"] = ["Request exceeded daily limit"]
    with pytest.raises(ValueError, match="limited"):
        stats.parse_bls(json.dumps(bad).encode(), 2015, 2024)
    bad = bls_payload()
    bad["Results"]["series"][0]["data"].append(copy.deepcopy(bad["Results"]["series"][0]["data"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        stats.parse_bls(json.dumps(bad).encode(), 2015, 2024)


def test_bls_publisher_declared_shutdown_missingness_is_preserved(isolated):
    data = bls_payload()
    for entry in data["Results"]["series"]:
        entry["data"].append({"year": "2024", "period": "M10", "value": "-",
                              "footnotes": [{"code": "X", "text": "Data unavailable due to lapse in appropriations"}]})
    parsed = stats.parse_bls(json.dumps(data).encode(), 2015, 2024)
    assert parsed["CORE_CPI"][0][0] == ["2024-10-01", None]
    assert parsed["CORE_CPI"][1]["missing_observation_notes"][0]["code"] == "X"
    save("CORE_CPI", parsed)
    source = original_publishers.load_series("CORE_CPI")
    assert source.tail_records()[0] == ["2024-10-01", None]
    assert "2024-10-01,\n" in methodology.render_series_csv(source)
    data["Results"]["series"][0]["data"][-1]["footnotes"] = []
    with pytest.raises(ValueError, match="explanation"):
        stats.parse_bls(json.dumps(data).encode(), 2015, 2024)


def test_bea_gdp_quarter_units_and_publication_date():
    rows, extra = stats.parse_bea(bea_xlsx())
    assert rows == [["2026-07-01", 33010.036]]
    assert extra == {"source_publication_date": "2026-10-29"}
    for body in (bea_xlsx(units="Billions of chained dollars"), bea_xlsx(series="A191RX")):
        with pytest.raises(ValueError, match="definition"):
            stats.parse_bea(body)
    with pytest.raises(ValueError, match="nonfinite"):
        stats.parse_bea(bea_xlsx(value="nan"))


def test_ecb_units_provider_and_week_date_are_exact():
    rows, _ = stats.parse_ecb("ECB_ASSETS", ecb_csv("ECB_ASSETS", value="5945410", period="2026-W44"))
    assert rows == [["2026-10-30", 5945410.0]]
    for key, value in (("UNIT_MULT", "0"), ("BS_REP_SECTOR", "B"), ("KEY", "ILM.unrelated")):
        with pytest.raises(ValueError, match="dimensions"):
            stats.parse_ecb("ECB_ASSETS", ecb_csv("ECB_ASSETS", period="2026-W44", change={key:value}))
    with pytest.raises(ValueError, match="nonfinite"):
        stats.parse_ecb("ESTR", ecb_csv("ESTR", value="inf"))


def test_eia_inventory_title_units_dates_and_duplicates():
    rows, extra = stats.parse_eia("CUSHING_STOCKS", eia_html("CUSHING_STOCKS"))
    assert len(rows) == 52
    assert rows[0] == ["2020-01-03", 10000.0]
    assert extra == {"source_publication_date": "2021-01-06"}
    with pytest.raises(ValueError, match="identity"):
        stats.parse_eia("CUSHING_STOCKS", eia_html("CUSHING_STOCKS", wrong_title=True))
    with pytest.raises(ValueError, match="duplicate"):
        stats.parse_eia("CUSHING_STOCKS", eia_html("CUSHING_STOCKS", duplicate=True))


def test_qualified_capture_and_ecb_notices(isolated):
    body = ecb_csv("ESTR")
    save("ESTR", {"ESTR": stats.parse_ecb("ESTR", body)}, body)
    s = original_publishers.load_series("ESTR")
    assert s.source == "ecb_original"
    assert s.provenance()["source_publication_time"] is None
    assert s.provenance()["historical_point_in_time"] is False
    assert methodology.csv_restriction("ESTR") is None
    exported = methodology.render_series_csv(s)
    assert "available free of charge" in exported
    assert "European Central Bank" in exported
    assert "Modifications: publisher effective date; values unchanged" in exported


def test_wrong_receipt_and_metadata_cannot_release_old_source(isolated):
    assert methodology.csv_restriction("GDP")
    body = bea_xlsx()
    cap = save("GDP", {"GDP": stats.parse_bea(body)}, body)
    assert methodology.csv_restriction("GDP") is None
    cap["series"]["GDP"]["extra"]["source_publication_date"] = "1947-01-01"
    store.save_blob(stats._key("GDP"), cap)
    assert original_publishers.load_series("GDP") is None
    assert methodology.csv_restriction("GDP")


def test_json_and_csv_use_new_capture_only(isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from seiche import api, assemble
    body = bea_xlsx()
    save("GDP", {"GDP": stats.parse_bea(body)}, body)
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy cache/assembler consulted")
    monkeypatch.setattr(store, "load_series", forbidden)
    monkeypatch.setattr(assemble, "snapshot", forbidden)
    client = TestClient(api.app)
    response = client.get("/api/series/GDP")
    assert response.status_code == 200
    assert response.json()["points"] == [["2026-07-01", 33010.036]]
    assert response.json()["provenance"]["source"] == "bea_original"
    assert client.get("/api/series/GDP.csv").status_code == 200
    assert client.get("/api/series/VIX.csv").status_code == 403


@pytest.mark.asyncio
async def test_failed_refresh_retains_only_qualified_old_capture(isolated, monkeypatch):
    body = ecb_csv("ESTR")
    cap = save("ESTR", {"ESTR": stats.parse_ecb("ESTR", body)}, body)
    cap["fetched_at"] = "2020-01-04T00:00:00+00:00"
    store.save_blob(stats._key("ESTR"), cap)
    async def fail(*args, **kwargs):
        raise ValueError("unavailable")
    monkeypatch.setattr(stats, "_download", fail)
    faults = []
    async with httpx.AsyncClient() as client:
        result = await stats.fetch_many(client, ["ESTR", "GDP"], faults)
    assert set(result) == {"ESTR"}
    assert result["ESTR"].fetched_at == cap["fetched_at"]
    assert len(faults) == 2
