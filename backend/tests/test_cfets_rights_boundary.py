"""Rights boundary for legacy CFETS inputs and completed snapshots.

The durable cache is intentionally not purged.  These tests prove that its
restricted observations are unreachable from live engines, restart hydration,
REST, and MCP until a redistribution-safe source contract exists.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import inspect
import json
from pathlib import Path

import pandas as pd
import pytest
import httpx
from fastapi.testclient import TestClient

from seiche import api, assemble, context_views, mcp_server, repository
from seiche.config import ALL_SERIES, SeriesSpec
from seiche.engines import harbors as harbors_engine
from seiche.sources import chinamoney
from seiche.sources.base import Series, SourceFault


def _series(
    mnemonic: str,
    *,
    source: str,
    remote_id: str | None = None,
    values: pd.Series | None = None,
) -> Series:
    points = values if values is not None else pd.Series(
        [1.0, 1.1], index=pd.date_range("2026-08-20", periods=2, freq="D")
    )
    return Series(
        mnemonic=mnemonic,
        source=source,
        remote_id=remote_id or f"test:{mnemonic}",
        label=mnemonic,
        unit="%",
        freq="D",
        fetched_at="2026-08-22T00:00:00+00:00",
        points=points,
    )


def _source_pack() -> dict:
    cny = _series("CNY", source="fred")
    shibor = _series("SHIBOR_ON", source="chinamoney")
    fdr007 = _series("CN_FDR007", source="palimpsest")
    parity = _series("CN_PARITY", source="palimpsest")
    fear = _series(
        "PALIMPSEST_FEAR",
        source="palimpsest",
        remote_id="ddti-history.jsonl:top_threat",
    )
    new = _series(
        "PALIMPSEST_NEW",
        source="palimpsest",
        remote_id="ddti-history.jsonl:n_new",
    )
    gfi = _series(
        "PALIMPSEST_GFI",
        source="palimpsest",
        remote_id="history.jsonl:gfi",
    )
    return {
        "fred": {"CNY": cny},
        "chinamoney": {"SHIBOR_ON": shibor},
        "palimpsest": {
            "fetched_at": "2026-08-22T00:00:00+00:00",
            "series": {
                "CN_FDR007": fdr007,
                "CN_PARITY": parity,
                "PALIMPSEST_FEAR": fear,
                "PALIMPSEST_NEW": new,
                "PALIMPSEST_GFI": gfi,
            },
            "latest": {"fear": 42.0},
        },
    }


def _reset_cache() -> None:
    assemble._cache.update(
        at=0.0,
        payload=None,
        source=None,
        release_receipt=None,
        release_handoff_id=None,
        producer_sha=None,
    )


@pytest.fixture(autouse=True)
def _preserve_process_caches():
    assemble_cache = dict(assemble._cache)
    overview_wire = dict(api._OVERVIEW_WIRE)
    yield
    assemble._cache.clear()
    assemble._cache.update(assemble_cache)
    api._OVERVIEW_WIRE.clear()
    api._OVERVIEW_WIRE.update(overview_wire)


def test_rights_projection_is_non_mutating_and_preserves_h10_cny(monkeypatch) -> None:
    raw = _source_pack()
    monkeypatch.setattr(assemble.store, "load_series", lambda _mnemonic: None)

    eligible = assemble._rights_eligible_sources(raw)

    assert "chinamoney" not in eligible
    assert eligible["fred"]["CNY"] is raw["fred"]["CNY"]
    assert set(eligible["palimpsest"]["series"]) == {
        "PALIMPSEST_FEAR",
        "PALIMPSEST_NEW",
        "PALIMPSEST_GFI",
    }
    assert set(raw["palimpsest"]["series"]) == {
        "CN_FDR007",
        "CN_PARITY",
        "PALIMPSEST_FEAR",
        "PALIMPSEST_NEW",
        "PALIMPSEST_GFI",
    }
    assert "chinamoney" in raw

    provenance = assemble._provenance(raw)
    assert {row["mnemonic"] for row in provenance} == {
        "CNY",
        "PALIMPSEST_FEAR",
        "PALIMPSEST_NEW",
        "PALIMPSEST_GFI",
    }
    assert not assemble._snapshot_contains_restricted_cfets(provenance)


def test_palimpsest_boundary_is_a_strict_native_series_allowlist() -> None:
    raw = _source_pack()
    series = raw["palimpsest"]["series"]
    series["PALIMPSEST_FEAR"] = _series(
        "PALIMPSEST_FEAR",
        source="palimpsest",
        remote_id="china-econ-history.jsonl:fdr007",
    )
    series["PALIMPSEST_ALIAS"] = _series(
        "PALIMPSEST_NEW",
        source="palimpsest",
        remote_id="ddti-history.jsonl:n_new",
    )
    series["PALIMPSEST_FUTURE"] = _series(
        "PALIMPSEST_FUTURE",
        source="palimpsest",
        remote_id="future.jsonl:value",
    )

    eligible = assemble._rights_eligible_sources(raw)

    assert set(eligible["palimpsest"]["series"]) == {
        "PALIMPSEST_NEW",
        "PALIMPSEST_GFI",
    }
    assert "PALIMPSEST_FEAR" in raw["palimpsest"]["series"]
    assert "PALIMPSEST_ALIAS" in raw["palimpsest"]["series"]
    assert "PALIMPSEST_FUTURE" in raw["palimpsest"]["series"]


def test_legacy_gather_no_longer_schedules_chinamoney() -> None:
    gather_source = inspect.getsource(assemble._gather_sources).casefold()

    assert "chinamoney" not in gather_source
    assert "chinamoney" not in assemble.__dict__


@pytest.mark.asyncio
async def test_legacy_chinamoney_entrypoints_are_offline_and_uncatalogued() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, request=request)

    spec = SeriesSpec(
        "SHIBOR_ON",
        "chinamoney",
        "shibor:ON",
        "retired SHIBOR overnight",
        "%",
        "D",
        360,
    )
    faults: list[dict] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SourceFault, match="legacy direct ChinaMoney"):
            await chinamoney.fetch_series(client, spec)
        assert await chinamoney.fetch_many(client, ["SHIBOR_ON"], faults) == {}

    assert requests == []
    assert faults and "retired" in faults[0]["detail"]
    assert not ({"SHIBOR_ON", "CN_FDR007", "CN_PARITY"} & set(ALL_SERIES))


def test_hermes_harbors_watch_fails_closed_on_cfets_values() -> None:
    root = Path(__file__).resolve().parents[2]
    skill = (
        root / "integrations" / "hermes" / "skills" / "seiche-harbors-watch" / "SKILL.md"
    ).read_text()

    assert "https://palimpsest.info/readings/china-econ-latest.json" not in skill
    assert "https://palimpsest.info/readings/ddti-latest.json" in skill
    assert "Never fetch or publish `china-econ-*`" in skill
    assert "H.10 CNY FX remains live" in skill
    assert "Absence never means calm" in skill


def test_public_copy_describes_china_as_h10_fx_only() -> None:
    root = Path(__file__).resolve().parents[2]
    frontend = (root / "frontend" / "src" / "App.tsx").read_text()
    harbors = (root / "backend" / "seiche" / "engines" / "harbors.py").read_text()
    basins = (root / "backend" / "seiche" / "engines" / "basins.py").read_text()
    ledger = (root / "docs" / "DATA_COVERAGE_LEDGER.md").read_text()

    assert "already reads its keyless CFETS feed" not in frontend
    assert "Federal Reserve H.10 CNY FX leg" in frontend
    assert "SHIBOR history accrues locally" not in harbors
    assert "China is FX-only from Federal Reserve H.10" in harbors
    assert "z quarantined while local history accrues" not in basins
    assert "no local China rate node is used" in basins
    assert "China percentile history is still accruing" not in ledger
    assert "China H.10 `CNY` FX only" in ledger


def test_engine_bindings_keep_cny_fx_but_have_no_china_rate_path(monkeypatch) -> None:
    raw = _source_pack()
    captured: dict[str, object] = {}

    def capture_basins(**kwargs):
        captured["basins"] = kwargs
        return {"ok": False, "reason": "captured"}

    def capture_harbors(harbors, effr):
        captured["harbors"] = harbors
        captured["harbors_effr"] = effr
        return {"ok": False, "reason": "captured"}

    def capture_spillover(series_map):
        captured["spillover"] = series_map
        return {"ok": False, "reason": "captured"}

    def capture_estuary(**kwargs):
        captured["estuary"] = kwargs
        return {"ok": False, "reason": "captured"}

    def capture_sonar(series_map):
        captured["sonar"] = series_map
        return {"ok": False, "reason": "captured"}

    monkeypatch.setattr(assemble.eng_basins, "analyze", capture_basins)
    monkeypatch.setattr(assemble.eng_harbors, "analyze", capture_harbors)
    monkeypatch.setattr(assemble.eng_spillover, "analyze", capture_spillover)
    monkeypatch.setattr(assemble.eng_estuary, "analyze", capture_estuary)
    monkeypatch.setattr(assemble.eng_sonar, "sweep", capture_sonar)
    monkeypatch.setattr(assemble.store, "load_series", lambda _mnemonic: None)

    assemble._run_engines(raw, assemble._derived(raw), [])

    basin_inputs = captured["basins"]
    assert isinstance(basin_inputs, dict)
    assert "shibor_on" not in basin_inputs
    pd.testing.assert_series_equal(
        basin_inputs["cny"], raw["fred"]["CNY"].points
    )

    harbor_inputs = captured["harbors"]
    assert isinstance(harbor_inputs, dict)
    china_harbor = harbor_inputs["CHINA"]
    assert set(china_harbor) == {"cadence", "fx", "fx_label"}
    pd.testing.assert_series_equal(china_harbor["fx"], raw["fred"]["CNY"].points)

    spillover_inputs = captured["spillover"]
    assert isinstance(spillover_inputs, dict)
    assert "CN·rate" not in spillover_inputs
    pd.testing.assert_series_equal(
        spillover_inputs["CNY·fx"], raw["fred"]["CNY"].points
    )

    estuary_inputs = captured["estuary"]
    assert isinstance(estuary_inputs, dict)
    cny_estuary = estuary_inputs["fx"]["CNY"]
    assert set(cny_estuary) == {
        "label",
        "bucket",
        "series",
        "quote",
        "source_id",
    }
    pd.testing.assert_series_equal(cny_estuary["series"], raw["fred"]["CNY"].points)

    sonar_inputs = captured["sonar"]
    assert isinstance(sonar_inputs, dict)
    assert "CNY" in sonar_inputs
    assert "PALIMPSEST_FEAR" in sonar_inputs
    assert not ({"SHIBOR_ON", "CN_FDR007", "CN_PARITY"} & set(sonar_inputs))


def test_missing_china_rate_stays_unavailable_instead_of_scoring_as_calm() -> None:
    index = pd.date_range("2025-12-01", periods=180, freq="B")
    cny = pd.Series(
        7.0 + pd.RangeIndex(len(index)).to_numpy() / 10_000,
        index=index,
        dtype=float,
    )
    result = harbors_engine.analyze(
        {
            "CHINA": {
                "cadence": "FX daily; local rate unavailable",
                "fx": cny,
                "fx_label": "CNY per USD",
            }
        },
        effr=pd.Series(dtype=float),
    )

    china = result["harbors"][0]
    assert china["harbor"] == "CHINA"
    assert china["rate"] is None
    assert china["rate2"] is None
    assert china["regime"] is None
    assert china["stress"] is not None
    assert china["stress_coverage"] == 0.75
    assert "CHINA" not in result["rate_labels"]
    assert "CHINA" in result["fx_labels"]


def test_lawful_palimpsest_exact_term_is_servable_by_rest(
    fake_snap, monkeypatch
) -> None:
    payload = deepcopy(fake_snap)
    payload["engines"]["farbasin"] = {
        "ok": True,
        "top_targets": [
            {
                "term": "SHIBOR",
                "domain": "public discussion",
                "threat": 0.72,
                "is_new": True,
            }
        ],
    }
    payload["deep"]["rights_commentary"] = {
        "description": "SHIBOR and FDR007 are distinct China money-market rates.",
        "note": (
            "The retired mirror was documented at "
            "https://palimpsest.info/readings/china-econ-latest.json."
        ),
        "notes": [
            "SHIBOR",
            (
                "https://www.chinamoney.com.cn/ags/ms/"
                "cm-u-bk-shibor/ShiborHis"
            ),
        ],
        "method": "CFETS values are absent, never interpreted as calm.",
        "caveats": ["DR007 is the transaction population behind FDR007."],
    }
    payload["deep"]["discussion_cards"] = [
        {
            "name": "Public discussion of SHIBOR liquidity",
            "label": "Public discussion of FDR007 methodology",
            "explanation": {"text": "SHIBOR"},
        }
    ]

    assert assemble._snapshot_contains_restricted_cfets(payload) is False
    assert assemble._servable_snapshot(payload) is True

    async def lawful_snapshot(force: bool = False) -> dict:
        return payload

    monkeypatch.delenv("SEICHE_BOARD_AUTH", raising=False)
    monkeypatch.setattr(assemble, "snapshot", lawful_snapshot)
    api._OVERVIEW_WIRE.update(src=None, body=None, gz=None, etag=None)
    response = TestClient(api.app).get(
        "/api/overview",
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == 200
    assert response.json()["engines"]["farbasin"]["top_targets"][0]["term"] == "SHIBOR"


@pytest.mark.parametrize(
    "poison",
    [
        {"provenance": [{"source": "chinamoney", "mnemonic": "overnight"}]},
        {"engines": {"sonar": {"leader": {"mnemonic": "CN_PARITY"}}}},
        {
            "engines": {
                "harbors": {
                    "harbors": [
                        {
                            "harbor": "CHINA",
                            "rate": {"last_pct": 1.5},
                            "rate2": None,
                            "regime": "HOLDING",
                        }
                    ]
                }
            }
        },
        {"engines": {"basins": {"basins": [{"basin": "CHINA", "z": 0.2}]}}},
        {
            "engines": {
                "estuary": {
                    "fx": {
                        "currencies": [
                            {
                                "key": "CNY",
                                "policy_diff_vs_effr_bp": 12.0,
                                "policy_rate_label": None,
                                "policy_rate_cadence": None,
                                "policy_asof": None,
                            }
                        ]
                    }
                }
            }
        },
        {"engines": {"spillover": {"nodes": ["US·rate", "CN·rate"]}}},
        {
            "deep": {
                "arbitrary_wrapper": [
                    {"another_wrapper": {"CN_FDR007": {"value": 1.52}}}
                ]
            }
        },
        {
            "editorial": {
                "arbitrary_wrapper": {"source": "chinamoney", "value": 1.31}
            }
        },
        {
            "calendar": {
                "arbitrary_wrapper": {
                    "instrument_id": "CN.CFETS.SHIBOR_ON",
                    "value": 1.31,
                }
            }
        },
        {"deep": {"wrapper": {"id": "CN.CFETS.FDR007"}}},
        {"deep": {"wrapper": {"name": "SHIBOR_ON"}}},
        {"deep": {"wrapper": {"metric": "FDR007"}}},
        {"deep": {"wrapper": {"series": ["safe", "CN_PARITY"]}}},
        {
            "deep": {
                "wrapper": {"input_series": {"primary": "CN_FDR007"}}
            }
        },
        {"deep": {"wrapper": {"columns": ["date", "SHIBOR_ON"]}}},
        {"deep": {"wrapper": {"selected_series": "CN.CFETS.SHIBOR_ON"}}},
        {"deep": {"wrapper": {"upstream_product_id": "FDR007"}}},
        {"deep": {"opaque": "CN CFETS FDR007"}},
        {"deep": {"wrapper": {"metrics": ["SHIBOR_ON"]}}},
        {
            "deep": {
                "wrapper": {
                    "instruments": [{"opaque": "CN-FDR007"}]
                }
            }
        },
        {"deep": {"wrapper": {"features": {"primary": "cfets rates"}}}},
        {"deep": {"wrapper": {"inputs": ["CFETS FDR007"]}}},
        {"deep": {"wrapper": {"outputs": ["CFETS FDR007"]}}},
        {"deep": {"wrapper": {"factors": ["CFETS FDR007"]}}},
        {"deep": {"wrapper": {"signals": ["CFETS FDR007"]}}},
        {"deep": {"wrapper": {"predictors": ["CFETS FDR007"]}}},
        {
            "deep": {
                "wrapper": {
                    "description": {"source": "chinamoney", "value": 1.52}
                }
            }
        },
        {
            "deep": {
                "wrapper": {
                    "notes": [
                        {"series": "CN.CFETS.FDR007", "value": 1.52}
                    ]
                }
            }
        },
        {
            "engines": {
                "farbasin": {
                    "ok": True,
                    "top_targets": [
                        {
                            "term": {
                                "instrument_id": "CN.CFETS.SHIBOR_ON",
                                "value": 1.31,
                            }
                        }
                    ],
                }
            }
        },
        {
            "deep": {
                "wrapper": {
                    "opaque": (
                        "https://www.chinamoney.com.cn/ags/ms/"
                        "cm-u-bk-shibor/ShiborHis"
                    )
                }
            }
        },
        {
            "deep": {
                "wrapper": {
                    "mirror_urls": [
                        "https://palimpsest.info/readings/"
                        "china-econ-history.jsonl"
                    ]
                }
            }
        },
    ],
)
def test_snapshot_gate_rejects_raw_and_derived_cfets_poison(fake_snap, poison) -> None:
    payload = deepcopy(fake_snap)
    for key, value in poison.items():
        if key == "engines":
            payload["engines"].update(value)
        else:
            payload[key] = value

    assert assemble._snapshot_contains_restricted_cfets(payload) is True
    assert assemble._servable_snapshot(payload) is False


def test_restart_ignores_poisoned_durable_snapshot_and_uses_clean_static(
    fake_snap, monkeypatch, tmp_path
) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["provenance"] = [
        {"source": "CFETS", "mnemonic": "SHIBOR_ON", "value": 1.5}
    ]
    static_path = tmp_path / "clean-bootstrap.json"
    static_path.write_text(json.dumps(fake_snap))

    class NoActiveRepository:
        @staticmethod
        def load_active_release_handoff():
            return None

    _reset_cache()
    monkeypatch.setattr(repository, "get_repository", NoActiveRepository)
    monkeypatch.setattr(assemble.store, "load_blob", lambda _key: poisoned)
    monkeypatch.setattr(assemble, "STATIC_SNAPSHOT_PATH", static_path)

    assert assemble.restore_cached_snapshot() == "static"
    assert assemble.cached_snapshot() == fake_snap


@pytest.mark.asyncio
async def test_replay_cache_does_not_serve_nested_restricted_payload(
    monkeypatch,
) -> None:
    cached = {
        "ok": True,
        "arbitrary_wrapper": {"CN_PARITY": {"value": 7.18}},
    }

    async def rebuild_required():
        raise RuntimeError("restricted replay was ignored")

    monkeypatch.setattr(assemble.store, "load_blob", lambda _key: cached)
    monkeypatch.setattr(assemble, "_gather_sources", rebuild_required)

    with pytest.raises(RuntimeError, match="restricted replay was ignored"):
        await assemble.snapshot_asof("2026-08-20")


def test_handoff_builder_rejects_poison_before_receipt_construction(fake_snap) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["nested"] = {"adapter_id": "cfets_rates", "value": 1.31}

    with pytest.raises(ValueError, match="restricted CFETS-derived data"):
        assemble._build_handoff(poisoned, {}, "a" * 40)


def test_live_publication_rejects_poison_before_cache_or_handoff(fake_snap) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["engines"]["spillover"] = {"nodes": ["CN·rate"]}
    _reset_cache()

    with pytest.raises(ValueError, match="restricted CFETS-derived data"):
        asyncio.run(assemble._publish_rebuilt_snapshot(poisoned, None))

    assert assemble.cached_snapshot() is None


def test_poisoned_memory_and_public_wire_caches_are_quarantined(fake_snap) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["deep"]["wrapper"] = {"input_series": ["CN_FDR007"]}
    _reset_cache()
    assemble._cache.update(
        at=1.0,
        payload=poisoned,
        source="rebuilt",
        release_receipt={"secret": "receipt"},
        release_handoff_id="a" * 64,
        producer_sha="b" * 40,
    )
    api._OVERVIEW_WIRE.update(
        src=None,
        body=b"old",
        gz=b"old",
        etag='"old"',
    )

    assert assemble.cached_snapshot() is None
    assert assemble._cache["payload"] is None
    assert assemble._cache["release_receipt"] is None
    with pytest.raises(ValueError, match="restricted CFETS-derived data"):
        api._overview_wire(poisoned)
    assert api._OVERVIEW_WIRE["body"] == b"old"


def test_poisoned_mcp_ttl_cache_is_quarantined(fake_snap) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["deep"]["wrapper"] = {"columns": ["date", "FDR007"]}
    prior = dict(mcp_server._cache)
    try:
        mcp_server._cache.update(snap=poisoned, at=123.0)
        assert mcp_server._rights_safe_memo(poisoned) is None
        assert mcp_server._cache == {"snap": None, "at": 0.0}
    finally:
        mcp_server._cache.clear()
        mcp_server._cache.update(prior)


def test_rest_overview_quarantines_unknown_scalar_identity_bypass(
    fake_snap, monkeypatch
) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["deep"]["opaque"] = "CN CFETS FDR007"
    clean = deepcopy(fake_snap)
    _reset_cache()
    assemble._cache.update(at=1.0, payload=poisoned, source="rebuilt")
    api._OVERVIEW_WIRE.update(src=None, body=None, gz=None, etag=None)
    monkeypatch.delenv("SEICHE_BOARD_AUTH", raising=False)

    async def clean_rebuild() -> dict:
        return clean

    monkeypatch.setattr(assemble, "_build_snapshot", clean_rebuild)
    response = TestClient(api.app).get(
        "/api/overview",
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == 200
    assert "CN CFETS FDR007" not in response.text
    assert assemble._cache["payload"] is None
    assert assemble._cache["release_receipt"] is None


def test_rest_overview_quarantines_palimpsest_term_mapping_bypass(
    fake_snap, monkeypatch
) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["engines"]["farbasin"] = {
        "ok": True,
        "top_targets": [
            {
                "term": {
                    "instrument_id": "CN.CFETS.SHIBOR_ON",
                    "value": 1.31,
                }
            }
        ],
    }
    clean = deepcopy(fake_snap)
    _reset_cache()
    assemble._cache.update(at=1.0, payload=poisoned, source="rebuilt")
    api._OVERVIEW_WIRE.update(src=None, body=None, gz=None, etag=None)
    monkeypatch.delenv("SEICHE_BOARD_AUTH", raising=False)

    async def clean_rebuild() -> dict:
        return clean

    monkeypatch.setattr(assemble, "_build_snapshot", clean_rebuild)
    response = TestClient(api.app).get(
        "/api/overview",
        headers={"Accept-Encoding": "identity"},
    )

    assert response.status_code == 200
    assert "CN.CFETS.SHIBOR_ON" not in response.text
    assert assemble._cache["payload"] is None
    assert assemble._cache["release_receipt"] is None


@pytest.mark.parametrize(
    "poison",
    (
        {"metrics": ["SHIBOR_ON"]},
        {"notes": [{"series": "CN.CFETS.FDR007", "value": 1.52}]},
    ),
)
def test_mcp_tool_quarantines_plural_and_nested_prose_mapping_bypasses(
    fake_snap, monkeypatch, poison
) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["deep"]["wrapper"] = poison
    clean = deepcopy(fake_snap)
    _reset_cache()
    assemble._cache.update(at=1.0, payload=poisoned, source="rebuilt")

    async def clean_rebuild() -> dict:
        return clean

    monkeypatch.setattr(assemble, "_build_snapshot", clean_rebuild)
    prior = dict(mcp_server._cache)
    try:
        mcp_server._cache.update(snap=poisoned, at=123.0)
        result = mcp_server.tool_stress_now({}, False)

        assert result["composite"] == clean["engines"]["composite"]
        assert mcp_server._cache["snap"] is clean
        assert assemble._cache["payload"] is None
    finally:
        mcp_server._cache.clear()
        mcp_server._cache.update(prior)


def test_deep_cache_poison_is_ignored_before_use(monkeypatch) -> None:
    poison = {"ok": True, "columns": ["date", "SHIBOR_ON"]}
    saved: list[dict] = []
    monkeypatch.setattr(assemble.store, "load_blob", lambda _key: poison)
    monkeypatch.setattr(
        assemble.store,
        "save_blob",
        lambda _key, value: saved.append(deepcopy(value)),
    )
    monkeypatch.setattr(
        assemble.eng_history,
        "build",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("stop after load")),
    )
    drv = {
        "spread_bp": pd.Series(
            [1.0], index=pd.DatetimeIndex(["2026-08-22"])
        ),
        "tail_bp": pd.Series(dtype=float),
        "srf": pd.Series(dtype=float),
        "dw_b": pd.Series(dtype=float),
        "rrp": pd.Series(dtype=float),
        "res_gdp": pd.Series(dtype=float),
    }

    result = assemble._deep_layer({}, drv, {}, [])

    assert result.get("columns") is None
    assert saved == [result]
    assert not assemble._snapshot_contains_restricted_cfets({"deep": result})


def test_deep_cache_poison_is_rejected_before_save(monkeypatch) -> None:
    saved: list[dict] = []
    monkeypatch.setattr(assemble.store, "load_blob", lambda _key: None)
    monkeypatch.setattr(
        assemble.store,
        "save_blob",
        lambda _key, value: saved.append(deepcopy(value)),
    )
    monkeypatch.setattr(
        assemble.eng_history,
        "build",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("history failed")),
    )
    monkeypatch.setattr(
        assemble,
        "safe_failure_envelope",
        lambda _error: {"source_id": "cfets_rates"},
    )
    drv = {
        "spread_bp": pd.Series(
            [1.0], index=pd.DatetimeIndex(["2026-08-22"])
        ),
        "tail_bp": pd.Series(dtype=float),
        "srf": pd.Series(dtype=float),
        "dw_b": pd.Series(dtype=float),
        "rrp": pd.Series(dtype=float),
        "res_gdp": pd.Series(dtype=float),
    }

    with pytest.raises(ValueError, match="restricted CFETS-derived data"):
        assemble._deep_layer({}, drv, {}, [])

    assert saved == []


@pytest.mark.asyncio
async def test_engine_poison_is_rejected_before_deep_or_publication_side_effects(
    monkeypatch,
) -> None:
    events: list[str] = []

    async def gather_sources():
        return {}, []

    def forbidden(name: str):
        def call(*_args, **_kwargs):
            events.append(name)
            raise AssertionError(f"{name} ran after engine poison")

        return call

    async def forbidden_async(*_args, **_kwargs):
        events.append("navigator")
        raise AssertionError("Navigator ran after engine poison")

    monkeypatch.setattr(assemble, "_gather_sources", gather_sources)
    monkeypatch.setattr(
        assemble,
        "_derived",
        lambda _src: {
            "spread_bp": pd.Series(
                [1.0], index=pd.DatetimeIndex(["2026-08-22"])
            )
        },
    )
    monkeypatch.setattr(
        assemble,
        "_run_engines",
        lambda *_args: {
            "harbors": {
                "harbors": [
                    {
                        "harbor": "CHINA",
                        "rate": {"last_pct": 1.31},
                        "regime": "HOLDING",
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(assemble, "_deep_layer", forbidden("deep"))
    monkeypatch.setattr(assemble.eng_navigator, "commit", forbidden_async)
    monkeypatch.setattr(assemble, "_record_pit", forbidden("pit"))
    monkeypatch.setattr(assemble, "_seal_release_evidence", forbidden("seal"))
    monkeypatch.setattr(assemble, "_publish_rebuilt_snapshot", forbidden_async)

    with pytest.raises(ValueError, match="restricted CFETS-derived data"):
        await assemble._build_snapshot()

    assert events == []


@pytest.mark.asyncio
async def test_complete_payload_poison_is_rejected_before_navigator_and_publish(
    monkeypatch,
) -> None:
    events: list[str] = []

    async def gather_sources():
        return {}, []

    async def forbidden_navigator(*_args, **_kwargs):
        events.append("navigator")
        return {"ok": False}

    def forbidden(name: str):
        def call(*_args, **_kwargs):
            events.append(name)
            return None

        return call

    monkeypatch.setattr(assemble, "_gather_sources", gather_sources)
    monkeypatch.setattr(
        assemble,
        "_derived",
        lambda _src: {
            "spread_bp": pd.Series(
                [1.0], index=pd.DatetimeIndex(["2026-08-22"])
            )
        },
    )
    monkeypatch.setattr(assemble, "_run_engines", lambda *_args: {})
    monkeypatch.setattr(assemble, "_deep_layer", lambda *_args: {"ok": False})
    monkeypatch.setattr(assemble.eng_modelcourt, "convene", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(assemble, "_headline", lambda *_args: {})
    monkeypatch.setattr(assemble, "_calendar", lambda *_args: {})
    monkeypatch.setattr(
        assemble,
        "_provenance",
        lambda _src: [{"source": "chinamoney", "value": 1.31}],
    )
    monkeypatch.setattr(assemble.editorial, "build_editorial", lambda **_kwargs: {})
    monkeypatch.setattr(
        assemble.editorial, "build_data_quality", lambda **_kwargs: {}
    )
    monkeypatch.setattr(assemble.eng_navigator, "commit", forbidden_navigator)
    monkeypatch.setattr(assemble, "_record_pit", forbidden("pit"))
    monkeypatch.setattr(assemble, "_seal_release_evidence", forbidden("seal"))
    monkeypatch.setattr(assemble, "_publish_rebuilt_snapshot", forbidden_navigator)

    with pytest.raises(ValueError, match="restricted CFETS-derived data"):
        await assemble._build_snapshot()

    assert events == []


def test_clean_cny_fx_snapshot_reaches_overview_estuary_static_and_mcp(
    fake_snap, monkeypatch
) -> None:
    payload = deepcopy(fake_snap)
    payload["engines"]["harbors"] = {
        "ok": True,
        "harbors": [
            {
                "harbor": "CHINA",
                "rate": None,
                "rate2": None,
                "regime": None,
                "stress": 64.0,
                "stress_coverage": 0.75,
                "fx": {"label": "CNY per USD", "last": 7.18},
            }
        ],
        "rate_labels": [],
        "fx_labels": ["CHINA"],
        "caveats": ["SHIBOR and CFETS observations are unavailable."],
        "method": "Missing local rates never score as calm.",
    }
    payload["engines"]["estuary"]["fx"]["currencies"].append(
        {
            "key": "CNY",
            "label": "Chinese yuan",
            "pressure": 64.0,
            "policy_diff_vs_effr_bp": None,
            "policy_rate_label": None,
            "policy_rate_cadence": None,
            "policy_asof": None,
            "source_id": "DEXCHUS",
        }
    )

    assert assemble._snapshot_contains_restricted_cfets(payload) is False
    assert assemble._servable_snapshot(payload) is True
    packaged = json.loads(assemble.STATIC_SNAPSHOT_PATH.read_text())
    assert assemble._servable_snapshot(packaged) is True
    assert not assemble._snapshot_contains_restricted_cfets(packaged)

    async def clean_snapshot(force: bool = False):
        return payload

    monkeypatch.setattr(assemble, "snapshot", clean_snapshot)
    monkeypatch.setitem(api._OVERVIEW_WIRE, "src", None)
    client = TestClient(api.app)
    overview = client.get("/api/overview", headers={"Accept-Encoding": "identity"})
    estuary = client.get("/api/estuary")
    assert overview.status_code == 200
    assert estuary.status_code == 200
    assert '"key":"CNY"' in overview.text
    assert estuary.json()["leaders"]["fx"][-1]["key"] == "CNY"

    monkeypatch.setattr(mcp_server, "_get_snapshot", lambda force=False: payload)
    mcp_estuary = mcp_server.tool_estuary({}, True)
    assert mcp_estuary == context_views.estuary(payload)
    assert mcp_estuary["leaders"]["fx"][-1]["key"] == "CNY"
    for public_projection in (overview.json(), estuary.json(), mcp_estuary):
        assert not assemble._snapshot_contains_restricted_cfets(public_projection)
