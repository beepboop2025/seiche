"""The buttery-serving layer: pre-serialized gzip/ETag overview responses and
the stale-while-revalidate snapshot cache. A reader must never pay the
assembly bill, and a poller must never re-download bytes it already has."""

import asyncio
import gzip
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from seiche import ai, api, assemble, brief, mcp_server, public_view, repository


def _release_receipt(payload: dict, marker: str = "a") -> dict:
    return {
        "generated_at": payload["generated_at"],
        "producer": "seiche.markets.us_usd.materialize.seal_legacy_snapshot",
        "products": {
            "overview": {
                "snapshot_id": marker * 64,
                "forward_record_id": "b" * 64,
                "snapshot_row_sha256": "1" * 64,
            },
            "gauge": {
                "snapshot_id": "c" * 64,
                "forward_record_id": "d" * 64,
                "snapshot_row_sha256": "2" * 64,
            },
        },
    }


class _NoActiveRepository:
    @staticmethod
    def load_active_release_handoff():
        return None


@pytest.fixture()
def client(monkeypatch, fake_snap):
    async def fake_snapshot(force=False):
        return fake_snap

    monkeypatch.setattr(assemble, "snapshot", fake_snapshot)
    # the wire cache keys on payload identity — drop state from other tests
    monkeypatch.setitem(api._OVERVIEW_WIRE, "src", None)
    return TestClient(api.app)


# ---- /api/overview wire format ------------------------------------------------

