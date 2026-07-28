"""Where the Dollars Sit tests: the identity closes to the dollar, the flow
decomposition sums to the reserve change, the residual sizes itself, and the
prose passes the same publish-blocking lint the letter does. Synthetic data
only, built so the identity holds by construction, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from seiche.dispatch_daily import lint_letter
from seiche.engines import ledger

pytestmark = pytest.mark.limit_memory("256 MB")

# Realistic H.4.1 levels in $M, so the engine's unit heuristic is exercised
# the way the live board exercises it.
ASSETS_M = 6_700_000.0
CURRENCY_M = 2_470_000.0
TGA_M = 830_000.0
FRRP_M = 350_000.0
ONRRP_B = 0.5
RESID_M = 30_000.0


def _weeks(n: int, start: str = "2025-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="W-WED")


def _flat(n: int, level: float) -> np.ndarray:
    return np.full(n, float(level), dtype=float)


def _daily_rrp(idx: pd.DatetimeIndex, values: np.ndarray) -> pd.Series:
    """ON RRP arrives daily; only the Wednesday print belongs in the ledger."""
    d = pd.date_range(idx[0], idx[-1], freq="D")
    s = pd.Series(np.nan, index=d)
    s.loc[idx] = values
    return s.ffill().bfill()


def _book(
    n: int = 60,
    assets_m: np.ndarray | None = None,
    currency_m: np.ndarray | None = None,
    tga_m: np.ndarray | None = None,
    onrrp_b: np.ndarray | None = None,
    frrp_m: np.ndarray | None = None,
    resid_m: np.ndarray | None = None,
) -> dict:
    """A balance sheet where the identity holds exactly: reserves are solved
    for, so any residual the engine reports is the residual we planted."""
    idx = _weeks(n)
    assets_m = _flat(n, ASSETS_M) if assets_m is None else assets_m
    currency_m = _flat(n, CURRENCY_M) if currency_m is None else currency_m
    tga_m = _flat(n, TGA_M) if tga_m is None else tga_m
    onrrp_b = _flat(n, ONRRP_B) if onrrp_b is None else onrrp_b
    frrp_m = _flat(n, FRRP_M) if frrp_m is None else frrp_m
    resid_m = _flat(n, RESID_M) if resid_m is None else resid_m

    reserves_m = assets_m - currency_m - tga_m - onrrp_b * 1000.0 - frrp_m - resid_m
    return {
        "walcl": pd.Series(assets_m, index=idx),
        "wcurcir": pd.Series(currency_m, index=idx),
        "wresbal": pd.Series(reserves_m, index=idx),
        "wtregen": pd.Series(tga_m, index=idx),
        "rrp_daily": _daily_rrp(idx, onrrp_b),
        "foreign_rrp": pd.Series(frrp_m, index=idx),
    }


# ---------------------------------------------------------------------------
# The identity: it closes to the dollar or the engine has no business printing
# ---------------------------------------------------------------------------

def test_levels_reconcile_exactly():
    r = ledger.reconcile(**_book())
    assert r["ok"]
    lv = r["levels"]
    assert lv["check_b"] == 0.0
    assert lv["assets_b"] == round(ASSETS_M / 1000.0, 1)
    assert lv["currency_b"] == round(CURRENCY_M / 1000.0, 1)
    assert lv["tga_b"] == round(TGA_M / 1000.0, 1)
    assert lv["onrrp_b"] == round(ONRRP_B, 1)
    assert lv["foreign_rrp_b"] == round(FRRP_M / 1000.0, 1)
    # the planted residual comes back as the planted residual, not a fudge
    assert lv["residual_b"] == round(RESID_M / 1000.0, 1)
    named = (lv["currency_b"] + lv["reserves_b"] + lv["tga_b"] + lv["onrrp_b"]
             + lv["foreign_rrp_b"] + lv["residual_b"])
    assert abs(lv["assets_b"] - named) < 0.05
    assert lv["residual_share_pct"] == round(RESID_M / ASSETS_M * 100.0, 3)


def test_flows_sum_to_the_reserve_change_every_week():
    rng = np.random.default_rng(11)
    n = 60
    r = ledger.reconcile(**_book(
        n=n,
        assets_m=ASSETS_M + np.cumsum(rng.normal(0, 4_000.0, n)),
        currency_m=CURRENCY_M + np.cumsum(rng.normal(0, 800.0, n)),
        tga_m=TGA_M + np.cumsum(rng.normal(0, 30_000.0, n)),
        onrrp_b=np.abs(rng.normal(20.0, 8.0, n)),
        frrp_m=FRRP_M + np.cumsum(rng.normal(0, 2_000.0, n)),
        resid_m=RESID_M + rng.normal(0, 400.0, n),
    ))
    assert r["ok"]
    for row in r["flows_13w"]:
        assert row["check_b"] == 0.0
        legs = sum(row[f"{k}_b"] for k in
                   ("assets", "currency", "tga", "onrrp", "foreign_rrp", "residual"))
        assert abs(legs - row["reserves_chg_b"]) < 0.05
    assert len(r["flows_13w"]) == 13
    assert r["week"] == r["flows_13w"][-1]


def test_units_read_the_same_in_millions_or_billions():
    book_m = _book()
    book_b = {k: (v if k == "rrp_daily" else v / 1000.0) for k, v in book_m.items()}
    a, b = ledger.reconcile(**book_m), ledger.reconcile(**book_b)
    assert a["ok"] and b["ok"]
    assert a["levels"] == b["levels"]
    assert a["letter_line"] == b["letter_line"]


# ---------------------------------------------------------------------------
# Attribution: the question every desk asks on H.4.1 day
# ---------------------------------------------------------------------------

def test_reserves_fall_entirely_on_a_tga_rebuild():
    n = 60
    tga = _flat(n, TGA_M)
    tga[-1] += 100_000.0            # the TGA rebuilds $100B, nothing else moves
    r = ledger.reconcile(**_book(n=n, tga_m=tga))
    assert r["ok"]
    att = r["attribution"]
    assert att["driver"] == "tga"
    assert att["driver_contribution_b"] == -100.0
    assert att["reserves_chg_b"] == -100.0
    assert att["share_of_move"] == 1.0
    assert att["driver_share_of_gross"] == 1.0
    assert att["sole_driver"] is True
    # and the sentence says so, in words, with the number inline
    line = r["letter_line"]
    assert "the TGA rebuilt $100B" in line
    assert "Reserves fell $100B" in line
    assert r["week"]["tga_b"] == -100.0
    assert all(r["week"][f"{k}_b"] == 0.0
               for k in ("assets", "currency", "onrrp", "foreign_rrp", "residual"))


def test_reserves_fall_on_a_shrinking_balance_sheet():
    n = 60
    assets = _flat(n, ASSETS_M)
    assets[-1] -= 60_000.0
    r = ledger.reconcile(**_book(n=n, assets_m=assets))
    att = r["attribution"]
    assert att["driver"] == "assets" and att["sole_driver"] is True
    assert att["driver_contribution_b"] == -60.0
    assert "the balance sheet shrank $60.0B" in r["letter_line"]


def test_offsetting_legs_are_not_a_sole_driver():
    n = 60
    tga, onrrp = _flat(n, TGA_M), _flat(n, ONRRP_B)
    tga[-1] += 100_000.0        # TGA drains 100
    onrrp[-1] = ONRRP_B + 90.0  # ON RRP absorbs another 90
    r = ledger.reconcile(**_book(n=n, tga_m=tga, onrrp_b=onrrp))
    att = r["attribution"]
    assert att["driver"] == "tga"
    assert att["sole_driver"] is False
    assert att["reserves_chg_b"] == -190.0
    assert "ON RRP absorbed $90.0B" in r["letter_line"]


def test_flat_week_says_flat():
    r = ledger.reconcile(**_book())
    assert r["attribution"]["reserves_chg_b"] == 0.0
    assert r["letter_line"].startswith("Reserves held flat on the week at ")
    assert "no leg moved more than a rounding error" in r["letter_line"]


# ---------------------------------------------------------------------------
# The residual: stable is the check, jumping is the alarm
# ---------------------------------------------------------------------------

def _noisy_residual(n: int, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return RESID_M + rng.normal(0.0, 400.0, n)


def test_stable_residual_reads_as_stable():
    n = 60
    r = ledger.reconcile(**_book(n=n, resid_m=_noisy_residual(n)))
    rc = r["residual_check"]
    assert rc["jump"] is False
    assert abs(rc["robust_z"]) < ledger.JUMP_Z
    assert rc["sigma_b"] is not None and rc["sigma_b"] < 2.0
    assert "holding at" in rc["verdict"]
    assert "not an error term" in rc["note"]
    assert not any("jumped" in c for c in r["caveats"])


def test_residual_jump_is_flagged_and_caveated():
    n = 60
    resid = _noisy_residual(n)
    resid[-1] += 40_000.0          # $40B appears in a week that names no leg
    r = ledger.reconcile(**_book(n=n, resid_m=resid))
    rc = r["residual_check"]
    assert rc["jump"] is True
    assert rc["chg_b"] > 35.0
    assert abs(rc["robust_z"]) >= ledger.JUMP_Z
    assert "read the named legs again" in rc["verdict"]
    assert any("jumped" in c and "question rather than an answer" in c for c in r["caveats"])
    # the jump is still inside the identity: the legs still sum to the change
    assert r["week"]["check_b"] == 0.0


def test_small_residual_wobble_does_not_trip_the_alarm():
    n = 60
    resid = _noisy_residual(n)
    resid[-1] += 2_000.0           # $2B, under the absolute floor
    r = ledger.reconcile(**_book(n=n, resid_m=resid))
    assert r["residual_check"]["jump"] is False


# ---------------------------------------------------------------------------
# Honest degradation
# ---------------------------------------------------------------------------

def test_missing_currency_degrades_with_a_caveat():
    book = _book()
    for absent in (None, pd.Series(dtype=float)):
        b = dict(book, wcurcir=absent)
        r = ledger.reconcile(**b)
        assert r["ok"] and r["currency_named"] is False
        assert r["levels"]["currency_b"] == 0.0
        # currency is not invented, it falls into the residual and says so
        assert r["levels"]["residual_b"] == round((RESID_M + CURRENCY_M) / 1000.0, 1)
        assert r["levels"]["check_b"] == 0.0
        assert any("currency in circulation was not supplied" in c for c in r["caveats"])
    # with currency named the residual is the small block it should be
    assert ledger.reconcile(**book)["levels"]["residual_b"] == round(RESID_M / 1000.0, 1)


def test_missing_onrrp_degrades_with_a_caveat():
    book = dict(_book(), rrp_daily=pd.Series(dtype=float))
    r = ledger.reconcile(**book)
    assert r["ok"] and r["onrrp_named"] is False
    assert r["levels"]["onrrp_b"] == 0.0
    assert r["levels"]["check_b"] == 0.0
    assert any("no ON RRP series" in c for c in r["caveats"])


def test_holiday_wednesday_carries_and_says_so():
    book = _book()
    idx = book["walcl"].index
    book["rrp_daily"] = book["rrp_daily"].drop(idx[-1])   # no print that Wednesday
    r = ledger.reconcile(**book)
    assert r["ok"] and r["onrrp_named"] is True
    assert any("carry the last print before them" in c for c in r["caveats"])


def test_onrrp_takes_the_wednesday_print_not_the_week():
    n = 60
    onrrp = _flat(n, 20.0)
    book = _book(n=n, onrrp_b=onrrp)
    idx = book["walcl"].index
    # a spike on the Monday before the last Wednesday must not enter the ledger
    book["rrp_daily"].loc[idx[-1] - pd.Timedelta(days=2)] = 900.0
    r = ledger.reconcile(**book)
    assert r["levels"]["onrrp_b"] == 20.0
    assert r["levels"]["check_b"] == 0.0


def test_refuses_without_inputs_or_history():
    book = _book()
    for key in ("walcl", "wresbal", "wtregen", "foreign_rrp"):
        r = ledger.reconcile(**dict(book, **{key: pd.Series(dtype=float)}))
        assert not r["ok"] and r["reason"]
    short = _book(n=6)
    r = ledger.reconcile(**short)
    assert not r["ok"] and "insufficient" in r["reason"]


def test_disjoint_grids_refuse_rather_than_invent():
    book = _book()
    book["wtregen"] = pd.Series(book["wtregen"].to_numpy(), index=_weeks(60, "2015-01-07"))
    r = ledger.reconcile(**book)
    assert not r["ok"]


# ---------------------------------------------------------------------------
# Payload contract and the house copy rules
# ---------------------------------------------------------------------------

def test_payload_contract():
    n = 200
    r = ledger.reconcile(**_book(n=n), weeks=52)
    for k in ("ok", "asof", "weeks", "currency_named", "onrrp_named", "levels", "week",
              "attribution", "residual_check", "flows_13w", "series", "legs",
              "letter_line", "caveats", "method"):
        assert k in r
    assert r["asof"] == _weeks(n)[-1].date().isoformat()
    assert r["weeks"] == 52
    assert len(r["series"]) == 53          # the window, not the whole history
    assert all(len(row) == 8 for row in r["series"])
    assert [leg["key"] for leg in r["legs"]] == list(ledger.LEG_KEYS)
    # a closing line that serializes as -0.0 makes a reader wonder what broke
    assert str(r["levels"]["check_b"]) == "0.0"
    assert all(str(row["check_b"]) == "0.0" for row in r["flows_13w"])
    r_long = ledger.reconcile(**_book(n=n), weeks=180)
    assert len(r_long["series"]) == 156    # capped for the chart


def test_window_floor_holds():
    r = ledger.reconcile(**_book(n=60), weeks=1)
    assert r["ok"] and r["weeks"] == ledger.MIN_WEEKS


def test_prose_passes_the_publish_blocking_lint():
    n = 60
    resid = _noisy_residual(n)
    resid[-1] += 40_000.0
    for r in (ledger.reconcile(**_book()),
              ledger.reconcile(**_book(n=n, resid_m=resid)),
              ledger.reconcile(**dict(_book(), wcurcir=None))):
        texts = [r["letter_line"], r["method"], r["residual_check"]["verdict"],
                 r["residual_check"]["note"], *r["caveats"],
                 *[leg["label"] for leg in r["legs"]]]
        assert lint_letter(*texts) == []
        for ch in ("—", "–"):
            assert all(ch not in t for t in texts if t)


def test_letter_line_is_one_sentence():
    line = ledger.reconcile(**_book())["letter_line"]
    assert isinstance(line, str) and line.endswith(".")
    assert ". " not in line            # one sentence; decimals in numbers allowed
