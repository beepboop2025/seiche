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
  backend/seiche/dispatches/state.json        the letter's memory: which prints it
                                              has already reported, so a slow series
                                              at an extreme is news once, not daily

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
STATE = PAID_DIR / "state.json"


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
# novelty state — the letter's memory of what it has already told the reader.
# A mover is news on the first letter after its print date; after that it is a
# standing flag, and re-headlining it would be the letter dressing persistence
# as news. The state keeps two maps: `reported` (as of after today's letter)
# and `reported_prev` (the baseline today's letter was built from), keyed by
# `date`, so a same-day rebuild — the CI announce step runs the generator
# again the better part of an hour after the write — reproduces the same
# novelty decisions instead of finding its own morning's letter in the state
# and calling everything old news.
# ---------------------------------------------------------------------------
def load_state(path: Path | None = None) -> dict:
    p = path or STATE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _novelty_baseline(state: dict, date: str) -> dict:
    """label -> asof of the last print the letter reported for that series."""
    if state.get("date") == date:
        return state.get("reported_prev") or {}
    return state.get("reported") or {}


def _is_novel(m: dict, baseline: dict) -> bool:
    prev = baseline.get(str(m.get("label")))
    asof = m.get("asof")
    # ISO dates compare lexicographically; an unknown asof is treated as news
    # because the letter cannot prove it already told the reader.
    return asof is None or prev is None or str(asof) > str(prev)


def _split_flagged(snap: dict, baseline: dict) -> tuple[list[dict], list[dict]]:
    """Flagged movers split into (novel, held). Novel sorts freshest print
    first, loudest second: the headline goes to what is actually new."""
    flagged = [m for m in snap.get("engines", {}).get("sonar", {}).get("movers", [])
               if m.get("flag")]
    novel = sorted((m for m in flagged if _is_novel(m, baseline)),
                   key=lambda m: ((m.get("age_d") or 0), -(m.get("max_abs_z") or 0)))
    held = [m for m in flagged if not _is_novel(m, baseline)]
    return novel, held


def _updated_state(snap: dict, baseline: dict, date: str) -> dict:
    """Every currently flagged print is on the record after today's letter
    (novel ones in the movers line, held ones in the standing-flags line).
    Labels that stop flagging fall out, which keeps the file bounded; a
    series can only re-flag on a newer print, which is novel again anyway."""
    reported = {str(m.get("label")): str(m.get("asof"))
                for m in snap.get("engines", {}).get("sonar", {}).get("movers", [])
                if m.get("flag") and m.get("asof") is not None}
    return {"date": date, "reported_prev": baseline, "reported": reported}


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


def _title_summary_tag(snap: dict, date: str, prev_value, baseline: dict) -> tuple[str, str, str]:
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

    novel, held = _split_flagged(snap, baseline)
    tell_v = tell.get("tell") if tell.get("ok") else None

    if delta is not None and abs(delta) >= 5:
        direction = "climbs" if delta > 0 else "eases"
        title = f"The board {direction} {abs(delta):.0f} points: {regime.lower()} at {_fmt(v)}"
    elif tell_v is not None and abs(tell_v) >= 30:
        side = "plumbing leads price" if tell_v > 0 else "price leads plumbing"
        title = f"{regime.title()} with a loud tell: {side} at {_signed(tell_v)}"
    elif novel:
        m = novel[0]
        # "Overnight" has to be earned. SONAR only flags prints within
        # SONAR_FRESH_D of the board, but a weekly release is still days old
        # when it is the freshest thing on the tape, so say which it is.
        when = "moved overnight" if (m.get("age_d") or 0) <= 1 else "moved on the latest print"
        title = f"{m.get('label', 'One gauge')} {when}: the {regime.lower()} tape gets a data point"
    elif held:
        # Everything flagged was already reported on the day it printed.
        # Persistence is worth a title, but a different one each day, not
        # the same headline until the next release cycle.
        title = _pick(date, "title-held", [
            f"{regime.title()} at {_fmt(v)}: the flags hold, no new print to report",
            f"No new prints, standing flags: the {regime.lower()} reading for {date}",
            f"{regime.title()}, carried: the same flags, one day older",
        ])
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