def test_overview_gzips_when_accepted(client, fake_snap):
    r = client.get("/api/overview", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    # httpx transparently decodes; the wire headers tell the real story
    assert r.headers.get("content-encoding") == "gzip"
    assert r.headers["vary"] == "Accept-Encoding"
    assert r.json()["generated_at"] == fake_snap["generated_at"]


def test_overview_plain_when_gzip_not_accepted(client, fake_snap):
    r = client.get("/api/overview", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert r.json()["generated_at"] == fake_snap["generated_at"]


def test_overview_etag_roundtrip_304(client):
    first = client.get("/api/overview")
    etag = first.headers["etag"]
    assert etag.startswith('"')
    again = client.get("/api/overview", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["etag"] == etag
    assert again.content == b""


def test_overview_cache_control_allows_short_shared_caching(client):
    r = client.get("/api/overview")
    cc = r.headers["cache-control"]
    assert "public" in cc and "max-age=60" in cc and "stale-while-revalidate" in cc


def test_overview_wire_serialized_once_per_payload(client, fake_snap):
    client.get("/api/overview")
    body_first = api._OVERVIEW_WIRE["body"]
    client.get("/api/overview")
    assert api._OVERVIEW_WIRE["body"] is body_first  # same bytes object reused
    # and the gzip really is the body
    assert json.loads(gzip.decompress(api._OVERVIEW_WIRE["gz"])) == json.loads(body_first)


def test_overview_answers_head_for_monitors(client):
    warm = client.get("/api/overview")
    r = client.head("/api/overview")
    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["etag"] == warm.headers["etag"]
    assert "max-age=60" in r.headers["cache-control"]


def test_public_and_gauge_carry_cache_control(client):
    assert "max-age=60" in client.get("/api/public").headers["cache-control"]
    assert "max-age=60" in client.get("/api/gauge").headers["cache-control"]


def test_health_reads_only_the_completed_snapshot(client, monkeypatch, fake_snap):
    async def boom(force=False):
        raise AssertionError("health must never call snapshot or its builder")

    monkeypatch.setattr(assemble, "snapshot", boom)
    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: fake_snap)

    r = client.get("/api/health")

    expected = {
        "generated_at": fake_snap["generated_at"],
        "version": fake_snap["version"],
        "faults": fake_snap["faults"],
        "provenance": fake_snap["provenance"],
    }
    assert r.status_code == 200
    assert r.json() == expected
    assert r.content == json.dumps(
        expected, ensure_ascii=False, separators=(",", ":")
    ).encode()
    assert r.headers["cache-control"] == "no-store"


def test_health_cold_cache_is_immediately_unavailable(client, monkeypatch):
    async def boom(force=False):
        raise AssertionError("health must never start or join a snapshot build")

    monkeypatch.setattr(assemble, "snapshot", boom)
    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: None)

    r = client.get("/api/health")

    assert r.status_code == 503
    assert r.json() == {
        "status": "warming_or_unavailable",
        "version": assemble.VERSION_LABEL,
    }
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["retry-after"] == "10"
    assert "generated_at" not in r.json()
    assert "faults" not in r.json()
    assert "provenance" not in r.json()


def test_health_treats_a_stale_completed_snapshot_as_ready(
        client, clean_cache, monkeypatch, fake_snap):
    async def boom(force=False):
        raise AssertionError("health must not refresh a stale snapshot")

    monkeypatch.setattr(assemble, "snapshot", boom)
    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    assemble._cache.update(payload=fake_snap, at=0.0)

    r = client.get("/api/health")

    assert r.status_code == 200
    assert r.json()["generated_at"] == fake_snap["generated_at"]


def test_health_can_gate_on_a_snapshot_rebuilt_by_this_process(
        client, monkeypatch, fake_snap):
    monkeypatch.setattr(assemble, "cached_snapshot", lambda: fake_snap)
    monkeypatch.setattr(assemble, "cached_snapshot_was_rebuilt", lambda: False)
    monkeypatch.setattr(assemble, "cached_snapshot_release_handoff", lambda: None)

    available = client.get("/api/health")
    candidate = client.get("/api/health?require_rebuilt=true")

    assert available.status_code == 200
    assert candidate.status_code == 503
    assert candidate.json() == {
        "status": "rebuilding_from_last_known_good",
        "version": assemble.VERSION_LABEL,
        "serving_generated_at": fake_snap["generated_at"],
    }
    assert candidate.headers["retry-after"] == "10"

    monkeypatch.setattr(assemble, "cached_snapshot_was_rebuilt", lambda: True)
    evidence_incomplete = client.get("/api/health?require_rebuilt=true")
    assert evidence_incomplete.status_code == 503
    assert evidence_incomplete.json()["status"] == "rebuilt_without_market_evidence"

    monkeypatch.setattr(
        assemble,
        "cached_snapshot_release_handoff",
        lambda: {
            "producer_sha": "a" * 40,
            "activation_token": "b" * 64,
        },
    )
    ready = client.get("/api/health?require_rebuilt=true")
    assert ready.status_code == 200
    assert "release_candidate" not in ready.json()

    operator_ready = client.get("/api/internal/v1/release-health")
    assert operator_ready.status_code == 200
    assert operator_ready.json()["release_candidate"] == {
        "producer_sha": "a" * 40,
        "activation_token": "b" * 64,
    }
    assert "/api/internal/v1/release-health" not in api.app.openapi()["paths"]


def test_production_release_health_requires_cached_agent_room_readiness(
    client, monkeypatch
):
    monkeypatch.setattr(api, "_PROD", True)
    monkeypatch.setattr(mcp_server, "agent_room_release_ready", lambda: False)

    result = client.get("/api/internal/v1/release-health")

    assert result.status_code == 503
    assert result.json() == {
        "status": "agent_room_not_ready",
        "version": assemble.VERSION_LABEL,
    }
    assert result.headers["cache-control"] == "no-store"
    assert set(result.json()) == {"status", "version"}


def test_health_openapi_lists_every_runtime_unavailable_status():
    schema = api._public_openapi_document()["paths"]["/api/health"]["get"]
    statuses = schema["responses"]["503"]["content"]["application/json"][
        "schema"
    ]["properties"]["status"]["enum"]

    assert set(statuses) == {
        "agent_room_not_ready",
        "warming_or_unavailable",
        "rebuilding_from_last_known_good",
        "rebuilt_without_market_evidence",
    }


def test_asof_replay_is_cacheable_for_a_day(client, monkeypatch):
    async def fake_asof(date):
        return {"ok": True, "asof": date, "engines": {}}

    monkeypatch.setattr(assemble, "snapshot_asof", fake_asof)
    r = client.get("/api/asof/2025-03-14")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=86400"


# ---- stale-while-revalidate snapshot cache -------------------------------------

@pytest.fixture()
def clean_cache(monkeypatch):
    monkeypatch.setitem(assemble._cache, "payload", None)
    monkeypatch.setitem(assemble._cache, "at", 0.0)
    monkeypatch.setitem(assemble._cache, "source", None)
    monkeypatch.setitem(assemble._cache, "release_receipt", None)
    monkeypatch.setitem(assemble._cache, "release_handoff_id", None)
    monkeypatch.setitem(assemble._cache, "producer_sha", None)
    monkeypatch.setattr(assemble, "_process_release_sha", None)
    monkeypatch.setattr(assemble, "_refreshing", False)
    monkeypatch.setattr(assemble, "_build_generation", 0)
    monkeypatch.setattr(assemble, "_lock", asyncio.Lock())


def test_fresh_cache_served_without_building(clean_cache, monkeypatch):
    async def boom():
        raise AssertionError("a fresh cache must never rebuild")

    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    fresh = {"generated_at": "fresh"}
    assemble._cache.update(payload=fresh, at=time.time())
    assert asyncio.run(assemble.snapshot()) is fresh


def test_cached_snapshot_is_passive_for_empty_and_stale_cache(
        clean_cache, monkeypatch):
    async def boom():
        raise AssertionError("cached_snapshot must never build")

    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    assert assemble.cached_snapshot() is None

    stale = {"generated_at": "stale"}
    assemble._cache.update(payload=stale, at=0.0)

    assert assemble.cached_snapshot() is stale


def test_restart_restores_durable_snapshot_as_stale_without_building(
        clean_cache, monkeypatch, fake_snap, tmp_path):
    async def boom():
        raise AssertionError("restart hydration must not build")

    monkeypatch.setattr(assemble, "_build_snapshot", boom)
    monkeypatch.setattr(repository, "get_repository", _NoActiveRepository)
    monkeypatch.setattr(assemble.store, "load_blob", lambda key: fake_snap)
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", tmp_path / "missing.json")

    source = assemble.restore_cached_snapshot()

    assert source == "durable"
    assert assemble.cached_snapshot() is fake_snap
    assert assemble._cache["at"] == 0.0
    assert assemble.cached_snapshot_was_rebuilt() is False
    assert assemble.cached_snapshot_release_receipt() is None


def test_read_only_restore_never_opens_legacy_sqlite(
    clean_cache,
    monkeypatch,
    fake_snap,
    tmp_path,
):
    static = tmp_path / "overview.json"
    static.write_text(json.dumps(fake_snap))
    monkeypatch.setattr(repository, "get_repository", _NoActiveRepository)
    monkeypatch.setattr(
        assemble.store,
        "load_blob",
        lambda _key: pytest.fail("read-only hydration must not open legacy SQLite"),
    )
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", static)

    assert assemble.restore_cached_snapshot(read_only=True) == "static"
    assert assemble.cached_snapshot()["generated_at"] == fake_snap["generated_at"]


def test_read_only_restore_admits_exact_active_handoff_without_rebuild(
    clean_cache,
    monkeypatch,
    fake_snap,
    tmp_path,
):
    release_sha = "a" * 40
    receipt = _release_receipt(fake_snap)
    envelope = assemble._build_handoff(fake_snap, receipt, release_sha)

    class ActiveRepository:
        @staticmethod
        def load_active_release_handoff():
            return envelope

    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "cutover_candidate")
    monkeypatch.setattr(repository, "get_repository", ActiveRepository)
    monkeypatch.setattr(assemble, "capture_process_release_sha", lambda: release_sha)
    monkeypatch.setattr(
        assemble.store,
        "load_blob",
        lambda _key: pytest.fail("read-only hydration must not open legacy SQLite"),
    )
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", tmp_path / "missing.json")

    assert assemble.restore_cached_snapshot() == "prebuilt"
    assert assemble.cached_snapshot() is fake_snap
    assert assemble.cached_snapshot_was_rebuilt() is True
    assert assemble.cached_snapshot_release_receipt() == receipt
    assert assemble.cached_snapshot_release_handoff() == {
        "producer_sha": release_sha,
        "activation_token": envelope["handoff_id"],
    }


