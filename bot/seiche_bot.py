#!/usr/bin/env python3
"""Seiche Telegram bot — the US money-market plumbing desk in your chat.

Division of labor across the fleet: this bot watches the PLUMBING (dollar
funding stress: reserves, repo, the Fed's balance sheet); LiquiLens watches
the INSTITUTIONS. It serves the same public API the terminal runs on
(api.seiche.info) and never computes numbers of its own — every reply is
traceable to a served, sourced reading. Seiche is a free public good: no
paywall, no sign-in, voluntary support only.

Modes
  (no args)     long-poll command loop (systemd service)
  --letter      compose and send the daily letter to all subscribers
                (systemd timer, 11:30 UTC = pre-US-open)
  --tandem      cross-desk check (plumbing × institutions): message subscribers
                ONLY when the joint quadrant changes class (systemd timer, 6h)
  --alert-scan  between-letter flip detector (systemd timer, ~30min): pings
                subscribers when the regime flips or the composite jumps;
                silence when nothing moved. Also accrues the bot's own daily
                gauge history (the sparkline record).
  --setup       register the command menu and bot description with Telegram

Commands
  /start /stop     subscribe / unsubscribe from the daily letter
  /now             the gauge right now: regime, composite, the Tell
  /snap            the forwardable card: meter, trend, next turn (monospace)
  /odds            forward event odds (Navigator, with its caveats out loud)
  /turns           the next calendar turn + crunch windows + auction desk
  /analogs         historical analogs from the wreck ledger
  /proof           the backtest scoreboard, misses included
  /letter          today's dispatch: title, summary, link
  /institutions    the other desk: LiquiLens Failure Radar summary
  /tandem          the cross-desk read: plumbing × institutions quadrant
  /ask <question>  desk assistant, grounded strictly in the live board
  /help            this list

Any plain text in a private chat (no slash) is treated as a question for
/ask — the desk answers, grounded in the live board. Inline mode: type
@seiche_desk_bot in ANY chat to drop the live gauge card there (enable
inline mode for the bot in BotFather once).

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


def send(chat_id: int, text: str, keyboard: list | None = None) -> None:
    while text:
        chunk, text = text[:4000], text[4000:]
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if keyboard and not text:   # keyboard rides the last chunk
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        tg_call("sendMessage", payload)


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


# ------------------------------------------------------- history + sparks ---
SPARK_CHARS = "▁▂▃▄▅▆▇█"


def spark(values: list) -> str:
    """Unicode sparkline. Empty/one-point series -> ''. Pure."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return SPARK_CHARS[3] * len(vals)
    return "".join(SPARK_CHARS[round((v - lo) / (hi - lo) * 7)] for v in vals)


def gauge_history_append(gauge: dict | None) -> None:
    """Accrue the bot's own daily as-seen gauge record (the Undertow
    pattern): one {index, regime, tell} per UTC day, capped at 120 days.
    The bot never computes a number — this is the served gauge, replayed."""
    if not gauge or gauge.get("index") is None:
        return
    hist = load_state("gauge_history.json", {})
    day = datetime.now(timezone.utc).date().isoformat()
    hist[day] = {"index": gauge.get("index"), "regime": gauge.get("regime"),
                 "tell": gauge.get("tell")}
    for k in sorted(hist)[:-120]:
        hist.pop(k, None)
    save_state("gauge_history.json", hist)


def gauge_spark(days: int = 30) -> str:
    hist = load_state("gauge_history.json", {})
    keys = sorted(hist)[-days:]
    return spark([(hist[k] or {}).get("index") for k in keys])


def meter(x, width: int = 20) -> str:
    """A 0..100 reading as a monospace bar. Pure."""
    try:
        filled = max(0, min(width, round(float(x) / 100 * width)))
    except (TypeError, ValueError):
        return "?" * width
    return "█" * filled + "░" * (width - filled)


# ------------------------------------------------------------- image card --
# /snap ships as a rendered card when Pillow is present (the box installs
# python3-pil); the monospace text card remains the universal fallback, so
# the stdlib-only deployment story survives — Pillow only ever adds.

CARD_W, CARD_H = 1200, 628
REGIME_RGB = {"CALM": (124, 205, 180), "EROSION": (221, 179, 118),
              "STRAIN": (229, 154, 122), "STRESS": (239, 128, 120)}


