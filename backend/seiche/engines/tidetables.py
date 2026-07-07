"""Tide Tables — analog forecasting: the pattern layer made predictive.

A tide table predicts a basin's future not by running physics forward but by
reading the basin's own recorded past: the same forcing configurations keep
recurring, so the best local forecast is "find every time the water looked
like this and read off what it did next" (Lorenz's analog method, the oldest
trick in operational weather forecasting). Funding markets are an enclosed
basin forced by a repeating calendar — they rhyme constantly.

Echo asks the *context* question against six labeled episodes: "does today
resemble a known break?" Tide Tables asks the *predictive* question against
ALL history, labeled or not: take today's trailing state trajectory, find its
k nearest historical analogs, and publish what actually followed them —

  1. a forward fan of SOFR−IORB (the empirical distribution of the analogs'
     next-10bd paths, anchored at today's spread);
  2. analog event odds: the share of analogs followed by a funding event
     within 5bd (same event definition as PROOF/ML Lab), with a Wilson CI,
     next to the climatological base rate and the lift over it;
  3. a NOVELTY gauge: today's nearest-neighbor distance as a percentile of
     history's own nearest-neighbor distances. Uncharted water means the fan
     is drawn from far-away matches — flagged on the forecast itself — and
     "the board has never looked like this" is surfaced as its own signal;
  4. an honest walk-forward hindcast: the same analog odds recomputed for
     every historical day and Brier/AUROC-scored against climatology. When
     analogs don't beat the base rate, the page says so.

Honesty rules (same bar as PROOF):
  - state trajectories use EXPANDING-window z-scores only — no look-ahead;
  - an analog may not share a single day with the query window;
  - every analog's forward outcome closed strictly before the query date
    (an analog's future is the operator's past) — enforced by construction
    since analogs end >= window days before the query, and verified by a
    unit test that truncating the future leaves the forecast unchanged;
  - analogs are de-clustered (min separation between end dates) so one
    episode can't vote k times.

Like Echo and The Tell, this is reported alongside the Seiche Index but not
weighted into it: it is a forecast, not evidence of stress.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from seiche.config import (
    EPISODES,
    TIDE_EVENT_FWD_D,
    TIDE_HORIZON_BD,
    TIDE_K,
    TIDE_MIN_CANDIDATES,
    TIDE_MIN_SEP_D,
    TIDE_NOVELTY_UNCHARTED,
    TIDE_WARMUP_D,
    TIDE_WINDOW_D,
    TIDE_Z_MIN_PERIODS,
)
from seiche.engines.backtest import _funding_events, _wilson


def build_state(components: dict[str, pd.Series]) -> pd.DataFrame:
    """Expanding-z daily state matrix — no look-ahead, unlike Echo's
    full-sample z (fine for resemblance, not for a forecast)."""
    df = pd.concat(components, axis=1).sort_index()
    df = df.asfreq("B").ffill(limit=7)
    mu = df.expanding(TIDE_Z_MIN_PERIODS).mean()
    sd = df.expanding(TIDE_Z_MIN_PERIODS).std()
    z = (df - mu) / sd.where(sd != 0)
    return z.dropna(how="all")


def _window_matrix(z: pd.DataFrame, window: int) -> tuple[np.ndarray, np.ndarray]:
    """Flattened trailing windows. Row i ends at z.index[i + window - 1].
    Returns (matrix, valid) — windows with >25% missing values are invalid."""
    arr = z.to_numpy(dtype=float)
    sw = np.lib.stride_tricks.sliding_window_view(arr, window, axis=0)  # (M, C, W)
    m = sw.reshape(sw.shape[0], -1)
    valid = np.isnan(m).mean(axis=1) <= 0.25
    return np.nan_to_num(m, nan=0.0), valid


def _select_analogs(
    dists: np.ndarray, allowed: np.ndarray, k: int, min_sep: int
) -> list[int]:
    """Greedy nearest-first selection with a minimum separation between end
    positions, so one clustered episode can't cast k votes."""
    chosen: list[int] = []
    for j in np.argsort(dists):
        if not allowed[j]:
            continue
        if any(abs(int(j) - c) < min_sep for c in chosen):
            continue
        chosen.append(int(j))
        if len(chosen) >= k:
            break
    return chosen


def _auroc(y: np.ndarray, p: np.ndarray) -> float | None:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = pd.Series(np.concatenate([pos, neg])).rank().to_numpy()
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2)
                 / (len(pos) * len(neg)))


def _episode_tag(end: pd.Timestamp) -> str | None:
    for ep_date, label in EPISODES.items():
        delta = (pd.Timestamp(ep_date) - end).days
        if 0 <= delta <= 45:
            return f"T-{delta}d before {label}"
    return None