def test_preactivation_snapshot_never_rebuilds_on_ttl_or_force(
    clean_cache,
    monkeypatch,
    fake_snap,
):
    async def forbidden_build():
        raise AssertionError("pre-activation reads must not rebuild")

    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", "shadow")
    monkeypatch.setattr(assemble, "_build_snapshot", forbidden_build)
    assemble._cache.update(payload=fake_snap, at=0.0, source="prebuilt")

    assert asyncio.run(assemble.snapshot()) is fake_snap
    assert asyncio.run(assemble.snapshot(force=True)) is fake_snap


def test_restart_falls_back_to_ci_snapshot_when_durable_copy_is_invalid(
        clean_cache, monkeypatch, fake_snap, tmp_path):
    static = tmp_path / "overview.json"
    static.write_text(json.dumps(fake_snap))
    monkeypatch.setattr(repository, "get_repository", _NoActiveRepository)
    monkeypatch.setattr(assemble.store, "load_blob", lambda key: {"bad": True})
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", static)

    assert assemble.restore_cached_snapshot() == "static"
    assert assemble.cached_snapshot()["generated_at"] == fake_snap["generated_at"]


def test_restart_falls_back_to_ci_snapshot_when_durable_read_fails(
        clean_cache, monkeypatch, fake_snap, tmp_path):
    static = tmp_path / "overview.json"
    static.write_text(json.dumps(fake_snap))

    def unreadable(_key):
        raise OSError("cache database unavailable")

    monkeypatch.setattr(repository, "get_repository", _NoActiveRepository)
    monkeypatch.setattr(assemble.store, "load_blob", unreadable)
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", static)

    assert assemble.restore_cached_snapshot() == "static"
    assert assemble.cached_snapshot()["generated_at"] == fake_snap["generated_at"]


