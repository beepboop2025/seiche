"""Citability Kit: CSV exports, the series catalog, the methodology page,
and the gauge's forward ensemble. Synthetic data only, no network."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from seiche import api, assemble, methodology, store
from seiche.config import ALL_SERIES, COMPOSITE_WEIGHTS, REGIMES
from seiche.sources.base import Series

_SNAP = {
    "generated_at": "2026-07-28T00:00:00+00:00",
    "engines": {
        "composite": {"value": 45.0, "regime": "STRAIN", "coverage_pct": 100},
    },
    "deep": {
        "tell": {"ok": True, "tell": 9.0},
        # The court sits on the finished deep layer, so assemble publishes it
        # under `deep`, and its pooled read is ensemble.p, not p_event_5bd.
        "modelcourt": {"ok": True, "ensemble": {"p": 0.11, "rule": "skill weighted"}},
        "stacker": {"ok": True, "p_now": 0.058, "dispersion_now": 0.019,
                    "members_now": {"rule": 0.076, "ml": 0.038}},
    },
    "calendar": {"next_turn": None, "crunch_windows": []},
    "faults": [],
    "navigator": {"ok": True, "p_event_5bd": 0.09},
}


def _sofr_series() -> Series:
    idx = pd.bdate_range("2026-07-13", periods=10)
    pts = pd.Series([3.6 + 0.01 * i for i in range(10)], index=idx)
    return Series("SOFR", "fred", "SOFR", "Secured overnight financing rate",
                  "%", "D", "2026-07-28T00:00:00+00:00", pts)


@pytest.fixture()
def client(monkeypatch):
    async def fake_snapshot(force=False):
        return _SNAP

    monkeypatch.setattr(assemble, "snapshot", fake_snapshot)
    return TestClient(api.app)


# ---- CSV downloads ----------------------------------------------------------

def test_series_csv_provenance_header_and_rows(client, monkeypatch):
    monkeypatch.setattr(store, "load_series",
                        lambda m: _sofr_series() if m == "SOFR" else None)
    r = client.get("/api/series/SOFR.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="SOFR.csv"' in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    header = [ln for ln in lines if ln.startswith("#")]
    assert any("source: fred" in ln and "unit: %" in ln and "native lag" in ln
               for ln in header)
    assert any("retrieved_at: 2026-07-28T00:00:00+00:00" in ln for ln in header)
    body = [ln for ln in lines if not ln.startswith("#")]
    assert body[0] == "date,value"
    assert body[1] == "2026-07-13,3.6"
    # plain decimals, no scientific notation, no trailing zeros
    assert all("e" not in ln.split(",")[1] for ln in body[1:])


def test_series_csv_unknown_mnemonic_404(client):
    assert client.get("/api/series/NOT_A_SERIES.csv").status_code == 404


def test_series_csv_not_yet_available_503(client, monkeypatch):
    monkeypatch.setattr(store, "load_series", lambda m: None)
    assert client.get("/api/series/SOFR.csv").status_code == 503


def test_series_csv_refuses_licensed_series(client, monkeypatch):
    # third-party licensed data mirrored on FRED is display-only, never bulk
    monkeypatch.setattr(store, "load_series",
                        lambda m: pytest.fail("must refuse before loading"))
    r = client.get("/api/series/SP500.csv")
    assert r.status_code == 403
    assert "licensed" in r.json()["detail"]


def test_series_json_refuses_licensed_series(client, monkeypatch):
    # The JSON twin is the same act of redistribution as the CSV: with board
    # auth a production no-op (Seiche is free), the route itself must enforce
    # the licence allow-list, or the full held history of licensed series
    # ships anonymously in a different format.
    monkeypatch.setattr(store, "load_series",
                        lambda m: pytest.fail("must refuse before loading"))
    for mnemonic in ("SP500", "VIX", "BTC_USD", "SHIBOR_ON"):
        r = client.get(f"/api/series/{mnemonic}")
        assert r.status_code == 403, mnemonic
        assert "redistribution" in r.json()["detail"], mnemonic


def test_series_json_free_series_stays_open(client, monkeypatch):
    # citability is the point: free public-data series keep full history
    monkeypatch.setattr(store, "load_series",
                        lambda m: _sofr_series() if m == "SOFR" else None)
    r = client.get("/api/series/SOFR")
    assert r.status_code == 200
    body = r.json()
    assert body["provenance"]["mnemonic"] == "SOFR"
    assert len(body["points"]) == 10


# ---- series catalog ---------------------------------------------------------

def test_series_index_catalogs_every_registry_series(client):
    # also a route-order regression check: the generic /api/series/{mnemonic}
    # route must not swallow index.json as a mnemonic
    r = client.get("/api/series/index.json")
    assert r.status_code == 200
    idx = r.json()
    assert idx["schema"] == "seiche.series-index.v1"
    assert idx["n_series"] == len(ALL_SERIES)
    rows = {row["mnemonic"]: row for row in idx["series"]}
    sofr = rows["SOFR"]
    assert sofr["csv"] == "/api/series/SOFR.csv"
    assert sofr["source"] == "fred" and sofr["unit"] == "%"
    assert sofr["cadence"] == "daily" and sofr["native_lag"]
    spx = rows["SP500"]
    assert spx["csv"] is None and spx["csv_restricted"] is True
    # the catalog advertises no link either export route would 403: the JSON
    # twin is restricted alongside the CSV, and source-restricted upstreams
    # (BIS, CFETS, exchanges) are marked too, not just the FRED-hosted four
    assert spx["json"] is None
    assert rows["SOFR"]["json"] == "/api/series/SOFR"
    for licensed in ("BTC_USD", "SHIBOR_ON", "CREDIT_GAP_US"):
        row = rows[licensed]
        assert row["csv"] is None and row["json"] is None, licensed
        assert row["csv_restricted"] is True, licensed


def test_csv_route_not_shadowed_by_generic_series_route(client, monkeypatch):
    # if the .csv route were registered after /api/series/{mnemonic}, this
    # request would 404 as unknown series "SOFR.csv"
    monkeypatch.setattr(store, "load_series",
                        lambda m: _sofr_series() if m == "SOFR" else None)
    r = client.get("/api/series/SOFR.csv")
    assert r.status_code == 200 and r.text.startswith("# Seiche series SOFR")


# ---- methodology page -------------------------------------------------------

def test_methodology_page_contents(tmp_path):
    out = methodology.write_methodology(tmp_path / "methodology.html")
    text = out.read_text()
    # weights and regimes are rendered from config, not hand-kept
    for component in COMPOSITE_WEIGHTS:
        assert f"<code>{component}</code>" in text
    for _, name in REGIMES:
        assert name in text
    # required citations
    assert "Afonso" in text and "La Spada" in text and "Williams" in text
    assert "Kaminsky" in text and "Reinhart" in text
    # engine subsections come from module docstrings
    assert "<h3><code>kink</code></h3>" in text
    assert "<h3><code>auctions</code></h3>" in text
    # changelog first entry, verbatim commitments
    assert "letter hygiene overhaul" in text
    assert "the archive is preserved as published" in text
    # cite block with stable URL and version string
    assert "Cite as" in text
    assert "https://seiche.info/methodology.html" in text
    assert f"methodology {methodology.METHODOLOGY_VERSION}" in text
    # repo convention: no em or en dashes in generated prose
    assert "—" not in text and "–" not in text
    # self-contained: no externally loaded assets (links are fine)
    assert "@import" not in text and "<script src" not in text


def test_plain_number_formatting():
    assert methodology._plain(3.64) == "3.64"
    assert methodology._plain(0.00001) == "0.00001"
    assert methodology._plain(3062149.0) == "3062149"
    assert methodology._plain(0.0) == "0"


# ---- gauge ensemble ---------------------------------------------------------

def test_gauge_carries_ensemble_members(client):
    g = client.get("/api/gauge").json()
    assert g["schema"] == "seiche.gauge.v1"
    assert g["p_event_5bd"] == 0.058
    assert g["p_event_5bd_dispersion"] == 0.019
    members = g["p_event_5bd_members"]
    assert members["rule"] == 0.076 and members["ml"] == 0.038
    assert members["navigator"] == 0.09
    # the court's pooled read joins from `deep`, where assemble writes it
    assert members["modelcourt"] == 0.11


def test_gauge_ensemble_degrades_to_null(client, monkeypatch):
    async def bare_snapshot(force=False):
        return {"generated_at": "2026-07-28T00:00:00+00:00",
                "engines": {"composite": {}}, "faults": []}

    monkeypatch.setattr(assemble, "snapshot", bare_snapshot)
    g = client.get("/api/gauge").json()
    assert g["p_event_5bd"] is None
    assert g["p_event_5bd_dispersion"] is None
    assert g["p_event_5bd_members"] is None


# ---- CSV export licensing ---------------------------------------------------

def test_csv_export_is_allowlisted_by_upstream():
    """Bulk export fails closed: a licensed upstream added tomorrow is
    refused by default rather than silently redistributed."""
    from seiche import methodology as m
    # US government upstreams export freely
    assert m.csv_restriction("WRESBAL") is None
    assert m.csv_restriction("DVP_VOL") is None
    # FRED-hosted third-party index data stays refused, by name
    assert "mirrored on FRED" in (m.csv_restriction("VIX") or "")
    # licensed non-US upstreams are refused with their owner named
    for mnemonic, owner in (("SHIBOR_ON", "CFETS"),
                            ("CREDIT_GAP_US", "Bank for International Settlements"),
                            ("BTC_USD", "exchanges")):
        reason = m.csv_restriction(mnemonic)
        assert reason and owner in reason, (mnemonic, reason)


def test_every_restricted_series_is_refused_by_the_route(client):
    from seiche import methodology as m
    from seiche.config import ALL_SERIES
    for mnemonic, spec in ALL_SERIES.items():
        if spec.source not in m.CSV_ALLOWED_SOURCES or mnemonic in m.CSV_RESTRICTED:
            assert m.csv_restriction(mnemonic) is not None, mnemonic
