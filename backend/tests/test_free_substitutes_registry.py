"""The free substitutes registry: classes per the audit, labels on every row."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from seiche.sources.substitutes import registry
from seiche.sources.substitutes._common import SubstituteRow

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "free_substitutes"
ACQUIRED = datetime(2026, 11, 9, 4, 0, tzinfo=UTC)
PINK_LINK = (
    "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/"
    "related/CMO-Historical-Data-Monthly.xlsx"
)

# Section 4.2 of the 2026-11-09 inventory, restricted to the substitutes built.
EXPECTED = {
    ("VIXCLS", "ALT-OFRFSI", "volatility", "proxy"),
    ("VIXCLS", "ALT-CISS", "ciss", "proxy"),
    ("BAMLH0A0HYM2", "ALT-GZ", "gz_spread", "proxy"),
    ("BAMLH0A0HYM2", "ALT-OFRFSI", "credit", "proxy"),
    ("BAMLH0A0HYM2", "ALT-CISS", "ciss", "proxy"),
    ("BAMLC0A0CM", "ALT-GZ", "gz_spread", "close substitute"),
    ("BAMLC0A0CM", "ALT-HQM", "spread_10y", "proxy"),
    ("BAMLC0A0CM", "ALT-OFRFSI", "credit", "proxy"),
    ("SP500", "ALT-OFRFSI", "equity_valuation", "proxy"),
    ("SP500", "ALT-CISS", "ciss", "proxy"),
    ("NASDAQCOM", "ALT-OFRFSI", "equity_valuation", "proxy"),
    ("NASDAQCOM", "ALT-CISS", "ciss", "proxy"),
    ("DCOILWTICO", "ALT-PINK", "crude_oil_wti", "close substitute"),
    ("DCOILBRENTEU", "ALT-PINK", "crude_oil_brent", "close substitute"),
    ("DHHNGSP", "ALT-PINK", "natural_gas_us", "close substitute"),
    ("TEDRATE", "ALT-CPBILL", "cp_aa_financial_3m_minus_tbill_3m", "close substitute"),
    ("TEDRATE", "ALT-OFRFSI", "funding", "proxy"),
    ("PALLFNFINDEXM", "ALT-PINK", "index_total", "close substitute"),
    ("PALLFNFINDEXM", "ALT-PINK", "index_energy", "close substitute"),
    ("PALLFNFINDEXM", "ALT-PINK", "index_non_energy", "close substitute"),
    ("PCOPPUSDM", "ALT-PINK", "copper", "close substitute"),
    ("PALUMUSDM", "ALT-PINK", "aluminum", "close substitute"),
    ("PNICKUSDM", "ALT-PINK", "nickel", "close substitute"),
    ("PCOALAUUSDM", "ALT-PINK", "coal_australia", "close substitute"),
    ("PWHEAMTUSDM", "ALT-PINK", "wheat_us_hrw", "close substitute"),
    ("PMAIZMTUSDM", "ALT-PINK", "maize", "close substitute"),
    ("NFCI", "ALT-OFRFSI", "fsi", "proxy"),
    ("NFCI", "ALT-CISS", "ciss", "proxy"),
    ("NFCI", "ALT-FCIG", "fci_g", "proxy"),
    ("JPNASSETS", "ALT-BISCBTA", "total_assets_jpy", "close substitute"),
    ("DCPN3M", "ALT-OFRFSI", "funding", "proxy"),
    ("DCPF3M", "ALT-OFRFSI", "funding", "proxy"),
}
BLOCKED_17 = {
    "VIXCLS", "BAMLH0A0HYM2", "BAMLC0A0CM", "SP500", "NASDAQCOM", "DCOILWTICO",
    "DCOILBRENTEU", "DHHNGSP", "TEDRATE", "PALLFNFINDEXM", "PCOPPUSDM", "PALUMUSDM",
    "PNICKUSDM", "PCOALAUUSDM", "PWHEAMTUSDM", "PMAIZMTUSDM", "NFCI",
}


def test_registry_is_consistent_with_its_adapters():
    registry.validate_registry()


def test_committed_json_is_the_rendered_registry():
    path = ROOT / registry.DOCUMENT_PATH
    text = path.read_text(encoding="utf-8")
    rendered = json.loads(json.dumps(registry.registry_document(), ensure_ascii=False))
    assert json.loads(text) == rendered, (
        "regenerate with: python -m seiche.sources.substitutes.registry > "
        + registry.DOCUMENT_PATH
    )
    assert not re.search("[\u2012\u2013\u2014\u2015]", text)


def test_classes_are_exactly_the_audit_mapping_and_cover_all_17_blocked_ids():
    found = {(e.substitute_for, e.substitute_id, e.measure, e.substitute_class)
             for e in registry.ENTRIES}
    assert found == EXPECTED
    assert registry.BLOCKED_FRED_IDS == BLOCKED_17
    assert registry.CONDITIONAL_FRED_IDS == {"JPNASSETS", "DCPN3M", "DCPF3M"}
    for fred_id in BLOCKED_17 | registry.CONDITIONAL_FRED_IDS:
        entries = registry.entries_for(fred_id)
        assert entries and [e.rank for e in entries] == sorted(e.rank for e in entries)
        assert registry.FRED_ORIGINALS[fred_id].role


def test_every_substitute_documents_role_cadence_and_attribution():
    document = registry.registry_document()
    assert document["blocked_fred_ids"] == sorted(BLOCKED_17)
    for item in document["substitutes"]:
        assert item["role"] and item["cadence_difference"] and item["notice"]
        assert item["substitute_for"] in item["attribution"]
        assert item["substitute_class"] in item["attribution"]
        assert item["redistribution_status"] in {"allowed", "metadata_only"}
    for source in document["sources"].values():
        assert source["terms_quote"] and source["terms_url"].startswith("https://")
        assert source["measures"] and source["attribution"].startswith("Source:")
        assert all("stlouisfed" not in endpoint for endpoint in source["endpoints"])


def test_cp_bill_publication_follows_the_owner_cp_decision(monkeypatch):
    import importlib

    from seiche import source_decisions

    cpbill = registry.SOURCES["ALT-CPBILL"]
    # The terms reading is unchanged; the owner's recorded decision is what allows publication.
    assert cpbill.terms_status == "unclear_dtcc_provenance"
    assert source_decisions.allows(source_decisions.FED_CP)
    assert cpbill.redistribution_status == "allowed"
    assert source_decisions.FED_CP in cpbill.owner_decision and "DTCC" in cpbill.owner_decision
    monkeypatch.setattr(source_decisions, "allows", lambda decision_id: False)
    try:
        assert importlib.reload(registry).SOURCES["ALT-CPBILL"].redistribution_status == "metadata_only"
    finally:
        monkeypatch.undo()
        importlib.reload(registry)
    assert registry.SOURCES["ALT-CPBILL"].redistribution_status == "allowed"


def test_open_rights_questions_stay_explicit():
    bis = registry.SOURCES["ALT-BISCBTA"]
    assert bis.terms_status == "unrestricted_with_conditions_free_only"
    assert "additional charge" in bis.terms_quote and "never" in bis.charge_rule
    assert "LSEG" in registry.SOURCES["ALT-PINK"].residual_risk


def _observations():
    from seiche.sources.substitutes import ofr_fsi
    from seiche.sources.substitutes._common import Acquisition

    document = Acquisition(ofr_fsi.SOURCE_URL, "text/csv",
                           (FIXTURES / "ofr_fsi.csv").read_bytes(), ACQUIRED)
    return ofr_fsi.parse((document,))


def test_labelled_rows_carry_substitute_for_class_and_provenance():
    observations = _observations()
    entry = registry.entries_for("VIXCLS")[0]
    rows = registry.label(entry, observations)
    assert len(rows) == 7
    for row in rows:
        data = row.as_dict()
        assert data["substitute_for"] == "VIXCLS" and data["substitute_class"] == "proxy"
        assert data["measure"] == "volatility" and "not VIXCLS" in data["notice"]
        assert data["source_url"] == observations[0].source_url
        assert data["publisher"] and data["terms_url"] and data["attribution"]
        assert data["knowledge_time"] == ACQUIRED.isoformat()
        assert data["raw_documents"][0]["sha256"] == observations[0].raw_documents[0][1]
        assert data["redistribution_status"] == "allowed"
        assert set(data) == set(registry.registry_document()["row_fields"])
    assert rows[-1].value == observations[-1].value or rows[-1].event_date.isoformat() == "2026-11-04"


def test_a_substitute_row_cannot_drop_or_fake_its_label():
    row = registry.label(registry.entries_for("NFCI")[0], _observations())[0]
    with pytest.raises(ValueError):
        replace(row, substitute_class="same data")
    with pytest.raises(ValueError):
        replace(row, substitute_for="")
    with pytest.raises(ValueError):
        replace(row, notice="OFR Financial Stress Index")
    with pytest.raises(ValueError):
        replace(row, source_url="https://fred.stlouisfed.org/series/NFCI")
    with pytest.raises(ValueError):
        replace(row, redistribution_status="public")
    assert isinstance(row, SubstituteRow)


@pytest.mark.asyncio
async def test_conditional_series_need_an_explicit_owner_decision():
    for fred_id in ("JPNASSETS", "DCPN3M", "DCPF3M"):
        with pytest.raises(LookupError):
            await registry.rows_for(fred_id)
    with pytest.raises(KeyError):
        registry.entries_for("WALCL")


def _fixture_transport(requests: list[str]):
    routes = {
        "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv": "ofr_fsi.csv",
        "https://data-api.ecb.europa.eu/service/data/CISS/D.US.Z0Z.4F.EC.SS_CIN.IDX?format=csvdata": "ecb_ciss_us.csv",
        "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv": "frb_gz_ebp.csv",
        "https://www.federalreserve.gov/econres/notes/feds-notes/fci_g_public_monthly_3yr.csv": "frb_fcig_monthly.csv",
        "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBTA/1.0/M.JP.B.XDC.JPY.N?format=csv": "bis_cbta_jp.csv",
        "https://www.worldbank.org/en/research/commodity-markets": "wb_commodity_markets.html",
        PINK_LINK: "wb_pink_monthly.xlsx",
        "https://home.treasury.gov/system/files/226/hqm_qh_pars.xls": "ust_hqm_pars.xls",
        "https://www.federalreserve.gov/releases/cp/data/FRB_CP_xml.zip": "frb_cp_release.zip",
        "https://www.federalreserve.gov/releases/h15/data/FRB_H15_xml.zip": "frb_h15_release.zip",
    }

    class Body(httpx.AsyncByteStream):
        # A streamed body, as on the network; httpx pre-reads content= bodies.
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        async def __aiter__(self):
            yield self._payload

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        if url not in routes:
            return httpx.Response(404)
        return httpx.Response(200, stream=Body((FIXTURES / routes[url]).read_bytes()))

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_every_source_collects_end_to_end_from_its_fixed_endpoints():
    requests: list[str] = []
    async with httpx.AsyncClient(transport=_fixture_transport(requests)) as client:
        for substitute_id, source in registry.SOURCES.items():
            observations = await registry.collect(substitute_id, client, clock=lambda: ACQUIRED)
            assert observations and all(o.substitute_id == substitute_id for o in observations)
            assert all(o.knowledge_time == ACQUIRED for o in observations)
            assert set(source.endpoints) <= set(requests)
    assert all("stlouisfed" not in url for url in requests)
    assert PINK_LINK in requests  # resolved from the page, not hard-coded in the adapter


@pytest.mark.asyncio
async def test_rows_for_returns_labelled_rows_best_rank_first():
    requests: list[str] = []
    async with httpx.AsyncClient(transport=_fixture_transport(requests)) as client:
        rows = await registry.rows_for("NFCI", client, clock=lambda: ACQUIRED)
        tedrate = await registry.rows_for("TEDRATE", client, clock=lambda: ACQUIRED)
        jpn = await registry.rows_for("JPNASSETS", client, clock=lambda: ACQUIRED,
                                      include_conditional=True)
    assert [r.substitute_id for r in rows][:1] == ["ALT-OFRFSI"]
    assert {r.substitute_id for r in rows} == {"ALT-OFRFSI", "ALT-CISS", "ALT-FCIG"}
    assert all(r.substitute_for == "NFCI" and r.substitute_class == "proxy" for r in rows)
    cp = [r for r in tedrate if r.substitute_id == "ALT-CPBILL"]
    assert cp and all(r.substitute_class == "close substitute" for r in cp)
    assert all(r.redistribution_status == "allowed" for r in cp)  # under the owner's CP decision
    assert jpn and all(r.unit == "JPY billions" and r.substitute_class == "close substitute" for r in jpn)
    assert requests.count("https://www.federalreserve.gov/econres/notes/feds-notes/fci_g_public_monthly_3yr.csv") == 1


def test_package_text_has_no_em_or_en_dashes():
    package = ROOT / "backend" / "seiche" / "sources" / "substitutes"
    paths = [*package.glob("*.py"), Path(__file__),
             Path(__file__).with_name("test_free_substitutes_sources.py"),
             FIXTURES / "MANIFEST.json"]
    for path in paths:
        assert not re.search("[\u2012\u2013\u2014\u2015]", path.read_text(encoding="utf-8")), path