def _movers_para(snap: dict, date: str, baseline: dict) -> list[str]:
    sonar = snap.get("engines", {}).get("sonar", {})
    novel, held = _split_flagged(snap, baseline)
    out: list[str] = []

    if novel:
        bits = []
        for m in novel[:3]:
            bits.append(
                f"**{m.get('label')}** printed {_fmt(m.get('last'), 2)} {m.get('unit', '')} "
                f"(level z {_signed(m.get('level_z'), 1)}, change z {_signed(m.get('change_z'), 1)}, as of {m.get('asof')})"
            )
        # The lead describes the whole list, so it is bound by the OLDEST print in
        # it, not the newest. Claiming "overnight" because one of three gauges
        # printed last night is the same overstatement this gate exists to stop.
        oldest = max((m.get("age_d") or 0) for m in novel[:3])
        if oldest <= 1:
            lead = "Overnight, the tape did move: " if len(bits) > 1 else "One gauge moved overnight: "
        else:
            lead = ("The tape moved on its latest prints, none of them from last night: "
                    if len(bits) > 1 else "One gauge moved on its latest print, not overnight: ")
        out.append(lead + "; ".join(bits) + ".")
    elif held:
        out.append(_pick(date, "held-quiet", [
            "No new print cleared the ±2.5 robust z bar since the last letter. What is flagged today was flagged when it printed, and re-breaking old news is not this letter's trade.",
            "Nothing new crossed the ±2.5 robust z bar overnight. The standing flags below are persistence, not news, and the letter labels them as such.",
        ]))
    else:
        out.append(_pick(date, "quiet", [
            "Overnight, nothing cleared the ±2.5 robust z bar. A quiet tape is a data point too; it is what erosion looks like from the inside.",
            "No gauge moved beyond ±2.5 robust z overnight. The letter reports the silence rather than decorating it.",
        ]))
        # A slow series sitting at an extreme level is worth knowing about,
        # but it is not news and the letter must not dress it as news.
        stale = [m for m in sonar.get("movers", []) if m.get("stale")
                 and (m.get("max_abs_z") or 0) >= 2.5]
        if stale:
            s = stale[0]
            out.append(
                f"One reading is extreme but not fresh: **{s.get('label')}** sits at "
                f"{_fmt(s.get('max_abs_z'), 1)} robust z on a print from {s.get('asof')}, "
                f"{s.get('age_d')} days behind the board. That is a level worth knowing and "
                "not a move, so it is reported here rather than in the movers line."
            )

    if held:
        bits = "; ".join(
            f"**{m.get('label')}** (|z| {_fmt(m.get('max_abs_z'), 1)}, as of {m.get('asof')})"
            for m in held[:3]
        )
        out.append(
            f"Still flagged from prints already covered in an earlier letter: {bits}. "
            "A standing flag is context, not news; when a fresh print lands it goes back in the movers line."
        )
    return out


def _press_para(snap: dict) -> list[str]:
    """Scuttlebutt, only when a topic actually flags: press attention on the
    plumbing, display only, never a score input. One sentence, no dashes."""
    flags = (snap.get("engines", {}).get("scuttlebutt", {}) or {}).get("flags") or []
    if not flags:
        return []
    shown = "; ".join(str(f) for f in flags[:2])
    return [f"The scuttlebutt, display only and feeding no score: {shown}."]


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


def _forward_sentences(snap: dict) -> list[str]:
    """The forward-odds sentences, one per live model. The desk read always
    carries them; the free letter borrows them on days with no new print."""
    eng = snap.get("engines", {})
    deep = snap.get("deep", {})
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
    return fwd


def _forward_pulse(snap: dict, date: str) -> list[str]:
    """On a day with no new print, the free letter takes its spine from the
    forward read: the odds recompute daily even when the tape does not, so
    this paragraph is the part of a quiet letter that is genuinely new."""
    fwd = _forward_sentences(snap)
    if not fwd:
        return []
    lead = _pick(date, "pulse", [
        "With nothing new on the tape, the forward read carries the letter. ",
        "No fresh print does not mean no information; the odds recompute either way. ",
        "The tape is quiet, so the forward odds do the talking. ",
    ])
    return [lead + " ".join(fwd[:3])]


