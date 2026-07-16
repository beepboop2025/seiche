#!/usr/bin/env python3
"""Seiche Telegram bot — the US money-market plumbing desk in your chat.

Division of labor across the fleet: this bot watches the PLUMBING (dollar
funding stress: reserves, repo, the Fed's balance sheet); LiquiLens watches
the INSTITUTIONS. It serves the same public API the terminal runs on
(api.seiche.info) and never computes numbers of its own — every reply is
traceable to a served, sourced reading. Seiche is a free public good: no
paywall, no sign-in, voluntary support only.

Modes
  (no args)   long-poll command loop (systemd service)
  --letter    compose and send the daily letter to all subscribers
              (systemd timer, 11:30 UTC = pre-US-open)
  --tandem    cross-desk check (plumbing × institutions): message subscribers
              ONLY when the joint quadrant changes class (systemd timer, 6h)
  --setup     register the command menu and bot description with Telegram

Commands
  /start /stop     subscribe / unsubscribe from the daily letter
  /now             the gauge right now: regime, composite, the Tell
  /odds            forward event odds (Navigator, with its caveats out loud)
  /turns           the next calendar turn + crunch windows + auction desk
  /analogs         historical analogs from the wreck ledger
  /proof           the backtest scoreboard, misses included
  /letter          today's dispatch: title, summary, link
  /institutions    the other desk: LiquiLens Failure Radar summary
  /tandem          the cross-desk read: plumbing × institutions quadrant
  /ask <question>  desk assistant, grounded strictly in the live board
  /help            this list

Tandem: both bots read each other's PUBLIC APIs and recompute the joint
quadrant from source — no shared state, no trust in the other's summary.

Stdlib only (urllib) so deployment is: copy file, set env, start unit.
Env: SEICHE_BOT_TOKEN (required) · SEICHE_API (default
https://api.seiche.info) · LIQUILENS_API (default
https://api.liquilens.in/api) · SEICHE_BOT_STATE (default
/var/lib/seiche-bot). Honesty rules carried over from the terminal: an API
that does not answer is said out loud; absence is not calm.
"""

from __future__ import annotations

import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

TOKEN = os.environ.get("SEICHE_BOT_TOKEN", "")
API = os.environ.get("SEICHE_API", "https://api.seiche.info").rstrip("/")
LL_API = os.environ.get("LIQUILENS_API", "https://api.liquilens.in/api").rstrip("/")
SITE = "https://seiche.info"
STATE_DIR = os.environ.get("SEICHE_BOT_STATE", "/var/lib/seiche-bot")
TG = f"https://api.telegram.org/bot{TOKEN}"

POLL_TIMEOUT = 50

FOOT = ("\n<i>Free public good — no paywall, no sign-in. Every number is on "
        "the board at seiche.info with sources and an honest backtest.</i>")


