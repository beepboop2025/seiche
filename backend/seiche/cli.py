"""seiche — the operator CLI.

  seiche pull                force-refresh everything, print the index line
  seiche brief [--save]      this morning's desk note (markdown to stdout)
  seiche alert               evaluate alert rules once (cron/launchd-friendly)
  seiche watch [-i SECONDS]  pull + alert on a loop
  seiche replay DATE         Time Machine: the board as of YYYY-MM-DD
  seiche backtest            PROOF summary in the terminal
  seiche analogs             Tide Tables: nearest analogs + forward fan
  seiche swell               the funding-stress forward curve, 6 weeks out
  seiche physics             the physics board: floor, modes, determinism, tail law
  seiche bathymetry          the basin floor in detail: potential, spectrum, first passage
  seiche serve [--port]      run the API + UI
  seiche mcp                 serve the board to AI agents over MCP (stdio)

Exit codes: 0 fine, 1 hard failure, 2 = alerts fired (useful in scripts).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

BOLD, DIM, RED, YEL, GRN, CYA, END = (
    "\033[1m", "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[36m", "\033[0m"
)
REGIME_COLOR = {"CALM": GRN, "EROSION": YEL, "STRAIN": YEL, "STRESS": RED}


def _index_line(snap: dict) -> str:
    c = snap.get("engines", {}).get("composite", {})
    col = REGIME_COLOR.get(c.get("regime"), "")
    tell = snap.get("deep", {}).get("tell", {})
    tell_txt = f" · tell {tell['tell']:+.0f}" if tell.get("ok") else ""
    faults = len(snap.get("faults") or [])
    fault_txt = f" · {RED}{faults} fault(s){END}" if faults else ""
    return (
        f"{BOLD}SEICHE {c.get('value')}{END} {col}{c.get('regime')}{END} "
        f"(coverage {c.get('coverage_pct')}%){tell_txt}{fault_txt}"
    )


def cmd_pull(args) -> int:
    from seiche import assemble, notary
    snap = asyncio.run(assemble.snapshot(force=True))
    print(_index_line(snap))
    # Anchor any un-stamped commitments to Bitcoin while we're here (best
    # effort: the chain is already committed; OTS just makes its age provable
    # to a fresh observer). Missing lib / offline calendars must never fail
    # the pull — the stamp retries on the next cycle.
    try:
        if notary.ots_available():
            r = notary.stamp_pending()
            if r.get("anchored"):
                print(f"{DIM}notary: anchored {r['anchored']} commitment(s) to OpenTimestamps{END}",
                      file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — anchoring is provability, not availability
        print(f"{DIM}notary: stamping deferred ({type(exc).__name__}: {exc}){END}", file=sys.stderr)
    return 0


def cmd_brief(args) -> int:
    from seiche import assemble, brief
    snap = asyncio.run(assemble.snapshot(force=args.force))
    print(brief.render_markdown(snap))
    if args.save:
        path = brief.save(snap)
        print(f"{DIM}saved -> {path}{END}", file=sys.stderr)
    return 0


def cmd_alert(args) -> int:
    from seiche import alerts, assemble
    snap = asyncio.run(assemble.snapshot(force=args.force))
    fired = alerts.evaluate(snap)
    if not fired:
        print(f"{DIM}no new alerts{END}")
        return 0
    for f in fired:
        print(f"{RED}ALERT{END} [{f['rule']}] {f['message']}")
    return 2


def cmd_watch(args) -> int:
    from seiche import alerts, assemble
    print(f"{DIM}watching every {args.interval}s — ctrl-c to stop{END}")
    while True:
        try:
            snap = asyncio.run(assemble.snapshot(force=True))
            print(f"{DIM}{snap.get('generated_at')}{END} {_index_line(snap)}")
            for f in alerts.evaluate(snap):
                print(f"{RED}ALERT{END} [{f['rule']}] {f['message']}")
        except KeyboardInterrupt:
            return 0
        except Exception as e:  # keep the watch alive; the fault is the news
            print(f"{RED}watch error:{END} {type(e).__name__}: {e}", file=sys.stderr)
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0


def cmd_replay(args) -> int:
    from seiche import assemble
    p = asyncio.run(assemble.snapshot_asof(args.date))
    if p.get("ok") is False:
        print(f"{RED}replay failed:{END} {p.get('reason')}", file=sys.stderr)
        return 1
    c = p["engines"]["composite"]
    col = REGIME_COLOR.get(c.get("regime"), "")
    print(f"{BOLD}SEICHE @ {p['asof']}{END}  {c.get('value')} {col}{c.get('regime')}{END} (coverage {c.get('coverage_pct')}%)")
    for d in c.get("decomposition", []):
        mark = f"{RED}DEAD{END}" if d["status"] == "DEAD" else f"{d['score']:.0f}"
        print(f"  {d['component']:<11} {mark}")
    w = p["engines"].get("weather", {})
    for cw in (w.get("crunch_windows") or [])[:5]:
        print(f"  {YEL}crunch{END} {cw['date']} — {cw['reason']}")
    print(f"{DIM}{p.get('vintage_note')}{END}")
    return 0


def cmd_backtest(args) -> int:
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    bt = snap.get("deep", {}).get("backtest", {})
    if not bt.get("ok"):
        print(f"{RED}backtest unavailable:{END} {bt.get('reason')}", file=sys.stderr)
        return 1
    cap = bt["event_capture"]
    s = bt["sample"]
    print(f"{BOLD}PROOF{END} sample {s['start']} → {s['end']} · {s['n_events']} funding events")
    rci = cap.get("recall_ci95") or ["?", "?"]
    pci = cap.get("precision_runs_ci95") or ["?", "?"]
    print(
        f"  recall {cap['recall']:.0%} (95% CI {rci[0]:.0%}-{rci[1]:.0%}) · "
        f"run-precision {cap['precision_runs']:.0%} (CI {pci[0]:.0%}-{pci[1]:.0%}, "
        f"{cap['runs_hit']}/{cap['n_alert_runs']} runs) · base rate {cap['base_rate']:.0%}"
    )
    print(f"  median alert run-up before events: {cap['median_lead_d']:.0f}d")
    o = bt.get("orthogonal", {})
    if o.get("ok"):
        oc = o["event_capture"]
        orci = oc.get("recall_ci95") or ["?", "?"]
        print(f"{BOLD}ORTHOGONAL{END} (no spread/tails in the signal)")
        print(
            f"  recall {oc['recall']:.0%} (CI {orci[0]:.0%}-{orci[1]:.0%}) · "
            f"run-precision {oc['precision_runs']:.0%} ({oc['runs_hit']}/{oc['n_alert_runs']} runs) — "
            "the claim survives without the target's own variables"
        )
    rig = bt.get("rigor", {})
    sig = rig.get("significance", {})
    if rig.get("event_auroc") is not None or sig.get("ok"):
        print(f"{BOLD}rigor{END} (the skeptic's two questions)")
        au = rig.get("event_auroc")
        if au is not None:
            col = GRN if au >= 0.7 else YEL if au >= 0.6 else RED
            print(f"  threshold-free AUROC (event within horizon): {col}{au}{END} "
                  f"{DIM}— skill across ALL thresholds, 0.5 = none{END}")
        if sig.get("ok"):
            col = GRN if sig["p_value"] < 0.05 else RED
            print(f"  permutation null: recall {sig['actual_recall']:.0%} vs chance "
                  f"{sig['null_mean_recall']:.0%} (95th {sig['null_p95_recall']:.0%}) · "
                  f"p={col}{sig['p_value']}{END} — {sig['verdict']}")

    cs = bt.get("class_split", {})
    if cs:
        endo, exo = cs.get("endogenous", {}), cs.get("exogenous", {})
        print(f"{BOLD}by competence class{END} (what it can vs can't see)")
        if endo.get("n"):
            lead = f", median {endo['median_lead_d']}d early" if endo.get("median_lead_d") is not None else ""
            print(f"  {GRN}endogenous{END} (reserve/calendar build-ups): "
                  f"caught {endo['caught']}/{endo['n']}{lead} — the job it's built for")
        if exo.get("n"):
            print(f"  {RED}exogenous{END} (pandemic / bank-run / policy shock): "
                  f"caught {exo['caught']}/{exo['n']} — outside the plumbing, expected blind spots")

    print(f"{BOLD}episodes{END}")
    for ep in bt["episodes"]:
        tag = {"endogenous": "endo", "exogenous": "exo"}.get(ep.get("class"), "")
        tagf = f"{DIM}[{tag}]{END} " if tag else ""
        if not ep.get("in_sample"):
            print(f"  {DIM}{ep['date']} {ep['episode']} — out of sample{END}")
            continue
        lead = f"alert {ep['first_alert_lead_d']}d early" if ep.get("first_alert_lead_d") else f"{RED}not alerted{END}"
        print(f"  {tagf}{ep['date']} {ep['episode'][:44]:<46} max pctl {ep['max_pctl_30d_before']} · {lead}")
    for c in bt.get("caveats", []):
        print(f"{DIM}  caveat: {c}{END}")
    return 0


def cmd_ml(args) -> int:
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    ml = snap.get("deep", {}).get("ml", {})
    if not ml.get("ok"):
        print(f"{RED}ML Lab unavailable:{END} {ml.get('reason')}", file=sys.stderr)
        return 1
    v = ml["validation"]
    print(f"{BOLD}ML LAB{END} P(funding event, 5bd) = {ml['p_event_5bd']:.1%}  ·  {ml['verdict']}")
    print(f"  OOS: {v['oos_days']}d / {v['oos_events']} events (base rate {v['base_rate']:.1%})")
    rule = f" vs rule-based {v['auroc_rule_based']:.3f}" if v.get("auroc_rule_based") is not None else ""
    print(f"  AUROC {v['auroc']:.3f}{rule} · Brier {v['brier']:.4f} vs climatology {v['brier_climatology']:.4f} · embargo {v.get('embargo_bd')}bd")
    u = ml.get("utility") or {}
    if u:
        print(f"  utility/yr (false alarm −{u['cost_per_false_alarm']}): ML@25% {u['ml_at_25pct']} · ML@50% {u['ml_at_50pct']} · rule@80th {u['rule_at_80pctl']}")
    om = ml.get("orthogonal") or {}
    if om.get("auroc") is not None:
        print(f"  {BOLD}orthogonal{END} (no spread/tail features): AUROC {om['auroc']:.3f} · utility@25% {(om.get('utility') or {}).get('ml_at_25pct')}")
    print(f"{BOLD}reliability{END} (predicted vs realized)")
    for r in ml.get("reliability", []):
        print(f"  {r['bin']:>10}  pred {r['mean_pred']:.3f}  real {r['realized']:.3f}  n={r['n']}")
    print(f"{BOLD}top features{END}")
    for f in ml.get("top_features", [])[:6]:
        print(f"  {f['feature']:<18} {f['importance']:+.4f}")
    for c in ml.get("caveats", []):
        print(f"{DIM}  caveat: {c}{END}")
    return 0


def cmd_book(args) -> int:
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    deep = snap.get("deep", {})
    bk = deep.get("book", {})
    if not bk.get("ok"):
        print(f"{RED}Book unavailable:{END} {bk.get('reason')}", file=sys.stderr)
        return 1
    t = bk["today"]
    col = RED if t["stance"] == "risk_off" else GRN if t["stance"] == "risk_on" else DIM
    print(f"{BOLD}THE BOOK{END} {col}{t['stance'].upper()}{END}  ·  {t['rationale']}")
    for p in t["positions"]:
        mark = "·" if p["weight"] == 0 else ("▲" if p["weight"] > 0 else "▼")
        print(f"  {mark} {p['label']:<16} w={p['weight']:+.3f}  ({p['direction']}, "
              f"vol {p['vol_ann_pct']}%/yr, cost {p['tcost_bp']}bp)")
    stk = deep.get("stacker", {})
    if stk.get("ok"):
        print(f"{BOLD}ensemble{END} P(event,5bd)={stk['p_now']} [{stk['published']}] "
              f"dispersion {stk['dispersion_now']} — {stk['verdict']}")
    b = bk["backtest"]
    ci = b.get("ci95") or ["?", "?"]
    print(f"{BOLD}walk-forward{END} {b['sample']['start']} → {b['sample']['end']}")
    print(f"  net Sharpe {b.get('sharpe')} (CI {ci[0]}–{ci[1]}, NW t={b.get('nw_tstat')}) · "
          f"{b.get('ann_return_pct')}%/yr vol {b.get('ann_vol_pct')}% · maxDD {b.get('max_dd_pct')}% · "
          f"turnover {b.get('turnover_ann')}x · cost drag {b.get('cost_drag_bp_ann')}bp/yr")
    for name, m in (b.get("benchmarks") or {}).items():
        print(f"  {DIM}vs {name:<12} Sharpe {m.get('sharpe')} · {m.get('ann_return_pct')}%/yr · "
              f"maxDD {m.get('max_dd_pct')}%{END}")
    print(f"  {BOLD}{b.get('verdict')}{END}")
    lv = bk.get("live", {})
    print(f"{BOLD}live record{END} {lv.get('n_days', 0)}d as-published"
          + (f" since {lv['since']} · cum {lv['cum_return_pct']}%" if lv.get("since") else "")
          + f" — {lv.get('note', '')}")
    for c in bk.get("caveats", []):
        print(f"{DIM}  caveat: {c}{END}")
    return 0


def cmd_analogs(args) -> int:
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    t = snap.get("deep", {}).get("tidetables", {})
    if not t.get("ok"):
        print(f"{RED}Tide Tables unavailable:{END} {t.get('reason')}", file=sys.stderr)
        return 1
    o, nov, sk = t["event_odds"], t["novelty"], t.get("skill", {})
    ci = o.get("ci95") or ["?", "?"]
    print(
        f"{BOLD}TIDE TABLES{END} {o['p']:.0%} of the {o['n']} nearest analogs saw a funding "
        f"event within 5bd (CI {ci[0]:.0%}-{ci[1]:.0%}) · base rate {o['base_rate']:.0%} · "
        f"lift {o.get('lift')}x"
    )
    col = RED if nov.get("verdict") == "uncharted" else DIM
    print(f"  water: {col}{nov.get('verdict')}{END} (NN-distance {nov.get('pctl')}th pctl)")
    if sk.get("ok"):
        print(f"  hindcast: Brier {sk['brier']:.4f} vs climatology {sk['brier_climatology']:.4f} "
              f"· AUROC {sk.get('auroc')} — {sk['verdict']}")
    print(f"{BOLD}nearest analogs{END}")
    for a in t.get("analogs", [])[:8]:
        ev = f"{RED}event{END}" if a["event_within_5bd"] else f"{DIM}quiet{END}"
        ep = f" · {a['episode']}" if a.get("episode") else ""
        print(f"  {a['end_date']}  dist {a['distance']:.2f}  next-5bd max {a['max_move_5bd_bp']:+.1f}bp  {ev}{ep}")
    if t.get("fan"):
        last = t["fan"][-1]
        print(f"{DIM}fan @ +{t['horizon_bd']}bd: p25 {last['p25']} / median {last['median']} / p75 {last['p75']} bp "
              f"(spread now {t['spread_now_bp']}bp){END}")
    return 0


def cmd_swell(args) -> int:
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    s = snap.get("deep", {}).get("swell", {})
    if not s.get("ok"):
        print(f"{RED}Swell unavailable:{END} {s.get('reason')}", file=sys.stderr)
        return 1
    hz = s.get("event_by_horizon", {})
    print(
        f"{BOLD}SWELL FORECAST{END} P(funding event) "
        f"5bd {hz.get('h5', 0):.0%} · 10bd {hz.get('h10', 0):.0%} · "
        f"21bd {hz.get('h21', 0):.0%} · {s['horizon_bd']}bd {hz.get('h' + str(s['horizon_bd']), 0):.0%}"
    )
    pk = s.get("peak") or {}
    print(f"  peak day: {pk.get('date')} ({pk.get('bucket')}) P(≥10bp) {pk.get('p10', 0):.0%}")
    st = s.get("state", {})
    if st.get("available"):
        hot = f"{RED}HOT{END} (lift {st['lift_10bp']}x)" if st.get("hot") else f"{DIM}calm{END}"
        print(f"  damping state (Undertow): {hot}")
    print(f"{BOLD}next 10 days{END}")
    for row in s.get("curve", [])[:10]:
        bar = "█" * int(round(row["p10"] * 40))
        settle = f" · settles ${row['settle_b']}B" if row.get("settle_b") else ""
        print(f"  {row['date']}  {row['p10']:6.1%} {bar} {DIM}{row['bucket']}{settle}{END}")
    v = s.get("validation", {})
    if v.get("ok"):
        print(f"  validation: AUROC {v['auroc']} · Brier {v['brier']:.4f} vs climatology "
              f"{v['brier_climatology']:.4f} — {v['verdict']}")
    return 0


def cmd_physics(args) -> int:
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    eng = snap.get("engines", {})
    deep = snap.get("deep", {})

    b = deep.get("bathymetry", {})
    if b.get("ok"):
        fl, spec, arrow = b.get("floor") or {}, b.get("spectrum") or {}, b.get("arrow") or {}
        p5 = b.get("p_event_5bd")
        col = RED if (p5 or 0) >= 0.35 else DIM
        print(f"{BOLD}BATHYMETRY{END} {col}P(event, 5bd) = {p5:.0%}{END}" if p5 is not None
              else f"{BOLD}BATHYMETRY{END} {DIM}in the event bin now{END}")
        if b.get("mfpt_bd") is not None:
            print(f"  expected days to next event (frozen dynamics): ~{b['mfpt_bd']:.0f}bd")
        elif b.get("mfpt_capped"):
            print(f"  expected days to next event: beyond {b.get('mfpt_cap_bd')}bd horizon")
        if fl.get("ok"):
            print(f"  floor: well @ {fl.get('well_bp')}bp · stiffness {fl.get('stiffness')} · "
                  f"barrier {fl.get('barrier_kt')} k_BT")
        print(f"  spectrum: relaxation τ {spec.get('tau_bd')}bd ({spec.get('tau_pctl', '?')}th pctl) · "
              f"arrow: σ {arrow.get('sigma_nats_bd')} nats/bd ({arrow.get('pctl', '?')}th)")
        v = b.get("validation") or {}
        if v.get("ok"):
            print(f"  {DIM}validation: AUROC {v.get('auroc')} · Brier {v.get('brier')} vs climatology {v.get('brier_climatology')} — {v.get('verdict')}{END}")
    else:
        print(f"{RED}Bathymetry down:{END} {b.get('reason')}", file=sys.stderr)

    m = eng.get("merian", {})
    if m.get("ok"):
        inst = m.get("instability") or {}
        col = RED if (inst.get("g_now") or 0) > 0 and (inst.get("pctl") or 0) >= 90 else DIM
        print(f"{BOLD}MERIAN MODES{END} instability {col}{inst.get('g_now', 0):+.4f}/bd ({inst.get('pctl', '?')}th pctl){END}")
        for mode in (m.get("modes") or [])[:4]:
            per = f"{mode['period_bd']:.0f}bd" if mode.get("period_bd") else "non-osc"
            lbl = f" ← {mode['label']}" if mode.get("label") else ""
            print(f"  {per:>8} · {mode.get('direction')} (e-fold {mode.get('efold_bd')}bd) · amp {mode.get('amp_share', 0):.0%}{lbl}")
        fs = m.get("forecast_skill") or {}
        print(f"  {DIM}{fs.get('verdict', '')}{END}")
    else:
        print(f"{RED}Merian down:{END} {m.get('reason')}", file=sys.stderr)

    g = deep.get("gyre", {})
    if g.get("ok"):
        det, nl, st = g.get("determinism") or {}, g.get("nonlinearity") or {}, g.get("stability") or {}
        print(f"{BOLD}THE GYRE{END} E={g.get('embedding', {}).get('E')} · {det.get('verdict')}")
        print(f"  nonlinearity: {nl.get('verdict')}")
        print(f"  local stability λ {st.get('lambda_now')} ({st.get('pctl')}th pctl)")
        fc = g.get("forecast") or {}
        print(f"  5bd: {fc.get('point_bp')}bp [{fc.get('p25_bp')}, {fc.get('p75_bp')}] — {DIM}{fc.get('verdict', '')}{END}")
    else:
        print(f"{RED}Gyre down:{END} {g.get('reason')}", file=sys.stderr)

    r = eng.get("roguewave", {})
    if r.get("ok"):
        fit = r.get("fit") or {}
        print(f"{BOLD}ROGUE WAVE{END} ξ {fit.get('xi')} [{(fit.get('xi_ci95') or ['?', '?'])[0]}, {(fit.get('xi_ci95') or ['?', '?'])[1]}] · {r.get('tail_verdict')}")
        for rl in r.get("return_levels", []):
            ci = rl.get("ci95") or ["?", "?"]
            print(f"  {rl['years']:>4.0f}y wave ~{rl['bp']:.0f}bp (CI {ci[0]:.0f}–{ci[1]:.0f}) · sample max {r.get('sample_max_bp')}bp")
        for p in r.get("p_exceed", []):
            print(f"  P(pop ≥ {p['x_bp']:.0f}bp): 5bd {p.get('h5', 0):.1%} · 21bd {p.get('h21', 0):.1%} · 63bd {p.get('h63', 0):.1%} {DIM}[{p.get('basis')}]{END}")
    else:
        print(f"{RED}Rogue Wave down:{END} {r.get('reason')}", file=sys.stderr)
    return 0


def cmd_bathymetry(args) -> int:
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    b = snap.get("deep", {}).get("bathymetry", {})
    if not b.get("ok"):
        print(f"{RED}Bathymetry unavailable:{END} {b.get('reason')}", file=sys.stderr)
        return 1
    hz = b.get("p_by_horizon", {})
    print(
        f"{BOLD}BATHYMETRY{END} first-passage P(funding event) "
        f"1bd {hz.get('h1', 0):.0%} · 5bd {hz.get('h5', 0):.0%} · 10bd {hz.get('h10', 0):.0%}"
    )
    mfpt = b.get("mfpt_bd")
    if b.get("state_now", {}).get("in_event_bin"):
        print(f"  {RED}state is already inside the event bin{END} (pop {b['state_now']['pop_bp']}bp)")
    elif mfpt is not None:
        print(f"  expected first passage to the event bin: {mfpt:.0f}bd (frozen dynamics)")
    else:
        print(f"  expected first passage: {DIM}beyond {b.get('mfpt_cap_bd')}bd — the well holds{END}")
    fl = b.get("floor", {})
    if fl.get("ok"):
        print(f"{BOLD}the floor{END} well at {fl['well_bp']}bp · stiffness {fl['stiffness']}/bd · "
              f"temperature {fl['temperature_bp2_bd']}bp²/bd · escape barrier {fl['barrier_kt']} kT")
    sp = b.get("spectrum", {})
    print(f"{BOLD}the spectrum{END} gap {sp.get('gap')} · slowest relaxation τ {sp.get('tau_bd')}bd "
          f"({sp.get('tau_pctl')}th pctl vs own history) · levels {sp.get('energy_levels')}")
    ar = b.get("arrow", {})
    print(f"{BOLD}the arrow{END} entropy production {ar.get('sigma_nats_bd')} nats/bd "
          f"({ar.get('pctl')}th pctl) — how hard the basin is being driven")
    v = b.get("validation", {})
    if v.get("ok"):
        print(f"  validation: AUROC {v['auroc']} · Brier {v['brier']:.4f} vs climatology "
              f"{v['brier_climatology']:.4f} — {v['verdict']}")
    for c in b.get("caveats", []):
        print(f"{DIM}  caveat: {c}{END}")
    return 0


def cmd_navigator(args) -> int:
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    n = snap.get("navigator", {})
    if not n.get("ok"):
        print(f"{RED}Navigator ashore:{END} {n.get('reason')}", file=sys.stderr)
        return 1
    print(f"{BOLD}NAVIGATOR{END} committed P(funding event, 5bd) = {n['p_event_5bd']:.0%} "
          f"{DIM}({n['asof']}{', cached' if n.get('cached') else ''}){END}")
    print(f"  {n.get('rationale', '')}")
    rec = n.get("record") or {}
    if rec.get("ok") and rec.get("brier") is not None:
        col = GRN if rec["brier"] < rec["brier_climatology"] else RED
        print(f"  forward record: {col}Brier {rec['brier']:.4f}{END} vs climatology "
              f"{rec['brier_climatology']:.4f} over {rec['n_resolved']} resolved — {rec['verdict']}")
    elif rec.get("verdict"):
        print(f"  {DIM}{rec['verdict']}{END}")
    return 0


def cmd_ask(args) -> int:
    from seiche import ai, assemble
    snap = asyncio.run(assemble.snapshot())
    res = asyncio.run(ai.ask(" ".join(args.question), snap))
    if res.get("ok"):
        print(f"{DIM}[{res['route']}]{END} {res['answer']}")
        print(f"{DIM}{res['grounding']}{END}")
        return 0
    print(f"{RED}{res.get('reason')}{END}", file=sys.stderr)
    print(f"{DIM}{res.get('hint')}{END}", file=sys.stderr)
    print(json.dumps(res.get("context_pack", {}), indent=1, default=str)[:4000])
    return 1


def cmd_scenarios(args) -> int:
    """Three stochastic views of where the index goes: regime Markov chain,
    OU+jump analytic marginal, and a Monte Carlo path fan."""
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot())
    deep = snap.get("deep", {})
    mk, ou, mc = deep.get("markov", {}), deep.get("oujump", {}), deep.get("montecarlo", {})

    if mk.get("ok"):
        odds = " · ".join(f"{h[1:]}bd {p:.0%}" for h, p in mk["p_reach_stress"].items())
        print(f"{BOLD}MARKOV{END} from {mk['current_regime']} · P(STRESS) {odds} · "
              f"dwell ~{mk['expected_dwell_bd']}bd")
    else:
        print(f"{RED}Markov down:{END} {mk.get('reason')}", file=sys.stderr)

    if ou.get("ok"):
        f = ou["fit"]
        odds = " · ".join(f"{h['h']}bd {h['p_above_stress']:.0%}"
                          f"(jmp {h['jump_share_of_tail']:.0%})" for h in ou["horizons"])
        print(f"{BOLD}OU+JUMP{END} half-life {f['half_life_bd']}bd · pull θ {f['theta']} · "
              f"P(>STRESS) {odds}")
    else:
        print(f"{RED}OU+Jump down:{END} {ou.get('reason')}", file=sys.stderr)

    if mc.get("ok"):
        last = mc["fan"][-1]
        touch = " · ".join(f"{h[1:]}bd {p:.1%}" for h, p in mc["p_touch_stress"].items())
        print(f"{BOLD}MONTE CARLO{END} {mc['n_paths']} paths · fan@21bd "
              f"p25 {last['p25']} / med {last['median']} / p75 {last['p75']}")
        print(f"  P(touch STRESS): {touch}")
    else:
        print(f"{RED}Monte Carlo down:{END} {mc.get('reason')}", file=sys.stderr)
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("seiche.api:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_mcp(args) -> int:
    """Serve the board to AI agents over the Model Context Protocol (stdio)."""
    from seiche import mcp_server
    return mcp_server.serve_stdio()


def cmd_provision(args) -> int:
    """Turn a confirmed payment into a subscriber account + token (operator
    path — the manual counterpart to the /api/provision webhook)."""
    from seiche import provisioning
    try:
        res = provisioning.provision(args.tier, email=args.email,
                                     username=args.username, payment_ref=args.ref)
    except provisioning.ProvisionError as e:
        print(f"{RED}{e}{END}", file=sys.stderr)
        return 1
    if res.get("already"):
        print(f"{YEL}already provisioned{END} for that reference — "
              f"user '{res['username']}' ({res['tier']})")
        return 0
    print(f"provisioned '{res['username']}' ({res['tier']})")
    print(f"password (shown ONCE, share over a safe channel): {res['password']}")
    print(f"token (30d bearer): {res['token']}")
    if args.email:
        print(f"{DIM}credentials emailed to {args.email} (if SMTP configured){END}",
              file=sys.stderr)
    return 0


def cmd_notary(args) -> int:
    """The tamper-evident record ledger: prove no past call was altered."""
    from seiche import notary

    if args.action == "verify":
        v = notary.verify_chain()
        if v["ok"]:
            print(f"{GRN}CHAIN OK{END} {v['n']} readings · head {v['head'][:16]}…")
            return 0
        print(f"{RED}CHAIN BROKEN{END} at seq {v['break_at']}: {v['reason']}", file=sys.stderr)
        return 1
    if args.action == "stamp":
        r = notary.stamp_pending()
        if not r.get("ok"):
            print(f"{YEL}{r.get('reason')}{END}", file=sys.stderr)
            return 1
        print(f"anchored {r['anchored']} commitment(s) to OpenTimestamps (Bitcoin)")
        return 0
    # status
    v = notary.verify_chain()
    state = f"{GRN}OK{END}" if v["ok"] else f"{RED}BROKEN{END}"
    print(f"{BOLD}NOTARY{END} {v['n']} readings · chain {state} · head {notary.head()[:16]}…")
    ots = "available" if notary.ots_available() else "not installed (pip install 'seiche[notary]')"
    print(f"  bitcoin anchor: {ots}")
    for e in notary.entries(10):
        mark = "⚓" if e["anchored"] else " ·"
        print(f"  {mark} {e['pit_date']}  {e['record_sha256'][:16]}…  {DIM}{e['utc']}{END}")
    return 0


def cmd_user(args) -> None:
    from seiche import accounts

    if args.action == "add":
        import secrets as _secrets
        password = args.password or _secrets.token_urlsafe(14)
        accounts.add_user(args.username, password, tier=args.tier)
        print(f"user '{args.username}' ({args.tier}) provisioned")
        print(f"password (shown ONCE, share over a safe channel): {password}")
    elif args.action == "list":
        for u in accounts.list_users():
            print(f"{u['username']:24s} {u['tier']}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="seiche", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("pull", help="force-refresh, print index line").set_defaults(fn=cmd_pull)

    p = sub.add_parser("brief", help="morning desk note (markdown)")
    p.add_argument("--save", action="store_true", help="archive to data/briefs/")
    p.add_argument("--force", action="store_true", help="force-refresh first")
    p.set_defaults(fn=cmd_brief)

    p = sub.add_parser("alert", help="evaluate alert rules once")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_alert)

    p = sub.add_parser("watch", help="pull + alert on a loop")
    p.add_argument("-i", "--interval", type=int, default=1800, help="seconds (default 1800)")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("replay", help="Time Machine: board as of a date")
    p.add_argument("date", help="YYYY-MM-DD")
    p.set_defaults(fn=cmd_replay)

    sub.add_parser("backtest", help="PROOF summary").set_defaults(fn=cmd_backtest)

    p = sub.add_parser("user", help="subscriber accounts (add/list)")
    p.add_argument("action", choices=["add", "list"])
    p.add_argument("username", nargs="?", default="")
    p.add_argument("--tier", default="pro")
    p.add_argument("--password", default="", help="omit to auto-generate")
    p.set_defaults(fn=cmd_user)

    sub.add_parser("ml", help="ML Lab: event probability + validation").set_defaults(fn=cmd_ml)

    sub.add_parser("analogs", help="Tide Tables: nearest historical analogs + forward fan").set_defaults(fn=cmd_analogs)

    sub.add_parser("swell", help="funding-stress forward curve (6 weeks)").set_defaults(fn=cmd_swell)

    sub.add_parser("bathymetry", help="the basin floor: potential, spectrum, entropy, first passage").set_defaults(fn=cmd_bathymetry)

    sub.add_parser("book", help="the Book: today's positions + walk-forward P&L verdict").set_defaults(fn=cmd_book)

    sub.add_parser("navigator", help="the LLM's committed daily forecast + forward record").set_defaults(fn=cmd_navigator)

    sub.add_parser("physics", help="the physics board: landscape, modes, determinism, tail law").set_defaults(fn=cmd_physics)

    sub.add_parser("scenarios", help="stochastic scenarios: Markov regime chain, OU+jump, Monte Carlo fan").set_defaults(fn=cmd_scenarios)

    p = sub.add_parser("ask", help="desk assistant, grounded in the live board")
    p.add_argument("question", nargs="+", help="your question")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("serve", help="run API + UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(fn=cmd_serve)

    sub.add_parser("mcp", help="serve the board to AI agents over MCP (stdio)").set_defaults(fn=cmd_mcp)

    p = sub.add_parser("notary", help="tamper-evident record ledger (status/verify/stamp)")
    p.add_argument("action", nargs="?", choices=["status", "verify", "stamp"], default="status")
    p.set_defaults(fn=cmd_notary)

    p = sub.add_parser("provision", help="confirmed payment -> subscriber account + token")
    p.add_argument("--tier", default="pro", help="pro | founder | enterprise")
    p.add_argument("--email", default="", help="deliver credentials here (needs SMTP)")
    p.add_argument("--username", default="", help="omit to auto-generate")
    p.add_argument("--ref", default="", help="payment reference (txid/invoice) for idempotency")
    p.set_defaults(fn=cmd_provision)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
