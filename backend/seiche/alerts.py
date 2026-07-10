"""Alert engine — rules from config, dedupe via sqlite, fail-loud always.

Each rule fires once per distinct state (the state_key). A regime that stays
STRAIN for a week alerts once; the day it flips to STRESS is a new state and
alerts again. Delivery: stdout (always), macOS notification (best effort),
optional webhook POST — set $SEICHE_WEBHOOK_URL to a Slack/Telegram/ntfy
endpoint that accepts {"text": ...} JSON.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess

import httpx

from seiche.config import ALERT_RULES, ALERT_WEBHOOK_ENV, DB_PATH

logger = logging.getLogger("seiche.alerts")
from seiche.sources.base import utcnow_iso


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS alerts (
             fired_at TEXT, rule TEXT, state_key TEXT, message TEXT,
             PRIMARY KEY (rule, state_key))"""
    )
    return conn


def _already_fired(conn: sqlite3.Connection, rule: str, state_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM alerts WHERE rule=? AND state_key=?", (rule, state_key)
    ).fetchone()
    return row is not None


def _record(conn: sqlite3.Connection, rule: str, state_key: str, message: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO alerts VALUES (?,?,?,?)",
        (utcnow_iso(), rule, state_key, message),
    )
    conn.commit()


def _notify_macos(message: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(message)} with title "SEICHE"'],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass  # notification is best-effort; stdout + log are the record


def _notify_webhook(message: str) -> None:
    url = os.environ.get(ALERT_WEBHOOK_ENV)
    if not url:
        return
    try:
        httpx.post(url, json={"text": f"SEICHE: {message}"}, timeout=10)
    except Exception:
        pass


def _notify_telegram(message: str) -> None:
    """Native Telegram delivery: set SEICHE_TELEGRAM_BOT_TOKEN and
    SEICHE_TELEGRAM_CHAT_ID (bot must have been /start-ed once)."""
    token = os.environ.get("SEICHE_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("SEICHE_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"SEICHE: {message}"},
            timeout=10,
        )
    except Exception:
        pass