def _desk_read(snap: dict, date: str) -> str:
    """The continuation: the forward read. Free, like everything else."""
    eng = snap.get("engines", {})
    parts: list[str] = ["## The desk's forward read", ""]

    fwd = _forward_sentences(snap)
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


def build_dispatch(snap: dict, prev_value=None, date: str | None = None,
                   state: dict | None = None) -> dict:
    comp = snap.get("engines", {}).get("composite", {})
    if comp.get("value") is None or not comp.get("regime"):
        raise SystemExit("refusing to write a dispatch without a live composite (no board, no letter)")
    date = date or (snap.get("generated_at") or datetime.now(timezone.utc).isoformat())[:10]
    baseline = _novelty_baseline(state or {}, date)
    title, summary, tag = _title_summary_tag(snap, date, prev_value, baseline)

    paras: list[str] = []
    paras += _opening(snap, date, prev_value)
    paras += _tell_para(snap, date)
    paras += _movers_para(snap, date, baseline)
    novel, _ = _split_flagged(snap, baseline)
    if not novel:
        paras += _forward_pulse(snap, date)
    paras += _press_para(snap)
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
        "state": _updated_state(snap, baseline, date),
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

    if d.get("state"):
        state_path = paid_dir / "state.json"
        state_path.write_text(json.dumps(d["state"], indent=2) + "\n")
        written.append(str(state_path))

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
# Telegram announcement — a digest with the numbers, then the link
# ---------------------------------------------------------------------------
def build_telegram_digest(d: dict, snap: dict | None = None) -> str:
    """A Telegram-sized digest of the letter: the reading, the hook numbers,
    the next date, the link. Plain text, no markup surprises, no dashes."""
    lines = [
        f"SEICHE · the daily letter · {d['date']}",
        "",
        d["title"],
        "",
        d["summary"],
    ]
    if snap:
        cal = snap.get("calendar", {}) or {}
        weather = snap.get("engines", {}).get("weather", {}) or {}
        crunches = cal.get("crunch_windows") or weather.get("crunch_windows") or []
        if crunches:
            c = crunches[0]
            lines += ["", f"Next date that matters: {c.get('date')} ({c.get('reason', 'flagged window')})."]
    lines += ["", f"Full letter: https://seiche.info/#dispatches/{d['slug']}"]
    return "\n".join(lines)


def announce_telegram(d: dict, snap: dict | None = None) -> None:
    """Send the digest. Fail loud: an explicit announce with missing or
    refused credentials is an error, not a silent skip."""
    import os

    token = os.environ.get("SEICHE_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("SEICHE_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("announce needs SEICHE_TELEGRAM_BOT_TOKEN and SEICHE_TELEGRAM_CHAT_ID")
    body = json.dumps({
        "chat_id": chat_id,
        "text": build_telegram_digest(d, snap),
        "disable_web_page_preview": False,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise SystemExit(f"telegram refused the message: {resp}")
    print(f"announced on telegram (message_id {resp['result']['message_id']})")


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
    ap.add_argument("--announce", action="store_true",
                    help="after writing, send the Telegram digest "
                         "(needs SEICHE_TELEGRAM_BOT_TOKEN and SEICHE_TELEGRAM_CHAT_ID)")
    ap.add_argument("--announce-only", action="store_true",
                    help="skip writing files; just build today's letter and send the digest")
    args = ap.parse_args(argv)

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = f"{date}-daily"

    snap = _get_json(f"{args.api}/api/overview")
    prev = _prev_published_value(args.history_url)
    d = build_dispatch(snap, prev_value=prev, date=date, state=load_state())

    if args.announce_only:
        announce_telegram(d, snap)
        return 0

    if INDEX.exists() and not args.force:
        if any(e.get("slug") == slug for e in json.loads(INDEX.read_text())):
            print(f"dispatch {slug} already published — nothing to do")
            return 0

    for p in write_dispatch(d):
        print(f"wrote {p}")
    print(f"dispatch ready: {d['slug']} — {d['title']}")
    if args.announce:
        announce_telegram(d, snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
