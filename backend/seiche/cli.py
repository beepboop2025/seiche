"""seiche — the operator CLI.

  seiche pull                force-refresh everything, print the index line
  seiche brief [--save]      this morning's desk note (markdown to stdout)
  seiche alert               evaluate alert rules once (cron/launchd-friendly)
  seiche watch [-i SECONDS]  pull + alert on a loop
  seiche replay DATE         Time Machine: the board as of YYYY-MM-DD
  seiche backtest            PROOF summary in the terminal
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
    print(
        f"  recall {cap['recall']:.0%} · precision {cap['precision']:.0%} "
        f"(base rate {cap['base_rate']:.0%}) · alert = ≥{cap['alert_pctl']:.0f}th pctl"
    )
    print(f"  median alert run-up before events: {cap['median_lead_d']:.0f}d")
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

    p = sub.add_parser("serve", help="run API + UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