# ---------------------------------------------------------------- plumbing --
def tg_call(method: str, payload: dict) -> dict | None:
    req = urllib.request.Request(
        f"{TG}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=POLL_TIMEOUT + 10) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"tg {method} failed: {exc}", file=sys.stderr)
        return None


def send(chat_id: int, text: str) -> None:
    # Telegram caps messages at 4096 chars; split on a hard seam.
    while text:
        chunk, text = text[:4000], text[4000:]
        tg_call("sendMessage", {
            "chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })


def _get_json(url: str, timeout: int = 25, tries: int = 2) -> dict | list | None:
    # Explicit User-Agent: some edges 403 Python's default one.
    req = urllib.request.Request(url, headers={"User-Agent": "seiche-bot"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == tries - 1:
                print(f"GET {url} failed: {exc}", file=sys.stderr)
            else:
                time.sleep(1.5)
    return None


def api_get(path: str):
    return _get_json(f"{API}{path}")


def ll_get(path: str):
    """The other desk: LiquiLens's public institutions API, read verbatim."""
    return _get_json(f"{LL_API}{path}")


def _state_path(name: str) -> str:
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, name)


def load_state(name: str, default):
    try:
        with open(_state_path(name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def save_state(name: str, value) -> None:
    tmp = _state_path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    os.replace(tmp, _state_path(name))


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def pct(x, digits: int = 0) -> str:
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


# -------------------------------------------------------------- formatters --
REGIME_ICON = {"CALM": "🟢", "EROSION": "🟡", "STRAIN": "🟠"}


def _regime_icon(regime) -> str:
    return REGIME_ICON.get(str(regime or "").upper(), "🔴")


def fmt_now(gauge: dict | None, pub: dict | None) -> str:
    if not gauge:
        return ("The board did not answer — absence is not calm; the gauge "
                f"is at {SITE}.")
    lines = [f"{_regime_icon(gauge.get('regime'))} <b>US funding stress, right now</b>",
             "",
             f"Regime: <b>{esc(gauge.get('regime'))}</b> · composite "
             f"<b>{gauge.get('index')}</b>/100 · coverage {gauge.get('coverage_pct')}%"]
    line = ((pub or {}).get("conclusion") or {}).get("line")
    if line:
        lines.append(esc(line))   # already carries the Tell reading
    else:
        tell = gauge.get("tell")
        if isinstance(tell, (int, float)):
            reading = ((pub or {}).get("conclusion") or {}).get("tell_reading") or (
                "plumbing leads price" if tell > 0 else "price leads plumbing")
            lines.append(f"The Tell: {tell:+.0f} — {esc(reading)}.")
    nt = gauge.get("next_turn") or {}
    if nt.get("date"):
        lines.append(f"\nNext turn: <b>{esc(nt['date'])}</b> "
                     f"({esc(nt.get('mode', '').replace('_', ' '))}) — forecast "
                     f"{nt.get('forecast_bp')}bp, severity {nt.get('severity')}/5")
    for w in (gauge.get("crunch_windows") or [])[:2]:
        lines.append(f"⚠ {esc(w.get('date'))}: {esc(w.get('reason'))}")
    return "\n".join(lines) + FOOT


def fmt_odds(overview: dict | None) -> str:
    nav = (overview or {}).get("navigator") or {}
    if not nav.get("ok"):
        return "The Navigator did not answer — try again shortly."
    lines = [f"🎲 <b>Forward event odds</b> — as of {esc(nav.get('asof'))}",
             "",
             f"P(funding event, next 5 business days): "
             f"<b>{pct(nav.get('p_event_5bd'))}</b>"]
    if nav.get("rationale"):
        lines.append(f"\n{esc(nav['rationale'])}")
    for c in (nav.get("caveats") or [])[:2]:
        lines.append(f"\n<i>Caveat, said out loud: {esc(c)}</i>")
    lines.append(f"<i>Method: {esc(nav.get('method', 'n/a'))}</i>")
    return "\n".join(lines) + FOOT


def fmt_turns(gauge: dict | None, overview: dict | None) -> str:
    if not gauge and not overview:
        return "The board did not answer — absence is not calm."
    lines = ["📅 <b>The calendar desk</b>", ""]
    nt = (gauge or {}).get("next_turn") or {}
    if nt.get("date"):
        band = nt.get("band_bp") or [None, None]
        lines.append(f"Next turn <b>{esc(nt['date'])}</b> "
                     f"({esc(nt.get('mode', '').replace('_', ' '))}): forecast "
                     f"{nt.get('forecast_bp')}bp in [{band[0]}, {band[1]}] · "
                     f"severity {nt.get('severity')}/5")
    for w in ((gauge or {}).get("crunch_windows") or [])[:3]:
        lines.append(f"⚠ {esc(w.get('date'))}: {esc(w.get('reason'))} "
                     f"(forecast reserves ${w.get('forecast_reserves_b')}B, "
                     f"worst case ${w.get('worst_case_b')}B)")
    cal = (overview or {}).get("calendar") or {}
    fomc = cal.get("fomc_next_90d") or []
    if fomc:
        lines.append("\nFOMC: " + " · ".join(
            f"{esc(f.get('date'))} ({f.get('days_until')}d)" for f in fomc[:3]))
    setts = cal.get("upcoming_settlements") or []
    if setts:
        lines.append("Auction settlements: " + " · ".join(
            f"{esc(s.get('date'))} ${s.get('amount_b')}B" for s in setts[:3]))
    tax = cal.get("corporate_tax_next_90d") or []
    if tax:
        lines.append("Corporate tax dates: " + " · ".join(
            f"{esc(t.get('date'))} ({t.get('days_until')}d)" for t in tax[:2]))
    return "\n".join(lines) + FOOT


def fmt_analogs(wrecks: dict | None) -> str:
    eps = (wrecks or {}).get("episodes") or []
    if not eps:
        return "The wreck ledger did not answer — try again shortly."
    lines = ["🕰 <b>The wreck ledger</b> — what the board read before past storms", ""]
    for e in eps[:6]:
        then = next((b for b in (e.get("board") or []) if b.get("offset_bd") == 0),
                    None) or next(iter(e.get("board") or []), {})
        lines.append(f"• <b>{esc(e.get('date'))}</b> — {esc(e.get('episode'))}\n"
                     f"  board then: {esc(then.get('regime', '?'))} "
                     f"{then.get('value', '?')}/100")
    lines.append(f"\nReplay any date yourself: {SITE} (Time Machine).")
    return "\n".join(lines) + FOOT


def fmt_proof(pub: dict | None) -> str:
    proof = (pub or {}).get("proof") or {}
    if not proof.get("n_events"):
        return "The proof scoreboard did not answer — try again shortly."
    ci = proof.get("recall_ci95") or [None, None]
    lines = ["📜 <b>The PROOF scoreboard</b> — the backtest, misses included", "",
             f"Recall: <b>{pct(proof.get('recall'))}</b> "
             f"(95% CI {pct(ci[0])}–{pct(ci[1])}) over {proof.get('n_events')} events",
             f"Precision (runs): {pct(proof.get('precision_runs'))} · "
             f"base rate {pct(proof.get('base_rate'), 1)}",
             f"Median lead: {proof.get('median_lead_d'):.0f} days"]
    lines.append("\nEvery episode with its verdict — hits AND misses — is on "
                 f"the board: {SITE}/#proof")
    return "\n".join(lines) + FOOT


def fmt_letter(index: list | None) -> str:
    if not index:
        return "The dispatch index did not answer — the letters live at seiche.info."
    d = index[0]
    lines = [f"✉️ <b>{esc(d.get('title'))}</b>",
             f"{esc(d.get('date'))} · tag {esc(d.get('tag'))}", "",
             esc(d.get("summary", "")),
             f"\nRead it: {SITE}/dispatches/{urllib.parse.quote(d.get('slug', ''))}.md"]
    return "\n".join(lines) + FOOT


def fmt_ask(res) -> str:
    if not res:
        return "The desk assistant did not answer — try again shortly."
    if isinstance(res, dict):
        ans = res.get("answer") or res.get("text") or res.get("detail") or ""
        lines = [esc(ans).strip()]
        cites = res.get("citations") or res.get("sources") or []
        if cites:
            lines.append("\n<i>Sources: " + esc(" · ".join(str(c) for c in cites)) + "</i>")
        return "\n".join(lines) or "The desk assistant returned an empty answer."
    return esc(str(res))


# ------------------------------------------------ cross-desk (LiquiLens) ----
TIER_ICON = {"red": "🔴", "orange": "🟠", "yellow": "🟡", "green": "🟢"}
PLUMB_LEVEL = {"CALM": 0, "EROSION": 1, "STRAIN": 2}
INST_LEVEL = {"green": 0, "yellow": 1, "orange": 2, "red": 3}


def _plumb_level(regime) -> int | None:
    if not regime:
        return None
    return PLUMB_LEVEL.get(str(regime).upper(), 3)


def _inst_level(board) -> int | None:
    rows = (board or {}).get("rows") or []
    if not rows:
        return None
    return max(INST_LEVEL.get(r.get("tier"), 0) for r in rows)


def fmt_institutions(board: dict | None) -> str:
    if not board or not board.get("rows"):
        return ("LiquiLens's desk did not answer — the institutions board is at "
                "demo.liquilens.in. Absence is not calm.")
    rows = board["rows"]
    t = board.get("tiers", {})
    tier_line = " · ".join(f"{TIER_ICON[k]} {v}" for k, v in t.items() if v)
    lines = [f"🏦 <b>The institutions desk</b> (LiquiLens, read verbatim) — "
             f"{esc(board.get('as_of'))}",
             f"{len(rows)} institutions scored · {tier_line}", ""]
    for r in rows[:5]:
        lines.append(f"{TIER_ICON.get(r.get('tier'), '·')} {esc(r['name'])} — "
                     f"12m failure PD {r['hazard']['pd_12m'] * 100:.2f}%")
    lines.append("\n<i>Institutions are LiquiLens's desk: liquilens.in · "
                 "@LiquiLens_bot. Plumbing is this desk's.</i>")
    return "\n".join(lines)


def fmt_tandem(gauge: dict | None, board: dict | None) -> str:
    """The joint read — identical logic to the LiquiLens bot's /tandem, both
    recomputed from the two public APIs so neither trusts a summary."""
    p = _plumb_level((gauge or {}).get("regime"))
    i = _inst_level(board)
    lines = ["🔀 <b>Cross-desk read: plumbing × institutions</b>", ""]
    if p is None and i is None:
        return "\n".join(lines + ["Neither desk answered. Absence is not calm — "
                                  "check seiche.info and demo.liquilens.in directly."])
    if p is not None:
        lines.append(f"Plumbing (this desk): <b>{esc(gauge.get('regime'))}</b> "
                     f"{gauge.get('index')}/100 · Tell {gauge.get('tell')}")
    else:
        lines.append("Plumbing (this desk): did not answer — absence is not calm.")
    if i is not None:
        t = {v: k for k, v in INST_LEVEL.items()}[i]
        n_watch = sum(1 for r in board["rows"] if r.get("tier") != "green")
        lines.append(f"Institutions (LiquiLens): worst tier "
                     f"{TIER_ICON.get(t, '')} <b>{t.upper()}</b> · "
                     f"{n_watch} of {len(board['rows'])} on watch")
    else:
        lines.append("Institutions (LiquiLens): board did not answer.")
    lines.append("")
    if p is not None and i is not None:
        lines.append(_quadrant_verdict(p, i))
    lines.append("\n<i>Two desks, two public APIs, one read: seiche.info × "
                 "liquilens.in.</i>")
    return "\n".join(lines)


def _quadrant_verdict(p: int, i: int) -> str:
    """Shared quadrant language (kept in lockstep with the LiquiLens bot).
    The 🚨 word is reserved for RED institutions under stressed plumbing."""
    if p >= 2 and i >= 3:
        return ("🚨 <b>The dangerous quadrant.</b> Systemic funding stress while "
                "named institutions sit in the red tier — transmission is live. "
                "Historically this is when idiosyncratic trouble goes systemic; "
                "funding lines fail first.")
    if p >= 2 and i == 2:
        return ("⚠️ <b>One notch off the dangerous quadrant.</b> Funding stress "
                "with orange-tier institutions on the board. If any name turns "
                "red while plumbing stays stressed, transmission risk is live — "
                "watch those funding lenses first.")
    if p >= 2:
        return ("Plumbing-led stress; the institutions board is contained so "
                "far. The order to watch: new names appearing on the radar "
                "with funding flags.")
    if i >= 2:
        return ("Institution weakness inside calm plumbing — this configuration "
                "historically stays idiosyncratic. Watch the weak names' "
                "funding lenses, not the system.")
    return ("Both desks read contained. The quadrant to fear is stressed "
            "plumbing × weak institutions; today is not it.")


def _tandem_class(p: int, i: int) -> int:
    """3 = dangerous quadrant (stress × red), 2 = one notch off (stress ×
    orange), 1 = one desk stressed, 0 = contained."""
    if p >= 2 and i >= 3:
        return 3
    if p >= 2 and i == 2:
        return 2
    if p >= 2 or i >= 2:
        return 1
    return 0


HELP = (
    "🌊 <b>Seiche</b> — US funding-stress early warning, from free public data.\n\n"
    "/now — the gauge: regime, composite, the Tell\n"
    "/odds — forward event odds (Navigator)\n"
    "/turns — next calendar turn + crunch windows\n"
    "/analogs — the wreck ledger: past storms on this board\n"
    "/proof — the backtest scoreboard, misses included\n"
    "/letter — today's dispatch\n"
    "/institutions — the other desk: LiquiLens Failure Radar\n"
    "/tandem — cross-desk read: plumbing × institutions\n"
    "/ask &lt;question&gt; — desk assistant, grounded in the live board\n"
    "/start — subscribe to the daily letter (11:30 UTC, pre-US-open)\n"
    "/stop — unsubscribe\n\n"
    "Free public good: no paywall, no sign-in. Institutions are "
    "@LiquiLens_bot's desk."
)


# ----------------------------------------------------------------- letter ---
def fmt_daily_letter() -> str:
    today = date.today().strftime("%d %b %Y")
    gauge = api_get("/api/gauge")
    pub = api_get("/api/public")
    overview = api_get("/api/overview")
    lines = [f"🌊 <b>Seiche morning letter</b> — {today}", ""]
    if not gauge:
        lines.append("The board did not answer this morning. No number is shown "
                     f"rather than a stale one; the gauge is at {SITE}.")
        return "\n".join(lines)
    line = ((pub or {}).get("conclusion") or {}).get("line")
    lines.append(f"{_regime_icon(gauge.get('regime'))} "
                 + (esc(line) if line else
                    f"Regime <b>{esc(gauge.get('regime'))}</b>, composite "
                    f"{gauge.get('index')}/100."))
    nt = gauge.get("next_turn") or {}
    if nt.get("date"):
        lines.append(f"Next turn {esc(nt['date'])} "
                     f"({esc(nt.get('mode', '').replace('_', ' '))}): forecast "
                     f"{nt.get('forecast_bp')}bp, severity {nt.get('severity')}/5.")
    for w in (gauge.get("crunch_windows") or [])[:2]:
        lines.append(f"⚠ {esc(w.get('date'))}: {esc(w.get('reason'))}")
    nav = (overview or {}).get("navigator") or {}
    if nav.get("ok"):
        lines.append(f"Navigator: P(event, 5bd) {pct(nav.get('p_event_5bd'))}.")

    # the other desk — institutions, read verbatim
    board = ll_get("/failure-radar/board")
    i = _inst_level(board)
    if i is not None:
        t = {v: k for k, v in INST_LEVEL.items()}[i]
        n_watch = sum(1 for r in board["rows"] if r.get("tier") != "green")
        lines.append(f"\n🏦 <b>Institutions (LiquiLens):</b> worst tier "
                     f"{TIER_ICON.get(t, '')} {t.upper()} · {n_watch} of "
                     f"{len(board['rows'])} on watch · /institutions")
        p = _plumb_level(gauge.get("regime"))
        if p is not None:
            cls = _tandem_class(p, i)
            if cls == 3:
                lines.append("🚨 <b>Cross-desk: the dangerous quadrant</b> — "
                             "funding stress while institutions sit red. /tandem")
            elif cls == 2:
                lines.append("⚠️ Cross-desk: one notch off the dangerous quadrant "
                             "— funding stress with orange institutions. /tandem")
            elif cls == 1:
                lines.append("Cross-desk: one desk stressed, the other contained. "
                             "/tandem")
    else:
        lines.append("\n🏦 Institutions (LiquiLens): did not answer — absence "
                     "is not calm; demo.liquilens.in has the board.")

    idx = _get_json(f"{SITE}/dispatches/index.json")
    if isinstance(idx, list) and idx:
        d = idx[0]
        lines.append(f"\n✉️ Today's letter: <b>{esc(d.get('title'))}</b>\n"
                     f"{SITE}/dispatches/{urllib.parse.quote(d.get('slug', ''))}.md")
    return "\n".join(lines) + FOOT


# ------------------------------------------------------------------ wiring --
def handle(chat_id: int, text: str) -> None:
    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/start":
        subs = load_state("subscribers.json", {})
        subs[str(chat_id)] = {"since": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        save_state("subscribers.json", subs)
        send(chat_id, "Subscribed to the daily letter (11:30 UTC, pre-US-open).\n\n" + HELP)
        send(chat_id, fmt_now(api_get("/api/gauge"), api_get("/api/public")))
    elif cmd == "/stop":
        subs = load_state("subscribers.json", {})
        subs.pop(str(chat_id), None)
        save_state("subscribers.json", subs)
        send(chat_id, "Unsubscribed. /start any time.")
    elif cmd == "/now":
        send(chat_id, fmt_now(api_get("/api/gauge"), api_get("/api/public")))
    elif cmd == "/odds":
        send(chat_id, fmt_odds(api_get("/api/overview")))
    elif cmd == "/turns":
        send(chat_id, fmt_turns(api_get("/api/gauge"), api_get("/api/overview")))
    elif cmd == "/analogs":
        send(chat_id, fmt_analogs(api_get("/api/wrecks")))
    elif cmd == "/proof":
        send(chat_id, fmt_proof(api_get("/api/public")))
    elif cmd == "/letter":
        send(chat_id, fmt_letter(_get_json(f"{SITE}/dispatches/index.json")))
    elif cmd == "/institutions":
        send(chat_id, fmt_institutions(ll_get("/failure-radar/board")))
    elif cmd == "/tandem":
        send(chat_id, fmt_tandem(api_get("/api/gauge"), ll_get("/failure-radar/board")))
    elif cmd == "/ask":
        if not arg.strip():
            send(chat_id, "Usage: /ask <question> — e.g. /ask why is the regime STRAIN?")
        else:
            q = urllib.parse.quote(arg.strip()[:600])
            send(chat_id, fmt_ask(_get_json(f"{API}/api/ask?q={q}", timeout=60)))
    else:
        send(chat_id, HELP)


def poll_loop() -> None:
    offset = load_state("offset.json", 0)
    print(f"Seiche bot polling (api={API})")
    while True:
        res = tg_call("getUpdates", {"timeout": POLL_TIMEOUT, "offset": offset,
                                     "allowed_updates": ["message"]})
        if not res or not res.get("ok"):
            time.sleep(5)
            continue
        for u in res.get("result", []):
            offset = max(offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            text = msg.get("text")
            chat = (msg.get("chat") or {}).get("id")
            if text and chat:
                try:
                    handle(chat, text)
                except Exception as exc:   # one bad update must not kill the loop
                    print(f"handle failed: {exc}", file=sys.stderr)
        save_state("offset.json", offset)


def run_letter() -> None:
    subs = load_state("subscribers.json", {})
    text = fmt_daily_letter()
    if not subs:
        print("no subscribers yet; letter composed but not sent")
    for chat_id in subs:
        send(int(chat_id), text)
    print(f"letter sent to {len(subs)} subscriber(s)")


def run_tandem() -> None:
    """Cross-desk escalation check — mirror of the LiquiLens bot's --tandem.
    Sends ONLY when the joint quadrant changes class; silence otherwise."""
    gauge = api_get("/api/gauge")
    board = ll_get("/failure-radar/board")
    p, i = _plumb_level((gauge or {}).get("regime")), _inst_level(board)
    if p is None or i is None:
        print("tandem: a desk did not answer; no state change recorded")
        return
    cls = _tandem_class(p, i)
    prev = load_state("tandem_class.json", None)
    save_state("tandem_class.json", cls)
    if prev is None or cls == prev:
        print(f"tandem: class {cls} (unchanged); nothing sent")
        return
    if cls == 3:
        head = "🚨 <b>Cross-desk escalation: the dangerous quadrant.</b>"
    elif cls > prev:
        head = "⚠️ <b>Cross-desk escalation.</b>"
    else:
        head = "🟢 <b>Cross-desk de-escalation.</b>"
    text = head + "\n\n" + fmt_tandem(gauge, board)
    subs = load_state("subscribers.json", {})
    for chat_id in subs:
        send(int(chat_id), text)
    print(f"tandem: class {prev} → {cls}, alerted {len(subs)} subscriber(s)")


def run_setup() -> None:
    tg_call("setMyCommands", {"commands": [
        {"command": "now", "description": "The gauge: regime, composite, the Tell"},
        {"command": "odds", "description": "Forward event odds (Navigator)"},
        {"command": "turns", "description": "Next turn + crunch windows"},
        {"command": "tandem", "description": "Cross-desk read: plumbing × institutions"},
        {"command": "institutions", "description": "The LiquiLens Failure Radar"},
        {"command": "analogs", "description": "The wreck ledger: past storms"},
        {"command": "proof", "description": "The backtest scoreboard, misses included"},
        {"command": "letter", "description": "Today's dispatch"},
        {"command": "ask", "description": "Desk assistant: /ask why STRAIN?"},
        {"command": "start", "description": "Subscribe to the daily letter"},
        {"command": "stop", "description": "Unsubscribe"},
    ]})
    tg_call("setMyShortDescription", {
        "short_description": "US funding-stress early warning from free public "
                             "data. Free public good — seiche.info"})
    tg_call("setMyDescription", {
        "description": "The Seiche desk bot: dollar funding stress read from the "
                       "Fed's own public data (H.4.1, NY Fed ops, OFR repo, "
                       "Treasury cash) with a regime gauge, forward event odds, "
                       "calendar crunch windows and an honest backtest. Free "
                       "public good — no paywall, no sign-in. Institutions are "
                       "LiquiLens's desk (@LiquiLens_bot). seiche.info"})
    me = tg_call("getMe", {})
    print("setup done:", json.dumps((me or {}).get("result", {})))


if __name__ == "__main__":
    if not TOKEN:
        sys.exit("SEICHE_BOT_TOKEN not set")
    if "--letter" in sys.argv:
        run_letter()
    elif "--tandem" in sys.argv:
        run_tandem()
    elif "--setup" in sys.argv:
        run_setup()
    else:
        poll_loop()