def test_release_evidence_receipt_requires_both_sealed_products(
        monkeypatch, fake_snap):
    from seiche.markets.us_usd import materialize

    monkeypatch.setattr(
        materialize,
        "seal_legacy_snapshot",
        lambda payload: {
            "overview": {
                "snapshot_id": "overview-snapshot",
                "forward_record_id": "overview-record",
                "snapshot_row_sha256": "overview-row",
            },
            "gauge": {
                "snapshot_id": "gauge-snapshot",
                "forward_record_id": "gauge-record",
                "snapshot_row_sha256": "gauge-row",
            },
        },
    )

    receipt = assemble._seal_release_evidence(fake_snap)

    assert receipt == {
        "generated_at": fake_snap["generated_at"],
        "producer": "seiche.markets.us_usd.materialize.seal_legacy_snapshot",
        "products": {
            "overview": {
                "snapshot_id": "overview-snapshot",
                "forward_record_id": "overview-record",
                "snapshot_row_sha256": "overview-row",
            },
            "gauge": {
                "snapshot_id": "gauge-snapshot",
                "forward_record_id": "gauge-record",
                "snapshot_row_sha256": "gauge-row",
            },
        },
    }

    monkeypatch.setattr(
        materialize,
        "seal_legacy_snapshot",
        lambda payload: {
            "overview": {
                "snapshot_id": "overview-snapshot",
                "forward_record_id": "overview-record",
                "snapshot_row_sha256": "overview-row",
            }
        },
    )
    assert assemble._seal_release_evidence(fake_snap) is None


def test_only_a_receipted_rebuild_stages_the_durable_handoff(
        clean_cache, monkeypatch, fake_snap):
    persisted = []

    def persist(payload, receipt):
        persisted.append((payload, receipt))
        return "e" * 64

    monkeypatch.setattr(
        assemble,
        "_persist_pending_snapshot",
        persist,
    )

    asyncio.run(assemble._publish_rebuilt_snapshot(fake_snap, None))
    assert assemble.cached_snapshot() is fake_snap
    assert assemble.cached_snapshot_was_rebuilt() is True
    assert assemble.cached_snapshot_release_receipt() is None
    assert persisted == []

    monkeypatch.setattr(assemble, "capture_process_release_sha", lambda: "f" * 40)
    receipt = _release_receipt(fake_snap)
    asyncio.run(assemble._publish_rebuilt_snapshot(fake_snap, receipt))
    assert assemble.cached_snapshot_release_receipt() == receipt
    assert assemble.cached_snapshot_release_handoff() == {
        "producer_sha": "f" * 40,
        "activation_token": "e" * 64,
    }
    assert persisted == [(fake_snap, receipt)]

    monkeypatch.setattr(
        assemble,
        "_persist_pending_snapshot",
        lambda payload, receipt: None,
    )
    asyncio.run(assemble._publish_rebuilt_snapshot(fake_snap, receipt))
    assert assemble.cached_snapshot_release_receipt() is None


@pytest.mark.parametrize(
    "case",
    [
        "composite", "tell", "stacker", "members_now", "navigator",
        "modelcourt", "court_ensemble", "backtest", "event_capture",
        "episodes", "calendar", "crunch_windows", "tell_missing_fields",
        "fault_row", "provenance_row",
    ],
)
def test_restart_rejects_nested_shapes_that_would_break_public_routes(
        fake_snap, case):
    payload = json.loads(json.dumps(fake_snap))
    mutations = {
        "composite": lambda p: p["engines"].__setitem__("composite", "bad"),
        "tell": lambda p: p["deep"].__setitem__("tell", "bad"),
        "stacker": lambda p: p["deep"].__setitem__("stacker", "bad"),
        "members_now": lambda p: p["deep"].__setitem__(
            "stacker", {"members_now": "bad"}),
        "navigator": lambda p: p.__setitem__("navigator", []),
        "modelcourt": lambda p: p["deep"].__setitem__("modelcourt", "bad"),
        "court_ensemble": lambda p: p["deep"].__setitem__(
            "modelcourt", {"ensemble": []}),
        "backtest": lambda p: p["deep"].__setitem__("backtest", "bad"),
        "event_capture": lambda p: p["deep"].__setitem__(
            "backtest", {"event_capture": []}),
        "episodes": lambda p: p["deep"].__setitem__(
            "backtest", {"episodes": ["bad"]}),
        "calendar": lambda p: p.__setitem__("calendar", []),
        "crunch_windows": lambda p: p.__setitem__(
            "calendar", {"crunch_windows": "bad"}),
        "tell_missing_fields": lambda p: p["deep"].__setitem__(
            "tell", {"ok": True}),
        "fault_row": lambda p: p.__setitem__("faults", ["bad"]),
        "provenance_row": lambda p: p.__setitem__("provenance", ["bad"]),
    }
    mutations[case](payload)

    assert assemble._servable_snapshot(payload) is False


