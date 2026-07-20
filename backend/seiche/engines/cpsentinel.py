"""CP Sentinel — do DeFi exploits narrow commercial-paper spreads?

arXiv:2601.08263 ("A Blessing in Disguise? DeFi Exploits and Short-Horizon
Responses in U.S. Commercial Paper Spreads", Lin) documents a short-horizon
FLIGHT-TO-QUALITY pattern that runs opposite to the prevailing contagion
hypothesis: in the wake of major DeFi exploits, spreads on 3-month AA
commercial paper tend to NARROW rather than widen. The paper's reading is a
liquidity-recycling channel — capital leaving DeFi is re-intermediated into
traditional cash-management markets, and SEC Rule 2a-7 segmentation makes
prime-eligible paper a plausible marginal destination. The paper is explicit
that the channel is INFERRED from pricing patterns and monthly holdings, not
directly identified (no daily fund-level routing is observed). This engine is
therefore an associational event-study, not causal evidence, and every
payload says so.

Design (expanding/trailing windows only, zero look-ahead):

  events      every hacks_usd day above the exploit threshold, declustered:
              exceedances within 5bd of each other are ONE event, with the
              max-loss day kept as the representative (runs declustering, the
              hydrology standard — independent exploits, not aftershocks).
  per event   the signed path of cp_spread_bp in [-5,+10]bd vs its own
              trailing 60bd median; pre = median over [-5,-1]bd, post_min =
              min over [+1,+10]bd, change = post_min − pre (negative =
              narrowing); hit = narrowing beyond 1 trailing-sigma (level std
              of the spread over the 60bd strictly before the event).
  placebo     100 seeded shuffles of the event dates over every position with
              full trailing/forward coverage (same 5bd min-gap): the hit rate
              a random calendar would print. The headline statistic is the
              percentile of the REAL hit rate inside the placebo
              distribution. Taking the MIN over a 10bd window is a
              selection-biased statistic by construction — the placebo is
              what keeps that honest, and it is the verdict driver.
  live        days since the last big exploit, whether a post-exploit
              narrowing window is open right now, and the spread level with
              its expanding percentile.

Honesty rules (the house bar):
  - trailing statistics are strictly pre-event; forward windows still open at
    the sample edge are published as censored and excluded from the hit rate;
  - the placebo rng is seeded (CPS_SEED): the board is deterministic, and
    every published number recomputes identically on a truncated sample —
    no published value changes when future data arrives (unit-tested);
  - fewer than CPS_MIN_EVENTS declustered events -> ok=True with verdict
    "insufficient events": a case table, not a statistic (no rate claimed);
  - NO composite score: this is context about a cross-market channel, not
    evidence of dollar-funding stress today (doctrine).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seiche.engines.backtest import _wilson

CPS_CLUSTER_BD = 5          # decluster run length: exceedances within 5bd = one event
CPS_PRE_BD = 5              # pre-event window: median over [-5,-1]bd
CPS_POST_BD = 10            # post-event window: min over [+1,+10]bd
CPS_TRAIL_BD = 60           # trailing baseline window (median + sigma), strictly pre-event
CPS_MIN_EVENTS = 3          # below this: case table, no rate claimed
CPS_SHUFFLES = 100          # placebo draws
CPS_SEED = 26010826         # arXiv:2601.08263 — fixed, deterministic board
CPS_MIN_HISTORY_BD = 90     # 60bd trailing + window + margin before the engine speaks
CPS_ACTIVE_PERCENTILE = 95.0  # placebo percentile needed for "channel active"

_OFFSETS_BD = list(range(-CPS_PRE_BD, CPS_POST_BD + 1))  # -5..+10


def _decluster(hacks: pd.Series, threshold: float) -> list[tuple[pd.Timestamp, float]]:
    """Runs declustering of exceedance days: two exceedances within
    CPS_CLUSTER_BD business days of each other belong to the same cluster;
    the cluster's representative event is its max-loss day (first on ties).
    Returns (date, usd) pairs sorted by date."""
    exc = hacks[hacks > threshold].sort_index()
    if exc.empty:
        return []
    clusters: list[list[tuple[pd.Timestamp, float]]] = []
    for d, v in exc.items():
        if clusters:
            prev_d = clusters[-1][-1][0]
            if np.busday_count(prev_d.date(), d.date()) <= CPS_CLUSTER_BD:
                clusters[-1].append((d, float(v)))
                continue
        clusters.append([(d, float(v))])
    events = []
    for cl in clusters:
        d, v = max(cl, key=lambda t: (t[1], -t[0].toordinal()))
        events.append((d, v))
    return sorted(events, key=lambda t: t[0])


def _event_row(cp: pd.Series, loc: int, date: pd.Timestamp, usd: float) -> dict:
    """The event-study row for one declustered exploit. `loc` is the position
    on the cp business-day grid of the first trading day at/after the exploit
    date. Trailing stats are strictly pre-event (no look-ahead); a post
    window that runs past the sample edge is censored, not scored."""
    idx = cp.index
    trail = cp.iloc[max(0, loc - CPS_TRAIL_BD) : loc]
    trail_ok = len(trail) >= CPS_TRAIL_BD
    trail_med = float(trail.median()) if len(trail) else None
    trail_sigma = float(trail.std()) if len(trail) >= 30 else None

    pre = cp.iloc[max(0, loc - CPS_PRE_BD) : loc]
    pre_bp = float(pre.median()) if len(pre) else None

    post = cp.iloc[loc + 1 : loc + 1 + CPS_POST_BD]  # (0, +10]bd
    window_open = len(post) < CPS_POST_BD
    post_min = float(post.min()) if len(post) else None

    change = (post_min - pre_bp) if (post_min is not None and pre_bp is not None) else None
    scored = trail_ok and trail_sigma is not None and not window_open and change is not None
    hit = bool(change < -trail_sigma) if scored else None

    path = []
    for off in _OFFSETS_BD:
        j = loc + off
        v = float(cp.iloc[j] - trail_med) if 0 <= j < len(idx) and trail_med is not None else None
        path.append(round(v, 2) if v is not None else None)

    return {
        "date": date.date().isoformat(),
        "cp_date": idx[loc].date().isoformat(),
        "exploit_usd": round(usd, 0),
        "pre_bp": round(pre_bp, 2) if pre_bp is not None else None,
        "post_min_bp": round(post_min, 2) if post_min is not None else None,
        "change_bp": round(change, 2) if change is not None else None,
        "trail_med_bp": round(trail_med, 2) if trail_med is not None else None,
        "trail_sigma_bp": round(trail_sigma, 2) if trail_sigma is not None else None,
        "hit": hit,
        "window_open": bool(window_open),
        "path_offsets_bd": _OFFSETS_BD,
        "path_bp_vs_trail_med": path,
    }


def _valid_positions(n: int) -> np.ndarray:
    """Grid positions where a full trailing baseline AND a closed forward
    window exist — the same constraint the scored real events satisfy."""
    lo = CPS_TRAIL_BD
    hi = n - 1 - CPS_POST_BD
    if hi < lo:
        return np.array([], dtype=int)
    return np.arange(lo, hi + 1, dtype=int)


def _placebo_rates(cp: pd.Series, n_events: int, valid: np.ndarray) -> list[float]:
    """100 seeded shuffles of the event dates: each draw picks n_events grid
    positions (uniformly, without replacement, respecting the same 5bd
    min-gap the declustering enforces) and re-scores the narrowing hit test.
    The distribution of those rates is the coin-flip calendar's answer."""
    rng = np.random.default_rng(CPS_SEED)
    x = cp.to_numpy(dtype=float)
    rates: list[float] = []
    for _ in range(CPS_SHUFFLES):
        allowed = valid.copy()
        picked: list[int] = []
        for _k in range(n_events):
            if allowed.size == 0:
                break
            loc = int(rng.choice(allowed))
            picked.append(loc)
            allowed = allowed[np.abs(allowed - loc) > CPS_CLUSTER_BD]
        hits = 0
        for loc in picked:
            trail = x[loc - CPS_TRAIL_BD : loc]
            sigma = float(np.std(trail, ddof=1))
            pre = float(np.median(x[loc - CPS_PRE_BD : loc]))
            change = float(np.min(x[loc + 1 : loc + 1 + CPS_POST_BD])) - pre
            if change < -sigma:
                hits += 1
        if picked:
            rates.append(hits / len(picked))
    return rates


