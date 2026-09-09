from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from seiche import nyfed_history as history, store
from seiche.domain.observation import QualityState, StalenessState
from seiche.markets.registry import default_registry
from seiche.repository import SQLiteMarketRepository
from seiche.sources import canonical
from seiche.sources.canonical import (
    FetchedDocument,
    FunctionalCanonicalAdapter,
    PublicationTimePolicy,
)
from seiche.sources.official import parse_nyfed_rates, parse_nyfed_unsecured_rates


def _encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _row(family, day="2024-01-02"):
    return {
        "type": family,
        "effectiveDate": day,
        "percentRate": "3.65",
        "percentPercentile1": "3.55",
        "percentPercentile25": "3.62",
        "percentPercentile75": "3.68",
        "percentPercentile99": "3.75",
        "volumeInBillions": "102.5",
    }


def _reseal(root):
    entries = [
        {
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": _sha(path.read_bytes()),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "artifact-inventory.json"
    ]
    body = _encoded(
        {
            "schema": "seiche.private-artifact-inventory.v1",
            "private_root": str(root),
            "files": len(entries),
            "bytes_excluding_inventory_and_checksum": sum(e["bytes"] for e in entries),
            "entries": entries,
        }
    )
    (root / "artifact-inventory.json").write_bytes(body)
    return _sha(body)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    root.mkdir()
    requests = []
    documents = []
    for path, uri in history._DOCUMENTS.items():
        target = root / path
        target.parent.mkdir(exist_ok=True)
        body = ("retained official source " + uri).encode()
        target.write_bytes(body)
        documents.append(
            {
                "source_uri": uri,
                "path": str(target),
                "captured_at": "2026-09-08T07:00:00+00:00",
                "status": 200,
                "bytes": len(body),
                "sha256": _sha(body),
            }
        )
    (root / "documentation/sources.json").write_bytes(_encoded(documents))
    for category in ("secured", "unsecured"):
        rows = [
            _row(family)
            for family in (
                ("SOFR", "TGCR", "BGCR") if category == "secured" else ("EFFR", "OBFR")
            )
        ]
        if category == "secured":
            rows.append(
                {
                    "type": "SOFRAI",
                    "effectiveDate": "2024-01-02",
                    "average30day": "3.65123",
                    "average90day": "3.65456",
                    "average180day": "3.65789",
                    "index": "1.23456789",
                }
            )
        body = _encoded({"refRates": rows})
        adapter = "nyfed_rates" if category == "secured" else "nyfed_unsecured_rates"
        path = (
            root
            / f"raw/market=US-USD/source={adapter}/date=2026-09-08/{_sha(body)}.json"
        )
        path.parent.mkdir(parents=True)
        path.write_bytes(body)
        requests.append(
            {
                "category": category,
                "label": "annual",
                "start": "2024-01-01",
                "end": "2024-12-31",
                "url": f"https://markets.newyorkfed.org/api/rates/{category}/all/search.json?startDate=2024-01-01&endDate=2024-12-31",
                "raw_path": str(path),
                "captured_at": "2026-09-08T07:01:02.123456+00:00",
                "sha256": _sha(body),
                "bytes": len(body),
                "status_code": 200,
                "rows": len(rows),
                "request_key": f"annual-{category}-2024",
            }
        )
    (root / "annual-results.json").write_bytes(_encoded(requests))
    (root / "requests.jsonl").write_bytes(
        b"\n".join(_encoded(row) for row in requests) + b"\n"
    )
    (root / "summary.json").write_bytes(
        _encoded(
            {
                "canonical_observation_count": 34,
                "canonical_instruments": 34,
                "pre2016_effr_rows_retained_native_only": 0,
                "canonical_series": {
                    instrument: {"count": 1, "absent_within_native_benchmark_dates": 0}
                    for instrument in history.INSTRUMENT_IDS
                },
            }
        )
    )
    # These deliberately invalid bytes are hashed, never decoded or loaded.
    (root / "canonical").mkdir()
    (root / "canonical/do-not-import.jsonl").write_bytes(
        b"not a canonical JSONL record"
    )
    monkeypatch.setattr(history, "EXPECTED_ARCHIVE_OBSERVATIONS", 34)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "isolated.sqlite")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    return root, _reseal(root)


def test_archive_dry_run_reparses_raw_without_repository_or_state(archive, tmp_path):
    root, pin = archive
    state = tmp_path / "state/intent.json"
    proof = history.import_nyfed_history(
        root, inventory_sha256=pin, state_path=state, dry_run=True
    )
    assert proof["accepted_observations"] == proof["instruments"] == 34
    assert proof["canonical_jsonl_used"] is False
    assert proof["source_freshness_updated"] is False
    assert not state.parent.exists() and not store.DB_PATH.exists()


