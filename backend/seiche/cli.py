"""seiche — the operator CLI.

  seiche pull                force-refresh everything, print the index line
  seiche brief [--save]      this morning's desk note (markdown to stdout)
  seiche alert               evaluate alert rules once (cron/launchd-friendly)
  seiche watch [-i SECONDS]  pull + alert on a loop
  seiche replay DATE         Time Machine: the board as of YYYY-MM-DD
  seiche backtest            PROOF summary in the terminal
  seiche analogs             Tide Tables: nearest analogs + forward fan
  seiche serve [--port]      run the API + UI

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
    from seiche import assemble
    snap = asyncio.run(assemble.snapshot(force=True))
    print(_index_line(snap))
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
    print(f"{BOLD}episodes{END}")
    for ep in bt["episodes"]:
        if not ep.get("in_sample"):
            print(f"  {DIM}{ep['date']} {ep['episode']} — out of sample{END}")
            continue
        lead = f"alert {ep['first_alert_lead_d']}d early" if ep.get("first_alert_lead_d") else f"{RED}not alerted{END}"
        print(f"  {ep['date']} {ep['episode'][:48]:<50} max pctl {ep['max_pctl_30d_before']} · {lead}")
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


def cmd_serve(args) -> int:
    import uvicorn
    uvicorn.run("seiche.api:app", host=args.host, port=args.port, reload=False)
    return 0


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

    sub.add_parser("ml", help="ML Lab: event probability + validation").set_defaults(fn=cmd_ml)

    sub.add_parser("analogs", help="Tide Tables: nearest historical analogs + forward fan").set_defaults(fn=cmd_analogs)

    p = sub.add_parser("ask", help="desk assistant, grounded in the live board")
    p.add_argument("question", nargs="+", help="your question")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("serve", help="run API + UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
