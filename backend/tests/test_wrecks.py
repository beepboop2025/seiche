"""Wrecks: the crypto-episode case table, summarized honestly."""

import pytest

from seiche.config import CRYPTO_EPISODE_CLASS, CRYPTO_EPISODES, WRECKS_OFFSETS_BD
from seiche.engines import wrecks


def _read(date, value, regime):
    return {"date": date, "value": value, "regime": regime, "coverage_pct": 90}


def _ladder(regimes):
    """offset -> board read, one regime per configured offset."""
    return {k: _read(f"2020-01-{i + 2:02d}", 40 + i, r)
            for i, (k, r) in enumerate(zip(WRECKS_OFFSETS_BD, regimes))}


@pytest.fixture()
def replays():
    """External wrecks elevated, crypto-native quiet — the flattering case."""
    out = {}
    for anchor, cls in CRYPTO_EPISODE_CLASS.items():
        if cls == "external":
            out[anchor] = _ladder(["CALM", "EROSION", "STRAIN", "STRESS", "STRESS"])
        else:
            out[anchor] = _ladder(["CALM"] * len(WRECKS_OFFSETS_BD))
    return out


def test_summarize_splits_classes_honestly(replays):
    payload = wrecks.summarize(replays)
    s = payload["summary"]
    n_ext = sum(1 for c in CRYPTO_EPISODE_CLASS.values() if c == "external")
    n_nat = len(CRYPTO_EPISODE_CLASS) - n_ext
    assert s["external_with_board_elevated"] == f"{n_ext}/{n_ext}"
    assert s["crypto_native_board_quiet"] == f"{n_nat}/{n_nat}"
    assert len(payload["episodes"]) == len(CRYPTO_EPISODES)


def test_quiet_on_crypto_native_reads_as_specificity(replays):
    payload = wrecks.summarize(replays)
    nat = [e for e in payload["episodes"] if e["class"] == "crypto_native"]
    assert nat and all(not e["board_elevated"] for e in nat)
    assert all("quiet" in e["reading"] for e in nat)


def test_elevated_on_crypto_native_is_not_credited():
    replays = {a: _ladder(["STRESS"] * len(WRECKS_OFFSETS_BD))
               for a in CRYPTO_EPISODES}
    payload = wrecks.summarize(replays)
    nat = [e for e in payload["episodes"] if e["class"] == "crypto_native"]
    assert all(e["board_elevated"] for e in nat)
    assert all("do not credit" in e["reading"] for e in nat)


def test_missing_coverage_is_reported_not_filled():
    payload = wrecks.summarize({})  # no replays at all
    for ep in payload["episodes"]:
        assert ep["peak_regime"] is None
        assert "no board coverage" in ep["reading"]
        assert all(r["regime"] is None for r in ep["board"])
    # honest denominators: nothing counted when nothing was seen
    assert payload["summary"]["external_with_board_elevated"].endswith("/0")


def test_offset_dates_are_business_days_no_lookahead():
    pairs = wrecks.offset_dates("2023-03-10")  # a Friday
    assert pairs[-1] == (0, "2023-03-10")
    dates = [d for _, d in pairs]
    assert dates == sorted(dates)          # T-21 ... T-0, ascending
    assert all(d <= "2023-03-10" for d in dates)


def test_caveats_always_publish(replays):
    payload = wrecks.summarize(replays)
    assert any("final-vintage" in c for c in payload["caveats"])
    assert any("not a statistic" in c for c in payload["caveats"])


def test_mcp_tool_serves_blob_and_fails_loud(monkeypatch):
    from seiche import mcp_server, store

    monkeypatch.setattr(store, "load_blob", lambda key, ttl_minutes=None: None)
    with pytest.raises(mcp_server.ToolError):
        mcp_server.tool_wrecks({}, True)

    canned = {"episodes": [], "summary": {}, "offsets_bd": [], "caveats": []}
    monkeypatch.setattr(store, "load_blob", lambda key, ttl_minutes=None: canned)
    out = mcp_server.tool_wrecks({}, True)
    assert "reading" in out and "specificity" in out["reading"]
