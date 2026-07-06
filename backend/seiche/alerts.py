"""Alert engine — rules from config, dedupe via sqlite, fail-loud always.

Each rule fires once per distinct state (the state_key). A regime that stays
STRAIN for a week alerts once; the day it flips to STRESS is a new state and
alerts again. Delivery: stdout (always), macOS notification (best effort),
optional webhook POST — set $SEICHE_WEBHOOK_URL to a Slack/Telegram/ntfy
endpoint that accepts {"text": ...} JSON.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess

import httpx

from seiche.config import ALERT_RULES, ALERT_WEBHOOK_ENV, DB_PATH
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
            fired.append({"rule": rule, "state": state_key, "message": message})
    finally:
        conn.close()
    return fired
