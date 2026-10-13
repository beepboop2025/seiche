from datetime import datetime, timezone, timedelta
import json

import httpx
import pytest

from seiche_agent_network.core_freshness import audit, inspect, read_report

NOW = datetime(2026, 10, 13, 17, tzinfo=timezone.utc)


def bank_board():
    return {"as_of": "2026-10-13", "rows": [
        {"slug": "annual", "as_of": "2026-03-31", "freshness": {
            "cadence": "annual", "current_eligible": True}}],
        "excluded_stale": [{"slug": "old"}], "current_disclosures": {"rows": [
            {"slug": "quarterly", "period_end": "2026-06-30", "status": "observed",
             "freshness_limit_days": 183}]}}


def test_filings_keep_native_cadences_and_stale_corpus_exclusions():
    payload = bank_board()
    result = inspect("liquilens", payload, NOW)
    assert result["all_inspected_current_rows_fresh"]
    assert result["historical_corpus_rows_excluded"] == 1
    assert not result["all_sources_fresh"] and not result["training_eligible"]
    payload["rows"][0]["as_of"] = "2023-03-31"
    payload["rows"][0]["freshness"]["max_age_days"] = 999999
    assert inspect("liquilens", payload, NOW)["current_view_violation_count"] == 1
    payload["as_of"] = "2027-01-01"
    assert inspect("liquilens", payload, NOW)["status"] == "failed"


def test_restricted_undertow_values_cannot_supply_a_current_score():
    row = {"measure": "restricted", "source_status": "restricted", "stress_pctl": None}
    payload = {"schema": "undertow.board.v1", "provenance": {
        "generated_at": NOW.isoformat(), "input_health": {"packs": {
            "china.json": {"stale_series": {"old": {"asof": "2026-08-21"}}, "n_declared_faults": 2}}}},
        "segments": {"CN": {"measures": [row]}}}
    result = inspect("undertow", payload, NOW)
    assert result["status"] == "partial" and not result["all_sources_fresh"]
    row["stress_pctl"] = 0.1
    assert inspect("undertow", payload, NOW)["status"] == "failed"


def test_refetch_does_not_promote_stale_seiche_observations():
    payload = {"generated_at": NOW.isoformat(), "provenance": [{
        "source": "fred", "mnemonic": "DGS10", "freq": "D", "asof": "2026-10-08",
        "fetched_at": NOW.isoformat(), "staleness": "aging", "freshness_grace_days": 4}]}
    result = inspect("seiche", payload, NOW)
    assert result["source_counts"]["stale"] == 1
    assert result["current_source_ids"] == [] and result["not_checked_within_day"] == []


def test_crypto_observation_date_cannot_be_hidden_by_board_date():
    payload = {"available": True, "stale": False, "as_of": "2026-10-13", "keys": {
        "BTC-USD": {"series": {"dates": ["2026-09-01"]}}}}
    assert inspect("liquilens_crypto", payload, NOW)["status"] == "failed"
    payload["keys"]["BTC-USD"]["series"]["dates"] = ["2026-10-13"]
    result = inspect("liquilens_crypto", payload, NOW)
    assert result["all_inspected_current_rows_fresh"] and not result["all_sources_fresh"]


def test_rails_preview_does_not_become_decision_eligible():
    payload = {"available": True, "stale": False, "as_of": "2026-10-13",
               "rows": [{"symbol": "USDC"}], "decision_eligible": False, "display_only": True}
    result = inspect("liquilens_rails", payload, NOW)
    assert result["native_display_only"] and not result["native_decision_eligible"]
    assert not result["all_sources_fresh"] and not result["production_model_eligible"]


async def test_daily_audit_preserves_first_cut_and_fails_closed_on_bad_upstream(tmp_path, monkeypatch):
    output = tmp_path / "core.json"
    transport = httpx.MockTransport(lambda _: httpx.Response(503))
    report = await audit(output, transport=transport)
    assert all(r["status"] == "unavailable" for r in report["products"])
    history = next((tmp_path / "daily").glob("*.json"))
    before = history.read_bytes()
    await audit(output, transport=transport)
    assert history.read_bytes() == before
    assert history.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv("SEICHE_CORE_FRESHNESS_REPORT", str(output))
    assert read_report()["monitor_current"]
    report["checked_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    output.write_text(json.dumps(report))
    assert not read_report()["monitor_current"]
    report["checked_at"] = "broken"
    output.write_text(json.dumps(report))
    assert read_report()["products"] == []


@pytest.mark.parametrize("product", ["liquilens", "seiche", "undertow"])
def test_unqualified_empty_payload_is_not_ready(product):
    try:
        result = inspect(product, {}, NOW)
    except (ValueError, KeyError, TypeError):
        return
    assert result["all_sources_fresh"] is False