def test_optional_legacy_calendar_none_still_renders_a_brief(fake_snap):
    payload = json.loads(json.dumps(fake_snap))
    payload["calendar"] = None

    assert assemble._servable_snapshot(payload) is True
    assert brief.render_markdown(payload).startswith("# SEICHE BRIEF")


@pytest.mark.parametrize("legacy_backtest", [None, {"status": "UNVERIFIED"}])
def test_optional_legacy_or_unverified_backtest_is_servable(
        fake_snap, legacy_backtest):
    payload = json.loads(json.dumps(fake_snap))
    if legacy_backtest is None:
        payload["deep"].pop("backtest", None)
    else:
        payload["deep"]["backtest"] = legacy_backtest

    assert assemble._servable_snapshot(payload) is True


@pytest.mark.parametrize(
    ("section", "engine", "consumer"),
    [
        ("engines", "sonar", brief.render_markdown),
        ("deep", "ml", ai.context_pack),
    ],
)
def test_optional_null_engine_blocks_are_safe_for_durable_consumers(
        section, engine, consumer):
    payload = json.loads(assemble.STATIC_SNAPSHOT_PATH.read_text())
    payload[section][engine] = None

    assert assemble._servable_snapshot(payload) is True
    assert consumer(payload)


def test_packaged_static_snapshot_satisfies_the_boot_contract(monkeypatch):
    assert assemble.STATIC_SNAPSHOT_PATH.parent == Path(
        assemble.__file__
    ).resolve().parent
    payload = json.loads(assemble.STATIC_SNAPSHOT_PATH.read_text())

    assert assemble._servable_snapshot(payload) is True
    assert public_view.public_payload(payload)["schema"] == "seiche.public.v2"
    assert ai.context_pack(payload)["provenance_staleness"] == {"stale": 1}
    assert "all sources and engines live" not in brief.render_markdown(payload)

    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: payload)
    assert mcp_server.tool_stress_now({}, True)["schema"] == "seiche.public.v2"
    assert mcp_server.tool_proof({}, True)["event_capture"]["n_events"] == 13

    async def packaged_snapshot(force=False):
        return payload

    monkeypatch.setattr(assemble, "snapshot", packaged_snapshot)
    client = TestClient(api.app)
    assert client.get("/api/public").status_code == 200
    assert client.get("/api/gauge").status_code == 200


