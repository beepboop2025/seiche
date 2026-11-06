"""Source migration rights, definitions, atomic capture and failure boundaries."""
import copy
import io
import zipfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest

from seiche import methodology, store
from seiche.config import ALL_SERIES
from seiche.sources import fed_releases as fed, fred
from seiche.sources.base import Series


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "test.sqlite")
    monkeypatch.setattr(methodology, "DB_PATH", tmp_path / "test.sqlite")
    return tmp_path


def archive(release, mutate=None):
    root = ET.Element("Data")
    seen = set()
    for m, c in fed.CONTRACTS.items():
        if c["release"] != release or c["dimensions"]["SERIES_NAME"] in seen:
            continue
        seen.add(c["dimensions"]["SERIES_NAME"])
        node = ET.SubElement(root, "Series", c["dimensions"])
        day = "2020-01-31" if release == "g17" else "2020-01-08"
        if m == "IORB":
            day = "2022-01-05"
        ET.SubElement(node, "Obs", TIME_PERIOD=day, OBS_STATUS="A", OBS_VALUE="1.25")
    if mutate:
        mutate(root)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(release.upper() + "_data.xml", ET.tostring(root))
    return out.getvalue()


def capture(release, mutate=None):
    return fed.parse_capture(release, archive(release, mutate), datetime.now(UTC).isoformat(timespec="seconds"))


def test_old_fred_data_never_grants_direct_export(isolated):
    spec = ALL_SERIES["WALCL"]
    store.save_series(Series("WALCL", "fred", "WALCL", spec.label, spec.unit, spec.freq,
                             datetime.now(UTC).isoformat(), pd.Series([999999], index=pd.to_datetime(["2020-01-01"]))))
    assert fed.load_series("WALCL") is None
    assert methodology.csv_restriction("WALCL") is not None
    store.save_blob(fed._key("h41"), capture("h41"))
    direct = fed.load_series("WALCL")
    assert direct.source == "fed_h41"
    assert direct.points.tolist() == [1.25]
    assert direct.points.index[0].date().isoformat() == "2020-01-08"
    assert store.load_series("WALCL").points.tolist() == [999999]
    assert methodology.csv_restriction("WALCL") is None


@pytest.mark.parametrize("release", list(fed.URLS))
def test_each_release_roundtrips_exact_identity_and_unknown_publication(isolated, release):
    snap = capture(release)
    store.save_blob(fed._key(release), snap)
    result = fed.load_many(list(fed.CONTRACTS))
    assert len(result) == sum(c["release"] == release for c in fed.CONTRACTS.values())
    for m, s in result.items():
        p = s.provenance()
        assert s.remote_id == fed.CONTRACTS[m]["dimensions"]["SERIES_NAME"]
        assert p["source_publication_time"] is None
        assert p["knowledge_time"] == snap["fetched_at"]
        assert p["historical_point_in_time"] is False
        assert p["redistribution_status"] == "allowed"
        assert "Board of Governors" in methodology.render_series_csv(s)


def test_weekly_average_and_consolidation_are_pinned():
    for m in ("WTREGEN", "WRESBAL", "WCURCIR", "TGA_LONG"):
        assert fed.CONTRACTS[m]["dimensions"]["SERIESTYPE"] == "A"
    assert fed.CONTRACTS["WALCL"]["dimensions"]["DISTRIBUTION"] == "TOTC"
    assert fed.CONTRACTS["CUSTODY_TSY"]["dimensions"]["SERIESTYPE"] == "L"


def test_changed_quote_currency_or_units_fail_closed():
    for field, value in (("CURRENCY", "XXX"), ("UNIT_MULT", "1000"), ("FREQ", "129")):
        def mutate(root):
            root[0].set(field, value)
        with pytest.raises(ValueError, match="dimensions"):
            capture("h10", mutate)


@pytest.mark.parametrize("value", ["nan", "inf", "-999999"])
def test_invalid_numeric_observations_refused(value):
    with pytest.raises(ValueError, match="invalid available"):
        capture("h15", lambda root: root[0][0].set("OBS_VALUE", value))


