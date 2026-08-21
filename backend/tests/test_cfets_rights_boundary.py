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

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from seiche import api, assemble, context_views, mcp_server, repository
from seiche.engines import harbors as harbors_engine
from seiche.sources.base import Series


def _series(
    mnemonic: str,
    *,
    source: str,
    values: pd.Series | None = None,
) -> Series:
    points = (
        values
        if values is not None
        else pd.Series(
            [1.0, 1.1], index=pd.date_range("2026-08-20", periods=2, freq="D")
        )
    )
    return Series(
        mnemonic=mnemonic,
        source=source,
        remote_id=f"test:{mnemonic}",
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
    fear = _series("PALIMPSEST_FEAR", source="palimpsest")
    return {
        "fred": {"CNY": cny},
        "chinamoney": {"SHIBOR_ON": shibor},
        "palimpsest": {
            "fetched_at": "2026-08-22T00:00:00+00:00",
            "series": {
                "CN_FDR007": fdr007,
                "CN_PARITY": parity,
                "PALIMPSEST_FEAR": fear,
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


def _encoded_has_restricted_marker(payload: object) -> bool:
    encoded = json.dumps(payload, ensure_ascii=False).casefold()
    return any(marker in encoded for marker in assemble.RESTRICTED_SNAPSHOT_MARKERS)


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
    assert set(eligible["palimpsest"]["series"]) == {"PALIMPSEST_FEAR"}
    assert set(raw["palimpsest"]["series"]) == {
        "CN_FDR007",
        "CN_PARITY",
        "PALIMPSEST_FEAR",
    }
    assert "chinamoney" in raw

    provenance = assemble._provenance(raw)
    assert {row["mnemonic"] for row in provenance} == {
        "CNY",
        "PALIMPSEST_FEAR",
    }
    assert not _encoded_has_restricted_marker(provenance)


def test_legacy_gather_no_longer_schedules_chinamoney() -> None:
    gather_source = inspect.getsource(assemble._gather_sources).casefold()

    assert "chinamoney" not in gather_source
    assert "chinamoney" not in assemble.__dict__


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
    pd.testing.assert_series_equal(basin_inputs["cny"], raw["fred"]["CNY"].points)

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


def test_live_publication_rejects_poison_before_cache_or_handoff(fake_snap) -> None:
    poisoned = deepcopy(fake_snap)
    poisoned["engines"]["spillover"] = {"nodes": ["CN·rate"]}
    _reset_cache()

    with pytest.raises(ValueError, match="restricted CFETS-derived data"):
        asyncio.run(assemble._publish_rebuilt_snapshot(poisoned, None))

    assert assemble.cached_snapshot() is None


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