def _card_font(size: int):
    from PIL import ImageFont
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
              "/System/Library/Fonts/Monaco.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_snap_card(gauge: dict | None) -> bytes | None:
    """The abyss card: standing blurple waves on black whose chop scales
    with the live composite, the reading painted on top, the bot's accrued
    30-day history as a glowing polyline. None without Pillow/data."""
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        return None
    import io
    import math
    if not gauge or gauge.get("index") is None:
        return None
    idx = float(gauge["index"])
    regime = str(gauge.get("regime") or "?")
    stress = max(0.0, min(1.0, idx / 100))
    rc = REGIME_RGB.get(regime, (156, 143, 232))

    img = Image.new("RGB", (CARD_W, CARD_H))
    px = img.load()
    for y in range(CARD_H):
        t = y / (CARD_H - 1)
        row = (round(10 * t), round(11 * t), round(5 + 21 * t))
        for x in range(CARD_W):
            px[x, y] = row
    img = img.convert("RGBA")

    # the basin: wave amplitude and frequency rise with the composite
    for k, col in enumerate(((69, 60, 114), (128, 113, 204), (156, 143, 232))):
        amp = (12 + 9 * k) * (0.6 + stress)
        yb = int(CARD_H * 0.86) - k * 30
        pts = [(x, yb + amp * math.sin(x / CARD_W * math.tau
                                       * (1.2 + 0.4 * k + stress) + k * 1.3))
               for x in range(0, CARD_W + 6, 6)]
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).line(pts, fill=col + (80,), width=10)
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6)))
        ImageDraw.Draw(img).line(pts, fill=col + (230,), width=3)

    d = ImageDraw.Draw(img)
    ink, dim, faint = (237, 238, 244), (154, 160, 182), (120, 127, 149)
    accent = (156, 143, 232)
    f_h, f_big = _card_font(30), _card_font(124)
    f_m, f_s = _card_font(26), _card_font(21)

    d.text((60, 46), "SEICHE", font=f_h, fill=accent)
    d.text((228, 46), "· US FUNDING STRESS", font=f_h, fill=dim)
    d.text((CARD_W - 60, 50), str(gauge.get("generated_at", ""))[:10],
           font=f_m, fill=faint, anchor="ra")

    d.text((56, 120), f"{idx:.0f}", font=f_big, fill=ink)
    bx = 70 + d.textlength(f"{idx:.0f}", font=f_big)
    # regime chip: translucent tint composited (ImageDraw alone would stamp
    # the alpha instead of blending), label in the regime colour on top
    tw = int(d.textlength(regime, font=f_h))
    chip_box = (bx + 24, 196, bx + 24 + tw + 44, 250)
    chip = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(chip).rounded_rectangle(chip_box, radius=12,
                                           fill=rc + (38,),
                                           outline=rc + (255,), width=2)
    img.alpha_composite(chip)
    d = ImageDraw.Draw(img)
    d.text((chip_box[0] + 22, chip_box[1] + 10), regime, font=f_h, fill=rc)

    # meter
    mw = 460
    d.rounded_rectangle((60, 292, 60 + mw, 306), radius=7, fill=(30, 32, 48, 255))
    d.rounded_rectangle((60, 292, 60 + int(mw * stress), 306), radius=7,
                        fill=rc + (255,))
    tell = gauge.get("tell")
    y = 336
    if isinstance(tell, (int, float)):
        lead = "plumbing leads price" if tell > 0 else "price leads plumbing"
        d.text((60, y), f"tell {tell:+.0f} · {lead}", font=f_m, fill=dim)
        y += 40
    nt = gauge.get("next_turn") or {}
    if nt.get("date"):
        d.text((60, y), f"next turn {nt['date']} · {nt.get('forecast_bp')}bp "
                        f"· severity {nt.get('severity')}/5", font=f_m, fill=dim)
        y += 40
    for w in (gauge.get("crunch_windows") or [])[:1]:
        d.text((60, y), f"crunch {w.get('date')}: "
                        f"{str(w.get('reason', ''))[:52]}", font=f_s, fill=faint)

    # the 30-day history as a glowing polyline (the bot's own accrued record)
    hist = load_state("gauge_history.json", {})
    vals = [(hist[k] or {}).get("index") for k in sorted(hist)[-30:]]
    vals = [v for v in vals if isinstance(v, (int, float))]
    if len(vals) >= 2:
        x0, x1, y0, y1 = 720, CARD_W - 60, 200, 330
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1
        pts = [(x0 + i * (x1 - x0) / (len(vals) - 1),
                y1 - (v - lo) / span * (y1 - y0)) for i, v in enumerate(vals)]
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(glow).line(pts, fill=accent + (90,), width=9)
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(5)))
        d = ImageDraw.Draw(img)
        d.line(pts, fill=accent + (255,), width=3)
        d.text((x0, y1 + 16), f"composite · last {len(vals)} days",
               font=f_s, fill=faint)

    d.text((60, CARD_H - 52), "free public good · seiche.info",
           font=f_s, fill=faint)
    d.text((CARD_W - 60, CARD_H - 52), "honest backtest, misses included",
           font=f_s, fill=faint, anchor="ra")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def send_photo(chat_id: int, png: bytes, caption: str,
               keyboard: list | None = None) -> bool:
    """sendPhoto via stdlib multipart. False on failure — callers fall
    back to the text card."""
    boundary = "----seichecard" + os.urandom(12).hex()
    fields = {"chat_id": str(chat_id), "caption": caption[:1024],
              "parse_mode": "HTML"}
    if keyboard:
        fields["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{k}"\r\n\r\n{v}\r\n').encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; "
             f'name="photo"; filename="seiche.png"\r\n'
             "Content-Type: image/png\r\n\r\n").encode() + png + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{TG}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return bool(json.load(r).get("ok"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"sendPhoto failed: {exc}", file=sys.stderr)
        return False


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
    trend = gauge_spark()
    if trend:
        lines.append(f"30d composite: <code>{trend}</code>")
    nt = gauge.get("next_turn") or {}
    if nt.get("date"):
        lines.append(f"\nNext turn: <b>{esc(nt['date'])}</b> "
                     f"({esc(nt.get('mode', '').replace('_', ' '))}) — forecast "
                     f"{nt.get('forecast_bp')}bp, severity {nt.get('severity')}/5")
    for w in (gauge.get("crunch_windows") or [])[:2]:
        lines.append(f"⚠ {esc(w.get('date'))}: {esc(w.get('reason'))}")
    return "\n".join(lines) + FOOT


def fmt_snap(gauge: dict | None, pub: dict | None) -> str:
    """The forwardable card: the whole desk in one monospace block that
    survives any chat theme. Pure over the served gauge (+ the bot's own
    accrued daily history for the trend row)."""
    if not gauge:
        return ("The board did not answer — absence is not calm; the gauge "
                f"is at {SITE}.")
    idx = gauge.get("index")
    regime = str(gauge.get("regime") or "?")
    rows = [f"SEICHE  US funding stress   {esc(gauge.get('generated_at', '')[:10])}",
            "",
            f"{meter(idx)}  {idx}/100  {regime}"]
    trend = gauge_spark()
    if trend:
        rows.append(f"{trend}  30d")
    tell = gauge.get("tell")
    if isinstance(tell, (int, float)):
        rows.append(f"tell {tell:+.0f}  "
                    + ("plumbing leads price" if tell > 0 else "price leads plumbing"))
    nt = gauge.get("next_turn") or {}
    if nt.get("date"):
        rows.append(f"next turn {esc(nt['date'])}  {nt.get('forecast_bp')}bp  "
                    f"sev {nt.get('severity')}/5")
    for w in (gauge.get("crunch_windows") or [])[:1]:
        rows.append(f"crunch {esc(w.get('date'))}")
    proof = (pub or {}).get("proof") or {}
    if proof.get("n_events"):
        rows.append(f"backtest recall {pct(proof.get('recall'))} over "
                    f"{proof.get('n_events')} events")
    body = "<pre>" + "\n".join(rows) + "</pre>"
    return (f"{_regime_icon(regime)} {body}\n"
            f"Free public good — {SITE} · forward this card to a desk that "
            "watches money markets.")


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
    "/snap — the forwardable card (meter, trend, next turn)\n"
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
    "Or just type a question — no slash needed; the desk answers, grounded "
    "in the live board. Type @seiche_desk_bot in any other chat to drop the "
    "live gauge card there.\n\n"
    "Free public good: no paywall, no sign-in. Institutions are "
    "@LiquiLens_bot's desk."
)


