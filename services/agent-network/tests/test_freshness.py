from datetime import datetime, timedelta, timezone
import json
import stat

import httpx
import pytest

from seiche_agent_network.freshness import SourcePolicy, _LimitedStream, inspect_result, probe_product, probe_products

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def stamp(days=0):
    return (NOW - timedelta(days=days)).isoformat()


def row(days=1, freq="D", **fields):
    grace = {"D": 4, "W": 10, "M": 45}[freq]
    return {"source": "fred", "mnemonic": "TEST", "freq": freq, "asof": stamp(days),
            "freshness_grace_days": grace, "staleness": "fresh", **fields}


def test_generated_fetched_and_catalog_clocks_never_prove_observation_freshness():
    value = {"generated_at": stamp(), "fetched_at": stamp(), "status": "fresh"}
    assert inspect_result("seiche", "data_health", value, now=NOW)["freshness"] == "unknown"
    catalog = {"schema": "palimpsest.research-catalog.v1", "generated_at": stamp(), "datasets": [
        {"artifacts": {"observed_at": stamp(), "evidence_state": "fresh"}, "cadence": "PT1H"}]}
    result = inspect_result("palimpsest", "research_catalog", catalog, now=NOW)
    assert result["freshness"] == "unknown" and result["source_age_seconds"] is None


def test_seiche_qualified_native_cadences_and_conservative_calendar_limits():
    assert inspect_result("seiche", "data_health", {"provenance": [row(3)]}, now=NOW)["freshness"] == "fresh"
    assert inspect_result("seiche", "data_health", {"provenance": [row(9, "W")]}, now=NOW)["freshness"] == "fresh"
    assert inspect_result("seiche", "data_health", {"provenance": [row(30, "M")]}, now=NOW)["freshness"] == "fresh"
    # Seiche's generic native grace counts UTC calendar dates, not elapsed noon
    # since a date-only midnight placeholder.
    assert inspect_result("seiche", "data_health", {"provenance": [row(4, asof="2026-10-07")]}, now=NOW)["freshness"] == "fresh"
    result = inspect_result("seiche", "data_health", {"generated_at": stamp(), "provenance": [row(5)]}, now=NOW)
    assert result["freshness"] == "stale" and result["source_age_seconds"] == 5 * 86400
    calendar = row(1, freshness_policy="fred-h10-weekly-v1", publication_schedule={"latest_due_at": stamp()})
    assert inspect_result("seiche", "data_health", {"provenance": [calendar]}, now=NOW)["freshness"] == "unknown"
    future = row(-1)
    result = inspect_result("seiche", "data_health", {"provenance": [future]}, now=NOW)
    assert result["freshness"] == "unknown" and result["source_age_seconds"] is None
    assert inspect_result("seiche", "data_health", {"provenance": [row()], "faults": [{"source": "unavailable"}]}, now=NOW)["freshness"] == "unknown"


def test_scoped_funding_subset_is_separate_from_historical_catalog():
    rows = [row(1, mnemonic=mnemonic) for mnemonic in ("SOFR", "EFFR", "IORB")]
    rows.append(row(1500, mnemonic="HISTORICAL", staleness="dead"))
    result = inspect_result("seiche", "data_health", {"provenance": rows}, now=NOW)
    assert result["freshness"] == "stale"
    assert result["critical_subset"]["freshness"] == "fresh"
    assert result["coverage"] == {"inspected_rows": 4, "observation_clock_rows": 4, "fresh": 3, "stale": 1, "unknown": 0, "restricted": 0}
    assert result["source_reason_counts"]["source_reports_aging_stale_or_dead"] == 1


@pytest.mark.parametrize("restriction", [{"publication_allowed": False}, {"status": "restricted"}, {"context_only": True}, {"scoring_allowed": False}])
def test_palimpsest_rights_and_context_limits_preserved(restriction):
    result = inspect_result("palimpsest", "china_economic_health", {**restriction, "generated_at": stamp()}, now=NOW)
    assert result["freshness"] == "restricted"
    assert result["source_age_seconds"] is None