def analyze(
    hacks_usd: pd.Series,
    cp_spread_bp: pd.Series,
    exploit_threshold_usd: float = 25_000_000,
) -> dict:
    """Event-study of CP-spread responses to major DeFi exploits.

    hacks_usd: daily total DeFi exploit losses in USD (zeros on quiet days),
    DatetimeIndex. cp_spread_bp: DCPN3M − DGS3MO in basis points, business-
    day DatetimeIndex. Pure function of the two series — expanding/trailing
    statistics only, so no published value changes when future data arrives.
    """
    cp = cp_spread_bp.dropna().sort_index()
    if len(cp) < CPS_MIN_HISTORY_BD:
        return {
            "ok": False,
            "reason": f"insufficient cp_spread_bp history ({len(cp)}bd < {CPS_MIN_HISTORY_BD}bd)",
        }
    hacks = hacks_usd.dropna().sort_index() if hacks_usd is not None else pd.Series(dtype=float)
    idx = cp.index
    asof = idx[-1].date().isoformat()

    # ---- live spread state (prefix-only: expanding percentile) -------------
    last = float(cp.iloc[-1])
    level_pctl = round(float((cp <= last).mean()) * 100.0, 0)
    raw_exc = hacks[hacks > exploit_threshold_usd]
    days_since = (
        int((hacks.index[-1].date() - raw_exc.index[-1].date()).days)
        if not raw_exc.empty else None
    )

    live: dict = {
        "cp_spread_bp": round(last, 2),
        "level_pctl": level_pctl,
        "days_since_big_exploit": days_since,
        "last_big_exploit_date": (
            raw_exc.index[-1].date().isoformat() if not raw_exc.empty else None
        ),
        "window_active": False,
        "last_event": None,
    }

    # ---- event catalog ------------------------------------------------------
    events = _decluster(hacks, exploit_threshold_usd)
    rows: list[dict] = []
    n_no_coverage = 0
    for d, usd in events:
        loc = int(idx.searchsorted(d))
        if loc >= len(idx):
            n_no_coverage += 1  # exploit after the last pricing day: no window at all
            continue
        rows.append(_event_row(cp, loc, d, usd))

    scored = [r for r in rows if r["hit"] is not None]
    hits = sum(1 for r in scored if r["hit"])
    n_scored = len(scored)

    if rows:
        last_row = rows[-1]
        last_loc = int(idx.searchsorted(pd.Timestamp(last_row["date"])))
        live["window_active"] = bool(last_loc + CPS_POST_BD >= len(idx))
        live["last_event"] = {
            "date": last_row["date"],
            "exploit_usd": last_row["exploit_usd"],
            "change_bp_so_far": last_row["change_bp"],
            "hit_so_far": last_row["hit"],
        }

    expanding = []
    run_hits = 0
    for k, r in enumerate(scored, start=1):
        run_hits += 1 if r["hit"] else 0
        expanding.append([k, round(run_hits / k, 3)])

    out: dict = {
        "ok": True,
        "asof": asof,
        "n_events": len(rows),
        "n_events_scored": n_scored,
        "n_events_no_cp_coverage": n_no_coverage,
        "events": rows,
        "hit_rate": None,
        "hit_rate_ci95": None,
        "hit_rate_expanding": expanding,
        "placebo": None,
        "placebo_percentile": None,
        "live": live,
    }

    if n_scored < CPS_MIN_EVENTS:
        out["verdict"] = "insufficient events"
        out["method"] = _method(exploit_threshold_usd, len(rows))
        out["caveats"] = _caveats(n_scored)
        return out

    hit_rate = hits / n_scored
    valid = _valid_positions(len(cp))
    rates = _placebo_rates(cp, n_scored, valid)
    percentile = round(float(np.mean([r <= hit_rate for r in rates])) * 100.0, 0) if rates else None

    out["hit_rate"] = round(hit_rate, 3)
    out["hit_rate_ci95"] = _wilson(hits, n_scored)
    out["placebo"] = {
        "n_shuffles": CPS_SHUFFLES,
        "seed": CPS_SEED,
        "hit_rate_mean": round(float(np.mean(rates)), 3) if rates else None,
        "hit_rate_p05": round(float(np.percentile(rates, 5)), 3) if rates else None,
        "hit_rate_p95": round(float(np.percentile(rates, 95)), 3) if rates else None,
    }
    out["placebo_percentile"] = percentile
    out["verdict"] = (
        "channel active"
        if (percentile is not None and percentile >= CPS_ACTIVE_PERCENTILE)
        else "no evidence yet"
    )
    out["method"] = _method(exploit_threshold_usd, len(rows))
    out["caveats"] = _caveats(n_scored)
    return out