def test_month_end_normalization_and_identical_duplicate_datasets():
    snap = capture("g17", lambda root: root.append(copy.deepcopy(root[0])))
    assert snap["series"]["INDPRO"]["points"] == [["2020-01-01", 1.25]]
    def conflict(root):
        duplicate = copy.deepcopy(root[0])
        duplicate[0].set("OBS_VALUE", "2.5")
        root.append(duplicate)
    with pytest.raises(ValueError, match="duplicate"):
        capture("g17", conflict)
    with pytest.raises(ValueError, match="month-end"):
        capture("g17", lambda root: root[0][0].set("TIME_PERIOD", "2020-01-30"))


@pytest.mark.parametrize("status", ["NA", "ND", "NC"])
def test_missing_cp_prints_not_zero_or_forward_filled(status):
    def mutate(root):
        ET.SubElement(root[0], "Obs", TIME_PERIOD="2020-01-09", OBS_STATUS=status, OBS_VALUE="-9999")
    snap = capture("cp", mutate)
    rows = snap["series"]["CP_FIN_3M"]["points"]
    assert rows == [["2020-01-08", 1.25], ["2020-01-09", None]]
    s = fed.decode_capture("cp", snap, ["CP_FIN_3M"])["CP_FIN_3M"]
    assert s.asof == "2020-01-08"
    assert s.provenance()["missing_observations"] == 1


@pytest.mark.parametrize("field,value", [("source", "fred"), ("rights", "unreviewed"), ("unit", "$B")])
def test_cached_contract_or_points_tampering_withholds(isolated, field, value):
    snap = capture("h41")
    snap["series"]["WALCL"]["contract"][field] = value
    store.save_blob(fed._key("h41"), snap)
    assert fed.load_series("WALCL") is None
    assert methodology.csv_restriction("WALCL")
    snap = capture("h41")
    snap["series"]["WALCL"]["points"][0][1] = 999
    store.save_blob(fed._key("h41"), snap)
    assert fed.load_series("WALCL") is None


def test_unrelated_proprietary_inputs_remain_restricted(isolated):
    store.save_blob(fed._key("h15"), capture("h15"))
    for m in ("VIX", "HY_OAS", "IG_OAS", "SP500", "GDP", "CALL_IN"):
        assert m not in fed.CONTRACTS
        assert methodology.csv_restriction(m)


def test_catalog_and_exports_use_capture_identity(isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from seiche import api, assemble
    store.save_blob(fed._key("h41"), capture("h41"))
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy cache/assembler must not be used for direct exports")
    monkeypatch.setattr(store, "load_series", forbidden)
    monkeypatch.setattr(assemble, "snapshot", forbidden)
    client = TestClient(api.app)
    response = client.get("/api/series/WALCL")
    assert response.status_code == 200
    assert response.json()["provenance"]["source"] == "fed_h41"
    assert client.get("/api/series/WALCL.csv").status_code == 200
    assert client.get("/api/series/VIX.csv").status_code == 403
    row = next(row for row in methodology.series_index()["series"] if row["mnemonic"] == "WALCL")
    assert row["source"] == "fed_h41"
    assert row["available"] is True
    assert row["csv"] == "/api/series/WALCL.csv"


@pytest.mark.asyncio
async def test_failed_direct_refresh_never_falls_back_to_fred(isolated, monkeypatch):
    async def fail(*args, **kwargs):
        raise ValueError("provider unavailable")
    async def forbidden(*args, **kwargs):
        raise AssertionError("FRED fallback forbidden")
    monkeypatch.setattr(fed, "_download", fail)
    monkeypatch.setattr(fred, "fetch_series", forbidden)
    faults = []
    async with httpx.AsyncClient() as client:
        result = await fred.fetch_many(client, ["WALCL"], faults)
    assert result == {}
    assert faults[0]["cache_used"] is False
    assert faults[0]["source"] == "fed_h41"


@pytest.mark.asyncio
async def test_failed_refresh_keeps_original_clocks(isolated, monkeypatch):
    snap = capture("h41")
    snap["fetched_at"] = "2020-01-09T00:00:00+00:00"
    store.save_blob(fed._key("h41"), snap)
    async def fail(*args, **kwargs):
        raise ValueError("provider unavailable")
    monkeypatch.setattr(fed, "_download", fail)
    faults = []
    async with httpx.AsyncClient() as client:
        result = await fed.fetch_many(client, ["WALCL"], faults)
    assert result["WALCL"].fetched_at == snap["fetched_at"]
    assert faults[0]["cache_used"] is True