def analyze(
    components: dict[str, pd.Series],
    spread_bp: pd.Series,
    *,
    window: int = TIDE_WINDOW_D,
    k: int = TIDE_K,
    horizon: int = TIDE_HORIZON_BD,
    min_sep: int = TIDE_MIN_SEP_D,
    warmup: int = TIDE_WARMUP_D,
    with_hindcast: bool = True,
) -> dict:
    z = build_state(components)
    if len(z) < warmup + 3 * window:
        return {"ok": False, "reason": f"insufficient state history ({len(z)}d)"}

    grid = z.index
    spread_g = spread_bp.reindex(grid).ffill(limit=7)
    if spread_g.dropna().empty:
        return {"ok": False, "reason": "no spread history on the state grid"}
    sp = spread_g.to_numpy(dtype=float)
    n = len(grid)

    m, valid = _window_matrix(z, window)  # row i ends at position i + window - 1
    n_win = m.shape[0]
    if not valid[-1]:
        return {"ok": False, "reason": "current window too sparse"}

    # Same event definition as PROOF / ML Lab, mapped to grid positions.
    events = _funding_events(spread_bp)
    ev_flag = np.zeros(n, dtype=bool)
    ev_pos = grid.searchsorted(events)
    ev_flag[ev_pos[ev_pos < n]] = True
    # fwd_event[p]: any event strictly after p, within TIDE_EVENT_FWD_D bd.
    fwd_event = np.zeros(n, dtype=bool)
    for p in range(n):
        fwd_event[p] = bool(ev_flag[p + 1 : p + 1 + TIDE_EVENT_FWD_D].any())

    # Pairwise RMSE distances between all windows (needed for the novelty
    # baseline and the hindcast anyway; ~2k x 2k floats — cheap).
    sq = (m * m).sum(axis=1)
    d2 = sq[:, None] + sq[None, :] - 2.0 * (m @ m.T)
    dist = np.sqrt(np.clip(d2, 0.0, None) / m.shape[1])
    big = float(np.nanmax(dist)) + 1.0
    dist[:, ~valid] = big  # invalid windows can never be analogs

    def _allowed(row: int) -> np.ndarray:
        """Candidate analog ends for the query window ending at row `row`:
        no shared day with the query (end <= query end - window), which also
        guarantees every 5bd forward outcome closed before the query date."""
        a = np.zeros(n_win, dtype=bool)
        a[: max(row - window + 1, 0)] = True
        return a & valid

    def _odds(row: int) -> tuple[float | None, float | None, list[int]]:
        allowed = _allowed(row)
        if allowed.sum() < TIDE_MIN_CANDIDATES:
            return None, None, []
        chosen = _select_analogs(dist[row], allowed, k, min_sep)
        if len(chosen) < max(3, k // 2):
            return None, None, []
        ends = [c + window - 1 for c in chosen]
        p = float(np.mean([fwd_event[e] for e in ends]))
        cand_ends = np.nonzero(allowed)[0] + window - 1
        clim = float(fwd_event[cand_ends].mean())
        return p, clim, chosen

    # ---- live forecast ------------------------------------------------------
    live_row = n_win - 1
    p_live, clim_live, chosen = _odds(live_row)
    if p_live is None:
        return {"ok": False, "reason": "too few candidate analogs"}
    hits = int(round(p_live * len(chosen)))

    # Forward fan of the spread, anchored at today's level.
    now_pos = n - 1
    spread_now = float(pd.Series(sp).ffill().iloc[-1])
    paths = []
    for c in chosen:
        e = c + window - 1
        if e + horizon < n:
            paths.append(sp[e + 1 : e + 1 + horizon] - sp[e])
    fan = []
    if paths:
        pm = np.vstack(paths)
        future = pd.bdate_range(grid[now_pos], periods=horizon + 1)[1:]
        for h in range(horizon):
            col = pm[:, h][~np.isnan(pm[:, h])]
            if len(col) < 3:
                continue
            q = np.percentile(col, [10, 25, 50, 75, 90])
            fan.append({
                "date": future[h].date().isoformat(),
                "p10": round(spread_now + q[0], 1),
                "p25": round(spread_now + q[1], 1),
                "median": round(spread_now + q[2], 1),
                "p75": round(spread_now + q[3], 1),
                "p90": round(spread_now + q[4], 1),
            })

    # Analog table — the receipts behind the fan.
    analog_rows = []
    for c in chosen:
        e = c + window - 1
        nxt = sp[e + 1 : e + 1 + TIDE_EVENT_FWD_D]
        nxt = nxt[~np.isnan(nxt)]
        analog_rows.append({
            "end_date": grid[e].date().isoformat(),
            "distance": round(float(dist[live_row, c]), 3),
            "event_within_5bd": bool(fwd_event[e]),
            "max_move_5bd_bp": round(float(nxt.max() - sp[e]), 1) if len(nxt) and not math.isnan(sp[e]) else None,
            "episode": _episode_tag(grid[e]),
        })

    # ---- novelty: how charted is today's water? -----------------------------
    # NN distance for every scored historical row, each against only its own
    # past (expanding — honest), forms the reference distribution.
    first_scored = max(warmup - window + 1, window + min_sep)
    nn_hist: list[float] = []
    for r in range(first_scored, n_win - 1):
        a = _allowed(r)
        if valid[r] and a.any():
            nn_hist.append(float(dist[r, a].min()))
    a_live = _allowed(live_row)
    d_now = float(dist[live_row, a_live].min())
    nov_pctl = round(float(np.mean(np.array(nn_hist) <= d_now) * 100.0), 0) if nn_hist else None
    nov_verdict = (
        "uncharted" if nov_pctl is not None and nov_pctl >= TIDE_NOVELTY_UNCHARTED
        else "sparsely charted" if nov_pctl is not None and nov_pctl >= 70
        else "well charted"
    )

    # ---- walk-forward hindcast: does this beat climatology? -----------------
    skill: dict = {"ok": False, "reason": "hindcast not run"}
    hind = None
    if with_hindcast:
        ps, cs, ys, rows_scored = [], [], [], []
        for r in range(first_scored, n_win):
            e = r + window - 1
            if e + TIDE_EVENT_FWD_D >= n:  # label not closed yet
                continue
            if not valid[r]:
                continue
            p_r, c_r, ch = _odds(r)
            if p_r is None:
                continue
            ps.append(p_r); cs.append(c_r); ys.append(float(fwd_event[e]))
            rows_scored.append(e)
        if len(ps) >= 60:
            pa, ca, ya = np.array(ps), np.array(cs), np.array(ys)
            brier = float(np.mean((pa - ya) ** 2))
            brier_clim = float(np.mean((ca - ya) ** 2))
            auroc = _auroc(ya, pa)
            beats = brier < brier_clim and (auroc or 0.0) > 0.5
            skill = {
                "ok": True,
                "n_scored": len(ps),
                "n_events": int(ya.sum()),
                "brier": round(brier, 4),
                "brier_climatology": round(brier_clim, 4),
                "brier_skill": round(1.0 - brier / brier_clim, 3) if brier_clim > 0 else None,
                "auroc": round(auroc, 3) if auroc is not None else None,
                "verdict": (
                    "analogs beat climatology out-of-sample — use the odds"
                    if beats else
                    "analogs do NOT beat climatology on this sample — read the fan as context, trust the base rate"
                ),
            }
            hind = pd.Series(pa, index=grid[rows_scored])

    lift = round(p_live / clim_live, 2) if clim_live and clim_live > 0 else None
    result = {
        "ok": True,
        "asof": grid[-1].date().isoformat(),
        "window_d": window,
        "k": len(chosen),
        "horizon_bd": horizon,
        "state_components": list(z.columns),
        "spread_now_bp": round(spread_now, 1),
        "recent_spread": [
            [d.date().isoformat(), round(float(v), 1)]
            for d, v in spread_g.dropna().iloc[-45:].items()
        ],
        "fan": fan,
        "analogs": analog_rows,
        "event_odds": {
            "p": round(p_live, 3),
            "hits": hits,
            "n": len(chosen),
            "ci95": _wilson(hits, len(chosen)),
            "base_rate": round(clim_live, 3),
            "lift": lift,
        },
        "novelty": {"distance": round(d_now, 3), "pctl": nov_pctl, "verdict": nov_verdict},
        "skill": skill,
        "caveats": [
            "k analogs overlap in market regimes even after de-clustering — the CI understates true uncertainty",
            "expanding-z state — early-sample analogs are scored on less-settled statistics",
            f"novelty {nov_verdict}: " + (
                "nearest analogs are far — the fan is extrapolation, weight the base rate"
                if nov_verdict != "well charted" else "nearest analogs are genuinely close"
            ),
        ],
        "method": (
            f"state = expanding-z of {len(z.columns)} plumbing series; query = trailing {window}bd "
            f"trajectory; analogs = {k} nearest RMSE matches over all history, min {min_sep}bd apart, "
            f"ending >= {window}bd before today (no shared days, forward outcomes closed); fan = "
            f"percentiles of analog forward spread paths ({horizon}bd); event odds = share of analogs "
            f"with a funding event (PROOF definition) within {TIDE_EVENT_FWD_D}bd, vs candidate base "
            "rate; novelty = NN-distance pctl vs each day's own past; skill = walk-forward Brier/AUROC "
            "vs climatology"
        ),
    }
    if hind is not None:
        result["_hindcast"] = hind  # stripped before serialization; used by tests
    return result
