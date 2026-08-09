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
  --setup       register the bot name, command menu and descriptions with Telegram

Commands
  /start /stop     follow / unfollow the daily letter, alerts and desk news
  /now             the gauge right now: regime, composite, the Tell
  /snap            the forwardable card: meter, trend, next turn (monospace)
  /odds            forward event odds (Navigator, with its caveats out loud)
  /turns           the next calendar turn + crunch windows + auction desk
  /oil             Oil × Funding: spot, cash spreads, coupling, scenarios
  /estuary         FX/material pressure + holdout-tested Passage links
  /analogs         historical analogs from the wreck ledger
  /proof           historical evidence status, misses included
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

# The free Liquidity Lab channel (@LiquidityLabDesk). Empty = publishing off,
# which is how every offline test and every laptop run must behave: a timer
# that is only meant to DM subscribers should never accidentally publish.
LAB_CHANNEL = os.environ.get("LAB_CHANNEL_ID", "")
LAB_LINK = "https://t.me/LiquidityLabDesk"
BOT_USERNAME = "seiche_desk_bot"
BOT_URL = f"https://t.me/{BOT_USERNAME}"

POLL_TIMEOUT = 50

# The backend keys its /api/ask limiter on the client IP, and this bot reaches
# the backend over loopback, so every Telegram user shares one server-side
# bucket. These cap a single chat well under that shared ceiling. Nothing here
# gates who may ask: the desk stays free, only one chat's rate is bounded.
ASK_PER_CHAT_LIMIT = 6
ASK_PER_CHAT_WINDOW_S = 60

ASK_BUSY = object()

FOOT = ("\n<i>Free public good: no paywall, no sign-in. Every number is on "
        "the board at seiche.info with sources, served evidence status, and "
        "eligibility flags.</i>")


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
    except urllib.error.HTTPError as exc:
        # Telegram answers errors with a JSON body (error_code/description);
        # surface it so callers can react (403 blocked → prune, 400 → retry
        # without parse_mode) instead of losing the distinction.
        try:
            body = json.load(exc)
        except Exception:
            body = {"ok": False, "error_code": exc.code}
        print(f"tg {method} failed: {exc.code} "
              f"{body.get('description', '')}", file=sys.stderr)
        return body
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"tg {method} failed: {exc}", file=sys.stderr)
        return None


def send(chat_id: int, text: str, keyboard: list | None = None) -> dict | None:
    """Send, chunked at line seams (Telegram caps at 4096 and HTML must never
    be cut mid-tag). Returns the last Telegram reply so timer loops can spot
    blocked chats (error_code 403) and prune them."""
    res = None
    while text:
        cut = len(text)
        if cut > 4000:
            nl = text.rfind("\n", 1, 4000)
            cut = nl if nl > 0 else 4000
        chunk, text = text[:cut], text[cut:].lstrip("\n")
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if keyboard and not text:   # keyboard rides the last chunk
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        res = tg_call("sendMessage", payload)
        if isinstance(res, dict) and not res.get("ok") \
                and res.get("error_code") == 429:
            # rate-limited: honor retry_after (capped) and retry once
            wait = (res.get("parameters") or {}).get("retry_after") or 3
            time.sleep(min(float(wait), 30.0))
            res = tg_call("sendMessage", payload)
        if isinstance(res, dict) and not res.get("ok") \
                and res.get("error_code") == 400:
            # HTML Telegram refuses to parse: deliver the words anyway
            res = tg_call("sendMessage",
                          {k: v for k, v in payload.items() if k != "parse_mode"})
    return res


def _send_all(subs: dict, text: str, keyboard: list | None = None) -> int:
    """Deliver to every subscriber, prune chats that blocked the bot, and
    return how many deliveries actually succeeded."""
    gone, delivered = [], 0
    for chat_id in list(subs):
        res = send(int(chat_id), text, keyboard)
        if isinstance(res, dict) and res.get("ok"):
            delivered += 1
        elif isinstance(res, dict) and res.get("error_code") == 403:
            gone.append(chat_id)
        time.sleep(0.05)   # stay far under Telegram's broadcast ceiling
    if gone:
        fresh = load_state("subscribers.json", {})
        for chat_id in gone:
            fresh.pop(str(chat_id), None)
        save_state("subscribers.json", fresh)
        print(f"pruned {len(gone)} blocked subscriber(s)")
    return delivered


def post_channel(text: str, ref: str) -> bool:
    """Publish a read to the free Liquidity Lab channel.

    `ref` rides the desk deep link as `?start=<ref>`, so `record_lead` can
    attribute every arrival to the exact post type that earned it. That is the
    only way the channel's worth is measurable rather than believed.

    Never raises and never blocks the caller: publishing is strictly additive
    to the subscriber DMs, so a channel outage must not cost anyone their
    letter.
    """
    if not LAB_CHANNEL:
        return False
    desk_url = f"{BOT_URL}?start={urllib.parse.quote(ref, safe='')}"
    body = text + (
        f"\n\n<i>Seiche is the lab's free plumbing desk. Open it for the live "
        f"gauge, forward odds and historical diagnostic: {desk_url}</i>"
    )
    keyboard = [
        [{"text": "📈 Open the Seiche desk",
          "url": desk_url}],
        [{"text": "🏦 Bank failure radar",
          "url": f"https://t.me/LiquiLens_bot?start={ref}"},
         {"text": "🌊 Market depth",
          "url": f"https://t.me/undertow_LiquiLens_bot?start={ref}"}],
    ]
    try:
        res = send(int(LAB_CHANNEL), body, keyboard)
    except Exception as exc:                      # noqa: BLE001 - see docstring
        print(f"channel post failed: {exc}", file=sys.stderr)
        return False
    ok = isinstance(res, dict) and res.get("ok")
    if not ok:
        print(f"channel post rejected: {res}", file=sys.stderr)
    return bool(ok)


