"""Model Court, the adjudication layer over Seiche's disagreeing event odds.

Seiche publishes five forward views of the same 5bd question and the letter
prints them side by side with no ruling: Bathymetry (first-passage escape
odds), the ML Lab (walk-forward gradient model), Swell (calendar forcing
curve), Tide Tables (analog odds) and the Markov map (regime reach). The
Court makes them argue. Each member testifies with its CURRENT probability
plus whatever out-of-sample evidence its own payload already carries, the
Court pools the members it can defend pooling, and it keeps a live odds
ledger so the ranking is eventually EARNED out of sample instead of argued
from each model's own backtest.

What each piece of published skill evidence is, and is not:

  ml.validation          walk-forward OOS AUROC/Brier vs climatology for
                         P(event within 5bd). IS a genuine out-of-sample
                         record over oos_days. Is NOT proof the levels are
                         calibrated: the payload's own reliability table
                         shows bins above 0.30 overshooting realized rates,
                         and brier > brier_climatology on the sample.
  bathymetry.validation  walk-forward Brier/AUROC of the escape-time odds.
                         Positive Brier skill IS possible while AUROC sits
                         below 0.5 (predicting small numbers in a rare-event
                         sample). Is NOT evidence the model ranks risk days;
                         the Court gates calibration credit on ranking.
  swell.validation       walk-forward Brier/AUROC of the calendar curve's
                         5bd integral. IS the only member whose own verdict
                         claims both dates and levels. Is NOT immune to the
                         damping-state drift its caveats disclose.
  tidetables.skill       walk-forward Brier/AUROC of the analog event odds
                         (odds defined as event within 5bd per its method).
                         IS honest about not beating climatology on the
                         sample. n in the odds (25 analogs) is NOT the
                         validation n (n_scored days).
  markov                 no probabilistic validation at all, counting only,
                         and the target differs (reaching the STRESS regime,
                         not the PROOF pop event). Testifies for context,
                         never enters the pool.

These blocks are NOT comparable head to head: different warmups, scored
windows, event counts and event bases. That is exactly why the ledger
exists.

Weighting rule (stated because the letter prints the pooled number):
  weight_i = max(brier_skill_i, 0) * gate(auroc_i)
  gate = clip((auroc - 0.5) / 0.2, 0, 1), and 1.0 when no AUROC is published.
Members that do not beat climatology get zero weight; a member that ranks
worse than chance gets zero weight regardless of its Brier. If every weight
is zero the ensemble is the plain median of the pool and says so.

Odds ledger contract (dispatch CI appends one row per model per day):
    {"date": "YYYY-MM-DD",   the day the odds were published
     "model": "swell",       member name, matches the names in members[]
     "horizon_bd": 5,        horizon the probability refers to
     "p": 0.082,             published probability in [0, 1]
     "realized": null}       backfilled to true/false once horizon_bd
                             business days have fully elapsed (PROOF event
                             within the horizon); stays null until then
The backfill must never edit p, only flip realized from null. With >= 30
resolved rows per model the Court issues a ranked verdict on live Brier;
below that it reports accrual honestly and refuses to rank.
"""

from __future__ import annotations

import numpy as np

MIN_RESOLVED = 30       # resolved ledger rows per model before ranking
MIN_RANKED = 2          # a ranking of one is not a ranking
AUROC_GATE_LO = 0.5     # chance-level discrimination earns zero weight
AUROC_GATE_SPAN = 0.2   # full calibration credit at AUROC >= 0.7

_NOTES = {
    "bathymetry": "escape-time odds from fitted dynamics; Brier skill can be positive while AUROC is below chance, so calibration credit is gated on ranking",
    "ml": "walk-forward gradient model; genuine OOS record, but its own reliability table overshoots above 0.30 and levels lose to climatology on the sample",
    "swell": "calendar forcing curve 5bd integral; the only member whose own verdict claims both dates and levels",
    "tidetables": "analog odds (share of 25 nearest analogs with an event within 5bd); its skill block says levels do not beat climatology",
    "markov": "regime-reach odds, a DIFFERENT event (hit STRESS regime), counting only, no validation; context, never pooled",
}