def test_normalized_source_requires_operator_policy_not_payload_claim():
    source = {"schema": "financial-evidence.source-freshness.v1", "observations": [
        {"source_id": "release", "cadence": "P1M", "observed_at": stamp(20), "qualified": True, "max_age_seconds": 999999999}]}
    assert inspect_result("liquilens", "data_health", source, now=NOW)["freshness"] == "unknown"
    policies = {("liquilens", "release"): SourcePolicy("observed_at", "P1M", 45 * 86400)}
    assert inspect_result("liquilens", "data_health", source, now=NOW, policies=policies)["freshness"] == "fresh"
    policies[("liquilens", "release")] = SourcePolicy("generated_at", "P1M", 999999999)
    assert inspect_result("liquilens", "data_health", source, now=NOW, policies=policies)["freshness"] == "unknown"


def test_mcp_envelopes_and_malformed_metadata_are_safe():
    value = {"provenance": [row()]}
    envelope = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(value)}]}}
    assert inspect_result("seiche", "data_health", envelope, now=NOW)["freshness"] == "fresh"
    envelope["result"]["isError"] = True
    assert inspect_result("seiche", "data_health", envelope, now=NOW)["freshness"] == "unknown"
    malformed = {"schema": "financial-evidence.source-freshness.v1", "observations": [{"source_id": {"private": "text"}, "cadence": {"raw": "secret"}}]}
    result = inspect_result("liquilens", "data_health", malformed, now=NOW)
    assert result["freshness"] == "unknown" and "secret" not in json.dumps(result)


async def test_probe_report_is_status_only_atomic_and_private(tmp_path, monkeypatch):
    async def fake(product, **kwargs):
        return {"product": product, "transport": "available", "freshness": "unknown", "clocks": [], "reasons": ["no_qualified_health_tool_advertised"]}
    monkeypatch.setattr("seiche_agent_network.freshness.probe_product", fake)
    path = tmp_path / "status.json"
    report = await probe_products(path)
    assert len(report["products"]) == 6 and report["raw_payloads_retained"] is False
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == report
    assert all(row["freshness"] == "unknown" for row in report["products"])