def evaluate(snap: dict) -> list[dict]:
    """Evaluate all rules against a snapshot; fire + persist new alerts."""
    eng = snap.get("engines", {})
    deep = snap.get("deep", {})
    hl = snap.get("headline", {})
    comp = eng.get("composite", {})
    fired: list[dict] = []

    candidates: list[tuple[str, str, str]] = []  # (rule, state_key, message)

    regime = comp.get("regime")
    if ALERT_RULES.get("regime_change") and regime:
        candidates.append(("regime_change", str(regime), f"regime is {regime} (index {comp.get('value')})"))

    tail_z = (eng.get("tails") or {}).get("tail_index_z")
    thr = ALERT_RULES.get("tail_z")
    if thr is not None and tail_z is not None and tail_z >= thr:
        candidates.append(("tail_z", f"ge{thr}:{(snap.get('generated_at') or '')[:10]}",
                           f"tail index z {tail_z} ≥ {thr} — tails detaching"))

    srf = (hl.get("srf_accepted_b") or {}).get("value")
    thr = ALERT_RULES.get("srf_accepted_b")
    if thr is not None and srf is not None and srf >= thr:
        candidates.append(("srf", f"{(hl.get('srf_accepted_b') or {}).get('asof')}",
                           f"SRF take-up ${srf}B ≥ ${thr}B — the confession channel is open"))

    dw = (hl.get("dw_b") or {}).get("value")
    thr = ALERT_RULES.get("discount_window_b")
    if thr is not None and dw is not None and dw >= thr:
        candidates.append(("discount_window", f"{(hl.get('dw_b') or {}).get('asof')}",
                           f"discount window ${dw}B ≥ ${thr}B"))

    tell = (deep.get("tell") or {})
    thr = ALERT_RULES.get("tell_abs")
    if thr is not None and tell.get("ok") and abs(tell.get("tell", 0.0)) >= thr:
        sign = "plumbing>price" if tell["tell"] > 0 else "price>plumbing"
        candidates.append(("tell", f"{sign}:{tell.get('asof')}",
                           f"Tell {tell['tell']:+.0f} ({tell['reading']})"))

    horizon = ALERT_RULES.get("crunch_within_d")
    if horizon:
        import datetime as _dt
        today = _dt.date.today()
        for c in (eng.get("weather") or {}).get("crunch_windows", []):
            try:
                d = _dt.date.fromisoformat(c["date"])
            except (KeyError, ValueError):
                continue
            if 0 <= (d - today).days <= horizon:
                candidates.append(("crunch", c["date"],
                                   f"crunch window {c['date']}: {c['reason']}"))

    turn = (deep.get("turn") or {}).get("next_turn") or {}
    thr = ALERT_RULES.get("turn_severity")
    if thr is not None and turn.get("severity") is not None and turn["severity"] >= thr:
        candidates.append(("turn", turn.get("date", "?"),
                           f"turn {turn.get('date')} forecast {turn.get('forecast_bp')}bp severity {turn['severity']}/5"))

    swap = ((eng.get("basins") or {}).get("swap_lines") or {})
    thr = ALERT_RULES.get("swap_line_usd_m")
    if thr is not None and (swap.get("ops_30d_total_m") or 0.0) >= thr:
        candidates.append(("swap_lines", f"{swap.get('outstanding_asof')}",
                           f"USD swap lines drawn ${swap['ops_30d_total_m']:.0f}M over 30d "
                           f"({', '.join(list(swap.get('ops_30d_by_counterparty', {}))[:3])}) — global dollar confession"))

    moor = eng.get("moorings") or {}
    thr = ALERT_RULES.get("peg_dev_bp")
    if thr is not None and moor.get("ok"):
        for p in moor.get("pegs", []):
            if p.get("dev_bp") is not None and abs(p["dev_bp"]) >= thr:
                candidates.append(("peg", f"{p['symbol']}:{(moor.get('usdt') or {}).get('asof', '')}",
                                   f"{p['symbol']} peg {p['dev_bp']:+.0f}bp off $1 "
                                   f"(${p.get('circulating_b')}B circulating) — offshore dollar strain"))
    thr = ALERT_RULES.get("stable_drain_30d_pct")
    dem = (moor.get("demand") or {})
    if thr is not None and dem.get("chg_30d_pct") is not None and dem["chg_30d_pct"] <= thr:
        candidates.append(("stable_drain", dem.get("asof", "?"),
                           f"stablecoin circulation {dem['chg_30d_pct']:+.1f}%/30d "
                           f"(${dem.get('chg_30d_b')}B) — offshore dollar redemptions"))

    ml = (deep.get("ml") or {})
    thr = ALERT_RULES.get("ml_event_prob")
    if thr is not None and ml.get("ok") and (ml.get("p_event_5bd") or 0.0) >= thr:
        candidates.append(("ml_event", ml.get("asof", "?"),
                           f"ML Lab: P(funding event, 5bd) = {ml['p_event_5bd']:.0%} "
                           f"({ml.get('verdict', '')[:60]})"))

    tide = deep.get("tidetables") or {}
    thr = ALERT_RULES.get("analog_event_odds")
    odds = (tide.get("event_odds") or {}) if tide.get("ok") else {}
    if thr is not None and odds.get("p") is not None and odds["p"] >= thr:
        nov = (tide.get("novelty") or {}).get("verdict", "?")
        candidates.append(("analog_event", tide.get("asof", "?"),
                           f"Tide Tables: {odds['p']:.0%} of the {odds.get('n')} nearest analogs "
                           f"saw a funding event within 5bd (base rate {odds.get('base_rate', 0):.0%}, "
                           f"water {nov})"))

    sw = deep.get("swell") or {}
    thr = ALERT_RULES.get("swell_event_prob")
    if thr is not None and sw.get("ok") and (sw.get("p_event_5bd") or 0.0) >= thr:
        peak = sw.get("peak") or {}
        candidates.append(("swell_event", sw.get("asof", "?"),
                           f"Swell curve: P(funding event, 5bd) = {sw['p_event_5bd']:.0%}; "
                           f"peak day {peak.get('date')} ({peak.get('bucket')}, "
                           f"P(≥10bp) {peak.get('p10', 0):.0%})"))

    ba = deep.get("bathymetry") or {}
    thr = ALERT_RULES.get("bathymetry_event_prob")
    if thr is not None and ba.get("ok") and (ba.get("p_event_5bd") or 0.0) >= thr:
        mfpt = ba.get("mfpt_bd")
        candidates.append(("bathymetry_event", ba.get("asof", "?"),
                           f"Bathymetry: first-passage P(funding event, 5bd) = {ba['p_event_5bd']:.0%} "
                           f"from the fitted dynamics"
                           + (f"; expected {mfpt:.0f}bd to the event bin" if mfpt else "")
                           + f" (barrier {((ba.get('floor') or {}).get('barrier_kt'))} kT)"))

    stk = deep.get("stacker") or {}
    thr = ALERT_RULES.get("stack_dispersion")
    if thr is not None and stk.get("ok") and (stk.get("dispersion_now") or 0.0) >= thr:
        vs = stk.get("members_now") or {}
        candidates.append(("stack_dispersion", stk.get("asof", "?"),
                           f"forecast members disagree (dispersion {stk['dispersion_now']:.2f}): "
                           + ", ".join(f"{k} {p:.0%}" for k, p in vs.items() if p is not None)
                           + " — regime ambiguity, trust ranges not points"))

    rt = (deep.get("riptide") or {})
    thr = ALERT_RULES.get("riptide_sticky")
    lv = rt.get("live") if rt.get("ok") else None
    if thr is not None and lv and (lv.get("p_sticky") or 0.0) >= thr:
        candidates.append(("riptide_sticky", lv.get("date", "?"),
                           f"Riptide: the {lv.get('pop_bp')}bp pop on {lv.get('date')} reads as a "
                           f"CURRENT (P(sticky) {lv['p_sticky']:.0%}, P(escalates) "
                           f"{(lv.get('p_escalates') or 0):.0%}; RRP co-sign "
                           f"{'present' if lv.get('rrp_cosigned') else 'ABSENT — genuine scarcity'})"))

    ms = deep.get("microseism") or {}
    thr = ALERT_RULES.get("microseism_branching")
    ms_fit = (ms.get("fit") or {}) if ms.get("ok") else {}
    ms_identified = bool((ms.get("lr_test") or {}).get("identified"))
    if thr is not None and ms_identified and (ms_fit.get("branching") or 0.0) >= thr:
        candidates.append(("microseism_branching", ms.get("asof", "?"),
                           f"Microseism: aftershock chain near-critical — each shock breeds "
                           f"~{ms_fit['branching']:.2f} aftershocks (half-life "
                           f"{ms_fit.get('half_life_bd') or 0:.0f}bd, LR p="
                           f"{(ms.get('lr_test') or {}).get('p', 1):.4f} vs the calendar null); "
                           f"at n=1 the chain reaction is self-sustaining"))

    mer = eng.get("merian") or {}
    thr = ALERT_RULES.get("merian_instability")
    inst = (mer.get("instability") or {}) if mer.get("ok") else {}
    if thr is not None and inst.get("pctl") is not None and inst["pctl"] >= thr \
            and (inst.get("g_now") or 0.0) > 0.0:
        candidates.append(("merian_instability", mer.get("asof", "?"),
                           f"Merian Modes: a growing mode is live (growth {inst['g_now']:+.3f}/bd, "
                           f"{inst['pctl']:.0f}th pctl vs own history) — instability before levels move"))

    bw = eng.get("breakwater") or {}
    thr = ALERT_RULES.get("breakwater_proximity")
    if thr is not None and bw.get("ok") and (bw.get("rescue_proximity") or 0.0) >= thr:
        candidates.append(("breakwater", bw.get("asof", "?"),
                           f"Breakwater: board at {bw['rescue_proximity']:.0f}% of historical rescue "
                           f"conditions ({bw.get('reading', '')})"))

    book_today = ((deep.get("book") or {}).get("today") or {}) if (deep.get("book") or {}).get("ok") else {}
    if ALERT_RULES.get("book_flip") and book_today.get("stance"):
        sig = ",".join(
            f"{p.get('sleeve')}{p.get('weight'):+.1f}"
            for p in book_today.get("positions", []) if p.get("weight")
        ) or "flat"
        candidates.append(("book_flip", f"{book_today['stance']}:{sig}",
                           f"the Book is {book_today['stance']} ({sig}) — "
                           f"P(event,5bd)={book_today.get('p_ensemble')}, "
                           f"dispersion {book_today.get('dispersion')}"))

    # Dead-man switch on the as-published record: the whole business is an
    # unbroken PIT ledger, so a hole in it is a first-class incident. Scan the
    # trailing 45 days of pit:* keys for runs of missing business days; each
    # distinct hole alerts once (state_key = the hole's span). Note the honest
    # limit: this fires when the system RESUMES — a stopped process cannot
    # alert about itself, so the external observer is the box's systemd timer.
    thr = ALERT_RULES.get("pit_gap_bd")
    if thr is not None:
        try:
            import pandas as pd
            with sqlite3.connect(DB_PATH) as bconn:
                keys = [r[0] for r in bconn.execute(
                    "SELECT key FROM blobs WHERE key LIKE 'pit:%' ORDER BY key")]
            days = pd.DatetimeIndex([k.split("pit:")[1] for k in keys])
            recent = days[days >= days.max() - pd.Timedelta(days=45)] if len(days) else days
            if len(recent) >= 2:
                expected = pd.bdate_range(recent.min(), recent.max())
                missing = expected.difference(recent)
                run: list = []
                holes: list[tuple] = []
                for d in missing:
                    if run and (d - run[-1]).days > 3:
                        holes.append((run[0], run[-1], len(run)))
                        run = []
                    run.append(d)
                if run:
                    holes.append((run[0], run[-1], len(run)))
                for h0, h1, n_miss in holes:
                    if n_miss >= int(thr):
                        candidates.append((
                            "pit_gap",
                            f"{h0.date().isoformat()}:{h1.date().isoformat()}",
                            f"the PIT record has a HOLE: {n_miss} business days missing "
                            f"({h0.date().isoformat()} → {h1.date().isoformat()}) — the "
                            f"as-published chain is broken for that span and must be "
                            f"disclosed, not papered over",
                        ))
        except Exception as exc:  # noqa: BLE001 — the dead-man must not kill the live path
            logger.warning("pit_gap check failed: %s", exc)

    if ALERT_RULES.get("engine_dead"):
        for d in comp.get("decomposition", []):
            if d.get("status") == "DEAD":
                candidates.append(("engine_dead", f"{d['component']}:{(snap.get('generated_at') or '')[:10]}",
                                   f"composite input DEAD: {d['component']}"))

    conn = _conn()
    try:
        for rule, state_key, message in candidates:
            if _already_fired(conn, rule, state_key):
                continue
            _record(conn, rule, state_key, message)
            _notify_macos(message)
            _notify_webhook(message)
            _notify_telegram(message)
            fired.append({"rule": rule, "state": state_key, "message": message})
    finally:
        conn.close()
    if fired:
        _notify_subscribers(fired, snap)
    return fired


def _notify_subscribers(fired: list[dict], snap: dict) -> None:
    """Email every subscriber who has alerts on. Best-effort; a mail outage
    never affects the alert record or the pull cycle."""
    try:
        from seiche import accounts, mailer
        if not mailer.configured():
            return
        recipients = accounts.alert_recipients()
        if not recipients:
            return
        comp = snap.get("engines", {}).get("composite", {})
        subject = f"Seiche alert · {comp.get('regime', '?')} {comp.get('value', '')}".strip()
        lines = [f"- {a['message']}" for a in fired]
        body = (
            "Seiche funding-stress alert.\n\n"
            + "\n".join(lines)
            + f"\n\nBoard: {comp.get('regime')} ({comp.get('value')}/100), "
            + f"as of {(snap.get('generated_at') or '')[:16].replace('T', ' ')}Z.\n"
            + "Full board: https://seiche.info (sign in)\n\n"
            + "You are receiving this because alerts are on for your Seiche account. "
            + "Turn them off in the terminal or reply to desk@seiche.info."
        )
        for to in recipients:
            mailer.send(to, subject, body)
    except Exception as exc:  # never break the caller
        logger.warning("subscriber alert fan-out failed: %s", exc)
