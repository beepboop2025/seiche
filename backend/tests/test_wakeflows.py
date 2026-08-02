"""wakeflows loader + the institutional_flows MCP tool.

Same discipline as the rest of the gate: no network, packs are canned
files in tmp_path, the tool degrades to ToolError — never a crash —
when the pack is missing, stale, or malformed.
"""

import datetime as dt
import json

import pytest

from seiche import mcp_server as mcp
from seiche import wakeflows


def _pack(as_of=None, product="seiche", schema="1.0"):
    as_of = as_of or dt.date.today().isoformat()
    return {
        "product": product,
        "generator": "wake/0.1.0",
        "schema_version": schema,
        "generated_at": "2026-08-02T12:00:00+00:00",
        "as_of": as_of,
        "method_versions": {"basis_nowcast": "1.0.0",
                            "kalman_fusion": "1.0.0", "hawkes": "1.0.0"},
        "provenance": [{"source": "CFTC TFF", "url": "https://x",
                        "reference_date": "2026-07-28",
                        "release_date": "2026-07-31",
                        "retrieved_at": "2026-08-02T11:00:00+00:00"}],
        "payload": {
            "positioning_flows": {
                "basis_nowcast": {
                    "ref_date": "2026-07-28", "basis_size_usd_bn": 904.5,
                    "gross_short_usd_bn": 1105.4,
                    "pension_long_usd_bn": 1160.8, "z_basis": -0.43,
                    "z_pension": 0.78, "delta_4w_usd_bn": -13.2,
                    "fragile": False, "funding_spread_bp": 0.0,
                    "notional_basis": "face_value"},
                "sovereign_custody": {
                    "ref_date": "2026-07-29", "level_usd_bn": 2656.6,
                    "chg_13w_usd_bn": -78.4, "z_level_5y": -2.47},
                "fusion_index": {"date": "2026-07-30", "index": 0.864,
                                 "sigma": 0.15, "band68": [0.714, 1.015]},
            },
            "stress_endogeneity": {"branching_ratio": 0.892,
                                   "endogeneity": "high",
                                   "p_event_next": {"21d": 0.62},
                                   "n_events": 85,
                                   "lr_stat_vs_poisson": 55.5},
        },
    }


@pytest.fixture()
def pack_file(tmp_path, monkeypatch):
    def write(pack):
        p = tmp_path / "wake_seiche.json"
        p.write_text(json.dumps(pack))
        monkeypatch.setenv("WAKE_PACK_PATH", str(p))
        return p
    return write


def _call(tool, args=None):
    return mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": tool, "arguments": args or {}}})


def _payload(resp):
    return json.loads(resp["result"]["content"][0]["text"])


# ---- loader ---------------------------------------------------------------

def test_load_and_flatten(pack_file):
    pack_file(_pack())
    out = wakeflows.readings(wakeflows.load())
    assert out["basis_trade"]["size_usd_bn"] == 904.5
    assert out["basis_trade"]["fragile"] is False
    assert out["sovereign_custody"]["z_level_5y"] == -2.47
    assert out["stress_endogeneity"]["branching_ratio"] == 0.892
    assert out["pension_duration"]["net_long_usd_bn"] == 1160.8


def test_missing_pack_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("WAKE_PACK_PATH", str(tmp_path / "absent.json"))
    with pytest.raises(wakeflows.WakePackError):
        wakeflows.load()


def test_stale_pack_refused(pack_file):
    old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    pack_file(_pack(as_of=old))
    with pytest.raises(wakeflows.WakePackError, match="stale"):
        wakeflows.load()


def test_wrong_product_refused(pack_file):
    pack_file(_pack(product="undertow"))
    with pytest.raises(wakeflows.WakePackError, match="undertow"):
        wakeflows.load()


def test_wrong_schema_refused(pack_file):
    pack_file(_pack(schema="2.0"))
    with pytest.raises(wakeflows.WakePackError, match="schema"):
        wakeflows.load()


def test_degraded_sections_stay_absent(pack_file):
    pack = _pack()
    pack["payload"]["positioning_flows"].pop("sovereign_custody")
    pack["payload"].pop("stress_endogeneity")
    pack_file(pack)
    out = wakeflows.readings(wakeflows.load())
    assert "sovereign_custody" not in out
    assert "stress_endogeneity" not in out
    assert out["basis_trade"]["size_usd_bn"] == 904.5


# ---- MCP tool -------------------------------------------------------------

def test_tool_listed_and_public():
    title, _desc, _schema, handler, public = mcp.TOOLS["institutional_flows"]
    assert handler is mcp.tool_flows
    assert public is True          # Seiche is a free public good
    assert "positioned" in title.lower() or "flows" in title.lower()


def test_tool_serves_pack(pack_file):
    pack_file(_pack())
    out = _payload(_call("institutional_flows"))
    assert out["basis_trade"]["size_usd_bn"] == 904.5
    assert "reading" in out and "public prints" in out["reading"]


def test_tool_degrades_to_tool_error(monkeypatch, tmp_path):
    monkeypatch.setenv("WAKE_PACK_PATH", str(tmp_path / "absent.json"))
    resp = _call("institutional_flows")
    assert resp["result"]["isError"] is True
    assert "unavailable" in resp["result"]["content"][0]["text"]