async def test_probe_rejects_unknown_endpoints_and_large_response_streams():
    with pytest.raises(ValueError):
        await probe_product("http://127.0.0.1/private")
    class Large(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 4194305
    with pytest.raises(httpx.StreamError):
        async for _ in _LimitedStream(Large()):
            pass


def test_seiche_weekly_calendars_recompute_release_deadlines_and_holidays():
    h10 = row(1, remote_id="DEXCHUS", asof="2026-10-02", freshness_policy="fred-h10-weekly-v1",
              freshness_grace_days=None, publication_schedule={"latest_due_at": stamp(), "missed_release_weeks": 0})
    def inspect(r, at):
        return inspect_result("seiche", "data_health", {"provenance": [r]}, now=datetime.fromisoformat(at))
    assert inspect(h10, "2026-10-12T22:00:00+00:00")["freshness"] == "fresh"  # Federal holiday.
    before = inspect(h10, "2026-10-13T20:14:59+00:00")
    assert before["freshness"] == "fresh"
    after = inspect(h10, "2026-10-13T20:15:00+00:00")
    assert after["freshness"] == "stale" and after["clocks"][0]["missed_release_weeks"] == 1
    assert after["clocks"][0]["schedule_is_publication_receipt"] is False
    h41 = row(1, freq="W", remote_id="WALCL", asof="2026-11-18", freshness_policy="fred-h41-weekly-v1", freshness_grace_days=None)
    assert inspect(h41, "2026-11-26T23:00:00+00:00")["freshness"] == "fresh"  # Thanksgiving.
    assert inspect(h41, "2026-11-27T21:30:00+00:00")["freshness"] == "stale"
    h10["remote_id"] = "UNREVIEWED"
    assert inspect(h10, "2026-10-11T12:00:00+00:00")["freshness"] == "unknown"


def test_iorb_announcement_keeps_availability_separate_from_current_observation():
    iorb = row(0, mnemonic="IORB", remote_id="IORB", asof="2026-10-13", fetched_at=stamp())
    source = {"generated_at": stamp(), "provenance": [row(1, mnemonic="SOFR"), row(1, mnemonic="EFFR"), iorb]}
    result = inspect_result("seiche", "data_health", source, now=NOW)
    clock = result["clocks"][-1]
    assert clock["freshness"] == "unknown" and clock["source_age_seconds"] is None
    assert clock["effective_at"] == "2026-10-13" and clock["availability_age_seconds"] == 0
    assert clock["current_observation_available"] is False
    assert clock["reason"] == "announced_effective_date_not_current_observation"
    assert result["critical_subset"]["current_observation_required"] == ["fred:IORB"]


@pytest.mark.parametrize("update", [
    {"remote_id": "SOFR"}, {"source": "other"}, {"freq": "W"},
    {"asof": "2026-10-13T00:00:00Z"}, {"asof": "2026-11-15"},
    {"fetched_at": "2026-10-11"}, {"fetched_at": "2026-10-11T10:00:00"},
    {"fetched_at": "2026-10-12T00:00:00Z"}, {"fetched_at": None},
])
def test_iorb_exception_cannot_be_applied_to_unqualified_future_clocks(update):
    iorb = row(0, mnemonic="IORB", remote_id="IORB", asof="2026-10-13", fetched_at=stamp())
    iorb.update(update)
    result = inspect_result("seiche", "data_health", {"generated_at": stamp(), "provenance": [iorb]}, now=NOW)
    assert "available_at" not in result["clocks"][0]
    assert result["freshness"] == "unknown"


def test_only_exact_native_retired_references_are_excluded():
    retired = row(1000, mnemonic="IOER", remote_id="IOER", asof="2021-07-28", staleness="dead")
    result = inspect_result("seiche", "data_health", {"provenance": [row(), retired]}, now=NOW)
    assert result["freshness"] == "fresh" and result["source_age_seconds"] == 86400
    assert result["historical_reference_clocks"][0]["replacement"] == "IORB"
    retired["remote_id"] = "UNREGISTERED"
    assert inspect_result("seiche", "data_health", {"provenance": [retired]}, now=NOW)["freshness"] == "stale"
    retired["remote_id"], retired["asof"] = "IOER", "2026-10-01"
    assert inspect_result("seiche", "data_health", {"provenance": [retired]}, now=NOW)["freshness"] == "stale"


def venue_result(age=60):
    from seiche_agent_network.freshness import UNDERTOW_VENUES
    observed = (NOW - timedelta(seconds=age)).isoformat()
    return {"status": "partial", "generated_at": stamp(),
            "venue_freshness": {venue: {"observed_at": observed, "status": "fresh"} for venue in UNDERTOW_VENUES}}


def test_undertow_research_venue_clocks_have_native_two_hour_policy():
    result = inspect_result("undertow", "exit_cost", venue_result(), now=NOW)
    assert result["freshness"] == "fresh" and result["coverage"]["fresh"] == 6
    assert result["rights"]["status"] == "not_verified_by_this_tool"
    assert result["native_availability"] == "partial"  # Clock freshness does not repair unavailable estimates.
    assert inspect_result("undertow", "exit_cost", venue_result(7201), now=NOW)["freshness"] == "stale"
    missing = venue_result()
    missing["venue_freshness"]["kraken"]["observed_at"] = "2026-10-11"
    assert inspect_result("undertow", "exit_cost", missing, now=NOW)["freshness"] == "unknown"
    assert inspect_result("undertow", "exit_cost", venue_result(-1), now=NOW)["freshness"] == "unknown"


def test_undertow_native_rights_remain_separate_and_fail_closed():
    data = venue_result()
    data.update(schema="undertow.trade-safety-exit-context.v1", status="available",
                rights={"status": "approved", "scope": "derived_metadata_only", "valid_until": stamp(-1)},
                clocks={"venue_observation_at_by_venue": {venue: row["observed_at"] for venue, row in data["venue_freshness"].items()}})
    assert inspect_result("undertow", "trade_safety_exit_context", data, now=NOW)["freshness"] == "fresh"
    data["rights"]["valid_until"] = stamp(1)
    assert inspect_result("undertow", "trade_safety_exit_context", data, now=NOW)["freshness"] == "restricted"
    data["rights"].update(status="pending_owner_review", valid_until=None)
    result = inspect_result("undertow", "trade_safety_exit_context", data, now=NOW)
    assert result["freshness"] == "restricted" and result["coverage"]["fresh"] == 6
    data["rights"]["status"] = {"private": "do not retain"}
    result = inspect_result("undertow", "trade_safety_exit_context", data, now=NOW)
    assert result["freshness"] == "unknown" and "do not retain" not in json.dumps(result)


def test_palimpsest_restriction_scope_is_not_all_china_sources():
    result = inspect_result("palimpsest", "query_economic_observations", {
        "schema_version": "palimpsest.mcp-china-economic-rights.v1", "publication_allowed": False,
        "status": "restricted", "mcp_checked_at": stamp()}, now=NOW)
    assert result["freshness"] == "restricted" and result["coverage"]["restricted"] == 1
    assert result["scope"] == "china_economic_observations_and_affected_derivatives_not_all_china_sources"
    assert result["clocks"] == []


def test_donia_weekend_and_native_dubai_publication_aim_are_recomputed():
    source = row(2, source="cbuae_donia", mnemonic="DONIA", remote_id="UAE_INTEREST_RATES/DONIA", asof="2026-10-09",
                 freshness_policy="cbuae-donia-publication-aim-v1", freshness_grace_days=None)
    assert inspect_result("seiche", "data_health", {"provenance": [source]}, now=NOW)["freshness"] == "fresh"
    # 09:30 Dubai publication aim plus 90 minutes collector allowance.
    before = datetime.fromisoformat("2026-10-12T06:59:59+00:00")
    after = datetime.fromisoformat("2026-10-12T07:00:00+00:00")
    assert inspect_result("seiche", "data_health", {"provenance": [source]}, now=before)["freshness"] == "fresh"
    assert inspect_result("seiche", "data_health", {"provenance": [source]}, now=after)["freshness"] == "stale"
    fx = {**source, "source": "cbuae_fx", "mnemonic": "CBUAEFX_USD", "remote_id": "VAT/AED_PER_USD",
          "freshness_policy": "cbuae-vat-fx-calendar-age-v1"}
    assert inspect_result("seiche", "data_health", {"provenance": [fx]}, now=NOW)["freshness"] == "stale"


def test_gdp_reference_period_does_not_claim_latest_release_freshness():
    gdp = {"source": "fred", "mnemonic": "GDP", "remote_id": "GDP", "freq": "Q", "asof": "2026-04-01",
           "freshness_grace_days": 120, "staleness": "aging", "observation_date_semantics": "quarter_start", "publication_freshness": "unknown"}
    result = inspect_result("seiche", "data_health", {"provenance": [gdp]}, now=NOW)
    assert result["freshness"] == "unknown" and result["source_age_seconds"] is None
    assert result["clocks"][0]["reference_period_age_seconds"] > 0
    assert result["clocks"][0]["clock_role"] == "reference_period_start"
    envelope = {"source": "nyfed", "mnemonic": "nyfed_rates", "asof": None, "fetched_at": stamp(),
                "freshness_basis": "fetch clock only; this table contains heterogeneous observation dates"}
    result = inspect_result("seiche", "data_health", {"provenance": [envelope]}, now=NOW)
    assert result["freshness"] == "unknown"
    assert result["clocks"][0]["reason"] == "per_observation_clocks_not_exposed_by_health_envelope"


def test_undertow_stronger_context_expires_at_exact_native_boundary():
    public = venue_result(7200)
    assert inspect_result("undertow", "exit_cost", public, now=NOW)["freshness"] == "fresh"
    context = {"schema": "undertow.trade-safety-exit-context.v1", "status": "available",
               "rights": {"status": "approved", "scope": "derived_metadata_only", "valid_until": stamp(-1)},
               "clocks": {"venue_observation_at_by_venue": {venue: row["observed_at"] for venue, row in public["venue_freshness"].items()}}}
    result = inspect_result("undertow", "trade_safety_exit_context", context, now=NOW)
    assert result["freshness"] == "stale"
    assert result["source_reason_counts"]["native_observation_expiry_reached"] == 6
