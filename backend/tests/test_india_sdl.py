"""RBI state government securities auctions: printed figures, checked releases, a bounded desk."""
import json

import pytest

from seiche import india_sdl as sdl


def test_the_pack_cites_every_release_and_no_release_failed_its_printed_total():
    pack = sdl._pack()
    assert pack["schema"] == "seiche.india-sdl-auctions.v1"
    assert pack["source"]["releases"] == len(pack["releases"]) >= 290
    for prid, rel in pack["releases"].items():
        assert rel["url"].endswith(f"prid={prid}") and rel["url"].startswith("https://www.rbi.org.in/")
        assert rel["total_check"] in {"matched", "no_printed_total"}
        assert len(rel["sha256"]) == 64 and rel["auction_date"]
    rows = pack["_rows"]
    assert all(r["auction_date"] and r["state"] for r in rows)
    yields = [r["cutoff_yield_pct"] for r in rows if r["cutoff_yield_pct"] is not None]
    assert 3 < min(yields) and max(yields) < 12       # a printed 0 or a price never becomes a yield


def test_the_latest_day_adds_up_from_its_securities():
    data = sdl.read()
    day = data["latest_auction"]
    rows = [r for r in sdl._pack()["_rows"] if r["auction_date"] == day["auction_date"]]
    assert day["securities"] == len(rows)
    assert day["notified_cr"] == pytest.approx(sum(r["notified_cr"] or 0 for r in rows))
    assert day["bid_cover"] == pytest.approx(sum(r["competitive_bids_cr"] or 0 for r in rows) /
                                             sum(r["notified_cr"] for r in rows), abs=1e-3)
    assert {t["state"] for t in data["states"]} == set(sdl.states())
    assert data["weekly"][-1]["auction_date"] == day["auction_date"]


def test_one_state_is_read_case_insensitively_and_unknown_states_are_refused():
    one = sdl.read("rajasthan")
    assert one["state"] == "RAJASTHAN" and one["securities"]
    assert all(s["release"].startswith("https://www.rbi.org.in/") for s in one["securities"])
    assert one["securities"] == sorted(one["securities"], key=lambda s: s["auction_date"], reverse=True)
    with pytest.raises(ValueError):
        sdl.read("Atlantis")


def test_the_mcp_section_is_bounded_and_listed():
    from seiche import mcp_server as mcp
    assert "india_states" in mcp.MONEY_MARKET_SELECTORS
    out = mcp.tool_money_market({"section": "india_states"}, True)
    assert out["ok"] is True and out["selection"] == "india_states"
    assert len(out["india_states"]["weekly"]) <= 26
    assert len(json.dumps(out).encode()) <= 24 * 1024
    with pytest.raises(mcp.ToolError):
        mcp.tool_money_market({"section": "india_states", "view": "summary"}, True)


def test_the_rest_route_serves_the_desk_and_refuses_unknown_parameters(monkeypatch):
    from fastapi.testclient import TestClient
    from seiche import api
    monkeypatch.setattr(api._market_series_limiter, "allow", lambda *_: True)
    with TestClient(api.app) as client:
        full = client.get("/api/v2/india-sdl")
        assert full.status_code == 200 and full.json()["latest_auction"]["securities"] > 0
        assert client.get("/api/v2/india-sdl", params={"state": "Tamil Nadu"}).json()["state"] == "TAMIL NADU"
        assert client.get("/api/v2/india-sdl", params={"state": "Atlantis"}).status_code == 422
        assert client.get("/api/v2/india-sdl", params={"bogus": "1"}).status_code == 422
    assert "/api/v2/india-sdl" in api._public_openapi_document()["paths"]