def _method(threshold: float, n_events: int) -> str:
    return (
        f"associational event-study, not causal — after arXiv:2601.08263 (Lin, "
        f"\"A Blessing in Disguise?\"): DeFi exploit days > ${threshold / 1e6:.0f}M, "
        f"declustered at {CPS_CLUSTER_BD}bd keeping the max (n={n_events}); per event the "
        f"signed path of the 3m AA CP−bill spread (DCPN3M−DGS3MO) in "
        f"[-{CPS_PRE_BD},+{CPS_POST_BD}]bd vs its trailing {CPS_TRAIL_BD}bd median; "
        f"hit = min post-window spread minus pre-window median < -1 trailing-sigma "
        f"(level std over the {CPS_TRAIL_BD}bd strictly pre-event). Significance vs a "
        f"placebo: {CPS_SHUFFLES} seeded shuffles of the event dates (seed {CPS_SEED}, "
        f"same min-gap and coverage constraints), percentile of the real hit rate "
        f"published. Trailing statistics only — no value changes when future data arrives."
    )


def _caveats(n_scored: int) -> list[str]:
    caveats = [
        "associational event-study, not causal: the paper itself states the liquidity-"
        "recycling channel is inferred from pricing patterns and monthly holdings — "
        "daily fund-level routing into prime money funds is not observed",
        "the hit statistic takes the MIN over a 10bd post window — selection-biased by "
        "construction; the shuffle placebo is the calibration, not the raw hit rate",
        "hacks_usd completeness is the collector's (llama.fi exploit coverage); zeros are "
        "taken at face value — missing exploit data masquerades as quiet days",
        "the spread is 3m AA nonfinancial CP minus the 3m bill (DCPN3M−DGS3MO): the "
        "result is specific to this spread, exploit-driven shocks, and short windows "
        "(per the paper's own scope note)",
        "exploit dating is UTC-day; a weekend exploit maps to the next trading day on "
        "the CP grid — timing slop of up to 2 calendar days is possible",
        "no composite score: a narrowing CP spread is context about a cross-market "
        "channel, not evidence of dollar-funding stress today (doctrine)",
    ]
    if n_scored < 10:
        caveats.insert(0, f"only {n_scored} scored events — wide Wilson CIs; treat the "
                          "hit rate as a case tally, not an established rate")
    return caveats
