"""The morning brief — a desk note the terminal writes for you.

Deterministic template over the snapshot payload (no LLM, no surprises):
index + regime + delta, The Tell, top drivers, overnight movers, the
two-week calendar, watchlist, faults. Markdown out; the CLI prints it with
ANSI color and can archive it to data/briefs/.
"""

from __future__ import annotations

import json
import sqlite3

from seiche.config import BRIEF_DIR, DB_PATH


def _pit_yesterday(today_value: float | None) -> str:
    """Delta vs the most recent prior as-published index value."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT key, payload FROM blobs WHERE key LIKE 'pit:%' ORDER BY key DESC LIMIT 5"
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return ""
    for key, payload in rows[1:]:  # rows[0] is today (written this snapshot)
        try:
            prev = json.loads(payload).get("value")
        except json.JSONDecodeError:
            continue
        if prev is not None and today_value is not None:
            d = today_value - prev
            return f" ({'+' if d >= 0 else ''}{d:.1f} vs {key[4:]})"
    return ""


def render_markdown(snap: dict) -> str:
    eng = snap.get("engines", {})
    deep = snap.get("deep", {})
    cal = snap.get("calendar", {})
    hl = snap.get("headline", {})
    comp = eng.get("composite", {})
    tell = deep.get("tell", {})
    turn = (deep.get("turn") or {}).get("next_turn")
    lines: list[str] = []

    date = (snap.get("generated_at") or "")[:10]
    lines.append(f"# SEICHE BRIEF — {date}")
    lines.append("")

    v = comp.get("value")
    lines.append(
        f"**INDEX {v} {comp.get('regime')}**{_pit_yesterday(v)} · "
        f"coverage {comp.get('coverage_pct')}%"
    )
    if tell.get("ok"):
        lines.append(
            f"**TELL {tell['tell']:+.0f}** — {tell['reading']} "
            f"(plumbing {tell['plumbing_pctl']:.0f}th / market {tell['market_pctl']:.0f}th pctl)"
        )
    lines.append("")

    # Top drivers by contribution
    decomp = [d for d in comp.get("decomposition", []) if d.get("contribution")]
    if decomp:
        top = decomp[:3]
        lines.append("## Drivers")
        for d in top:
            lines.append(f"- {d['component']}: score {d['score']} (w {d['weight']}, +{d['contribution']} pts)")
        dead = [d["component"] for d in comp.get("decomposition", []) if d.get("status") == "DEAD"]
        if dead:
            lines.append(f"- DEAD inputs: {', '.join(dead)}")
        lines.append("")

    # Overnight movers
    sonar = eng.get("sonar", {})
    flagged = [m for m in sonar.get("movers", []) if m.get("flag")]
    lines.append("## Moved overnight")
    if flagged:
        for m in flagged[:6]:
            lines.append(
                f"- {m['label']}: {m['last']} {m['unit']} "
                f"(level z {m['level_z']}, Δ z {m['change_z']}, asof {m['asof']})"
            )
    else:
        lines.append("- nothing beyond ±2.5 robust z")
    lines.append("")

    # Calendar
    lines.append("## Calendar")
    for f in (cal.get("fomc_next_90d") or [])[:2]:
        lines.append(f"- FOMC decision {f['date']} (in {f['days_until']}d)")
    if turn:
        lines.append(
            f"- Next turn {turn['date']} ({turn['mode']}): forecast {turn['forecast_bp']:+.1f}bp "
            f"[{turn['band_bp'][0]:+.1f}, {turn['band_bp'][1]:+.1f}] · severity {turn['severity']}/5"
        )
    for c in (cal.get("crunch_windows") or [])[:4]:
        lines.append(f"- Crunch {c['date']}: worst-case ${c['worst_case_b']:.0f}B ({c['reason']})")
    for s in (cal.get("upcoming_settlements") or [])[:4]:
        lines.append(f"- Settlement {s['date']}: ${s['amount_b']:.0f}B")
    for t in (cal.get("corporate_tax_next_90d") or [])[:1]:
        lines.append(f"- Corporate tax date {t['date']} (in {t['days_until']}d)")
    lines.append("")

    # Watchlist
    lines.append("## Watchlist")
    if hl.get("srf_accepted_b"):
        lines.append(f"- SRF take-up ${hl['srf_accepted_b']['value']}B (asof {hl['srf_accepted_b']['asof']})")
    if hl.get("dw_b"):
        lines.append(f"- Discount window ${hl['dw_b']['value']}B (asof {hl['dw_b']['asof']})")
    wh = eng.get("warehouse", {})
    if wh.get("ok"):
        lines.append(
            f"- Dealer warehouse ${wh['total_net_b']:.0f}B ({wh['total_pctl']:.0f}th pctl, "
            f"long-end {wh['long_end_share_pct']}%)"
        )
    res = eng.get("resonance", {})
    if res.get("ok"):
        wm = res.get("worst_mode", {})
        lines.append(f"- Resonance {res['score']}: {wm.get('label')} amplifying {wm.get('amplification')}x")
    bath = deep.get("bathymetry", {})
    if bath.get("ok"):
        spec = bath.get("spectrum") or {}
        arrow = bath.get("arrow") or {}
        mfpt = f", ~{bath['mfpt_bd']:.0f}bd to next event" if bath.get("mfpt_bd") is not None else ""
        lines.append(
            f"- Bathymetry: P(event 5bd) {bath.get('p_event_5bd', 0) or 0:.0%}{mfpt} · "
            f"relaxation τ {spec.get('tau_bd')}bd ({spec.get('tau_pctl', '?')}th pctl) · "
            f"entropy production {arrow.get('sigma_nats_bd')} nats/bd ({arrow.get('pctl', '?')}th)"
        )
    mer = eng.get("merian", {})
    if mer.get("ok") and (mer.get("instability") or {}).get("g_now") is not None:
        inst = mer["instability"]
        if (inst.get("g_now") or 0.0) > 0 and (inst.get("pctl") or 0.0) >= 90:
            lines.append(
                f"- Merian: GROWING mode live (g {inst['g_now']:+.3f}/bd, {inst['pctl']:.0f}th pctl)"
            )
    rogue = eng.get("roguewave", {})
    if rogue.get("ok"):
        rl = next((r for r in rogue.get("return_levels", []) if r.get("years") == 10.0), None)
        if rl:
            lines.append(
                f"- Rogue Wave: ξ {rogue['fit']['xi']} — 10y wave ~{rl['bp']:.0f}bp "
                f"(sample max {rogue.get('sample_max_bp')}bp)"
            )
    crowd = eng.get("crowding", {})
    if crowd.get("ok") and crowd.get("rows"):
        r = crowd["rows"][0]
        lines.append(f"- Most crowded: {r['contract']} lev net {r['lev_net_share_oi']:+.2f} of OI (z {r['z']})")
    echo = eng.get("echo", {})
    if echo.get("ok") and echo.get("top"):
        t = echo["top"]
        lines.append(f"- Echo: {t['similarity']:.2f} similar to T−{t['lead_days']}d before {echo['matches'][0]['episode']}")
    moor = eng.get("moorings", {})
    if moor.get("ok"):
        u = moor.get("usdt") or {}
        dem = moor.get("demand") or {}
        bits = []
        if u.get("dev_bp") is not None:
            bits.append(f"USDT peg {u['dev_bp']:+.0f}bp")
        if dem.get("total_b") is not None:
            bits.append(f"stablecoins ${dem['total_b']:.0f}B ({dem.get('chg_30d_pct', 0):+.1f}%/30d)")
        can = moor.get("canary") or {}
        if can.get("btc_rv10_z") is not None and abs(can["btc_rv10_z"]) >= 1.5:
            bits.append(f"BTC vol z {can['btc_rv10_z']}")
        if bits:
            lines.append(f"- Moorings: {' · '.join(bits)}")
    basins_e = eng.get("basins", {})
    if basins_e.get("ok"):
        hot = [b for b in basins_e.get("basins", []) if abs(b.get("z") or 0) >= 1.5]
        for b in hot[:2]:
            lines.append(f"- Basin {b['basin']}: {b['value_bp']} ({b['anchor']}) z {b['z']}")
    lines.append("")

    faults = snap.get("faults") or []
    lines.append("## Faults")
    if faults:
        for f in faults[:6]:
            lines.append(f"- {f.get('source')}: {str(f.get('detail'))[:110]}")
    else:
        lines.append("- none — all sources and engines live")
    lines.append("")
    lines.append(
        f"— seiche {snap.get('version')} · free public data with native lags · not investment advice"
    )
    return "\n".join(lines)


def save(snap: dict) -> str:
    """Archive today's brief to data/briefs/YYYY-MM-DD.md; returns the path."""
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    date = (snap.get("generated_at") or "")[:10]
    path = BRIEF_DIR / f"{date}.md"
    path.write_text(render_markdown(snap))
    return str(path)
