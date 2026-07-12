"""The daily dispatch — the desk's morning letter, written by the terminal.

Deterministic prose over the live snapshot (no LLM, no surprises): every
sentence carries the number it stands on, sections appear only when their
engine is live, and phrasing varies day to day by a date-seeded pick so the
letter does not read like a form. Same ethos as brief.py, different register:
the brief is a checklist for the desk, the dispatch is a letter to the reader.

Outputs (relative to the repo root):
  frontend/public/dispatches/{slug}.md        the free reading (+ HAS-PAID marker)
  backend/seiche/dispatches/{slug}.paid.md    the desk's forward read (also free;
                                              filename is the historical contract)
  frontend/public/dispatches/index.json       prepended, deduped, newest first

Run:  python -m seiche.dispatch_daily [--api URL] [--date YYYY-MM-DD] [--force]
Stdlib only, so CI can run it with PYTHONPATH=backend and no install.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_API = "https://api.seiche.info"
HISTORY_URL = "https://seiche.info/data/book_history.json"
MARKER = "<!--HAS-PAID-->"

# repo root = backend/seiche/dispatch_daily.py -> three parents up
REPO_ROOT = Path(__file__).resolve().parents[2]
FREE_DIR = REPO_ROOT / "frontend" / "public" / "dispatches"
PAID_DIR = REPO_ROOT / "backend" / "seiche" / "dispatches"
INDEX = FREE_DIR / "index.json"


# ---------------------------------------------------------------------------
# small formatting helpers — every number in the letter goes through these
# ---------------------------------------------------------------------------
def _fmt(x, d: int = 0) -> str:
    if x is None:
        return "?"
    try:
        return f"{float(x):,.{d}f}"
    except (TypeError, ValueError):
        return str(x)


def _signed(x, d: int = 0) -> str:
    if x is None:
        return "?"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{'+' if v >= 0 else ''}{v:,.{d}f}"


def _pick(date: str, salt: str, options: list[str]) -> str:
    """Date-seeded deterministic choice: varies day to day, reproducible."""
    h = int(hashlib.sha256(f"{date}:{salt}".encode()).hexdigest(), 16)
    return options[h % len(options)]


# ---------------------------------------------------------------------------
# the letter
# ---------------------------------------------------------------------------
_REGIME_FRAME = {
    "CALM": "The basin is flat. That is a reading, not a promise.",
    "EROSION": "Nothing is breaking. The margin for error is what is shrinking.",
    "STRAIN": "The pipes are working harder for the same result. This is the regime where surprises stop being cheap.",
    "STRESS": "The basin is sloshing. From here the board stops being early and starts being current.",
    "CRISIS": "The wave is over the edge. The board's job now is measurement, not warning.",
}


def _title_summary_tag(snap: dict, date: str, prev_value) -> tuple[str, str, str]:
    comp = snap.get("engines", {}).get("composite", {})
    tell = snap.get("deep", {}).get("tell", {}) or {}
    v = comp.get("value")
    regime = (comp.get("regime") or "UNRATED").upper()
    delta = None
    if prev_value is not None and v is not None:
        try:
            delta = float(v) - float(prev_value)
        except (TypeError, ValueError):
            delta = None

    movers = [m for m in snap.get("engines", {}).get("sonar", {}).get("movers", []) if m.get("flag")]
    tell_v = tell.get("tell") if tell.get("ok") else None

    if delta is not None and abs(delta) >= 5:
        direction = "climbs" if delta > 0 else "eases"
        title = f"The board {direction} {abs(delta):.0f} points: {regime.lower()} at {_fmt(v)}"
    elif tell_v is not None and abs(tell_v) >= 30:
        side = "plumbing leads price" if tell_v > 0 else "price leads plumbing"
        title = f"{regime.title()} with a loud tell: {side} at {_signed(tell_v)}"
    elif movers:
        m = movers[0]
        title = f"{m.get('label', 'One gauge')} moved overnight: the {regime.lower()} tape gets a data point"
    else:
        quiet = _pick(date, "title", [
            f"{regime.title()} at {_fmt(v)}: what the pipes say while nothing moves",
            f"A quiet tape, a {regime.lower()} board: the reading for {date}",
            f"{regime.title()}, held: the desk letter for {date}",
        ])
        title = quiet

    hook = ""
    if tell_v is not None:
        hook = f" The Tell reads {_signed(tell_v)}."
    summary = (
        f"The composite reads {_fmt(v)}, regime {regime}."
        + (f" That is {_signed(delta, 1)} on the day." if delta is not None else "")
        + hook
        + " Every number below is checkable on the board."
    )
    return title, summary, regime


def _opening(snap: dict, date: str, prev_value) -> list[str]:
    comp = snap.get("engines", {}).get("composite", {})
    v = comp.get("value")
    regime = (comp.get("regime") or "UNRATED").upper()
    cov = comp.get("coverage_pct")
    out = []
    delta_txt = ""
    if prev_value is not None and v is not None:
        try:
            d = float(v) - float(prev_value)
            delta_txt = f", {_signed(d, 1)} against the last published reading"
        except (TypeError, ValueError):
            pass
    out.append(
        f"The composite reads **{_fmt(v)} out of 100, {regime}**{delta_txt}, "
        f"on {_fmt(cov)}% coverage. "
        + _REGIME_FRAME.get(regime, "The board publishes what it sees and nothing else.")
    )
    decomp = [d for d in comp.get("decomposition", []) if d.get("contribution")]
    if decomp:
        top = decomp[0]
        out.append(
            f"The heaviest hand on the dial is **{top.get('component')}** at a score of "
            f"{_fmt(top.get('score'))}, worth {_signed(top.get('contribution'), 1)} points of the total. "
            + _pick(date, "driver", [
                "When one component carries the reading, watch that component, not the headline.",
                "A composite is only as honest as its decomposition, so here it is.",
                "That is where the reading comes from. The rest is arithmetic.",
            ])
        )
    dead = [d.get("component") for d in comp.get("decomposition", []) if d.get("status") == "DEAD"]
    if dead:
        out.append(
            f"Dead inputs today: {', '.join(dead)}. Coverage is reduced accordingly and "
            "the composite says so rather than filling the gap with yesterday."
        )
    return out


def _tell_para(snap: dict, date: str) -> list[str]:
    tell = snap.get("deep", {}).get("tell", {}) or {}
    if not tell.get("ok"):
        return []
    t = tell.get("tell")
    p, m = tell.get("plumbing_pctl"), tell.get("market_pctl")
    reading = tell.get("reading", "")
    lines = [
        f"The Tell, the gap between what the pipes measure and what the screens price, reads "
        f"**{_signed(t)}**"
        + (
            f": plumbing indicators at the {_fmt(p)}th percentile of their own history, "
            f"market indicators at the {_fmt(m)}th."
            if p is not None and m is not None else "."
        )
    ]
    if t is not None and abs(float(t)) >= 30:
        lines.append(
            _pick(date, "tell", [
                f"That is a wide disagreement, and it resolves one of two ways: the screens catch up to the pipes, or the pipes calm down to meet the screens. The reading is *{reading}*.",
                f"A gap this wide has a short shelf life. The board's read is *{reading}*, and the record of what happened after past gaps sits in PROOF.",
            ])
        )
    elif t is not None:
        lines.append("The gap is modest. Modest gaps are what most days look like, and saying so is part of the record.")
    return lines


def _movers_para(snap: dict, date: str) -> list[str]:
    sonar = snap.get("engines", {}).get("sonar", {})
    flagged = [m for m in sonar.get("movers", []) if m.get("flag")]
    if not flagged:
        return [_pick(date, "quiet", [
            "Overnight, nothing cleared the ±2.5 robust z bar. A quiet tape is a data point too; it is what erosion looks like from the inside.",
            "No gauge moved beyond ±2.5 robust z overnight. The letter reports the silence rather than decorating it.",
        ])]
    bits = []
    for m in flagged[:3]:
        bits.append(
            f"**{m.get('label')}** printed {_fmt(m.get('last'), 2)} {m.get('unit', '')} "
            f"(level z {_signed(m.get('level_z'), 1)}, change z {_signed(m.get('change_z'), 1)}, as of {m.get('asof')})"
        )
    lead = "Overnight, the tape did move: " if len(bits) > 1 else "One gauge moved overnight: "
    return [lead + "; ".join(bits) + "."]


def _calendar_para(snap: dict) -> list[str]:
    cal = snap.get("calendar", {}) or {}
    turn = (snap.get("deep", {}).get("turn") or {}).get("next_turn")
    weather = snap.get("engines", {}).get("weather", {}) or {}
    out = []
    crunches = (cal.get("crunch_windows") or weather.get("crunch_windows") or [])
    if crunches:
        c = crunches[0]
        wc = f", worst case ${_fmt(c.get('worst_case_b'))}B" if c.get("worst_case_b") is not None else ""
        out.append(
            f"The next date that matters is **{c.get('date')}**: {c.get('reason', 'a flagged crunch window')}{wc}."
        )
    if turn:
        band = turn.get("band_bp") or [None, None]
        out.append(
            f"The turn model puts {turn.get('date')} ({turn.get('mode')}) at "
            f"{_signed(turn.get('forecast_bp'), 1)}bp with a band of "
            f"[{_signed(band[0], 1)}, {_signed(band[1], 1)}], severity {turn.get('severity')}/5."
        )
    fomc = (cal.get("fomc_next_90d") or [])
    if fomc:
        f = fomc[0]
        out.append(f"FOMC decides {f.get('date')}, {f.get('days_until')} days out.")
    tax = (cal.get("corporate_tax_next_90d") or [])
    if tax:
        t = tax[0]
        out.append(f"The corporate tax date lands {t.get('date')}, {t.get('days_until')} days out; tax dates drain reserves on a schedule everyone can read.")
    return [" ".join(out)] if out else []


def _honesty_coda(snap: dict) -> list[str]:
    faults = snap.get("faults") or []
    if faults:
        srcs = ", ".join(str(f.get("source")) for f in faults[:4])
        return [
            f"Faults on the board today: {srcs}. The affected inputs are degraded or dead and the "
            "composite's coverage says so. A dashboard that hides its broken gauges is lying with a straight face."
        ]
    return [
        "All sources and engines report live. The misses this board has made sit in PROOF next to the hits; read those before weighting today's letter."
    ]


def _desk_read(snap: dict, date: str) -> str:
    """The continuation: the forward read. Free, like everything else."""
    eng = snap.get("engines", {})
    deep = snap.get("deep", {})
    parts: list[str] = ["## The desk's forward read", ""]

    fwd = []
    bath = deep.get("bathymetry", {}) or {}
    if bath.get("ok"):
        p5 = (bath.get("p_by_horizon") or {}).get("h5", bath.get("p_event_5bd"))
        if p5 is not None:
            mfpt = f", mean first-passage roughly {_fmt(bath.get('mfpt_bd'))} business days" if bath.get("mfpt_bd") is not None else ""
            fwd.append(f"Bathymetry puts the odds of an event inside five business days at **{float(p5):.0%}**{mfpt}.")
    ml = deep.get("ml", {}) or {}
    if ml.get("ok") and ml.get("p_event_5bd") is not None:
        verdict = str(ml.get("verdict", "")).split(";")[0].split("(")[0].strip()
        fwd.append(f"The learned model reads {float(ml['p_event_5bd']):.0%} for the same window" + (f" and calls it *{verdict}*." if verdict else "."))
    markov = deep.get("markov", {}) or {}
    if markov.get("ok"):
        reach = (markov.get("p_reach_stress") or {}).get("h21")
        if reach is not None:
            fwd.append(
                f"The regime chain gives {float(reach):.0%} odds of touching STRESS inside 21 business days, "
                f"with an expected dwell of {_fmt(markov.get('expected_dwell_bd'))} business days in the current state."
            )
    res = eng.get("resonance", {}) or {}
    if res.get("ok"):
        wm = res.get("worst_mode", {}) or {}
        if wm.get("label"):
            fwd.append(
                f"Resonance reads {_fmt(res.get('score'))}: the {wm.get('label')} mode is amplifying at "
                f"{_fmt(wm.get('amplification'), 1)}x, which is the basin ringing louder to the same calendar."
            )
    if fwd:
        parts += [" ".join(fwd), ""]

    pos = []
    crowd = eng.get("crowding", {}) or {}
    if crowd.get("ok") and crowd.get("rows"):
        r = crowd["rows"][0]
        pos.append(
            f"The most crowded seat is **{r.get('contract')}**, leveraged net {_signed(r.get('lev_net_share_oi'), 2)} "
            f"of open interest (z {_signed(r.get('z'), 1)})."
        )
    wh = eng.get("warehouse", {}) or {}
    if wh.get("ok"):
        pos.append(
            f"Dealer warehouse holds ${_fmt(wh.get('total_net_b'))}B, the {_fmt(wh.get('total_pctl'))}th percentile "
            f"of its history, {_fmt(wh.get('long_end_share_pct'))}% of it long end."
        )
    if pos:
        parts += ["### Positioning", "",
                  " ".join(pos) + " Positioning data is COT and carries its native T+3 lag; the lag is shown, never hidden.", ""]

    echo = eng.get("echo", {}) or {}
    if echo.get("ok") and echo.get("matches"):
        parts += ["### Echoes", ""]
        parts += ["| episode | window | similarity |", "|---|---|---|"]
        for m in echo["matches"][:4]:
            parts.append(f"| {m.get('episode')} | T−{m.get('lead_days')}d | {_fmt(m.get('similarity'), 2)} |")
        parts += ["", "Similarity is not destiny. The echo table says *this rhymes*, and PROOF says how often rhymes mattered.", ""]

    comp = eng.get("composite", {}) or {}
    regime = (comp.get("regime") or "").upper()
    mind = {
        "CALM": "a Tell above +30, an SRF print above zero on an ordinary day, or two consecutive movers on the funding side",
        "EROSION": "the Tell closing back under +15, reserves stabilising for two weeks, or the resonance amplification easing below 1x",
        "STRAIN": "SRF or discount window take-up on a day with no calendar excuse, a mover breaching ±3 z on the funding side, or the composite crossing 60",
        "STRESS": "the composite easing below 55 for three sessions, or the crunch calendar clearing without a print",
        "CRISIS": "facility usage normalising and the composite holding under 70 for a week",
    }.get(regime, "the numbers above moving against the read")
    parts += ["### What would change the desk's mind", "",
              f"{mind.capitalize()}. When one of those prints, the letter will say so, in this same place, with the number.", ""]

    parts += ["The board recomputes six times a day; this letter freezes one reading of it. "
              "Free public data with native lags. Not investment advice."]
    return "\n".join(parts)


def build_dispatch(snap: dict, prev_value=None, date: str | None = None) -> dict:
    comp = snap.get("engines", {}).get("composite", {})
    if comp.get("value") is None or not comp.get("regime"):
        raise SystemExit("refusing to write a dispatch without a live composite (no board, no letter)")
    date = date or (snap.get("generated_at") or datetime.now(timezone.utc).isoformat())[:10]
    title, summary, tag = _title_summary_tag(snap, date, prev_value)

    paras: list[str] = []
    paras += _opening(snap, date, prev_value)
    paras += _tell_para(snap, date)
    paras += _movers_para(snap, date)
    cal = _calendar_para(snap)
    if cal:
        paras += ["## The dates that matter"] + cal
    paras += ["## What the board is honest about"] + _honesty_coda(snap)

    free_md = "\n\n".join(p for p in paras if p)
    desk_md = _desk_read(snap, date)

    return {
        "slug": f"{date}-daily",
        "title": title,
        "date": date,
        "tag": tag,
        "summary": summary,
        "free_md": free_md,
        "desk_md": desk_md,
    }


# ---------------------------------------------------------------------------
# filesystem + index
# ---------------------------------------------------------------------------
def write_dispatch(d: dict, repo_root: Path | None = None) -> list[str]:
    root = repo_root or REPO_ROOT
    free_dir = root / "frontend" / "public" / "dispatches"
    paid_dir = root / "backend" / "seiche" / "dispatches"
    index = free_dir / "index.json"
    free_dir.mkdir(parents=True, exist_ok=True)
    paid_dir.mkdir(parents=True, exist_ok=True)

    free_path = free_dir / f"{d['slug']}.md"
    body = d["free_md"] + (f"\n\n{MARKER}\n" if d["desk_md"] else "\n")
    free_path.write_text(body)

    written = [str(free_path)]
    if d["desk_md"]:
        paid_path = paid_dir / f"{d['slug']}.paid.md"
        paid_path.write_text(d["desk_md"] + "\n")
        written.append(str(paid_path))

    entries = []
    if index.exists():
        entries = json.loads(index.read_text())
    entries = [e for e in entries if e.get("slug") != d["slug"]]
    entries.insert(0, {k: d[k] for k in ("slug", "title", "date", "tag", "summary")})
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    index.write_text(json.dumps(entries, indent=2) + "\n")
    written.append(str(index))
    return written


# ---------------------------------------------------------------------------
# CLI — stdlib fetch so CI needs no install
# ---------------------------------------------------------------------------
def _get_json(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "seiche-dispatch-daily"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _prev_published_value(history_url: str):
    """Yesterday's as-published composite from the hash-chained Book history."""
    try:
        hist = _get_json(history_url, timeout=30)
        if isinstance(hist, list) and hist:
            return hist[-1].get("value")
    except Exception:
        return None
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write today's dispatch from the live board.")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--history-url", default=HISTORY_URL)
    ap.add_argument("--date", default=None, help="override the dispatch date (YYYY-MM-DD)")
    ap.add_argument("--force", action="store_true", help="rewrite even if today's dispatch exists")
    args = ap.parse_args(argv)

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{date}-daily"
    if INDEX.exists() and not args.force:
        if any(e.get("slug") == slug for e in json.loads(INDEX.read_text())):
            print(f"dispatch {slug} already published — nothing to do")
            return 0

    snap = _get_json(f"{args.api}/api/overview")
    prev = _prev_published_value(args.history_url)
    d = build_dispatch(snap, prev_value=prev, date=date)
    for p in write_dispatch(d):
        print(f"wrote {p}")
    print(f"dispatch ready: {d['slug']} — {d['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
