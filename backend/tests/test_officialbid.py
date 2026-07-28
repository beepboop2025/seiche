"""Foreign Official Bid tests: stated rules, honest degradation, and the net
positioning additions to RV X-Ray. Synthetic data only, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche.engines import officialbid, rvxray

pytestmark = pytest.mark.limit_memory("256 MB")

NO_DASH = ("—", "–", "-")


def _weeks(n: int, start: str = "2024-01-03") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="W-WED")


def _flat(n: int, level: float) -> np.ndarray:
    return np.full(n, level, dtype=float)


def _ramp_tail(base: np.ndarray, per_week: float, weeks: int = 13) -> np.ndarray:
    """Add a linear per-week drift over the last `weeks` observations."""
    out = base.copy()
    out[-weeks:] += per_week * np.arange(1, weeks + 1)
    return out


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------

def test_rotation_custody_down_rrp_up():
    idx = _weeks(60)
    # custody sheds $3B/wk for 13 weeks (=39B), foreign RRP absorbs $1.5B/wk
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +1_500.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"]
    assert r["classification"] == "rotation"
    assert r["custody_chg_13w_b"] == -39.0
    assert r["foreign_rrp_chg_13w_b"] == 19.5
    assert r["custody_b"] == 2900.0 - 39.0
    assert r["footprint_b"] == round((2_900_000 - 39_000 + 350_000 + 19_500) / 1000.0, 1)
    assert "rotation" in r["letter_line"]


def test_rotation_custody_down_rrp_flat():
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_flat(60, 350_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "rotation"


def test_retreat_both_down():
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), -1_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "retreat"
    assert "retreat" in r["letter_line"] or "leaving" in r["letter_line"]


def test_build_both_up():
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), +2_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +1_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "build"
    assert "build" in r["letter_line"]


def test_steady_inside_flat_band():
    idx = _weeks(60)
    # 13w drifts of +-1B sit inside the 5B band
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -70.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +70.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    assert r["ok"] and r["classification"] == "steady"


# ---------------------------------------------------------------------------
# Letter line and payload contract
# ---------------------------------------------------------------------------

def test_letter_line_is_one_clean_sentence():
    idx = _weeks(60)
    cust = pd.Series(_ramp_tail(_flat(60, 2_900_000.0), -3_000.0), index=idx)
    rrp = pd.Series(_ramp_tail(_flat(60, 350_000.0), +1_500.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    line = r["letter_line"]
    assert isinstance(line, str) and line.endswith(".")
    assert ". " not in line  # one sentence; decimals in numbers allowed
    for ch in NO_DASH:
        assert ch not in line
    assert "39.0B" in line  # numbers inline, plain


def test_payload_keys_and_series_shape():
    idx = _weeks(200)
    cust = pd.Series(_flat(200, 2_900_000.0), index=idx)
    rrp = pd.Series(_flat(200, 350_000.0), index=idx)
    r = officialbid.analyze(cust, rrp)
    for k in ("ok", "method", "caveats", "asof", "classification", "letter_line",
              "custody_chg_4w_b", "footprint_chg_13w_b", "series"):
        assert k in r
    assert len(r["series"]) == 156  # capped history for the chart
    assert all(len(row) == 4 for row in r["series"])
    assert r["asof"] == idx[-1].date().isoformat()


def test_fima_repo_optional_and_drawn_flag():
    idx = _weeks(60)
    cust = pd.Series(_flat(60, 2_900_000.0), index=idx)
    rrp = pd.Series(_flat(60, 350_000.0), index=idx)
    # absent: honest caveat, no fields invented
    r0 = officialbid.analyze(cust, rrp)
    assert r0["fima_repo_b"] is None and not r0["fima_drawn"]
    assert any("FIMA" in c for c in r0["caveats"])
    # an empty Series (the assemble _pts default) counts as absent, not a crash
    re = officialbid.analyze(cust, rrp, fima_repo_weekly=pd.Series(dtype=float))
    assert re["ok"] and re["fima_repo_b"] is None
    # drawn: 5B outstanding flags the stress tell
    fima = pd.Series(_flat(60, 0.0), index=idx)
    fima.iloc[-3:] = 5_000.0
    r1 = officialbid.analyze(cust, rrp, fima_repo_weekly=fima)
    assert r1["fima_repo_b"] == 5.0 and r1["fima_drawn"]
    assert any("borrowing" in c for c in r1["caveats"])


def test_degrades_without_history_or_inputs():
    idx = _weeks(6)
    short = pd.Series(_flat(6, 2_900_000.0), index=idx)
    r = officialbid.analyze(short, pd.Series(_flat(6, 350_000.0), index=idx))
    assert not r["ok"] and "insufficient" in r["reason"]
    r2 = officialbid.analyze(pd.Series(dtype=float), pd.Series(_flat(6, 1.0), index=idx))
    assert not r2["ok"]


# ---------------------------------------------------------------------------
# RV X-Ray: net rides alongside gross, additive only
# ---------------------------------------------------------------------------

def _tff_two_weeks() -> pd.DataFrame:
    rows = []
    for date in (pd.Timestamp("2026-01-06"), pd.Timestamp("2026-01-13")):
        rows += [
            {
                "date": date, "contract": "UST 2Y NOTE", "open_interest_all": 100_000.0,
                "lev_money_positions_long_all": 10_000.0,
                "lev_money_positions_short_all": 40_000.0,
                "asset_mgr_positions_long_all": 35_000.0,
                "asset_mgr_positions_short_all": 5_000.0,
            },
            {
                "date": date, "contract": "UST 5Y NOTE", "open_interest_all": 80_000.0,
                "lev_money_positions_long_all": 20_000.0,
                "lev_money_positions_short_all": 5_000.0,
                "asset_mgr_positions_long_all": 8_000.0,
                "asset_mgr_positions_short_all": 1_000.0,
            },
            {
                "date": date, "contract": "FED FUNDS", "open_interest_all": 50_000.0,
                "lev_money_positions_long_all": 5_000.0,
                "lev_money_positions_short_all": 9_000.0,
                "asset_mgr_positions_long_all": 8_000.0,
                "asset_mgr_positions_short_all": 1_000.0,
            },
        ]
    return pd.DataFrame(rows)


def test_rvxray_net_total_and_per_contract():
    df = _tff_two_weeks()
    dvp = pd.Series([4e12, 4e12], index=pd.to_datetime(["2026-01-06", "2026-01-13"]))
    r = rvxray.analyze(df, dvp)
    assert r["ok"]
    # existing keys untouched
    assert r["gross_short_b"] == 8.5   # 40k x 200k + 5k x 100k
    assert r["pair_proxy_b"] == 7.5    # min(40k,35k) x 200k + min(5k,8k) x 100k
    # net total: 2Y (10k-40k) x 200k = -6.0B; 5Y (20k-5k) x 100k = +1.5B
    assert r["net_b"] == -4.5
    by = {row["contract"]: row for row in r["by_contract"]}
    assert set(by) == {"UST 2Y NOTE", "UST 5Y NOTE"}  # FED FUNDS is not an RV leg
    assert by["UST 2Y NOTE"]["net_b"] == -6.0 and by["UST 2Y NOTE"]["gross_short_b"] == 8.0
    assert by["UST 5Y NOTE"]["net_b"] == 1.5 and by["UST 5Y NOTE"]["gross_short_b"] == 0.5
    # series rows carry net as a 4th column, prior columns unchanged
    assert all(len(row) == 4 for row in r["series"])
    assert r["series"][-1][2] == 8.5 and r["series"][-1][3] == -4.5