def test_ingestion_clock_null_publication_units_provenance_and_idempotency(
    archive, tmp_path
):
    root, pin = archive
    state = tmp_path / "state/intent.json"
    repo = SQLiteMarketRepository()
    before = datetime.now(UTC).replace(microsecond=0)
    first = history.import_nyfed_history(
        root, inventory_sha256=pin, repository=repo, state_path=state
    )
    rows = repo.load_observation_revisions("US-USD", datetime.now(UTC))
    assert len(rows) == 34
    assert all(
        row.source_publication_time is None and row.knowledge_time >= before
        for row in rows
    )
    assert all(
        row.staleness is StalenessState.UNKNOWN
        and row.quality is QualityState.PROVISIONAL
        for row in rows
    )
    by_id = {row.instrument_id: row for row in rows}
    assert by_id["US.NYFED.SOFR_MEDIAN"].value == Decimal("365")
    assert by_id["US.NYFED.EFFR_VOLUME"].value == Decimal("102500")
    assert by_id["US.NYFED.SOFR_AVERAGE_30D"].value == Decimal("365.123")
    assert by_id["US.NYFED.SOFR_INDEX"].value == Decimal("1.23456789")
    assert {row.event_time.date() for row in rows} == {date(2024, 1, 2)}
    assert all(
        capture["captured_at"] == "2026-09-08T07:01:02.123456+00:00"
        for capture in first["captures"]
    )
    assert all(
        any(
            row.revision_id.endswith(capture["sha256"]) for capture in first["captures"]
        )
        for row in rows
    )
    knowledge = datetime.fromisoformat(first["knowledge_time"])
    assert (
        repo.load_observations_as_of("US-USD", knowledge - timedelta(seconds=1)) == []
    )
    second = history.import_nyfed_history(
        root, inventory_sha256=pin, repository=repo, state_path=state
    )
    assert first["inserted_this_run"] == 34 and second["inserted_this_run"] == 0
    assert first["knowledge_time"] == second["knowledge_time"]
    assert (
        first["imported_record_hashes_sha256"]
        == second["imported_record_hashes_sha256"]
    )
    assert len(repo.load_observation_revisions("US-USD", datetime.now(UTC))) == 34


def test_existing_current_observation_is_not_replaced(archive, tmp_path):
    root, pin = archive
    repo = SQLiteMarketRepository()
    # Generate a valid projection in a separate temporary store, then seed one
    # established live vintage in the real target before the archive import.
    original = store.DB_PATH
    store.DB_PATH = tmp_path / "seed.sqlite"
    try:
        history.import_nyfed_history(
            root,
            inventory_sha256=pin,
            repository=repo,
            state_path=tmp_path / "seed.json",
        )
        template = repo.load_observation_revisions("US-USD", datetime.now(UTC))[0]
    finally:
        store.DB_PATH = original
    existing = replace(
        template,
        value=Decimal("999"),
        revision_id="live-capture",
        source_publication_time=template.event_time + timedelta(hours=13),
        quality=QualityState.VERIFIED,
        staleness=StalenessState.FRESH,
    )
    repo.save_observations([existing])
    receipt = history.import_nyfed_history(
        root, inventory_sha256=pin, repository=repo, state_path=tmp_path / "main.json"
    )
    assert receipt["imported_observations"] == 33
    assert receipt["preserved_existing_observations"] == 1
    rows = repo.load_observation_revisions("US-USD", datetime.now(UTC))
    assert existing in rows and len(rows) == 34


def test_partial_failed_write_restarts_with_same_clock(archive, tmp_path):
    root, pin = archive
    repo = SQLiteMarketRepository()

    class InterruptedRepository:
        load_observation_revisions = staticmethod(repo.load_observation_revisions)

        def save_missing_observations(self, observations):
            repo.save_missing_observations(observations[:7])
            raise RuntimeError("simulated process interruption after committed batch")

    state = tmp_path / "state.json"
    with pytest.raises(RuntimeError, match="interruption"):
        history.import_nyfed_history(
            root,
            inventory_sha256=pin,
            repository=InterruptedRepository(),
            state_path=state,
        )
    intent = json.loads(state.read_text())
    assert intent["status"] == "PREPARED"
    existing = repo.load_observation_revisions("US-USD", datetime.now(UTC))
    assert len(existing) == 7
    receipt = history.import_nyfed_history(
        root, inventory_sha256=pin, repository=repo, state_path=state
    )
    assert receipt["inserted_this_run"] == 27 and receipt["imported_observations"] == 34
    assert receipt["knowledge_time"] == intent["knowledge_time"]
    assert all(
        row in repo.load_observation_revisions("US-USD", datetime.now(UTC))
        for row in existing
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "raw_bytes",
        "inventory_pin",
        "traversal",
        "foreign_uri",
        "capture_clock",
        "receipt_hash",
        "missing_rights",
    ],
)
def test_manifest_raw_capture_and_scope_fail_closed_before_writes(
    archive, tmp_path, mutation
):
    root, pin = archive
    results = json.loads((root / "annual-results.json").read_bytes())
    if mutation == "raw_bytes":
        path = Path(results[0]["raw_path"])
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "inventory_pin":
        pin = "0" * 64
    elif mutation == "traversal":
        inventory = json.loads((root / "artifact-inventory.json").read_bytes())
        inventory["entries"][0]["path"] = "../outside.json"
        body = _encoded(inventory)
        (root / "artifact-inventory.json").write_bytes(body)
        pin = _sha(body)
    elif mutation == "missing_rights":
        (root / "documentation/terms-of-use.html").unlink()
        pin = _reseal(root)
    else:
        if mutation == "foreign_uri":
            results[0]["url"] = results[0]["url"].replace(
                "markets.newyorkfed.org", "example.com"
            )
        elif mutation == "capture_clock":
            results[0]["captured_at"] = "2026-09-08T07:01:02"
        else:
            results[0]["sha256"] = "0" * 64
        (root / "annual-results.json").write_bytes(_encoded(results))
        pin = _reseal(root)
    state = tmp_path / "not-created/state.json"
    with pytest.raises(ValueError):
        history.import_nyfed_history(
            root,
            inventory_sha256=pin,
            repository=SQLiteMarketRepository(),
            state_path=state,
        )
    assert not state.parent.exists() and not store.DB_PATH.exists()