def _num(x) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if not np.isfinite(x):
        return None
    return float(x)


def _skill_from(block, source: str, fallback_verdict=None) -> dict | None:
    """Normalize a validation/skill block to one comparable-shaped record.

    Only Brier vs climatology and AUROC are mined; anything richer
    (reliability tables, utility curves) stays in the source payload.
    """
    if not isinstance(block, dict) or block.get("ok") is False:
        return None
    brier = _num(block.get("brier"))
    clim = _num(block.get("brier_climatology"))
    if brier is None or clim is None or clim <= 0:
        return None
    bs = _num(block.get("brier_skill"))
    if bs is None:
        bs = 1.0 - brier / clim
    return {
        "source": source,
        "brier": brier,
        "brier_climatology": clim,
        "brier_skill": round(bs, 3),
        "auroc": _num(block.get("auroc")),
        "n_scored": block.get("n_scored", block.get("oos_days")),
        "n_events": block.get("n_events", block.get("oos_events")),
        "verdict": block.get("verdict", fallback_verdict),
    }


def _weight(skill: dict | None) -> float:
    if not skill:
        return 0.0
    w = max(skill["brier_skill"], 0.0)
    auroc = skill.get("auroc")
    if auroc is not None:
        w *= float(np.clip((auroc - AUROC_GATE_LO) / AUROC_GATE_SPAN, 0.0, 1.0))
    return w


def _testimony(deep: dict, horizon_bd: int) -> tuple[list[dict], list[dict]]:
    """Mine each member's current p and published skill from the deep dict."""
    members: list[dict] = []
    absent: list[dict] = []

    def seat(model, p, skill, asof, in_pool, extra=None):
        if p is None:
            absent.append({"model": model, "reason": f"no {horizon_bd}bd probability published"})
            return
        m = {
            "model": model,
            "p": round(p, 4),
            "horizon_bd": horizon_bd,
            "asof": asof,
            "in_pool": bool(in_pool),
            "weight": None,  # filled by the pooling step for pool members
            "skill": skill,
            "note": _NOTES[model],
        }
        if extra:
            m.update(extra)
        members.append(m)

    def payload(name):
        pay = deep.get(name)
        if not isinstance(pay, dict) or not pay.get("ok"):
            reason = (pay or {}).get("reason") if isinstance(pay, dict) else None
            absent.append({"model": name, "reason": reason or "payload missing or not ok"})
            return None
        return pay

    pay = payload("bathymetry")
    if pay is not None:
        p = _num((pay.get("p_by_horizon") or {}).get(f"h{horizon_bd}"))
        if p is None and horizon_bd == 5:
            p = _num(pay.get("p_event_5bd"))
        seat("bathymetry", p, _skill_from(pay.get("validation"), "bathymetry.validation"),
             pay.get("asof"), True)

    pay = payload("ml")
    if pay is not None:
        p = _num(pay.get("p_event_5bd")) if horizon_bd == 5 else None
        seat("ml", p, _skill_from(pay.get("validation"), "ml.validation",
                                  fallback_verdict=pay.get("verdict")),
             pay.get("asof"), True)

    pay = payload("swell")
    if pay is not None:
        p = _num((pay.get("event_by_horizon") or {}).get(f"h{horizon_bd}"))
        if p is None and horizon_bd == 5:
            p = _num(pay.get("p_event_5bd"))
        seat("swell", p, _skill_from(pay.get("validation"), "swell.validation"),
             pay.get("asof"), True)

    pay = payload("tidetables")
    if pay is not None:
        # event_odds are defined as event within 5bd by the engine's method,
        # regardless of the payload's fan horizon_bd. Other horizons: absent.
        eo = pay.get("event_odds") or {}
        p = _num(eo.get("p")) if horizon_bd == 5 else None
        extra = {"odds_detail": {k: eo.get(k) for k in ("hits", "n", "ci95", "base_rate", "lift")}} if p is not None else None
        seat("tidetables", p, _skill_from(pay.get("skill"), "tidetables.skill"),
             pay.get("asof"), True, extra)

    pay = payload("markov")
    if pay is not None:
        p = _num((pay.get("p_reach_stress") or {}).get(f"h{horizon_bd}"))
        seat("markov", p, None, pay.get("asof"), False)

    return members, absent


