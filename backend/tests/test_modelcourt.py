"""Model Court: pooling honesty, the AUROC gate, ledger discipline.

The tests that matter most are the refusals: a member whose own backtest
ranks below chance gets zero calibration credit, a court with fewer than 30
resolved ledger rows per model refuses to rank, and the markov regime odds
never leak into the pooled probability because they answer a different
question.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from seiche.engines import modelcourt as mc

pytestmark = pytest.mark.limit_memory("256 MB")


def _deep_sample() -> dict:
    """Overview-shaped deep dict, values lifted from the live sample."""
    return {
        "bathymetry": {
            "ok": True,
            "asof": "2026-07-24",
            "p_event_5bd": 0.14,
            "p_by_horizon": {"h1": 0.055, "h5": 0.14, "h10": 0.201},
            "validation": {
                "ok": True, "n_scored": 1528, "n_events": 50, "auroc": 0.453,
                "brier": 0.0343, "brier_climatology": 0.039, "brier_skill": 0.12,
                "verdict": "levels do not beat climatology",
            },
        },
        "ml": {
            "ok": True,
            "asof": "2026-07-23",
            "p_event_5bd": 0.052,
            "verdict": "does not out-rank the rule-based index",
            "validation": {
                "oos_days": 1574, "oos_events": 63, "auroc": 0.819,
                "brier": 0.0396, "brier_climatology": 0.0384,
            },
        },
        "swell": {
            "ok": True,
            "asof": "2026-07-24",
            "p_event_5bd": 0.082,
            "event_by_horizon": {"h5": 0.082, "h10": 0.157},
            "validation": {
                "ok": True, "n_scored": 1664, "n_events": 68, "auroc": 0.732,
                "brier": 0.0401, "brier_climatology": 0.0457, "brier_skill": 0.123,
                "verdict": "curve beats climatology out of sample",
            },
        },
        "tidetables": {
            "ok": True,
            "asof": "2026-07-27",
            "horizon_bd": 10,  # the FAN horizon; event_odds are 5bd by method
            "event_odds": {"p": 0.12, "hits": 3, "n": 25, "ci95": [0.042, 0.3],
                           "base_rate": 0.062, "lift": 1.93},
            "skill": {
                "ok": True, "n_scored": 1884, "n_events": 96, "brier": 0.0511,
                "brier_climatology": 0.0507, "brier_skill": -0.008, "auroc": 0.7,
                "verdict": "analogs do not beat climatology",
            },
        },
        "markov": {
            "ok": True,
            "current_regime": "STRAIN",
            "p_reach_stress": {"h5": 0.0, "h10": 0.0, "h21": 0.0},
        },
    }


def _ledger(n_events: int, n_quiet: int, model: str, p_event: float, p_quiet: float,
            horizon_bd: int = 5) -> list[dict]:
    rows = []
    for i in range(n_events):
        rows.append({"date": f"2026-01-{i + 1:02d}", "model": model,
                     "horizon_bd": horizon_bd, "p": p_event, "realized": True})
    for i in range(n_quiet):
        rows.append({"date": f"2026-02-{i + 1:02d}", "model": model,
                     "horizon_bd": horizon_bd, "p": p_quiet, "realized": False})
    return rows


# ---------------------------------------------------------------------------
# Members, pooling, dispersion
# ---------------------------------------------------------------------------

def test_sample_shape_convenes_and_pools():
    r = mc.convene(_deep_sample())
    assert r["ok"]
    assert r["horizon_bd"] == 5
    names = {m["model"] for m in r["members"]}
    assert names == {"bathymetry", "ml", "swell", "tidetables", "markov"}

    pool = [m for m in r["members"] if m["in_pool"]]
    assert {m["model"] for m in pool} == {"bathymetry", "ml", "swell", "tidetables"}

    # Only swell both beats climatology AND ranks above chance in the sample.
    assert r["ensemble"]["rule"] == "skill_weighted"
    assert r["ensemble"]["n_weighted"] == 1
    assert r["ensemble"]["weights"]["swell"] == 1.0
    assert r["ensemble"]["p"] == pytest.approx(0.082)
    assert abs(sum(r["ensemble"]["weights"].values()) - 1.0) < 1e-9

    d = r["dispersion"]
    assert d["n"] == 4
    assert d["min"] == pytest.approx(0.052)
    assert d["max"] == pytest.approx(0.14)
    assert d["spread"] == pytest.approx(0.088)
    assert r["asof"] == "2026-07-27"


def test_markov_never_pooled_and_carries_no_weight():
    r = mc.convene(_deep_sample())
    mk = next(m for m in r["members"] if m["model"] == "markov")
    assert mk["in_pool"] is False
    assert mk["weight"] is None
    assert "markov" not in r["ensemble"]["weights"]
    # dispersion excludes it: min is ml's 0.052, not markov's 0.0
    assert r["dispersion"]["min"] > 0.0


def test_auroc_gate_zeroes_subchance_ranker():
    # bathymetry has positive Brier skill (0.12) but AUROC 0.453; the gate
    # must refuse it calibration credit.
    r = mc.convene(_deep_sample())
    assert r["ensemble"]["weights"]["bathymetry"] == 0.0
    # sanity on the gate arithmetic itself
    assert mc._weight({"brier_skill": 0.12, "auroc": 0.453}) == 0.0
    assert mc._weight({"brier_skill": 0.12, "auroc": 0.7}) == pytest.approx(0.12)
    assert mc._weight({"brier_skill": 0.12, "auroc": 0.6}) == pytest.approx(0.06)
    assert mc._weight({"brier_skill": 0.12, "auroc": None}) == pytest.approx(0.12)


def test_median_fallback_when_no_member_beats_climatology():
    deep = _deep_sample()
    deep["swell"]["validation"]["brier_skill"] = -0.05
    r = mc.convene(deep)
    assert r["ensemble"]["rule"] == "median"
    expected = float(np.median([0.14, 0.052, 0.082, 0.12]))
    assert r["ensemble"]["p"] == pytest.approx(expected)
    assert all(w == 0.0 for w in r["ensemble"]["weights"].values())
    assert "median" in r["adjudication"]


def test_degrades_below_two_members():
    assert mc.convene({})["ok"] is False
    assert mc.convene(None)["ok"] is False
    deep = {"bathymetry": _deep_sample()["bathymetry"]}
    r = mc.convene(deep)
    assert r["ok"] is False
    assert "reason" in r


def test_failed_member_listed_absent_court_still_sits():
    deep = _deep_sample()
    deep["ml"] = {"ok": False, "reason": "sklearn exploded"}
    r = mc.convene(deep)
    assert r["ok"]
    assert "ml" not in {m["model"] for m in r["members"]}
    ab = next(a for a in r["absent"] if a["model"] == "ml")
    assert "sklearn exploded" in ab["reason"]
    assert r["dispersion"]["n"] == 3


def test_alternate_horizon_uses_native_curves():
    r = mc.convene(_deep_sample(), horizon_bd=10)
    assert r["ok"]
    by = {m["model"]: m for m in r["members"]}
    assert by["bathymetry"]["p"] == pytest.approx(0.201)
    assert by["swell"]["p"] == pytest.approx(0.157)
    assert by["markov"]["in_pool"] is False
    # ml and tidetables publish 5bd only
    absent = {a["model"] for a in r["absent"]}
    assert {"ml", "tidetables"} <= absent


# ---------------------------------------------------------------------------
# The ledger and the live court
# ---------------------------------------------------------------------------

def test_no_ledger_is_reported_honestly():
    r = mc.convene(_deep_sample(), odds_ledger=None)
    assert r["court"]["in_session"] is False
    assert r["court"]["verdict"] is None
    assert r["ledger_status"].startswith("no ledger yet")

    r2 = mc.convene(_deep_sample(), odds_ledger=[])
    assert r2["court"]["in_session"] is False
    assert "no valid rows" in r2["ledger_status"]


def test_accruing_ledger_withholds_the_verdict():
    led = _ledger(2, 3, "swell", 0.6, 0.1)
    led += [{"date": "2026-03-01", "model": "swell", "horizon_bd": 5,
             "p": 0.08, "realized": None}] * 4
    r = mc.convene(_deep_sample(), odds_ledger=led)
    court = r["court"]
    assert court["in_session"] is False
    assert court["verdict"] is None
    s = court["scores"][0]
    assert s["model"] == "swell"
    assert s["n_resolved"] == 5
    assert s["n_pending"] == 4
    assert "brier" not in s
    assert f"5/{mc.MIN_RESOLVED} resolved" in r["ledger_status"]
    assert "withheld" in r["ledger_status"]


def test_court_ranks_on_live_brier():
    # Same 40 outcomes for both models (10 events, 30 quiet): swell sharp,
    # ml blurred at 0.5. Brier by hand: swell 0.004375, ml 0.25.
    led = _ledger(10, 30, "swell", 0.9, 0.05) + _ledger(10, 30, "ml", 0.5, 0.5)
    r = mc.convene(_deep_sample(), odds_ledger=led)
    court = r["court"]
    assert court["in_session"] is True
    by = {e["model"]: e for e in court["scores"]}
    assert by["swell"]["brier"] == pytest.approx(0.0044, abs=1e-4)
    assert by["ml"]["brier"] == pytest.approx(0.25)
    assert by["swell"]["rank"] == 1
    assert by["ml"]["rank"] == 2
    # shared base rate 0.25 -> climatology Brier 0.1875 for both
    assert by["swell"]["brier_climatology"] == pytest.approx(0.1875)
    assert by["swell"]["brier_skill"] > 0.9
    assert by["ml"]["brier_skill"] < 0
    assert "1. swell" in court["verdict"]
    assert "ranks swell first" in r["adjudication"]


def test_malformed_and_offhorizon_rows_are_ignored():
    led = _ledger(10, 30, "swell", 0.9, 0.05) + _ledger(10, 30, "ml", 0.5, 0.5)
    led += [
        {"date": "2026-04-01", "model": "swell", "horizon_bd": 10, "p": 0.0, "realized": True},
        {"date": "2026-04-02", "model": "swell", "horizon_bd": 5, "p": 1.5, "realized": True},
        {"date": "2026-04-03", "model": "", "horizon_bd": 5, "p": 0.5, "realized": True},
        {"date": "2026-04-04", "horizon_bd": 5, "p": 0.5, "realized": False},
        "not a dict",
        {"date": "2026-04-05", "model": "swell", "horizon_bd": 5, "p": None, "realized": True},
    ]
    r = mc.convene(_deep_sample(), odds_ledger=led)
    by = {e["model"]: e for e in r["court"]["scores"]}
    assert set(by) == {"swell", "ml"}
    assert by["swell"]["n_resolved"] == 40  # poison rows did not count
    assert by["swell"]["brier"] == pytest.approx(0.0044, abs=1e-4)


def test_integer_realized_accepted():
    led = _ledger(0, 0, "swell", 0.5, 0.5)
    led += [{"date": "2026-01-01", "model": "swell", "horizon_bd": 5, "p": 0.2, "realized": 1},
            {"date": "2026-01-02", "model": "swell", "horizon_bd": 5, "p": 0.2, "realized": 0}]
    r = mc.convene(_deep_sample(), odds_ledger=led)
    assert r["court"]["scores"][0]["n_resolved"] == 2


# ---------------------------------------------------------------------------
# Output discipline
# ---------------------------------------------------------------------------

def test_output_is_json_serializable_and_deterministic():
    led = _ledger(10, 30, "swell", 0.9, 0.05) + _ledger(10, 30, "ml", 0.5, 0.5)
    a = mc.convene(_deep_sample(), odds_ledger=led)
    b = mc.convene(_deep_sample(), odds_ledger=led)
    ja, jb = json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True)
    assert ja == jb


def test_adjudication_is_one_printable_line_without_dashes():
    r = mc.convene(_deep_sample())
    adj = r["adjudication"]
    assert isinstance(adj, str) and adj
    assert "\n" not in adj
    # repo convention: the court adds no em or en dashes of its own
    # (the fixture is dash free, so any dash here is court-generated)
    dumped = json.dumps(r)
    assert "\u2014" not in dumped and "\u2013" not in dumped
