from datetime import UTC, datetime
import json
import sqlite3

import pytest
from fastapi import Response
from seiche import api, store
from seiche.repository import PostgresMarketRepository, SQLiteMarketRepository


def test_coverage_batch_preserves_sealed_clocks_rights_faults_and_global_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "coverage.sqlite")
    base = SQLiteMarketRepository()
    cutoff = datetime(2026, 8, 9, tzinfo=UTC)
    for market, product, public in [("US-USD", "gauge", True), ("IN-INR", "gauge", False), ("GLOBAL", "tide", True)]:
        store.seal_market_snapshot(
            market_id=market, product=product, event_cutoff=cutoff,
            knowledge_cutoff=cutoff, calibration_id="test", evidence_eligible=False,
            payload={"visibility": api.PUBLIC_SNAPSHOT_VISIBILITY if public else "private",
                     "event_cutoff": cutoff.isoformat(), "knowledge_cutoff": cutoff.isoformat(),
                     "data_coverage": {"canonical_observations": ["public-evidence" if public else "private-evidence"]},
                     "status": "UNAVAILABLE", "faults": [], "stale_inputs": ["dated-input"],
                     "evidence_eligibility": {"eligible": False}},
        )
    # The same adapter name in another market must not leak into the US result.
    runs = [{"market_id": market, "adapter_id": "nyfed_rates", "status": "FAILED",
             "fault": "timeout", "finished_at": cutoff.isoformat(), "next_due": cutoff.isoformat()}
            for market in ("US-USD", "EA-EUR")]
    counts = {"US-USD": 3, "GLOBAL": 2, "OUTSIDE-REGISTRY": 7}
    monkeypatch.setattr(base, "latest_collector_runs", lambda market=None: [r for r in runs if market is None or r["market_id"] == market])
    monkeypatch.setattr(base, "forward_record_count", lambda market=None: sum(counts.values()) if market is None else counts.get(market, 0))
    monkeypatch.setattr(api, "get_repository", lambda: base)
    expected_response = Response()
    expected = api.coverage_v2(expected_response)
    calls = []

    class Batched:
        def load_latest_market_snapshots(self, markets, product):
            calls.append("snapshots")
            return {m: base.load_latest_market_snapshot(m, product) for m in markets}

        def latest_collector_runs(self):
            calls.append("runs")
            return runs

        def forward_record_counts(self):
            calls.append("counts")
            return counts

        def load_latest_market_snapshot(self, market, product):
            assert (market, product) == ("GLOBAL", "tide")
            calls.append("global")
            return base.load_latest_market_snapshot(market, product)

    monkeypatch.setattr(api, "get_repository", Batched)
    response = Response()
    actual = api.coverage_v2(response)
    assert actual == expected
    assert response.headers == expected_response.headers
    assert calls == ["snapshots", "runs", "counts", "global"]
    assert actual["forward_validation_records"] == 12
    assert "private-evidence" not in json.dumps(actual)
    us = next(m for m in actual["markets"] if m["market_id"] == "US-USD")
    assert us["event_cutoff"] == cutoff.isoformat()
    assert us["stale_inputs"] == ["dated-input"]
    assert len(us["faults"]) == 1
    assert us["forward_validation_records"] == 3
    assert next(m for m in actual["markets"] if m["market_id"] == "IN-INR")["latest_snapshot"] is None


def test_coverage_batch_failure_does_not_claim_empty_success(monkeypatch):
    class Broken:
        def load_latest_market_snapshots(self, markets, product):
            raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(api, "get_repository", Broken)
    with pytest.raises(RuntimeError, match="synthetic database failure"):
        api.coverage_v2(Response())


def test_grouped_forward_counts_use_one_read_and_preserve_unregistered_markets(monkeypatch):
    # This exact GROUP BY query has the same semantics in SQLite and PostgreSQL.
    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE forward_validation_records (market_id TEXT NOT NULL)")
    database.executemany("INSERT INTO forward_validation_records VALUES (?)", [("US-USD",), ("US-USD",), ("GLOBAL",), ("OUTSIDE-REGISTRY",)])
    calls = []

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, query):
            calls.append(query)
            return database.execute(query)

    repository = PostgresMarketRepository("unused")
    repository._initialized = True
    monkeypatch.setattr(repository, "_connect", Connection)
    before = database.total_changes
    assert repository.forward_record_counts() == {"US-USD": 2, "GLOBAL": 1, "OUTSIDE-REGISTRY": 1}
    assert len(calls) == 1
    assert database.total_changes == before
    database.execute("DELETE FROM forward_validation_records")
    assert repository.forward_record_counts() == {}
    database.close()
