"""Publisher identity, daily operation aggregation and mandatory reuse notices."""
import copy
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from seiche import methodology, store
from seiche.sources import official_statistics as stats, original_publishers


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "funding.sqlite")
    monkeypatch.setattr(methodology, "DB_PATH", tmp_path / "funding.sqlite")


def rate(m="SOFR"):
    return {"refRates": [{"type": m, "effectiveDate": "2026-10-30",
                          "percentRate": 3.94, "revisionIndicator": ""}]}


def operation(ident="RP 103026 1", amount=4375000000):
    return {"operationId": ident, "operationType": "Reverse Repo", "term": "Overnight",
            "operationMethod": "Fixed Rate", "auctionStatus": "Results", "settlementType": "Same Day",
            "operationDate": "2026-10-30", "settlementDate": "2026-10-30", "maturityDate": "2026-11-02",
            "lastUpdated": "2026-10-30 13:15:24",
            "details": [{"securityType": "Treasury", "amtAccepted": amount}]}


def save(m, parsed, body):
    group = stats.CONTRACTS[m]["group"]
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    doc = {"source_url": stats.source_url(m), "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
           "retrieved_at": stamp}
    cap = stats.make_capture(group, parsed, [doc], stamp)
    store.save_blob(stats._key(group), cap)
    return cap


@pytest.mark.parametrize("m", ["SOFR", "EFFR"])
def test_rate_identity_percent_and_effective_date(m):
    payload = rate(m)
    rows, _ = stats.parse_nyfed_rates(m, json.dumps(payload).encode())
    assert rows == [["2026-10-30", 3.94]]
    for change in ({"type": "TGCR"}, {"percentRate": "3.94"}, {"percentRate": True}, {"percentRate": float("inf")}):
        bad = copy.deepcopy(payload)
        bad["refRates"][0].update(change)
        with pytest.raises(ValueError):
            stats.parse_nyfed_rates(m, json.dumps(bad).encode())
    payload["refRates"].append(payload["refRates"][0])
    with pytest.raises(ValueError, match="duplicate"):
        stats.parse_nyfed_rates(m, json.dumps(payload).encode())


def test_rrp_daily_aggregate_includes_distinct_small_value_exercises():
    ops = [operation(), operation("RP 103026 99", 110000000)]
    parsed = stats.parse_nyfed_rrp(json.dumps({"repo": {"operations": ops}}).encode())
    assert parsed["RRPONTSYD"][0] == parsed["RRP_LONG"][0] == [["2026-10-30", 4.485]]
    extra = parsed["RRP_LONG"][1]
    assert len(extra["source_operation_ids"]["2026-10-30"]) == 2
    assert extra["source_revision_times"]["RP 103026 1"] == "2026-10-30 13:15:24"
    assert "not first-publication" in extra["source_revision_time_policy"]
    assert "2026-10-31" not in dict(parsed["RRP_LONG"][0])


@pytest.mark.parametrize("change", [{"term": "Term"}, {"operationType": "Repo"},
                                    {"settlementDate": "2026-11-02"},
                                    {"details": [{"securityType": "Agency", "amtAccepted": 1}]},
                                    {"details": [{"securityType": "Treasury", "amtAccepted": -1}]}])
def test_rrp_rejects_changed_measure(change):
    op = operation()
    op.update(change)
    with pytest.raises(ValueError):
        stats.parse_nyfed_rrp(json.dumps({"repo": {"operations": [op]}}).encode())


def test_rrp_duplicate_operation_is_not_double_counted():
    with pytest.raises(ValueError, match="duplicate"):
        stats.parse_nyfed_rrp(json.dumps({"repo": {"operations": [operation(), operation()]}}).encode())


def test_sonia_exact_code_no_index_substitution_or_missing_fill():
    body = b"DATE,IUDSOIA\r\n30 Oct 2026,3.7341\r\n"
    assert stats.parse_boe_sonia(body)[0] == [["2026-10-30", 3.7341]]
    for bad in (body.replace(b"IUDSOIA", b"IUDSOIAI"), body.replace(b"3.7341", b""), body.replace(b"3.7341", b"NaN")):
        with pytest.raises(ValueError):
            stats.parse_boe_sonia(bad)


@pytest.mark.parametrize("m", ["SOFR", "EFFR", "SONIA", "RRPONTSYD", "RRP_LONG"])
def test_rights_clocks_and_csv_json_use_only_qualified_capture(isolated, monkeypatch, m):
    from fastapi.testclient import TestClient
    from seiche import api, assemble
    if m.startswith("RRP"):
        body = json.dumps({"repo": {"operations": [operation()]}}).encode()
        parsed = stats.parse_nyfed_rrp(body)
    else:
        body = b"DATE,IUDSOIA\n30 Oct 2026,3.7341\n" if m == "SONIA" else json.dumps(rate(m)).encode()
        parsed = {m: stats.parse_boe_sonia(body) if m == "SONIA" else stats.parse_nyfed_rates(m, body)}
    cap = save(m, parsed, body)
    def forbidden(*args, **kwargs):
        pytest.fail("legacy cache, network, or assembler consulted")
    monkeypatch.setattr(store, "load_series", forbidden)
    monkeypatch.setattr(assemble, "snapshot", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    client = TestClient(api.app)
    response = client.get("/api/series/" + m)
    assert response.status_code == 200
    p = response.json()["provenance"]
    assert p["knowledge_time"] == cap["fetched_at"]
    assert p["source_retrieval_times"] == [cap["fetched_at"]]
    assert p["source_publication_time"] is None
    assert p["historical_point_in_time"] is False
    assert p["required_notices"]
    if m.startswith("RRP"):
        assert p["measure_kind"] == "derived_daily_total_of_original_publisher_operations"
        assert p["publisher_dimensions"]["source_amount_unit"] == "USD"
        assert response.json()["points"] == [["2026-10-30", 4.375]]
    csv = client.get("/api/series/" + m + ".csv")
    assert csv.status_code == 200
    for notice in p["required_notices"]:
        assert notice in csv.text
    assert p["attribution"] in csv.text
    if m.startswith("RRP"):
        assert "derived_daily_total" in csv.text
        assert "settlementDate must equal operationDate" in csv.text
        assert "reject repeated operationId" in csv.text
    for restricted in ["CALL_JP", "CREDIT_GAP_US", "SRF_CEILING", "VIX"]:
        assert client.get("/api/series/" + restricted).status_code == 403
    cap["series"][m]["contract"]["rights"] = "self_attested"
    store.save_blob(stats._key(stats.CONTRACTS[m]["group"]), cap)
    assert client.get("/api/series/" + m).status_code == 403


def test_registry_unit_and_frequency_cannot_silently_change(monkeypatch):
    spec = stats.ALL_SERIES["SOFR"]
    for changed in (replace(spec, unit="bp"), replace(spec, freq="M")):
        monkeypatch.setitem(stats.ALL_SERIES, "SOFR", changed)
        with pytest.raises(ValueError, match="contract needs review"):
            stats.contract("SOFR")