def test_completed_snapshot_handoff_is_staged_with_digest(monkeypatch, fake_snap):
    release_sha = "a" * 40
    staged = []

    class FakeRepository:
        @staticmethod
        def stage_release_handoff(handoff_id, producer_sha, envelope):
            staged.append((handoff_id, producer_sha, envelope))

        @staticmethod
        def load_active_release_handoff():
            return None

    monkeypatch.setattr(repository, "get_repository", FakeRepository)
    monkeypatch.setattr(assemble, "capture_process_release_sha", lambda: release_sha)

    receipt = _release_receipt(fake_snap)
    handoff_id = assemble._persist_pending_snapshot(fake_snap, receipt)

    assert isinstance(handoff_id, str) and len(handoff_id) == 64
    assert len(staged) == 1
    saved_id, saved_sha, envelope = staged[0]
    assert saved_id == handoff_id
    assert saved_sha == release_sha
    assert envelope["schema"] == assemble.SNAPSHOT_HANDOFF_SCHEMA
    assert envelope["producer_sha"] == release_sha
    assert envelope["payload"] is fake_snap
    assert envelope["release_receipt"] == receipt
    assert envelope["payload_sha256"] == assemble._snapshot_digest(fake_snap)
    assert envelope["handoff_id"] == handoff_id

    def fail(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(FakeRepository, "stage_release_handoff", fail)
    assert assemble._persist_pending_snapshot(fake_snap, receipt) is None


def test_controller_activation_is_bound_to_exact_payload_and_receipt(
        monkeypatch, fake_snap):
    from seiche.markets.us_usd import materialize

    release_sha = "a" * 40
    receipt = _release_receipt(fake_snap)
    envelope = assemble._build_handoff(fake_snap, receipt, release_sha)
    activated = []

    class FakeRepository:
        current = envelope

        @classmethod
        def load_release_handoff(cls, handoff_id):
            return cls.current

        @staticmethod
        def activate_release_handoff(handoff_id, producer_sha, snapshot_bindings):
            activated.append((handoff_id, producer_sha, tuple(snapshot_bindings)))

    monkeypatch.setattr(repository, "get_repository", FakeRepository)
    monkeypatch.setattr(
        materialize,
        "verify_release_receipt",
        lambda repo, value: tuple(
            (
                product,
                value["products"][product]["snapshot_id"],
                value["products"][product]["forward_record_id"],
                value["products"][product]["snapshot_row_sha256"],
            )
            for product in ("overview", "gauge")
        ),
    )
    legacy = []
    monkeypatch.setattr(
        assemble.store,
        "save_blob",
        lambda key, value: legacy.append((key, value)),
    )

    token = envelope["handoff_id"]
    assert assemble.verify_pending_snapshot(release_sha, token) is True
    assert assemble.activate_pending_snapshot(release_sha, token) is True
    assert activated == [(
        token,
        release_sha,
        (
            (
                "overview",
                receipt["products"]["overview"]["snapshot_id"],
                receipt["products"]["overview"]["forward_record_id"],
                receipt["products"]["overview"]["snapshot_row_sha256"],
            ),
            (
                "gauge",
                receipt["products"]["gauge"]["snapshot_id"],
                receipt["products"]["gauge"]["forward_record_id"],
                receipt["products"]["gauge"]["snapshot_row_sha256"],
            ),
        ),
    )]
    assert legacy == [(assemble.LAST_GOOD_SNAPSHOT_KEY, fake_snap)]

    tampered = json.loads(json.dumps(envelope))
    tampered["release_receipt"]["products"]["gauge"][
        "forward_record_id"
    ] = "e" * 64
    FakeRepository.current = tampered
    activated.clear()
    assert assemble.verify_pending_snapshot(release_sha, token) is False
    assert assemble.activate_pending_snapshot(release_sha, token) is False
    assert activated == []

    newer = json.loads(json.dumps(fake_snap))
    newer["generated_at"] = "2026-07-10T00:15:00Z"
    newer_envelope = assemble._build_handoff(
        newer, _release_receipt(newer, "e"), release_sha
    )
    FakeRepository.current = newer_envelope
    assert assemble.activate_pending_snapshot(release_sha, token) is False
    assert activated == []


def test_accepted_release_keeps_lkg_fresh_across_later_rebuild_and_restart(
        clean_cache, monkeypatch, fake_snap, tmp_path):
    from seiche.markets.us_usd import materialize

    release_sha = "b" * 40
    legacy = {}

    class FakeRepository:
        handoffs = {}
        active_id = None

        @classmethod
        def stage_release_handoff(cls, handoff_id, producer_sha, envelope):
            cls.handoffs[handoff_id] = envelope

        @classmethod
        def load_release_handoff(cls, handoff_id):
            return cls.handoffs.get(handoff_id)

        @classmethod
        def load_active_release_handoff(cls):
            return cls.handoffs.get(cls.active_id)

        @classmethod
        def activate_release_handoff(
                cls, handoff_id, producer_sha, snapshot_bindings):
            cls.active_id = handoff_id

    monkeypatch.setattr(repository, "get_repository", FakeRepository)
    monkeypatch.setattr(assemble, "capture_process_release_sha", lambda: release_sha)
    monkeypatch.setattr(
        assemble.store,
        "save_blob",
        lambda key, payload: legacy.__setitem__(key, payload),
    )
    monkeypatch.setattr(
        assemble.store, "load_blob", lambda key, ttl_minutes=None: legacy.get(key)
    )
    monkeypatch.setattr(
        materialize,
        "verify_release_receipt",
        lambda repo, value: tuple(
            (
                product,
                value["products"][product]["snapshot_id"],
                value["products"][product]["forward_record_id"],
                value["products"][product]["snapshot_row_sha256"],
            )
            for product in ("overview", "gauge")
        ),
    )

    first = json.loads(json.dumps(fake_snap))
    first_receipt = _release_receipt(first)
    first_token = assemble._persist_pending_snapshot(first, first_receipt)
    assert first_token is not None
    assert assemble.LAST_GOOD_SNAPSHOT_KEY not in legacy

    assert assemble.activate_pending_snapshot(release_sha, first_token) is True
    assert legacy[assemble.LAST_GOOD_SNAPSHOT_KEY] == first

    later = json.loads(json.dumps(fake_snap))
    later["generated_at"] = "2026-07-10T00:15:00Z"
    later_receipt = _release_receipt(later, "e")
    later_token = assemble._persist_pending_snapshot(later, later_receipt)
    assert later_token is not None and later_token != first_token
    assert FakeRepository.active_id == later_token
    assert legacy[assemble.LAST_GOOD_SNAPSHOT_KEY] == later

    assemble._cache.update(
        payload=None,
        at=0.0,
        source=None,
        release_receipt=None,
        release_handoff_id=None,
        producer_sha=None,
    )
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", tmp_path / "missing.json")
    assert assemble.restore_cached_snapshot() == "durable"
    assert assemble.cached_snapshot()["generated_at"] == later["generated_at"]


def test_process_release_sha_is_immutable_after_first_capture(monkeypatch):
    resolved = iter(("a" * 40, "b" * 40))
    monkeypatch.setattr(assemble, "_process_release_sha", None)
    monkeypatch.setattr(assemble, "_release_sha", lambda: next(resolved))

    assert assemble.capture_process_release_sha() == "a" * 40
    assert assemble.capture_process_release_sha() == "a" * 40


def test_malformed_explicit_release_sha_fails_closed(monkeypatch):
    monkeypatch.setenv("SEICHE_RELEASE_SHA", "not-a-commit")

    with pytest.raises(ValueError, match="canonical commit SHA"):
        assemble._release_sha()


def test_explicit_release_sha_must_match_checkout_head(monkeypatch):
    checkout_sha = "a" * 40
    monkeypatch.setattr(
        assemble.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=f"{checkout_sha}\n"),
    )
    monkeypatch.setenv("SEICHE_RELEASE_SHA", checkout_sha)

    assert assemble._release_sha() == checkout_sha

    monkeypatch.setenv("SEICHE_RELEASE_SHA", "b" * 40)
    with pytest.raises(ValueError, match="does not match the checkout HEAD"):
        assemble._release_sha()