# ----------------------------------------------------------------- letter ---
def fmt_daily_letter() -> str:
    today = date.today().strftime("%d %b %Y")
    gauge = api_get("/api/gauge")
    gauge_history_append(gauge)   # the letter is the sparkline's daily heartbeat
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


# ------------------------------------------------- share, fleet, keyboards

BOT_URL = "https://t.me/seiche_desk_bot"
SHARE_TEXT = ("Free US funding stress early warning, straight from the Fed's "
              "own public data. Regime gauge, forward odds, and a backtest "
              "that publishes its misses. No paywall, no sign in.")
SHARE_URL = ("https://t.me/share/url?url=" + BOT_URL + "?start=ref_shared"
             + "&text=" + urllib.parse.quote(SHARE_TEXT))

FLEET_ROW = [
    {"text": "\U0001f3e6 Institutions desk", "url": "https://t.me/LiquiLens_bot"},
    {"text": "\U0001f30a Markets desk", "url": "https://t.me/undertow_LiquiLens_bot"},
]


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def keyboard_for(cmd: str) -> list | None:
    """Inline keyboard rows per command. A button tap IS a command."""
    if cmd in ("/start", "/now"):
        return [[_btn("\U0001f4c9 Odds", "/odds"), _btn("\U0001f504 Turns", "/turns"),
                 _btn("\U0001f9fe Proof", "/proof")],
                [_btn("\U0001f5bc Card", "/snap"),
                 _btn("\U0001f4e8 Letter", "/letter"),
                 _btn("\U0001f4e4 Share", "/share")],
                FLEET_ROW]
    if cmd == "/snap":
        return [[{"text": "\U0001f4e4 Share Seiche", "url": SHARE_URL},
                 _btn("\U0001f321 Gauge now", "/now")], FLEET_ROW]
    if cmd in ("/odds", "/turns", "/analogs", "/proof", "/letter",
               "/institutions", "/tandem", "/ask"):
        return [[_btn("\U0001f321 Gauge now", "/now"),
                 _btn("\U0001f5bc Card", "/snap"),
                 _btn("\U0001f4e4 Share", "/share")], FLEET_ROW]
    if cmd == "/share":
        return [[{"text": "\U0001f4e4 Share Seiche", "url": SHARE_URL}], FLEET_ROW]
    return None