def test_unknown_adapter_never_infers_or_substitutes_publication_on_revision(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "unknown.sqlite")
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("unknown publication must not call a calendar")

    monkeypatch.setattr(canonical, "_inferred_publication_time", forbidden)
    clock = datetime(2026, 9, 8, tzinfo=UTC)
    rows = [
        _row("SOFR"),
        {"type": "SOFRAI", "effectiveDate": "2024-01-03", "index": "1.1234"},
    ]

    async def fetch(_):
        return (
            FetchedDocument(
                "https://markets.newyorkfed.org",
                "application/json",
                _encoded({"refRates": rows}),
            ),
        )

    adapter = FunctionalCanonicalAdapter(
        pack=default_registry().get("US-USD"),
        adapter_id="nyfed_rates",
        source="nyfed_rates",
        fetcher=fetch,
        parser=lambda document: parse_nyfed_rates(
            document, publication_time_policy=PublicationTimePolicy.UNKNOWN
        ),
        repository=SQLiteMarketRepository(),
        clock=lambda: clock,
        historical_backfill=True,
    )
    first = asyncio.run(adapter.collect())
    store.save_observations(first.observations)
    assert all(row.source_publication_time is None for row in first.observations)
    rows[0]["percentRate"] = "4.11"
    clock += timedelta(days=1)
    second = asyncio.run(adapter.collect())
    assert all(row.source_publication_time is None for row in second.observations)
    median = next(
        row
        for row in second.observations
        if row.instrument_id == "US.NYFED.SOFR_MEDIAN"
    )
    assert median.value == Decimal("411") and median.quality is QualityState.REVISED
    assert median.knowledge_time == clock


def test_effr_mean_cutoff_is_native_parser_boundary_and_sofrai_default_unchanged():
    document = FetchedDocument(
        "https://markets.newyorkfed.org",
        "application/json",
        _encoded(
            {"refRates": [_row("EFFR", "2016-02-29"), _row("EFFR", "2016-03-01")]}
        ),
    )
    points = parse_nyfed_unsecured_rates(
        document, publication_time_policy=PublicationTimePolicy.UNKNOWN
    )
    assert len(points) == 6 and {point.event_time for point in points} == {
        date(2016, 3, 1)
    }
    averages = FetchedDocument(
        "https://markets.newyorkfed.org",
        "application/json",
        _encoded(
            {
                "refRates": [
                    {"type": "SOFRAI", "effectiveDate": "2024-01-03", "index": "1.1"}
                ]
            }
        ),
    )
    live = parse_nyfed_rates(averages)[0]
    unknown = parse_nyfed_rates(
        averages, publication_time_policy=PublicationTimePolicy.UNKNOWN
    )[0]
    assert live.source_publication_time == datetime(2024, 1, 3, 13, tzinfo=UTC)
    assert unknown.source_publication_time is None
    assert live.event_time == unknown.event_time == date(2024, 1, 3)
    assert (
        live.row_evidence == unknown.row_evidence
        and live.revision_id == unknown.revision_id
    )


def test_resume_rejects_changed_projection_before_any_database_write(
    archive, tmp_path, monkeypatch
):
    root, pin = archive
    repo = SQLiteMarketRepository()
    state = tmp_path / "state.json"
    history.import_nyfed_history(
        root, inventory_sha256=pin, repository=repo, state_path=state
    )
    saved = json.loads(state.read_text())
    saved["normalized_projection_sha256"] = "0" * 64
    state.write_text(json.dumps(saved))

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "changed projection must be rejected before repository writes"
        )

    monkeypatch.setattr(repo, "save_missing_observations", forbidden)
    with pytest.raises(ValueError, match="projection changed"):
        history.import_nyfed_history(
            root, inventory_sha256=pin, repository=repo, state_path=state
        )


def test_import_state_cannot_enter_archive_through_a_symlink(archive, tmp_path):
    root, pin = archive
    link = tmp_path / "archive-alias"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="immutable source archive"):
        history.import_nyfed_history(
            root,
            inventory_sha256=pin,
            repository=SQLiteMarketRepository(),
            state_path=link / "state.json",
        )
    assert not (root / "state.json").exists()