def test_production_lifespan_restores_before_background_refresh(monkeypatch):
    events = []

    def capture_identity():
        events.append("identity")
        return "a" * 40

    def restore():
        events.append("restore")
        return "durable"

    async def refresh_forever():
        events.append("refresh")
        await asyncio.Event().wait()

    monkeypatch.setattr(api, "_PROD", True)
    monkeypatch.setattr(
        mcp_server,
        "initialize_agent_room_readiness",
        lambda: events.append("agent-room"),
    )
    monkeypatch.setattr(assemble, "capture_process_release_sha", capture_identity)
    monkeypatch.setattr(assemble, "restore_cached_snapshot", restore)
    monkeypatch.setattr(api, "_keep_warm", refresh_forever)

    async def scenario():
        async with api._lifespan(api.app):
            for _ in range(20):
                if "refresh" in events:
                    break
                await asyncio.sleep(0)
            assert events == ["agent-room", "identity", "restore", "refresh"]

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["shadow", "cutover_candidate"])
def test_preactivation_lifespan_never_starts_background_writer(monkeypatch, mode):
    events = []

    monkeypatch.setattr(api, "_PROD", True)
    monkeypatch.setenv("SEICHE_RAILWAY_STATEFUL_MODE", mode)
    monkeypatch.setattr(
        mcp_server,
        "initialize_agent_room_readiness",
        lambda: events.append("agent-room-read-only"),
    )
    monkeypatch.setattr(
        assemble,
        "capture_process_release_sha",
        lambda: events.append("identity") or "a" * 40,
    )
    def restore(*, read_only=False):
        assert read_only is True
        events.append("restore")
        return "durable"

    monkeypatch.setattr(assemble, "restore_cached_snapshot", restore)

    async def forbidden_refresh():
        events.append("unexpected-refresh")

    monkeypatch.setattr(api, "_keep_warm", forbidden_refresh)

    async def scenario():
        async with api._lifespan(api.app):
            await asyncio.sleep(0)
            assert events == ["agent-room-read-only", "identity", "restore"]

    asyncio.run(scenario())


def test_production_lifespan_aborts_before_release_identity_when_room_audit_fails(
    monkeypatch,
):
    events = []

    def fail_audit():
        events.append("agent-room-failed")
        raise ValueError("private storage detail")

    monkeypatch.setattr(api, "_PROD", True)
    monkeypatch.setattr(mcp_server, "initialize_agent_room_readiness", fail_audit)
    monkeypatch.setattr(
        assemble,
        "capture_process_release_sha",
        lambda: events.append("identity"),
    )
    monkeypatch.setattr(api, "_keep_warm", lambda: events.append("refresh"))

    async def scenario():
        with pytest.raises(ValueError, match="private storage detail"):
            async with api._lifespan(api.app):
                pytest.fail("failed room audit must not start the application")

    asyncio.run(scenario())
    assert events == ["agent-room-failed"]


def test_keep_warm_budgets_coalesced_snapshot_refresh_every_cycle(monkeypatch):
    refresh_calls = []
    sleep_intervals = []
    clock = iter((100.0, 100.0, 200.0, 200.0))

    async def refresh_snapshot():
        refresh_calls.append("refresh")

    async def stop_after_two_cycles(delay):
        sleep_intervals.append(delay)
        if len(sleep_intervals) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(assemble, "refresh_snapshot", refresh_snapshot)
    monkeypatch.setattr(api, "monotonic", lambda: next(clock))
    monkeypatch.setattr(api.asyncio, "sleep", stop_after_two_cycles)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(api._keep_warm())

    assert refresh_calls == ["refresh", "refresh"]
    assert sleep_intervals == [api._REFRESH_BUILD_BUDGET_S] * 2