def fmt_share(gauge: dict | None) -> str:
    line = ""
    if gauge and gauge.get("regime"):
        line = (f"\nRight now the gauge reads <b>{esc(gauge['regime'])}</b> "
                f"at {gauge.get('index', '?')}/100.")
    return ("<b>Know someone who watches money markets?</b>\n\n"
            "Forward this desk to them. Free early warning on dollar funding "
            "stress, built from the Fed's own published data, with the "
            f"backtest misses on the record.{line}\n\nTap Share below, or "
            f"send them {BOT_URL}")


def record_lead(chat_id: int, ref: str) -> None:
    path = _state_path("leads.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "chat_id": chat_id, "ref": ref},
                            sort_keys=True) + "\n")


def handle(chat_id: int, text: str, chat_type: str = "private") -> None:
    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.split("@")[0].lower()
    # A plain question in a private chat IS /ask — the desk answers,
    # grounded in the live board. Groups keep command-only discipline.
    if not cmd.startswith("/") and chat_type == "private" and text.strip():
        cmd, arg = "/ask", text.strip()
    if cmd == "/start":
        subs = load_state("subscribers.json", {})
        subs[str(chat_id)] = {"since": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        save_state("subscribers.json", subs)
        if arg.strip():   # t.me/seiche_desk_bot?start=ref_x arrives as "/start ref_x"
            record_lead(chat_id, arg.strip()[:64])
        send(chat_id, "Subscribed to the daily letter (11:30 UTC, pre-US-open).\n\n" + HELP)
        send(chat_id, fmt_now(api_get("/api/gauge"), api_get("/api/public")),
             keyboard_for("/start"))
    elif cmd == "/stop":
        subs = load_state("subscribers.json", {})
        subs.pop(str(chat_id), None)
        save_state("subscribers.json", subs)
        send(chat_id, "Unsubscribed. /start any time.")
    elif cmd == "/now":
        gauge = api_get("/api/gauge")
        gauge_history_append(gauge)
        send(chat_id, fmt_now(gauge, api_get("/api/public")),
             keyboard_for("/now"))
    elif cmd == "/snap":
        gauge = api_get("/api/gauge")
        gauge_history_append(gauge)
        png = render_snap_card(gauge)
        caption = ""
        if gauge:
            caption = (f"{_regime_icon(gauge.get('regime'))} "
                       f"<b>{esc(gauge.get('regime'))}</b> "
                       f"{gauge.get('index')}/100 · free public data · "
                       "seiche.info")
        if not (png and send_photo(chat_id, png, caption, keyboard_for("/snap"))):
            send(chat_id, fmt_snap(gauge, api_get("/api/public")),
                 keyboard_for("/snap"))
    elif cmd == "/odds":
        send(chat_id, fmt_odds(api_get("/api/overview")), keyboard_for("/odds"))
    elif cmd == "/turns":
        send(chat_id, fmt_turns(api_get("/api/gauge"), api_get("/api/overview")),
             keyboard_for("/turns"))
    elif cmd == "/analogs":
        send(chat_id, fmt_analogs(api_get("/api/wrecks")), keyboard_for("/analogs"))
    elif cmd == "/proof":
        send(chat_id, fmt_proof(api_get("/api/public")), keyboard_for("/proof"))
    elif cmd == "/letter":
        send(chat_id, fmt_letter(_get_json(f"{SITE}/dispatches/index.json")),
             keyboard_for("/letter"))
    elif cmd == "/institutions":
        send(chat_id, fmt_institutions(ll_get("/failure-radar/board")), keyboard_for("/institutions"))
    elif cmd == "/tandem":
        send(chat_id, fmt_tandem(api_get("/api/gauge"), ll_get("/failure-radar/board")),
             keyboard_for("/tandem"))
    elif cmd == "/ask":
        if not arg.strip():
            send(chat_id, "Usage: /ask <question> — e.g. /ask why is the regime "
                          "STRAIN? (Or just type your question, no slash.)")
        else:
            q = urllib.parse.quote(arg.strip()[:600])
            send(chat_id, fmt_ask(_get_json(f"{API}/api/ask?q={q}", timeout=60)),
                 keyboard_for("/ask"))
    elif cmd == "/share":
        record_lead(chat_id, "share-open")
        send(chat_id, fmt_share(api_get("/api/gauge")), keyboard_for("/share"))
    else:
        send(chat_id, HELP)


def answer_inline(iq: dict) -> None:
    """Inline mode: @seiche_desk_bot in any chat drops a live card there.
    The article list is the desk's shareable surfaces; the query filters by
    title. (Enable inline mode for the bot in BotFather once.)"""
    gauge = api_get("/api/gauge")
    gauge_history_append(gauge)
    pub = api_get("/api/public")
    regime = esc((gauge or {}).get("regime", "?"))
    idx = (gauge or {}).get("index", "?")
    cards = [
        ("snap", f"Gauge card — {regime} {idx}/100",
         "The forwardable monospace card", fmt_snap(gauge, pub)),
        ("now", f"Gauge now — {regime} {idx}/100",
         "Regime, composite, the Tell", fmt_now(gauge, pub)),
        ("odds", "Forward event odds",
         "Navigator: P(event, 5bd) with caveats", fmt_odds(api_get("/api/overview"))),
        ("proof", "The PROOF scoreboard",
         "The backtest, misses included", fmt_proof(pub)),
    ]
    q = (iq.get("query") or "").strip().lower()
    results = []
    for rid, title, desc, body in cards:
        if q and q not in title.lower() and q not in rid:
            continue
        results.append({
            "type": "article", "id": rid, "title": title, "description": desc,
            "input_message_content": {
                "message_text": body[:4000], "parse_mode": "HTML",
                "disable_web_page_preview": True},
        })
    tg_call("answerInlineQuery", {
        "inline_query_id": iq["id"], "results": results or [],
        "cache_time": 120, "is_personal": False})


def poll_loop() -> None:
    offset = load_state("offset.json", 0)
    print(f"Seiche bot polling (api={API})")
    while True:
        res = tg_call("getUpdates", {"timeout": POLL_TIMEOUT, "offset": offset,
                                     "allowed_updates": ["message",
                                                         "callback_query",
                                                         "inline_query"]})
        if not res or not res.get("ok"):
            time.sleep(5)
            continue
        for u in res.get("result", []):
            offset = max(offset, u["update_id"] + 1)
            iq = u.get("inline_query")
            if iq:
                try:
                    answer_inline(iq)
                except Exception as exc:   # one bad update must not kill the loop
                    print(f"inline failed: {exc}", file=sys.stderr)
                continue
            cb = u.get("callback_query")
            if cb:
                # a button tap IS a command: same handler path
                tg_call("answerCallbackQuery", {"callback_query_id": cb["id"]})
                msg = {"text": cb.get("data") or "",
                       "chat": (cb.get("message") or {}).get("chat") or {}}
            else:
                msg = u.get("message") or {}
            text = msg.get("text")
            chat_o = msg.get("chat") or {}
            text_type = chat_o.get("type") or "private"
            chat = chat_o.get("id")
            if text and chat:
                try:
                    handle(chat, text, text_type)
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


ALERT_JUMP_PTS = 8   # composite move (points) worth an intraday ping


def run_alert_scan() -> None:
    """Between-letter flip detector (systemd timer, ~30min). Pings
    subscribers when the REGIME flips or the composite jumps ≥8 points
    since the last scan; silence otherwise, so the timer never becomes
    noise. Also accrues the daily gauge history the sparklines read."""
    gauge = api_get("/api/gauge")
    if not gauge or gauge.get("index") is None:
        print("alert-scan: gauge did not answer; no state change recorded")
        return
    gauge_history_append(gauge)
    new = {"regime": gauge.get("regime"), "index": gauge.get("index")}
    old = load_state("alert_state.json", None)
    save_state("alert_state.json", new)
    if old is None:
        print("alert-scan: state seeded")
        return
    lines = []
    if old.get("regime") and new["regime"] != old["regime"]:
        lines.append(f"{_regime_icon(new['regime'])} Regime flip: "
                     f"<b>{esc(old['regime'])} → {esc(new['regime'])}</b> "
                     f"(composite {new['index']}/100)")
    try:
        jump = float(new["index"]) - float(old.get("index"))
    except (TypeError, ValueError):
        jump = 0.0
    if abs(jump) >= ALERT_JUMP_PTS and not lines:
        lines.append(f"⚡ Composite moved <b>{jump:+.0f} points</b> since the "
                     f"last scan, to {new['index']}/100 ({esc(new['regime'])})")
    if not lines:
        print("alert-scan: no changes")
        return
    text = "🌊 <b>Seiche alert</b>\n\n" + "\n".join(lines) + \
           "\n\n/now for the full gauge · /turns for what's on the calendar"
    subs = load_state("subscribers.json", {})
    for chat_id in subs:
        send(int(chat_id), text, keyboard_for("/now"))
    print(f"alert-scan: {len(lines)} change(s), alerted {len(subs)} subscriber(s)")


def run_setup() -> None:
    tg_call("setMyCommands", {"commands": [
        {"command": "now", "description": "The gauge: regime, composite, the Tell"},
        {"command": "snap", "description": "The forwardable gauge card"},
        {"command": "odds", "description": "Forward event odds (Navigator)"},
        {"command": "turns", "description": "Next turn + crunch windows"},
        {"command": "tandem", "description": "Cross-desk read: plumbing × institutions"},
        {"command": "institutions", "description": "The LiquiLens Failure Radar"},
        {"command": "analogs", "description": "The wreck ledger: past storms"},
        {"command": "proof", "description": "The backtest scoreboard, misses included"},
        {"command": "letter", "description": "Today's dispatch"},
        {"command": "ask", "description": "Desk assistant: /ask why STRAIN?"},
        {"command": "share", "description": "Send this free desk to someone"},
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
                       "calendar crunch windows and an honest backtest. Type any "
                       "question and the desk answers, grounded in the live "
                       "board; type @seiche_desk_bot in any chat to drop the "
                       "live gauge card there. Free public good — no paywall, "
                       "no sign-in. Institutions are LiquiLens's desk "
                       "(@LiquiLens_bot). seiche.info"})
    me = tg_call("getMe", {})
    print("setup done:", json.dumps((me or {}).get("result", {})))


if __name__ == "__main__":
    if not TOKEN:
        sys.exit("SEICHE_BOT_TOKEN not set")
    if "--letter" in sys.argv:
        run_letter()
    elif "--tandem" in sys.argv:
        run_tandem()
    elif "--alert-scan" in sys.argv:
        run_alert_scan()
    elif "--setup" in sys.argv:
        run_setup()
    else:
        poll_loop()