def _court(odds_ledger, horizon_bd: int) -> tuple[dict, str]:
    """Score the live ledger. Returns (court block, ledger_status string)."""
    if odds_ledger is None:
        return (
            {"in_session": False, "min_resolved": MIN_RESOLVED, "scores": [], "verdict": None},
            "no ledger yet; the court reads published backtests only until dispatch starts appending daily odds",
        )

    tallies: dict[str, dict] = {}
    for r in odds_ledger:
        if not isinstance(r, dict) or r.get("horizon_bd") != horizon_bd:
            continue
        model = r.get("model")
        p = _num(r.get("p"))
        if not isinstance(model, str) or not model or p is None or not 0.0 <= p <= 1.0:
            continue
        t = tallies.setdefault(model, {"resolved": [], "pending": 0})
        rz = r.get("realized")
        if rz is None:
            t["pending"] += 1
        elif isinstance(rz, (bool, int)) and rz in (0, 1):
            t["resolved"].append((p, 1.0 if rz else 0.0))

    if not tallies:
        return (
            {"in_session": False, "min_resolved": MIN_RESOLVED, "scores": [], "verdict": None},
            f"ledger present but holds no valid rows at the {horizon_bd}bd horizon; accrual not started",
        )

    all_y = [y for t in tallies.values() for (_, y) in t["resolved"]]
    base_rate = float(np.mean(all_y)) if all_y else None

    scores = []
    for model in sorted(tallies):
        t = tallies[model]
        n_res = len(t["resolved"])
        entry = {"model": model, "n_resolved": n_res, "n_pending": t["pending"]}
        if n_res >= MIN_RESOLVED:
            arr = np.asarray(t["resolved"], dtype=float)
            brier = float(np.mean((arr[:, 0] - arr[:, 1]) ** 2))
            clim = float(np.mean((base_rate - arr[:, 1]) ** 2))
            entry["brier"] = round(brier, 4)
            entry["brier_climatology"] = round(clim, 4)
            entry["brier_skill"] = round(1.0 - brier / clim, 3) if clim > 0 else None
        scores.append(entry)

    ranked = sorted((e for e in scores if "brier" in e), key=lambda e: e["brier"])
    for i, e in enumerate(ranked):
        e["rank"] = i + 1

    if len(ranked) >= MIN_RANKED:
        order = ", ".join(
            f"{e['rank']}. {e['model']} (Brier {e['brier']}, n {e['n_resolved']})" for e in ranked
        )
        verdict = (
            f"live court verdict at {horizon_bd}bd: {order}; lower is better, "
            f"shared base rate {round(base_rate, 3)}"
        )
        short = [e["model"] for e in scores if "brier" not in e]
        status = f"court in session: {len(ranked)} models ranked on live Brier"
        if short:
            status += "; still accruing: " + ", ".join(
                f"{e['model']} {e['n_resolved']}/{MIN_RESOLVED}" for e in scores if "brier" not in e
            )
        return (
            {"in_session": True, "min_resolved": MIN_RESOLVED, "scores": scores, "verdict": verdict},
            status,
        )

    accrual = ", ".join(
        f"{e['model']} {e['n_resolved']}/{MIN_RESOLVED} resolved ({e['n_pending']} pending)"
        for e in scores
    )
    return (
        {"in_session": False, "min_resolved": MIN_RESOLVED, "scores": scores, "verdict": None},
        f"ledger accruing: {accrual}; ranked verdict withheld until {MIN_RESOLVED} resolved rows per model",
    )