def test_stale_cache_served_instantly_then_refreshed_once(clean_cache, monkeypatch):
    calls = []

    async def fake_build():
        calls.append(1)
        payload = {"generated_at": "rebuilt"}
        assemble._cache.update(payload=payload, at=time.time())
        return payload

    monkeypatch.setattr(assemble, "_build_snapshot", fake_build)
    stale = {"generated_at": "stale"}
    assemble._cache.update(payload=stale, at=time.time() - assemble.CACHE_MIN * 60 - 1)

    async def scenario():
        got = await assemble.snapshot()          # must not block on the rebuild
        second = await assemble.snapshot()       # while refreshing: still stale, no 2nd task
        for _ in range(100):                     # let the background refresh land
            if calls:
                break
            await asyncio.sleep(0.01)
        after = await assemble.snapshot()
        return got, second, after

    got, second, after = asyncio.run(scenario())
    assert got is stale and second is stale
    assert calls == [1], "exactly one background rebuild"
    assert after["generated_at"] == "rebuilt"


def test_cold_cache_builds_inline(clean_cache, monkeypatch):
    async def fake_build():
        payload = {"generated_at": "cold-built"}
        assemble._cache.update(payload=payload, at=time.time())
        return payload

    monkeypatch.setattr(assemble, "_build_snapshot", fake_build)
    assert asyncio.run(assemble.snapshot())["generated_at"] == "cold-built"


@pytest.mark.asyncio
async def test_scheduled_refresh_coalesces_with_build_published_while_waiting(
    clean_cache, monkeypatch
):
    first_published = asyncio.Event()
    release_first = asyncio.Event()
    builds: list[int] = []

    async def fake_build():
        build_number = len(builds) + 1
        builds.append(build_number)
        payload = {"generated_at": f"build-{build_number}"}
        assemble._cache.update(payload=payload, at=time.time())
        if build_number == 1:
            # Model the real post-publication handoff-persistence await: the
            # scheduler begins after memory changes but before the build epoch
            # is complete and the lock is released.
            first_published.set()
            await release_first.wait()
        assemble._build_generation += 1
        return payload

    monkeypatch.setattr(assemble, "_build_snapshot", fake_build)

    forced = asyncio.create_task(assemble.snapshot(force=True))
    await first_published.wait()
    scheduled = asyncio.create_task(assemble.refresh_snapshot())
    await asyncio.sleep(0)
    release_first.set()

    forced_payload, scheduled_payload = await asyncio.gather(forced, scheduled)
    assert builds == [1]
    assert scheduled_payload is forced_payload


@pytest.mark.asyncio
async def test_scheduled_refresh_rebuilds_after_competing_build_fails(
    clean_cache, monkeypatch
):
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    builds: list[int] = []

    async def fake_build():
        build_number = len(builds) + 1
        builds.append(build_number)
        if build_number == 1:
            first_started.set()
            await release_first.wait()
            raise RuntimeError("synthetic competing build failure")
        payload = {"generated_at": f"build-{build_number}"}
        assemble._cache.update(payload=payload, at=time.time())
        assemble._build_generation += 1
        return payload

    monkeypatch.setattr(assemble, "_build_snapshot", fake_build)

    forced = asyncio.create_task(assemble.snapshot(force=True))
    await first_started.wait()
    scheduled = asyncio.create_task(assemble.refresh_snapshot())
    await asyncio.sleep(0)
    release_first.set()

    forced_result, scheduled_result = await asyncio.gather(
        forced, scheduled, return_exceptions=True
    )
    assert isinstance(forced_result, RuntimeError)
    assert builds == [1, 2]
    assert scheduled_result == {"generated_at": "build-2"}


@pytest.mark.asyncio
async def test_scheduled_refresh_does_not_coalesce_with_stale_restore(
    clean_cache, monkeypatch
):
    builds: list[int] = []

    async def fake_build():
        builds.append(1)
        payload = {"generated_at": "rebuilt"}
        assemble._cache.update(payload=payload, at=time.time())
        assemble._build_generation += 1
        return payload

    monkeypatch.setattr(assemble, "_build_snapshot", fake_build)

    async with assemble._lock:
        scheduled = asyncio.create_task(assemble.refresh_snapshot())
        await asyncio.sleep(0)
        assemble._cache.update(payload={"generated_at": "restored"}, at=0.0)

    assert await scheduled == {"generated_at": "rebuilt"}
    assert builds == [1]


@pytest.mark.asyncio
async def test_sequential_forced_refreshes_still_rebuild(clean_cache, monkeypatch):
    builds: list[int] = []

    async def fake_build():
        build_number = len(builds) + 1
        builds.append(build_number)
        payload = {"generated_at": f"build-{build_number}"}
        assemble._cache.update(payload=payload, at=time.time())
        assemble._build_generation += 1
        return payload

    monkeypatch.setattr(assemble, "_build_snapshot", fake_build)

    first = await assemble.snapshot(force=True)
    second = await assemble.snapshot(force=True)
    assert builds == [1, 2]
    assert second is not first