def _get_json(url: str, timeout: int = 25,
              tries: int = 2) -> dict | list | object | None:
    # Explicit User-Agent: some edges 403 Python's default one.
    req = urllib.request.Request(url, headers={"User-Agent": "seiche-bot"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            # HTTPError subclasses URLError, so this arm has to come first or
            # the one below swallows the status code. A 429 is the backend's
            # shared limiter refusing, not an outage, and retrying it spends
            # the bucket the 429 exists to protect.
            if exc.code == 429:
                return ASK_BUSY
            if attempt == tries - 1:
                print(f"GET {url} failed: {exc.code}", file=sys.stderr)
            else:
                time.sleep(1.5)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == tries - 1:
                print(f"GET {url} failed: {exc}", file=sys.stderr)
            else:
                time.sleep(1.5)
    return None


def board_get(url: str):
    """Any board read. Only /ask can act on the ASK_BUSY sentinel, so every
    other caller gets the plain "did not answer" None its formatter expects."""
    res = _get_json(url)
    return None if res is ASK_BUSY else res


def api_get(path: str):
    return board_get(f"{API}{path}")


def ask_desk(question: str):
    """Ask the desk assistant, keeping a 429 distinguishable from an outage.

    Returns the parsed answer, ASK_BUSY when the backend limiter refused, or
    None on a real failure. tries=1 because a retry on 429 spends the shared
    bucket the 429 is protecting.
    """
    url = f"{API}/api/ask?q={urllib.parse.quote(question[:600])}"
    return _get_json(url, timeout=60, tries=1)


def ll_get(path: str):
    """The other desk: LiquiLens's public institutions API, read verbatim."""
    return board_get(f"{LL_API}{path}")


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


def _persist_ask_rate(kept) -> None:
    """Write the window back, but never let a disk fault swallow a reply.

    load_state already treats an unreadable file as empty, but save_state
    raises OSError, and ask_quota runs BEFORE the answer is sent, inside
    poll_loop's broad per-update except. Unguarded, an unwritable state dir
    turned "the disk is full" into "the desk is silently ignoring you" on this
    bot's busiest path, which is the fleet's second silent-death shape.

    A rate limiter is not worth a dropped answer. On a write fault the throttle
    degrades to allowing the call and says so loudly in the journal: fail
    visible, never fail closed.
    """
    try:
        save_state("ask_rate.json", kept)
    except OSError as exc:
        print(f"ask_quota: cannot persist the rate window ({exc}); "
              f"serving this call unthrottled", file=sys.stderr)


def ask_quota(chat_id: int, now: float | None = None) -> tuple[bool, int]:
    """Per-chat sliding window over /ask. Returns (allowed, retry_after_s).

    Records the hit only when it is allowed, so a refused chat does not push
    its own window forward and lock itself out indefinitely.
    """
    now = time.time() if now is None else now
    state = load_state("ask_rate.json", {})
    if not isinstance(state, dict):
        state = {}
    kept = {}
    for chat, stamps in state.items():
        if not isinstance(stamps, list):
            continue
        live = [t for t in stamps
                if isinstance(t, (int, float)) and 0 <= now - t < ASK_PER_CHAT_WINDOW_S]
        if live:
            kept[chat] = live
    key = str(chat_id)
    hits = kept.get(key, [])
    if len(hits) >= ASK_PER_CHAT_LIMIT:
        retry = max(1, int(ASK_PER_CHAT_WINDOW_S - (now - min(hits))) + 1)
        _persist_ask_rate(kept)
        return False, retry
    hits.append(now)
    kept[key] = hits
    _persist_ask_rate(kept)
    return True, 0


def fmt_ask_throttled(retry_after: int) -> str:
    return (f"You have sent {ASK_PER_CHAT_LIMIT} questions in the last minute, "
            f"which is this chat's own limit. Try again in {retry_after}s.\n\n"
            "This is a per-user pace limit, not an outage: the desk is up and "
            "the board is live. It exists so one chat cannot take the "
            "assistant away from everyone else.\n\n"
            "/now, /odds, /turns, /oil, /estuary, /analogs and /proof are not "
            "rate limited and "
            "answer straight from the board." + FOOT)


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def pct(x, digits: int = 0) -> str:
    try:
        return f"{float(x) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def num(x, digits: int = 1, *, signed: bool = False,
        prefix: str = "", suffix: str = "") -> str:
    """Format a served number without ever leaking ``None`` into a reply."""
    try:
        value = float(x)
    except (TypeError, ValueError):
        return "n/a"
    body = f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"
    return f"{prefix}{body}{suffix}"


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
        bits = [f"next turn {nt['date']}"]
        if nt.get("forecast_bp") is not None:
            bits.append(f"{nt.get('forecast_bp')}bp")
        if nt.get("severity") is not None:
            bits.append(f"severity {nt.get('severity')}/5")
        d.text((60, y), " · ".join(bits), font=f_m, fill=dim)
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
    d.text((CARD_W - 60, CARD_H - 52), "construction-PIT diagnostic · misses included",
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
    idx = gauge.get("index")
    head = (f"Regime: <b>{esc(gauge.get('regime'))}</b> · composite "
            f"<b>{'?' if idx is None else idx}</b>/100")
    if gauge.get("coverage_pct") is not None:
        head += f" · coverage {gauge.get('coverage_pct')}%"
    lines = [f"{_regime_icon(gauge.get('regime'))} <b>US funding stress, right now</b>",
             "",
             head]
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
            f"{meter(idx)}  {'?' if idx is None else idx}/100  {esc(regime)}"]
    trend = gauge_spark()
    if trend:
        rows.append(f"{trend}  30d")
    tell = gauge.get("tell")
    if isinstance(tell, (int, float)):
        rows.append(f"tell {tell:+.0f}  "
                    + ("plumbing leads price" if tell > 0 else "price leads plumbing"))
    nt = gauge.get("next_turn") or {}
    if nt.get("date"):
        bits = [f"next turn {esc(nt['date'])}"]
        if nt.get("forecast_bp") is not None:
            bits.append(f"{nt['forecast_bp']}bp")
        if nt.get("severity") is not None:
            bits.append(f"sev {nt['severity']}/5")
        rows.append("  ".join(bits))
    for w in (gauge.get("crunch_windows") or [])[:1]:
        rows.append(f"crunch {esc(w.get('date'))}")
    proof = (pub or {}).get("proof") or {}
    if proof.get("n_events"):
        evidence = _proof_evidence(pub)
        rows.append(f"historical recall {pct(proof.get('recall'))} over "
                    f"{proof.get('n_events')} events")
        rows.append(f"status {evidence.get('status')} · "
                    f"validated eligible {_eligibility(evidence.get('validated_backtest_eligible'))}")
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


def fmt_oil(payload: dict | None) -> str:
    """Oil × Funding, with observations and scenario claims kept separate."""
    if not payload:
        return "The Oil × Funding desk did not answer — absence is not calm."
    if payload.get("ok") is False:
        return ("The Oil × Funding desk has no current reading: "
                f"{esc(payload.get('reason', 'unknown reason'))}.")

    oil = payload.get("oil") or {}
    wti = oil.get("wti") or {}
    brent = oil.get("brent") or {}
    funding = payload.get("funding") or {}
    cp_nf = funding.get("cp_nonfinancial") or {}
    cp_fin = funding.get("cp_financial") or {}
    sofr = funding.get("sofr_iorb") or {}
    lines = [f"🛢 <b>Oil × Funding</b> — as of {esc(payload.get('as_of', '?'))}", ""]

    if wti.get("price_usd_per_bbl") is not None:
        lines.append(
            f"WTI <b>{num(wti.get('price_usd_per_bbl'), 2, prefix='$')}</b>/bbl"
            f" · 5d {num(wti.get('change_5d_usd'), 2, signed=True, prefix='$')}"
            f" · 20d {num(wti.get('change_20d_pct'), 1, signed=True, suffix='%')}"
        )
    if brent.get("price_usd_per_bbl") is not None:
        lines.append(
            f"Brent <b>{num(brent.get('price_usd_per_bbl'), 2, prefix='$')}</b>/bbl"
            f" · 5d {num(brent.get('change_5d_usd'), 2, signed=True, prefix='$')}"
        )

    lines.extend([
        "\n<b>Funding already priced</b>",
        f"Nonfinancial CP−bill: <b>{num(cp_nf.get('spread_bp'), 1, suffix='bp')}</b>"
        f" · 20d {num(cp_nf.get('change_20d_bp'), 1, signed=True, suffix='bp')}",
        f"Financial CP−bill: {num(cp_fin.get('spread_bp'), 1, suffix='bp')}"
        f" · 20d {num(cp_fin.get('change_20d_bp'), 1, signed=True, suffix='bp')}",
        f"SOFR−IORB: {num(sofr.get('spread_bp'), 1, suffix='bp')}"
        f" · 20d {num(sofr.get('change_20d_bp'), 1, signed=True, suffix='bp')}",
    ])

    fit = ((payload.get("coupling") or {}).get("fit") or {})
    if fit.get("n"):
        lines.append(
            "\n5d WTI Δ vs nonfinancial CP-spread Δ: "
            f"r <b>{num(fit.get('correlation'), 2, signed=True)}</b> · "
            f"slope {num(fit.get('slope_bp_per_usd'), 2, signed=True, suffix='bp/$')} "
            f"(n={fit.get('n')})."
        )
    inr = ((payload.get("india") or {}).get("inr") or {})
    if inr.get("per_usd") is not None:
        lines.append(
            f"USD/INR {num(inr.get('per_usd'), 2)} · "
            f"20d {num(inr.get('change_20d_pct'), 1, signed=True, suffix='%')}."
        )

    ballast = payload.get("ballast") or {}
    if ballast.get("ok"):
        headline = ballast.get("headline") or {}
        dominant = headline.get("dominant_channel") or {}
        lines.append(
            "\n⚓ <b>Ballast futures-cash ledger</b> · "
            f"{esc(headline.get('state', 'CANNOT ASSESS'))} · "
            f"worst commodity p{num(headline.get('worst_channel_percentile'), 1)}"
        )
        if dominant.get("label"):
            lines.append(f"Dominant: {esc(dominant.get('label'))}.")
        for contract in (ballast.get("contracts") or [])[:2]:
            cash = contract.get("cash_transfer_scale") or {}
            gross = cash.get("gross_mark_displacement_usd")
            gross_b = float(gross) / 1e9 if gross is not None else None
            pos = contract.get("positioning") or {}
            lines.append(
                f"{esc(contract.get('key', '?'))}: "
                f"<b>{num(gross_b, 2, prefix='$', suffix='bn')}</b> gross proxy"
                f" · top-4 paying side {num(pos.get('top4_paying_side_pct'), 1, suffix='%')}"
            )
    elif ballast:
        lines.append(
            "\n⚓ Ballast unavailable: "
            f"{esc(ballast.get('reason', 'insufficient aligned public history'))}."
        )

    structure = payload.get("market_structure") or {}
    if structure.get("ok"):
        cushing = structure.get("cushing") or {}
        cushing_live = cushing.get("live") or {}
        lines.append("\n🏗 <b>Oil market structure</b>")
        if cushing_live.get("stocks_m_bbl") is not None:
            lines.append(
                f"Cushing <b>{num(cushing_live.get('stocks_m_bbl'), 1, suffix='m bbl')}</b>"
                f" · {num(cushing_live.get('fill_of_last_working_capacity_pct'), 1, suffix='%')} "
                f"of last working capacity ({esc(cushing.get('capacity_asof', '?'))})"
                f" · {num(cushing_live.get('buffer_to_20m_reference_m_bbl'), 1, signed=True, suffix='m')} "
                "vs 20m reference."
            )
        spread = structure.get("brent_wti_spread") or {}
        if spread.get("brent_minus_wti_usd_per_bbl") is not None:
            lines.append(
                "Brent−WTI "
                f"<b>{num(spread.get('brent_minus_wti_usd_per_bbl'), 2, signed=True)} "
                "USD/bbl</b>"
                f" · 5-observation average "
                f"{num(spread.get('average_5d_usd_per_bbl'), 2, signed=True)}."
            )
        benchmarks = structure.get("benchmark_architecture") or []
        benchmark_bits = [
            f"{esc(row.get('benchmark', '?'))}: {esc(row.get('settlement', '?'))}"
            for row in benchmarks[:2]
            if isinstance(row, dict)
        ]
        if benchmark_bits:
            lines.append(" · ".join(benchmark_bits) + ".")
        chokepoints = structure.get("chokepoints") or {}
        hormuz = next(
            (
                row for row in (chokepoints.get("rows") or [])
                if isinstance(row, dict) and row.get("name") == "Strait of Hormuz"
            ),
            None,
        )
        if hormuz and hormuz.get("q1_2026_mbd") is not None:
            lines.append(
                f"Strait of Hormuz {num(hormuz.get('q1_2026_mbd'), 1, suffix='mbd')} "
                f"(EIA {esc(chokepoints.get('latest_period', 'dated'))} reference; not live)."
            )
        india_structure = structure.get("india") or {}
        if india_structure.get("crude_import_dependence_pct") is not None:
            lines.append(
                "India crude import dependence "
                f"{num(india_structure.get('crude_import_dependence_pct'), 1, suffix='%')}."
            )
    elif structure:
        lines.append(
            "\n🏗 Oil market structure unavailable: "
            f"{esc(structure.get('reason', 'not present in this snapshot'))}."
        )

    lines.append(
        "\n<i>Context only: the correlation is associational; Ballast is a gross "
        "spot-proxy scale, not an observed margin call; Cushing capacity and "
        "chokepoint flows are dated references, not live constraints; cargo, "
        "margin and India outputs are editable scenarios. Nothing here enters "
        "the Seiche stress composite.</i>"
    )
    return "\n".join(lines) + FOOT


def fmt_estuary(payload: dict | None) -> str:
    """The Estuary headline plus the Passage's untouched-holdout verdict."""
    if not payload:
        return "The Estuary did not answer — absence is not calm."
    if payload.get("ok") is False:
        return ("The Estuary has no current reading: "
                f"{esc(payload.get('reason', 'unknown reason'))}.")

    head = payload.get("headline") or {}
    lines = [f"🌐 <b>The Estuary · FX × materials</b> — "
             f"as of {esc(payload.get('as_of', '?'))}", "",
             f"Regime: <b>{esc(head.get('regime', '?'))}</b>",
             f"Upstream pressure <b>{num(head.get('upstream_pressure'), 1)}</b>/100 "
             f"vs funding priced <b>{num(head.get('funding_priced'), 1)}</b>/100 "
             f"→ Passage gap <b>{num(head.get('transmission_gap'), 1, signed=True)}</b>",
             f"FX {num(head.get('fx_pressure'), 1)} · materials "
             f"{num(head.get('materials_pressure'), 1)} · coverage "
             f"{num(head.get('coverage_pct'), 1, suffix='%')}"]
    if head.get("verdict"):
        lines.append(f"\n{esc(head['verdict'])}")

    leaders = payload.get("leaders") or {}
    fx = next(iter(leaders.get("fx") or []), {})
    material = next(iter(leaders.get("materials") or []), {})
    if fx or material:
        bits = []
        if fx:
            bits.append(f"FX: {esc(fx.get('label') or fx.get('key') or '?')} "
                        f"({num(fx.get('pressure'), 0)})")
        if material:
            bits.append(f"physical: {esc(material.get('label') or material.get('key') or '?')} "
                        f"({num(material.get('pressure'), 0)})")
        lines.append("Loudest upstream rows · " + " · ".join(bits))

    passage = payload.get("passage") or {}
    lines.append(
        "\n<b>The Passage holdout ledger</b>: "
        f"{passage.get('earned', 0)} earned · "
        f"{passage.get('tentative', 0)} tentative · "
        f"{passage.get('not_earned', 0)} not earned"
    )
    earned = next((edge for edge in (passage.get("edges") or [])
                   if edge.get("status") == "earned"), None)
    if earned:
        lines.append(
            f"Earned: {esc(earned.get('source'))} → {esc(earned.get('target'))} "
            f"at {earned.get('lag_bd', '?')}bd · holdout r "
            f"{num(earned.get('corr_holdout'), 2, signed=True)}."
        )
    lines.append(
        "\n<i>Context only: targets and lags are selected on the first 60% "
        "of history and must survive the untouched final 40%. Earned does not "
        "mean causal; the gap never enters the Seiche composite.</i>"
    )
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


def _proof_evidence(pub: dict | None) -> dict:
    proof = (pub or {}).get("proof") or {}
    evidence = proof.get("historical_evidence") or (
        (pub or {}).get("historical_evidence")
    )
    if isinstance(evidence, dict):
        return evidence
    return {
        "status": "FINAL_VINTAGE_CONSTRUCTION_PIT",
        "validated_backtest_eligible": False,
        "real_money_eligible": False,
        "reason": "the public API did not serve a verified as-published data cut",
    }


def _eligibility(value) -> str:
    return "YES" if value is True else "NO"


def fmt_proof(pub: dict | None) -> str:
    proof = (pub or {}).get("proof") or {}
    if not proof.get("n_events"):
        return "The proof scoreboard did not answer — try again shortly."
    evidence = _proof_evidence(pub)
    ci = proof.get("recall_ci95") or [None, None]
    ml = proof.get("median_lead_d")
    lines = ["📜 <b>The PROOF historical diagnostic</b> — misses included", "",
             f"Status: <code>{esc(evidence.get('status'))}</code>",
             "Validated-backtest eligible: "
             f"<b>{_eligibility(evidence.get('validated_backtest_eligible'))}</b> · "
             "real-money eligible: "
             f"<b>{_eligibility(evidence.get('real_money_eligible'))}</b>", "",
             f"Recall: <b>{pct(proof.get('recall'))}</b> "
             f"(95% CI {pct(ci[0])}–{pct(ci[1])}) over {proof.get('n_events')} events",
             f"Precision (runs): {pct(proof.get('precision_runs'))} · "
             f"base rate {pct(proof.get('base_rate'), 1)}",
             (f"Median lead: {ml:.0f} days" if isinstance(ml, (int, float))
              else "Median lead: n/a (no hit leads on record)")]
    if evidence.get("reason"):
        lines.append(f"\n<i>Boundary: {esc(evidence.get('reason'))}</i>")
    lines.append("\nEvery episode with its verdict — hits AND misses — is on "
                 f"the board: {SITE}/#proof. The served status and flags are "
                 "authoritative for what this record can support.")
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
    if res is ASK_BUSY:
        return ("The desk assistant is at its shared answer limit right now, "
                "so this question was dropped, not queued: nothing is holding "
                "it and no reply is coming. This is a pace limit, not an "
                "outage, and the board itself is live. Send the question "
                "again in a minute, or use /now for the current gauge."
                + FOOT)
    if not res:
        return "The desk assistant did not answer. Try again shortly."
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
    # unknown tiers read as WORST, mirroring _plumb_level — schema drift must
    # never degrade the cross-desk read toward "contained"
    return max(INST_LEVEL.get(r.get("tier"), 3) for r in rows)


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
        # the other desk's schema is not ours to trust: degrade, never vanish
        pd12 = (r.get("hazard") or {}).get("pd_12m")
        pd_s = f"{pd12 * 100:.2f}%" if isinstance(pd12, (int, float)) else "n/a"
        lines.append(f"{TIER_ICON.get(r.get('tier'), '·')} "
                     f"{esc(r.get('name') or r.get('slug') or '?')} — "
                     f"12m failure PD {pd_s}")
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
    "/oil — Oil × Funding: spot, cash spreads, coupling, scenarios\n"
    "/estuary — FX/material pressure + holdout-tested Passage\n"
    "/analogs — the wreck ledger: past storms on this board\n"
    "/proof — historical evidence status, flags and misses\n"
    "/letter — today's dispatch\n"
    "/institutions — the other desk: LiquiLens Failure Radar\n"
    "/tandem — cross-desk read: plumbing × institutions\n"
    "/ask &lt;question&gt; — desk assistant, grounded in the live board\n"
    "/start — daily letter (11:30 UTC) + relevant alerts/news\n"
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
    flags = (((overview or {}).get("engines") or {}).get("scuttlebutt")
             or {}).get("flags") or []
    if flags:
        lines.append(f"🗞 {esc(flags[0])} (press attention; display only, "
                     "feeds no score).")

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

    idx = board_get(f"{SITE}/dispatches/index.json")
    if isinstance(idx, list) and idx:
        d = idx[0]
        lines.append(f"\n✉️ Today's letter: <b>{esc(d.get('title'))}</b>\n"
                     f"{SITE}/dispatches/{urllib.parse.quote(d.get('slug', ''))}.md")
    return "\n".join(lines) + FOOT


# ------------------------------------------------------------------ wiring --


# ------------------------------------------------- share, fleet, keyboards

SHARE_TEXT = ("Free US funding stress early warning, straight from the Fed's "
              "own public data. Regime gauge, forward odds, and a historical "
              "diagnostic that publishes misses and eligibility flags. No "
              "paywall, no sign in.")
SHARE_URL = ("https://t.me/share/url?url=" + BOT_URL + "?start=ref_shared"
             + "&text=" + urllib.parse.quote(SHARE_TEXT))

FLEET_ROW = [
    {"text": "\U0001f3e6 Institutions desk", "url": "https://t.me/LiquiLens_bot"},
    {"text": "\U0001f30a Markets desk", "url": "https://t.me/undertow_LiquiLens_bot"},
]
# The free channel is the fleet's top of funnel: a reader who joins it keeps
# receiving the lab long after this one conversation scrolls away.
LAB_ROW = [{"text": "\U0001f4e1 Liquidity Lab channel", "url": LAB_LINK}]


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def keyboard_for(cmd: str) -> list | None:
    """Inline keyboard rows per command. A button tap IS a command."""
    if cmd == "/start":
        return [[_btn("🌡 Full gauge", "/now"),
                 _btn("📨 Today's letter", "/letter")],
                [{"text": "📡 Liquidity Lab channel", "url": LAB_LINK},
                 {"text": "📤 Share Seiche", "url": SHARE_URL}]]
    if cmd == "/now":
        return [[_btn("\U0001f4c9 Odds", "/odds"), _btn("\U0001f504 Turns", "/turns"),
                 _btn("\U0001f9fe Proof", "/proof")],
                [_btn("🛢 Oil × Funding", "/oil"),
                 _btn("🌐 FX × Materials", "/estuary")],
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
    if cmd in ("/oil", "/estuary"):
        other = ("\U0001f30d FX × Materials", "/estuary") \
            if cmd == "/oil" else ("\U0001f6e2 Oil × Funding", "/oil")
        return [[_btn(other[0], other[1]), _btn("\U0001f321 Gauge now", "/now")],
                [_btn("\U0001f4e4 Share", "/share")], FLEET_ROW]
    if cmd == "/share":
        return [[{"text": "\U0001f4e4 Share Seiche", "url": SHARE_URL}],
                LAB_ROW, FLEET_ROW]
    return None


def fmt_share(gauge: dict | None) -> str:
    line = ""
    if gauge and gauge.get("regime"):
        line = (f"\nRight now the gauge reads <b>{esc(gauge['regime'])}</b> "
                f"at {gauge.get('index', '?')}/100.")
    return ("<b>Know someone who watches money markets?</b>\n\n"
            "Forward this desk to them. Free early warning on dollar funding "
            "stress, built from the Fed's own published data, with diagnostic "
            f"misses and eligibility flags on the record.{line}\n\nTap Share below, or "
            f"send them {BOT_URL}")


def fmt_welcome(gauge: dict | None, pub: dict | None) -> str:
    """One-screen onboarding: promise, delivery contract and served gauge.

    The full command catalogue deliberately stays in HELP. A first response
    should establish why the desk is useful and what following it will send,
    while the live artifact proves that the promise is already operational.
    """
    lines = [
        "🌊 <b>Seiche | US funding stress</b>",
        "Early warning for strain in dollar funding, built from public Fed, "
        "NY Fed, OFR and Treasury records.",
        "",
        "<b>Following this desk:</b> one daily letter at 11:30 UTC, plus "
        "relevant funding-state alerts and sourced desk news when they occur.",
        "",
    ]
    if gauge:
        idx = gauge.get("index")
        current = (f"{_regime_icon(gauge.get('regime'))} <b>Live gauge:</b> "
                   f"{esc(gauge.get('regime'))} · "
                   f"{'?' if idx is None else idx}/100")
        if gauge.get("generated_at"):
            current += f" · as of {esc(gauge.get('generated_at'))}"
        lines.append(current)
        conclusion = ((pub or {}).get("conclusion") or {}).get("line")
        if conclusion:
            lines.append(esc(conclusion))
        else:
            tell = gauge.get("tell")
            if isinstance(tell, (int, float)):
                lines.append(f"The Tell: {tell:+.0f}.")
    else:
        lines.append("<b>Live gauge:</b> the board did not answer. Absence is "
                     f"not calm; check {SITE} directly.")
    lines.extend([
        "",
        "/help opens the full desk · /stop unsubscribes at any time.",
        "",
        "<i>Public data, timestamped where available. Research context only — "
        "not investment advice or an execution instruction.</i>",
    ])
    return "\n".join(lines)


def record_lead(chat_id: int, ref: str) -> None:
    path = _state_path("leads.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "chat_id": chat_id, "ref": ref},
                            sort_keys=True) + "\n")


def handle(chat_id: int, text: str, chat_type: str = "private") -> None:
    raw_cmd, _, arg = text.strip().partition(" ")
    cmd, _, suffix = raw_cmd.partition("@")
    cmd = cmd.lower()
    if suffix and suffix.lower() != BOT_USERNAME:
        return   # addressed to another bot in a shared group — not ours
    # A plain question in a private chat IS /ask — the desk answers,
    # grounded in the live board. Groups keep command-only discipline:
    # non-command group chatter gets silence, never a help wall.
    if not cmd.startswith("/"):
        if chat_type == "private" and text.strip():
            cmd, arg = "/ask", text.strip()
        else:
            return
    if cmd == "/start":
        subs = load_state("subscribers.json", {})
        subs[str(chat_id)] = {"since": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        save_state("subscribers.json", subs)
        # t.me/seiche_desk_bot?start=ref_x arrives as "/start ref_x".
        # A group is not a lead. Seiche is free, so a room subscribing to the
        # letter is fine and stays, but booking it as an arrival would credit
        # one person's ref with a whole channel and inflate the only number
        # that decides what the desks publish more of. Leads are people.
        if arg.strip() and chat_type == "private":
            record_lead(chat_id, arg.strip()[:64])
        send(chat_id,
             fmt_welcome(api_get("/api/gauge"), api_get("/api/public")),
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
        png = None
        try:
            png = render_snap_card(gauge)
        except Exception as exc:   # a card render falls to text, never silence
            print(f"snap render failed: {exc}", file=sys.stderr)
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
    elif cmd == "/oil":
        send(chat_id, fmt_oil(api_get("/api/oil-funding")), keyboard_for("/oil"))
    elif cmd == "/estuary":
        send(chat_id, fmt_estuary(api_get("/api/estuary")),
             keyboard_for("/estuary"))
    elif cmd == "/analogs":
        send(chat_id, fmt_analogs(api_get("/api/wrecks")), keyboard_for("/analogs"))
    elif cmd == "/proof":
        send(chat_id, fmt_proof(api_get("/api/public")), keyboard_for("/proof"))
    elif cmd == "/letter":
        send(chat_id, fmt_letter(board_get(f"{SITE}/dispatches/index.json")),
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
            allowed, retry_after = ask_quota(chat_id)
            if not allowed:
                send(chat_id, fmt_ask_throttled(retry_after),
                     keyboard_for("/ask"))
            else:
                send(chat_id, fmt_ask(ask_desk(arg.strip())),
                     keyboard_for("/ask"))
    elif cmd == "/share":
        record_lead(chat_id, "share-open")
        send(chat_id, fmt_share(api_get("/api/gauge")), keyboard_for("/share"))
    else:
        send(chat_id, HELP)


_INLINE_TTL_S = 60
_inline_cache: dict = {"ts": 0.0, "data": None}


def _inline_payload() -> tuple:
    """Inline queries fire on every keystroke; one board fetch per minute is
    plenty. (gauge, pub, overview), cached in-process."""
    now = time.time()
    if _inline_cache["data"] is not None and now - _inline_cache["ts"] < _INLINE_TTL_S:
        return _inline_cache["data"]
    gauge = api_get("/api/gauge")
    gauge_history_append(gauge)
    data = (gauge, api_get("/api/public"), api_get("/api/overview"))
    _inline_cache.update(ts=now, data=data)
    return data


def answer_inline(iq: dict) -> None:
    """Inline mode: @seiche_desk_bot in any chat drops a live card there.
    The article list is the desk's shareable surfaces; the query filters by
    title. (Enable inline mode for the bot in BotFather once.)"""
    gauge, pub, overview = _inline_payload()
    regime = esc((gauge or {}).get("regime", "?"))
    idx = (gauge or {}).get("index", "?")
    cards = [
        ("snap", f"Gauge card — {regime} {idx}/100",
         "The forwardable monospace card", fmt_snap(gauge, pub)),
        ("now", f"Gauge now — {regime} {idx}/100",
         "Regime, composite, the Tell", fmt_now(gauge, pub)),
        ("odds", "Forward event odds",
         "Navigator: P(event, 5bd) with caveats", fmt_odds(overview)),
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
    fails = 0
    while True:
        res = tg_call("getUpdates", {"timeout": POLL_TIMEOUT, "offset": offset,
                                     "allowed_updates": ["message",
                                                         "callback_query",
                                                         "inline_query"]})
        if not res or not res.get("ok"):
            code = (res or {}).get("error_code")
            if code == 401:
                # a revoked token never comes back — die loudly so systemd's
                # restart limit surfaces a FAILED unit instead of a zombie
                sys.exit("Telegram rejected the token (401) — "
                         "check SEICHE_BOT_TOKEN")
            if code == 409:
                print("another getUpdates consumer is live (409); "
                      "backing off 60s", file=sys.stderr)
                time.sleep(60)
                continue
            fails = min(fails + 1, 7)
            time.sleep(min(5 * 2 ** (fails - 1), 300))
            continue
        fails = 0
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
    # Publish BEFORE the subscriber check. The channel is the top of the
    # funnel, so it has to work at zero subscribers; gating it behind a
    # non-empty subscriber list would silence it exactly when it matters most.
    published = post_channel(text, "lab_letter")
    if not subs:
        print(f"no subscribers yet; letter published to channel={published}")
        return
    n = _send_all(subs, text)
    print(f"letter sent to {n} subscriber(s), published to channel={published}")


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
    published = post_channel(text, "lab_tandem")
    n = _send_all(load_state("subscribers.json", {}), text)
    print(f"tandem: class {prev} → {cls}, alerted {n} subscriber(s), "
          f"published to channel={published}")


ALERT_JUMP_PTS = 8          # composite move (points) worth an intraday ping
ALERT_COOLDOWN_S = 2 * 3600  # min gap between non-escalating pings
ALERT_DWELL_SCANS = 2        # scans a de-escalation must hold before it pings


def _alert_decision(state: dict, gauge: dict, now_ts: float) -> tuple[list[str], dict]:
    """Pure: what to ping, given the prior alert state and the live gauge.

    Escalations (regime level rising) ping immediately. De-escalations and
    re-crossings must HOLD for ALERT_DWELL_SCANS consecutive scans and clear
    a 2h cooldown, so a composite hovering at a regime boundary can never
    turn the 30-min timer into spam. Flips are judged against the last
    reading actually ANNOUNCED, not the last scan — oscillation A→B→A→B
    therefore dedupes to at most one ping per side per cooldown.

    State: seen {regime,index} last scan (jump baseline) · alerted
    {regime,index,ts} last announced · pending {regime,n} dwell counter."""
    regime, index = gauge.get("regime"), gauge.get("index")
    alerted = state.get("alerted") or {}
    seen = state.get("seen") or {}
    pending = state.get("pending") or {}
    new_state = {"seen": {"regime": regime, "index": index},
                 "alerted": alerted, "pending": {}}
    if not alerted:
        new_state["alerted"] = {"regime": regime, "index": index, "ts": now_ts}
        return [], new_state

    lines: list[str] = []
    cooled = now_ts - float(alerted.get("ts") or 0) >= ALERT_COOLDOWN_S
    if regime is not None and regime != alerted.get("regime"):
        n = pending.get("n", 0) + 1 if pending.get("regime") == regime else 1
        escalating = ((_plumb_level(regime) or 0)
                      > (_plumb_level(alerted.get("regime")) or 0))
        if escalating or (n >= ALERT_DWELL_SCANS and cooled):
            lines.append(f"{_regime_icon(regime)} Regime flip: "
                         f"<b>{esc(alerted.get('regime'))} → {esc(regime)}</b> "
                         f"(composite {index}/100)")
        else:
            new_state["pending"] = {"regime": regime, "n": n}
    # the jump detector only speaks when the regime is stable — a move that
    # comes WITH a flip is the flip's story (held to the same dwell rules)
    if not lines and regime == alerted.get("regime"):
        try:
            jump = float(index) - float(seen.get("index"))
            drift = float(index) - float(alerted.get("index"))
        except (TypeError, ValueError):
            jump = drift = 0.0
        if abs(jump) >= ALERT_JUMP_PTS and abs(drift) >= ALERT_JUMP_PTS and cooled:
            lines.append(f"⚡ Composite moved <b>{jump:+.0f} points</b> since "
                         f"the last scan, to {index}/100 ({esc(regime)})")
    return lines, new_state


def run_alert_scan() -> None:
    """Between-letter flip detector (systemd timer, ~30min). Decision logic
    in _alert_decision (hysteresis: dwell + cooldown, escalations immediate);
    the announced-state marker advances only after delivery succeeds, so a
    network blip retries next scan instead of swallowing the alert. Also
    accrues the daily gauge history the sparklines read."""
    gauge = api_get("/api/gauge")
    if not gauge or gauge.get("index") is None:
        print("alert-scan: gauge did not answer; no state change recorded")
        return
    gauge_history_append(gauge)
    state = load_state("alert_state.json", {})
    if state and "seen" not in state:   # migrate the flat pre-hysteresis format
        state = {"seen": {k: state.get(k) for k in ("regime", "index")},
                 "alerted": {"regime": state.get("regime"),
                             "index": state.get("index"), "ts": 0.0},
                 "pending": {}}
    lines, new_state = _alert_decision(state, gauge, time.time())
    if not lines:
        save_state("alert_state.json", new_state)
        print("alert-scan: no changes")
        return
    text = "🌊 <b>Seiche alert</b>\n\n" + "\n".join(lines) + \
           "\n\n/now for the full gauge · /turns for what's on the calendar"
    published = post_channel(text, "lab_alert")
    subs = load_state("subscribers.json", {})
    delivered = _send_all(subs, text, keyboard_for("/now")) if subs else 0
    if delivered or not subs:
        new_state["alerted"] = {"regime": gauge.get("regime"),
                                "index": gauge.get("index"), "ts": time.time()}
        new_state["pending"] = {}
    save_state("alert_state.json", new_state)
    print(f"alert-scan: {len(lines)} change(s), alerted {delivered} "
          f"subscriber(s), published to channel={published}")


BOT_DISPLAY_NAME = "Seiche | US Funding Stress"
BOT_SHORT_DESCRIPTION = (
    "US funding-stress gauge, daily letter and relevant alerts from public "
    "data. Research context only."
)
BOT_DESCRIPTION = (
    "US dollar-funding stress from public Fed, NY Fed, OFR and Treasury "
    "records. Live gauge, daily 11:30 UTC letter, relevant state-change alerts "
    "and sourced desk news. Research context only; not investment advice or "
    "an execution instruction. Free, no sign-in: seiche.info"
)
BOT_COMMANDS = [
    {"command": "now", "description": "The gauge: regime, composite, the Tell"},
    {"command": "snap", "description": "The forwardable gauge card"},
    {"command": "odds", "description": "Forward event odds (Navigator)"},
    {"command": "turns", "description": "Next turn + crunch windows"},
    {"command": "oil", "description": "Oil × Funding transmission context"},
    {"command": "estuary", "description": "FX/material pressure + Passage"},
    {"command": "tandem", "description": "Cross-desk read: plumbing × institutions"},
    {"command": "institutions", "description": "The LiquiLens Failure Radar"},
    {"command": "analogs", "description": "The wreck ledger: past storms"},
    {"command": "proof", "description": "Evidence status, flags and misses"},
    {"command": "letter", "description": "Today's dispatch"},
    {"command": "ask", "description": "Desk assistant: /ask why STRAIN?"},
    {"command": "share", "description": "Send this free desk to someone"},
    {"command": "start", "description": "Follow daily letter + relevant alerts/news"},
    {"command": "stop", "description": "Unsubscribe"},
]


class TelegramSetupError(RuntimeError):
    """Telegram rejected or failed to acknowledge bot profile setup."""


def _telegram_text_units(value: str) -> int:
    """Bot API text limits are measured in UTF-16 code units."""
    return len(value.encode("utf-16-le")) // 2


def _validate_setup_metadata() -> None:
    fields = (
        ("name", BOT_DISPLAY_NAME, 1, 64),
        ("short_description", BOT_SHORT_DESCRIPTION, 0, 120),
        ("description", BOT_DESCRIPTION, 0, 512),
    )
    for label, value, minimum, maximum in fields:
        units = _telegram_text_units(value)
        if not minimum <= units <= maximum:
            raise ValueError(
                f"Telegram {label} must be {minimum}..{maximum} UTF-16 units; "
                f"got {units}"
            )
    if not 1 <= len(BOT_COMMANDS) <= 100:
        raise ValueError("Telegram command menu must contain 1..100 commands")
    for entry in BOT_COMMANDS:
        command = entry["command"]
        description = entry["description"]
        if (not 1 <= len(command) <= 32
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_"
                       for c in command)):
            raise ValueError(f"invalid Telegram command: {command!r}")
        units = _telegram_text_units(description)
        if not 1 <= units <= 256:
            raise ValueError(
                f"Telegram /{command} description must be 1..256 UTF-16 units; "
                f"got {units}"
            )


def _checked_setup_call(method: str, payload: dict) -> dict:
    response = tg_call(method, payload)
    if not isinstance(response, dict) or response.get("ok") is not True:
        code = response.get("error_code") if isinstance(response, dict) else None
        detail = response.get("description") if isinstance(response, dict) else None
        raise TelegramSetupError(
            f"Telegram {method} failed"
            f" ({code if code is not None else 'no response'}): "
            f"{detail or 'no description'}"
        )
    result = response.get("result")
    if method == "getMe":
        if not isinstance(result, dict):
            raise TelegramSetupError("Telegram getMe returned no bot profile")
    elif result is not True:
        raise TelegramSetupError(
            f"Telegram {method} returned an unexpected success payload"
        )
    return response


def run_setup() -> None:
    _validate_setup_metadata()
    _checked_setup_call("setMyName", {"name": BOT_DISPLAY_NAME})
    _checked_setup_call("setMyCommands", {"commands": BOT_COMMANDS})
    _checked_setup_call(
        "setMyShortDescription",
        {"short_description": BOT_SHORT_DESCRIPTION},
    )
    _checked_setup_call("setMyDescription", {"description": BOT_DESCRIPTION})
    me = _checked_setup_call("getMe", {})
    print("setup done:", json.dumps(me["result"]))


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