def convene(deep: dict, odds_ledger: list | None = None, horizon_bd: int = 5) -> dict:
    if not isinstance(deep, dict) or not deep:
        return {"ok": False, "reason": "no deep payload to adjudicate"}

    members, absent = _testimony(deep, horizon_bd)
    pool = [m for m in members if m["in_pool"]]
    if len(pool) < 2:
        return {
            "ok": False,
            "reason": f"only {len(pool)} comparable odds published for the {horizon_bd}bd horizon; a court needs at least 2",
            "absent": absent,
        }

    raw_w = {m["model"]: _weight(m["skill"]) for m in pool}
    total_w = sum(raw_w.values())
    ps = np.array([m["p"] for m in pool], dtype=float)

    if total_w > 0:
        pooled = float(sum(raw_w[m["model"]] * m["p"] for m in pool) / total_w)
        rule = "skill_weighted"
        for m in pool:
            m["weight"] = round(raw_w[m["model"]] / total_w, 3)
    else:
        pooled = float(np.median(ps))
        rule = "median"
        for m in pool:
            m["weight"] = 0.0
    n_weighted = sum(1 for m in pool if (m["weight"] or 0) > 0)

    dispersion = {
        "n": len(pool),
        "min": round(float(ps.min()), 4),
        "max": round(float(ps.max()), 4),
        "spread": round(float(ps.max() - ps.min()), 4),
        "stdev": round(float(ps.std(ddof=0)), 4),
    }

    court, ledger_status = _court(odds_ledger, horizon_bd)

    if court["in_session"]:
        leader = min((e for e in court["scores"] if "brier" in e), key=lambda e: e["brier"])
        tail = f"live ledger ranks {leader['model']} first (Brier {leader['brier']})"
    elif court["scores"]:
        min_res = min(e["n_resolved"] for e in court["scores"])
        tail = f"live ranking withheld ({min_res} of {MIN_RESOLVED} resolved at worst)"
    else:
        tail = "live ledger not yet accruing"
    rule_txt = (
        f"skill weighted, {n_weighted} of {len(pool)} beat climatology"
        if rule == "skill_weighted"
        else "median, no member beats climatology"
    )
    adjudication = (
        f"Model Court, {horizon_bd}bd event odds: {len(pool)} models span "
        f"{100 * dispersion['min']:.1f} to {100 * dispersion['max']:.1f} pct, pooled "
        f"{100 * pooled:.1f} pct ({rule_txt}); {tail}"
    )

    asofs = [m["asof"] for m in members if m.get("asof")]
    return {
        "ok": True,
        "asof": max(asofs) if asofs else None,
        "horizon_bd": horizon_bd,
        "members": members,
        "absent": absent,
        "ensemble": {
            "p": round(pooled, 4),
            "rule": rule,
            "n_pooled": len(pool),
            "n_weighted": n_weighted,
            "weights": {m["model"]: m["weight"] for m in pool},
        },
        "dispersion": dispersion,
        "adjudication": adjudication,
        "court": court,
        "ledger_status": ledger_status,
        "caveats": [
            "member validation blocks are not comparable head to head (different warmups, scored windows, event counts); the ledger is the only like for like scoreboard and it accrues slowly",
            "skill weights come from each model's own published backtest, which shares history with the model's design; the pooled number is a reasoned view, not a market",
            "markov answers a different question (regime reach, not the PROOF pop event); it testifies for context and never enters the pool or dispersion",
            "the ledger backfill must never edit p, only flip realized from null; edited rows would forge the live record",
        ],
        "method": (
            f"members = published {horizon_bd}bd odds mined from each engine's payload; "
            "weight = max(Brier skill vs climatology, 0) x AUROC gate clip((AUROC-0.5)/0.2, 0, 1), gate 1 when unpublished; "
            "pooled = weighted mean when any weight > 0 else median of the pool; "
            f"court = live per-model Brier from the odds ledger once {MIN_RESOLVED} rows per model resolve, ranked ascending"
        ),
    }
