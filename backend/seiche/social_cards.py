"""Build contextual 1200 x 630 social cards from Seiche's sealed snapshot.

Link unfurlers never receive a URL fragment and do not execute the React app.
Consequently ``/#money%20markets`` and ``/#board`` can only ever inherit the
home page's metadata.  This publisher gives shareable evidence a real path:

    /views/board/composite/
    /views/world-markets/forex/
    /views/money-markets/US-USD/
    /views/series/sofr-pct/

The module runs after the Vite build.  It reads only the already exported
``data/overview.json`` and the checked-in money-market coverage receipt, emits
content-addressed PNGs, writes small no-JavaScript evidence pages, and upgrades
the metadata on the root, market, article and dispatch pages.  It never calls a
collector, opens the canonical store, fits a model, or makes a network request.

Pillow is a hash-locked static-build tool, not a dependency of Seiche's signed
Python package identity.  The renderer uses Pillow's embedded font rather than
an operating-system font, making the output independent of the runner's font
inventory.  The image URL is derived from the rendered bytes, so an asset URL
can never name different content after a renderer upgrade.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image, ImageDraw, ImageFont

SITE = "https://seiche.info"
WIDTH = 1200
HEIGHT = 630
SAFE_GUTTER = 72
REPO_ROOT = Path(__file__).resolve().parents[2]

# Kept local so importing the renderer does not import pandas/scikit-learn via
# the world-markets projection. ``_world_spec`` imports the pure projector only
# when a complete site build actually asks for those views.
_WORLD_SELECTORS = (
    "summary",
    "money_markets",
    "forex",
    "capital_markets",
    "china_macro",
    "sources",
    "methodology",
    "all",
)

_WORLD_LABELS = {
    "summary": "World-markets evidence atlas",
    "money_markets": "Money markets across clearing systems",
    "forex": "Foreign-exchange reference evidence",
    "capital_markets": "Capital-market transmission",
    "china_macro": "China macro evidence boundary",
    "sources": "World-markets source register",
    "methodology": "World-markets method",
    "all": "World markets, one evidence contract",
}

_HEADLINE_LABELS = {
    "sofr_pct": ("SOFR", "%"),
    "iorb_pct": ("IORB", "%"),
    "sofr_iorb_bp": ("SOFR - IORB", "bp"),
    "reserves_b": ("Reserve balances", "$B"),
    "tga_b": ("Treasury General Account", "$B"),
    "rrp_b": ("Overnight reverse repo", "$B"),
    "vix": ("VIX", "index"),
    "hy_oas_pct": ("High-yield OAS", "%"),
}

_STATUS_COLORS = {
    "observed": (111, 226, 185),
    "live": (111, 226, 185),
    "available": (111, 226, 185),
    "derived": (187, 175, 254),
    "structural": (126, 184, 255),
    "restricted": (255, 194, 112),
    "stale": (255, 201, 136),
    "unavailable": (255, 130, 143),
    "fault": (255, 130, 143),
}

# Finite tab-level fallbacks for generic card/Chart share affordances. More
# specific routes (headline series, registered money markets and world-market
# selectors) remain attached deeper in the DOM and therefore win. Every fact
# below is read from overview.json; paths that are absent stay absent.
_TAB_DEFINITIONS: Mapping[str, Mapping[str, Any]] = {
    "today": {
        "title": "Today's dollar-funding argument",
        "description": "The current desk thesis, countercase, evidence and next dates that can change the call.",
        "primary": ("engines.composite.value", "number"),
        "unit": "funding-stress index · 0–100",
        "status": "engines.composite.regime",
        "availability": ("engines.composite", "deep.tell"),
        "metrics": (
            ("regime", "engines.composite.regime", "text"),
            ("the Tell", "deep.tell.tell", "number"),
            ("coverage", "engines.composite.coverage_pct", "percent"),
        ),
        "clocks": ("deep.tell.asof",),
    },
    "dispatches": {
        "title": "The Seiche dispatch record",
        "description": "Dated arguments from the funding desk; each issue keeps its original clock, regime and countercase.",
        "primary": ("generated_at", "date"),
        "unit": "latest sealed archive build",
        "literal_status": "PUBLISHED ARCHIVE",
        "availability": ("engines.composite",),
        "metrics": (
            ("board regime", "engines.composite.regime", "text"),
            ("snapshot version", "version", "text"),
        ),
        "clocks": ("generated_at",),
    },
    "fx-materials": {
        "title": "FX and physical cash pressure",
        "description": "Dollar regimes and physical-market pressure traced into funding spreads with separate clocks and holdout boundaries.",
        "primary": ("engines.estuary.headline.transmission_gap", "number"),
        "unit": "upstream minus funding · points",
        "status": "engines.estuary.headline.regime",
        "availability": ("engines.estuary",),
        "metrics": (
            (
                "upstream pressure",
                "engines.estuary.headline.upstream_pressure",
                "number",
            ),
            ("funding priced", "engines.estuary.headline.funding_priced", "number"),
            ("coverage", "engines.estuary.headline.coverage_pct", "percent"),
        ),
        "clocks": ("engines.estuary.asof",),
    },
    "oil-funding": {
        "title": "The barrel's funding loop",
        "description": "Oil prices, the price of cash, market structure and mechanical working-capital scenarios kept in their native units.",
        "primary": ("engines.oilfunding.live.wti.price_usd_per_bbl", "usd"),
        "unit": "WTI · USD per barrel",
        "literal_status": "OBSERVED + MECHANICAL SCENARIO",
        "availability": ("engines.oilfunding",),
        "metrics": (
            ("Brent", "engines.oilfunding.live.brent.price_usd_per_bbl", "usd"),
            ("SOFR − IORB", "engines.oilfunding.live.sofr_iorb.spread_bp", "bp"),
            (
                "Cushing stocks",
                "engines.oilfunding.live.cushing.stocks_m_bbl",
                "million bbl",
            ),
        ),
        "clocks": ("engines.oilfunding.asof", "engines.oilfunding.live.wti.asof"),
    },
    "scarcity": {
        "title": "Reserve scarcity and runway",
        "description": "The fitted reserve-demand kink, current distance and thirteen-week balance-sheet paths as structural estimates, not a universal floor.",
        "primary": ("engines.kink.distance_b", "number"),
        "unit": "$B reserves minus fitted kink",
        "literal_status": "STRUCTURAL ESTIMATE",
        "availability": ("engines.kink", "engines.runway", "engines.ledger"),
        "metrics": (
            ("current reserves", "engines.kink.current_reserves_b", "usd_b"),
            ("fitted kink", "engines.kink.kink_reserves_b", "usd_b"),
            ("base path end", "engines.runway.scenarios.base.end_reserves_b", "usd_b"),
        ),
        "clocks": ("engines.kink.asof", "engines.runway.asof"),
    },
    "supply": {
        "title": "Treasury supply and funding digestion",
        "description": "Announced cash needs, auction digestion and the foreign-official footprint with dated public-source clocks.",
        "primary": ("engines.supplydesk.totals.net_new_cash_b", "number"),
        "unit": "$B net new cash · published horizon",
        "literal_status": "ANNOUNCED SUPPLY",
        "availability": (
            "engines.supplydesk",
            "engines.auctions",
            "engines.officialbid",
        ),
        "metrics": (
            ("coupons net", "engines.supplydesk.totals.coupons_net_b", "usd_b"),
            ("bills net", "engines.supplydesk.totals.bills_net_b", "usd_b"),
            ("heaviest day", "engines.supplydesk.heaviest_day.net_new_cash_b", "usd_b"),
        ),
        "clocks": ("engines.supplydesk.asof",),
    },
    "forecast": {
        "title": "Funding-event diagnostics",
        "description": "Several forward estimators shown together with validation, dispersion and explicit non-trading claim boundaries.",
        "primary": ("deep.stacker.p_now", "probability"),
        "unit": "5bd ensemble event probability",
        "literal_status": "DIAGNOSTIC · NOT A TRADE SIGNAL",
        "availability": ("deep.stacker", "deep.swell", "deep.bathymetry"),
        "metrics": (
            ("Swell 5bd", "deep.swell.p_event_5bd", "probability"),
            ("Bathymetry 5bd", "deep.bathymetry.p_event_5bd", "probability"),
            ("fleet dispersion", "deep.stacker.dispersion_now", "probability"),
        ),
        "clocks": ("deep.stacker.asof", "deep.swell.asof"),
    },
    "physics": {
        "title": "The funding physics package",
        "description": "State transitions, latent modes, jump diagnostics and changepoints with their evidence and validation limits attached.",
        "primary": ("deep.bathymetry.p_event_5bd", "probability"),
        "unit": "5bd state-transition diagnostic",
        "literal_status": "DIAGNOSTIC",
        "availability": (
            "deep.bathymetry",
            "engines.merian",
            "deep.gyre",
            "deep.microseism",
        ),
        "metrics": (
            ("mean first passage", "deep.bathymetry.mfpt_bd", "days"),
            ("latent rank", "engines.merian.rank", "number"),
            ("series in modes", "engines.merian.n_series", "number"),
        ),
        "clocks": ("deep.bathymetry.asof", "engines.merian.asof"),
    },
    "helm": {
        "title": "The accountable paper book",
        "description": "Today's frozen rulebook stance, ensemble probability and walk-forward record; a paper proxy, never advice.",
        "primary": ("deep.book.today.stance", "text"),
        "unit": "paper proxy · not investment advice",
        "literal_status": "PAPER PROXY",
        "availability": ("deep.book", "deep.stacker"),
        "metrics": (
            ("ensemble 5bd", "deep.book.today.p_ensemble", "probability"),
            ("fleet dispersion", "deep.book.today.dispersion", "probability"),
            ("the Tell", "deep.book.today.tell", "number"),
        ),
        "clocks": ("deep.book.asof",),
    },
    "market": {
        "title": "Plumbing versus market pricing",
        "description": "The Tell compares funding-plumbing percentiles with market-priced stress, then keeps analog outcomes and press attention separate.",
        "primary": ("deep.tell.tell", "number"),
        "unit": "plumbing percentile minus market percentile",
        "status": "deep.tell.reading",
        "availability": ("deep.tell", "deep.playbook", "engines.scuttlebutt"),
        "metrics": (
            ("plumbing percentile", "deep.tell.plumbing_pctl", "number"),
            ("market percentile", "deep.tell.market_pctl", "number"),
            ("press baseline", "engines.scuttlebutt.baseline_ready", "boolean"),
        ),
        "clocks": ("deep.tell.asof",),
    },
    "calendar": {
        "title": "The next funding turn",
        "description": "Settlements, tax dates, auctions and turn windows projected from the published calendar without converting a scenario into a fact.",
        "primary": ("calendar.next_turn.date", "date"),
        "unit": "next published turn window",
        "literal_status": "FORWARD CALENDAR",
        "availability": ("calendar", "deep.turn", "engines.auctions"),
        "metrics": (
            ("turn mode", "calendar.next_turn.mode", "text"),
            ("published forecast", "calendar.next_turn.forecast_bp", "bp"),
            ("crunch windows", "calendar.crunch_windows", "count"),
        ),
        "clocks": ("deep.turn.asof", "generated_at"),
    },
    "positioning": {
        "title": "Leveraged positioning and dealer warehouse",
        "description": "Public positioning proxies, crowding and dealer inventory with scenario-quality limits left visible.",
        "primary": ("engines.warehouse.total_net_b", "number"),
        "unit": "$B dealer net Treasury inventory",
        "literal_status": "POSITIONING PROXY",
        "availability": ("engines.rvxray", "engines.crowding", "engines.warehouse"),
        "metrics": (
            ("warehouse percentile", "engines.warehouse.total_pctl", "number"),
            ("RV net", "engines.rvxray.net_b", "usd_b"),
            ("crowding rows", "engines.crowding.rows", "count"),
        ),
        "clocks": ("engines.rvxray.asof", "engines.warehouse.asof"),
    },
    "resonance": {
        "title": "Cross-channel resonance",
        "description": "Synchronization, persistence, changepoints and lead-lag diagnostics across the published funding channels.",
        "primary": ("engines.resonance.score", "number"),
        "unit": "resonance score · 0–100",
        "literal_status": "MULTIVARIATE DIAGNOSTIC",
        "availability": ("engines.resonance", "engines.undertow", "engines.edetect"),
        "metrics": (
            ("undertow score", "engines.undertow.score", "number"),
            ("e-detector streams", "engines.edetect.streams", "count"),
            ("hydrophone status", "engines.hydrophone.ok", "boolean"),
        ),
        "clocks": ("engines.resonance.asof", "engines.undertow.asof"),
    },
    "proof": {
        "title": "The historical claim boundary",
        "description": "Construction-point-in-time diagnostics, leak audits and model comparisons with vintage evidence and real-money eligibility explicit.",
        "primary": ("deep.backtest.status", "text"),
        "unit": "historical evidence status",
        "status": "deep.backtest.status",
        "availability": ("deep.backtest", "deep.leakaudit", "deep.regatta", "deep.ml"),
        "metrics": (
            ("validated backtest", "deep.backtest.validated_backtest", "boolean"),
            ("ML 5bd", "deep.ml.p_event_5bd", "probability"),
            ("real-money eligible", "deep.backtest.real_money_eligible", "boolean"),
        ),
        "clocks": ("deep.ml.asof", "generated_at"),
    },
    "referee": {
        "title": "Global-liquidity claims, refereed",
        "description": "Three popular quantitative claims tested on the public G3 central-bank layer with uncertainty and sample limits attached.",
        "primary": ("deep.refereegli.latest.yoy_pct", "number"),
        "unit": "G3 central-bank assets · YoY %",
        "literal_status": "OBSERVED + CLAIM TEST",
        "availability": ("deep.refereegli",),
        "metrics": (
            ("G3 assets", "deep.refereegli.latest.g3_usd_tn", "usd_tn"),
            ("asset lead peak", "deep.refereegli.claim1.peak_lead_months", "months"),
            (
                "cycle estimate",
                "deep.refereegli.claim3.dominant_period_months",
                "months",
            ),
        ),
        "clocks": ("deep.refereegli.asof",),
    },
    "system": {
        "title": "Publication health and provenance",
        "description": "The sealed snapshot clock, source inventory, explicit faults and evidence-boundary status behind every public reading.",
        "primary": ("faults", "count"),
        "unit": "source faults in this sealed snapshot",
        "availability": ("provenance",),
        "metrics": (
            ("source records", "provenance", "count"),
            ("stale headline rows", "headline", "stale_count"),
            ("snapshot version", "version", "text"),
        ),
        "clocks": ("generated_at",),
    },
}


@dataclass(frozen=True)
class CardMetric:
    """One compact fact carried on the card and its HTML evidence page."""

    label: str
    value: str


@dataclass(frozen=True)
class CardSpec:
    """A complete, deterministic social-card contract."""

    kind: str
    identifier: str
    canonical_url: str
    eyebrow: str
    title: str
    description: str
    status: str
    value: str | None = None
    unit: str | None = None
    metrics: tuple[CardMetric, ...] = ()
    as_of: str | None = None
    as_of_label: str = "EVIDENCE AS OF"
    generated_at: str | None = None
    source: str = "Seiche"
    rights: str | None = None

    @property
    def image_alt(self) -> str:
        facts = [f"Seiche {self.eyebrow}: {self.title}"]
        if self.value:
            facts.append(" ".join(part for part in (self.value, self.unit) if part))
        facts.append(f"status {self.status}")
        if self.as_of:
            facts.append(f"{_clean(self.as_of_label).lower()} {self.as_of}")
        return _clean(". ".join(facts))[:420]


def _clean(value: object, fallback: str = "") -> str:
    text = fallback if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


def _sentence(value: object) -> str:
    """Return one clean sentence without manufacturing doubled punctuation."""

    text = _clean(value)
    if not text:
        return ""
    return text if text.endswith((".", "!", "?")) else text + "."


def _slug(value: object) -> str:
    text = _clean(value).lower().replace("×", "x").replace("_", "-")
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("-.")
    return text[:100] or "view"


def _display_number(value: object, digits: int = 1) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—" if value is None else _clean(value)
    if not math.isfinite(float(value)):
        return "—"
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.{digits}f}".rstrip("0").rstrip(".")


def _percent(value: object) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    number = float(value)
    if 0 <= number <= 1:
        number *= 100
    return f"{number:.1f}%".replace(".0%", "%")


def _lookup_path(root: Mapping[str, Any], path: str) -> object | None:
    current: object = root
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return None
        current = current[segment]
    return current


def _format_tab_fact(value: object, kind: str) -> str:
    if value is None:
        return ""
    if kind == "text" or kind == "date":
        return _clean(value)
    if kind == "number":
        return _display_number(value, 1)
    if kind == "percent":
        shown = _display_number(value, 1)
        return "" if shown == "—" else f"{shown}%"
    if kind == "probability":
        return _percent(value)
    if kind == "boolean":
        return "yes" if value is True else "no" if value is False else "not reported"
    if kind == "count":
        if isinstance(value, (list, tuple, set, Mapping)):
            return str(len(value))
        return _display_number(value, 0)
    if kind == "stale_count":
        if not isinstance(value, Mapping):
            return "0"
        count = sum(
            1
            for row in value.values()
            if isinstance(row, Mapping)
            and (
                row.get("fresh") is False
                or "stale" in _clean(row.get("status") or row.get("freshness")).lower()
            )
        )
        return str(count)
    shown = _display_number(value, 1)
    if shown == "—":
        return ""
    suffixes = {
        "bp": "bp",
        "days": " business days",
        "months": " months",
        "million bbl": "m bbl",
    }
    if kind == "usd":
        return f"${shown}"
    if kind == "usd_b":
        return f"${shown}B"
    if kind == "usd_tn":
        return f"${shown}tn"
    return shown + suffixes.get(kind, "")


def _status_key(status: str) -> str:
    lowered = status.lower()
    for key in (
        "unavailable",
        "restricted",
        "stale",
        "fault",
        "observed",
        "live",
        "available",
        "derived",
        "structural",
    ):
        if key in lowered:
            return key
    return "derived"


def _stale_input_count(snapshot: Mapping[str, Any]) -> int:
    """Count explicitly stale published inputs without inferring from age.

    Only upstream records that say ``fresh: false`` or carry a stale status
    qualify. Missing clocks remain missing; the card publisher does not invent
    a freshness threshold or reinterpret a source's native cadence.
    """

    stale: set[str] = set()
    for group_name in ("provenance", "headline"):
        group = snapshot.get(group_name)
        if not isinstance(group, Mapping):
            continue
        for identifier, raw in group.items():
            if not isinstance(raw, Mapping):
                continue
            status = _clean(raw.get("status") or raw.get("freshness")).lower()
            if raw.get("fresh") is False or "stale" in status:
                stale.add(f"{group_name}:{identifier}")
    return len(stale)


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.ImageFont:
    # Pillow 10+ embeds a scalable Aileron face for load_default(size=...).
    # It avoids host-font drift and keeps the publisher self-contained.
    return ImageFont.load_default(size=size)


def _text_width(
    draw: ImageDraw.ImageDraw, value: str, font: ImageFont.ImageFont
) -> float:
    return float(draw.textlength(value, font=font))


def _wrap(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    words = _clean(value).split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    while words:
        word = words.pop(0)
        if _text_width(draw, word, font) > max_width:
            word = _ellipsize(draw, word, font, max_width)
        candidate = f"{current} {word}".strip()
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                current = ""
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    if words and lines:
        last = lines[-1]
        while last and _text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _ellipsize(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    text = _clean(value)
    if _text_width(draw, text, font) <= max_width:
        return text
    while text and _text_width(draw, text + "…", font) > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


def _rounded_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int],
    radius: int = 18,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)


def render_card(spec: CardSpec) -> bytes:
    """Render a deterministic, opaque PNG with the exact social-card geometry."""

    image = Image.new("RGB", (WIDTH, HEIGHT), (5, 6, 12))
    draw = ImageDraw.Draw(image)
    seed = hashlib.sha256(
        json.dumps(asdict(spec), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).digest()

    # A restrained violet depth gradient plus a content-seeded wave field. The
    # field changes with the evidence while remaining purely decorative.
    for y in range(HEIGHT):
        falloff = 1 - y / HEIGHT
        violet = int(13 * falloff)
        blue = int(24 * falloff)
        draw.line((0, y, WIDTH, y), fill=(5 + violet // 3, 6 + violet // 2, 12 + blue))
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    waves = ImageDraw.Draw(glow)
    phase = (seed[0] / 255) * math.tau
    amplitude = 24 + seed[1] % 28
    for lane in range(7):
        points = []
        base = 92 + lane * 54
        for x in range(610, 1241, 10):
            y = base + math.sin(x / (68 + lane * 9) + phase + lane * 0.7) * amplitude
            y += math.sin(x / 27 + seed[2] / 31) * 5
            points.append((x, int(y)))
        alpha = 45 - lane * 4
        waves.line(points, fill=(150, 132, 255, max(alpha, 14)), width=2)
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(image)

    # Masthead.
    accent = _STATUS_COLORS[_status_key(spec.status)]
    draw.ellipse((48, 41, 76, 69), outline=(187, 175, 254), width=2)
    draw.arc((53, 45, 71, 65), 205, 510, fill=(187, 175, 254), width=2)
    draw.text((91, 39), "S E I C H E", font=_font(21), fill=(238, 238, 246))
    draw.text(
        (91, 65), "EVIDENCE / CLOCKS / BOUNDARIES", font=_font(12), fill=(119, 125, 149)
    )
    domain = "seiche.info"
    draw.text(
        (WIDTH - 48 - _text_width(draw, domain, _font(15)), 48),
        domain,
        font=_font(15),
        fill=(159, 164, 184),
    )
    draw.line((48, 92, WIDTH - 48, 92), fill=(37, 40, 57), width=1)

    # Status and section identity.
    status_font = _font(13)
    label = _ellipsize(
        draw, _clean(spec.status, "UNAVAILABLE").upper(), status_font, 390
    )
    pill_w = int(_text_width(draw, label, status_font)) + 28
    pill_fill = tuple(10 + channel // 9 for channel in accent)
    draw.rounded_rectangle(
        (48, 112, 48 + pill_w, 143), radius=15, fill=pill_fill, outline=accent, width=1
    )
    draw.text((62, 119), label, font=status_font, fill=accent)
    eyebrow = _clean(spec.eyebrow).upper()
    draw.text((72 + pill_w, 119), eyebrow, font=_font(13), fill=(159, 164, 184))

    title_font = _font(42 if len(spec.title) < 76 else 36)
    # Keep the headline wholly inside the editorial column.  The reading panel
    # starts at x=796, so this gutter must remain explicit even for unusually
    # long article and dispatch titles.
    title_lines = _wrap(draw, spec.title, title_font, 700, 2)
    title_y = 166
    for line in title_lines:
        draw.text(
            (48, title_y), line, font=title_font, fill=(242, 242, 248), stroke_width=1
        )
        title_y += 50 if title_font.size >= 40 else 44

    description_font = _font(18)
    description_lines = _wrap(draw, spec.description, description_font, 690, 3)
    description_y = max(262, title_y + 4)
    for line in description_lines:
        draw.text(
            (48, description_y), line, font=description_font, fill=(176, 180, 199)
        )
        description_y += 25

    # The primary reading is isolated from the prose. An unavailable value is
    # rendered as an explicit em dash, never as zero.
    _rounded_panel(draw, (796, 163, 1152, 353), fill=(12, 14, 25), outline=(48, 51, 72))
    draw.text((824, 188), "CURRENT READING", font=_font(12), fill=(119, 125, 149))
    primary = _clean(spec.value, "—")
    value_font = _font(68 if len(primary) <= 8 else 43 if len(primary) <= 18 else 30)
    value_lines = _wrap(draw, primary, value_font, 300, 2)
    value_y = 220
    for line in value_lines:
        draw.text((824, value_y), line, font=value_font, fill=accent)
        value_y += value_font.size + 4
    if spec.unit:
        unit = _ellipsize(draw, _clean(spec.unit).upper(), _font(14), 300)
        draw.text((826, 320), unit, font=_font(14), fill=(159, 164, 184))

    # Three compact evidence facts. Always reserve the fourth for the clock so
    # freshness travels with the reading even when a source supplies few KPIs.
    metrics = list(spec.metrics[:3])
    metrics.append(
        CardMetric(
            _clean(spec.as_of_label, "EVIDENCE AS OF"),
            _clean(spec.as_of, "not available"),
        )
    )
    gap = 12
    cell_w = (WIDTH - SAFE_GUTTER * 2 - gap * 3) // 4
    top = 401
    for index, metric in enumerate(metrics):
        left = SAFE_GUTTER + index * (cell_w + gap)
        _rounded_panel(
            draw,
            (left, top, left + cell_w, 514),
            fill=(10, 12, 21),
            outline=(38, 41, 58),
            radius=14,
        )
        draw.text(
            (left + 15, top + 17),
            _ellipsize(
                draw,
                _clean(metric.label).upper(),
                _font(11),
                cell_w - 30,
            ),
            font=_font(11),
            fill=(119, 125, 149),
        )
        value_lines = _wrap(draw, metric.value, _font(19), cell_w - 30, 2)
        y = top + 48
        for line in value_lines:
            draw.text((left + 15, y), line, font=_font(19), fill=(224, 225, 236))
            y += 24

    draw.line(
        (SAFE_GUTTER, 548, WIDTH - SAFE_GUTTER, 548),
        fill=(37, 40, 57),
        width=1,
    )
    footer_left = _clean(spec.source) + " · point-in-time publication"
    if spec.rights:
        footer_left += " · " + _clean(spec.rights)
    footer_font = _font(13)
    generated = _ellipsize(
        draw,
        "generated " + _clean(spec.generated_at, "clock unavailable"),
        footer_font,
        310,
    )
    generated_width = _text_width(draw, generated, footer_font)
    generated_x = WIDTH - SAFE_GUTTER - generated_width
    footer_left = _ellipsize(
        draw,
        footer_left,
        footer_font,
        max(40, int(generated_x - SAFE_GUTTER - 28)),
    )
    draw.text((SAFE_GUTTER, 570), footer_left, font=footer_font, fill=(147, 152, 173))
    draw.text(
        (generated_x, 570),
        generated,
        font=footer_font,
        fill=(147, 152, 173),
    )
    draw.text(
        (SAFE_GUTTER, 598),
        "MISSING, STALE OR RESTRICTED EVIDENCE NEVER BECOMES A ZERO.",
        font=_font(11),
        fill=(116, 103, 173),
    )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _emit_image(site_dir: Path, spec: CardSpec) -> tuple[str, Path, str]:
    payload = render_card(spec)
    digest = hashlib.sha256(payload).hexdigest()[:16]
    relative = (
        Path("share")
        / "cards"
        / _slug(spec.kind)
        / f"{_slug(spec.identifier)}.{digest}.png"
    )
    destination = site_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != payload:
        destination.write_bytes(payload)
    return f"{SITE}/{relative.as_posix()}", destination, digest


def _meta_tags(
    spec: CardSpec, image_url: str, *, og_type: str = "website"
) -> list[tuple[str, str, str]]:
    description = _clean(spec.description)[:300]
    title = _clean(spec.title)[:140]
    return [
        ("property", "og:type", og_type),
        ("property", "og:site_name", "Seiche"),
        ("property", "og:locale", "en_US"),
        ("property", "og:url", spec.canonical_url),
        ("property", "og:title", title),
        ("property", "og:description", description),
        ("property", "og:image", image_url),
        ("property", "og:image:secure_url", image_url),
        ("property", "og:image:type", "image/png"),
        ("property", "og:image:width", str(WIDTH)),
        ("property", "og:image:height", str(HEIGHT)),
        ("property", "og:image:alt", spec.image_alt),
        ("name", "twitter:card", "summary_large_image"),
        ("name", "twitter:title", title),
        ("name", "twitter:description", description),
        ("name", "twitter:image", image_url),
        ("name", "twitter:image:alt", spec.image_alt),
    ]


_SOCIAL_META_BLOCK = re.compile(
    r"\n?<!--social-cards:meta-->.*?<!--/social-cards:meta-->\n?", re.S
)


def patch_page_metadata(
    document: str,
    spec: CardSpec,
    image_url: str,
    *,
    og_type: str = "website",
    patch_jsonld_image: bool = False,
) -> str:
    """Replace every social field as one idempotent, reviewable block."""

    document = _SOCIAL_META_BLOCK.sub("", document)
    for attribute, key, _value in _meta_tags(spec, image_url, og_type=og_type):
        pattern = re.compile(
            r"\s*<meta\b(?=[^>]*\b"
            + re.escape(attribute)
            + r"\s*=\s*([\"'])"
            + re.escape(key)
            + r"\1)[^>]*>\s*",
            re.I,
        )
        document = pattern.sub("\n", document)

    canonical = html.escape(spec.canonical_url, quote=True)
    canonical_pattern = re.compile(
        r"<link\b(?=[^>]*\brel\s*=\s*([\"'])canonical\1)[^>]*>", re.I
    )
    canonical_tag = f'<link rel="canonical" href="{canonical}">'
    if canonical_pattern.search(document):
        document = canonical_pattern.sub(canonical_tag, document, count=1)
    elif "</head>" in document:
        document = document.replace("</head>", canonical_tag + "\n</head>", 1)

    tags = []
    for attribute, key, value in _meta_tags(spec, image_url, og_type=og_type):
        tags.append(
            f'<meta {attribute}="{html.escape(key, quote=True)}" '
            f'content="{html.escape(value, quote=True)}">'
        )
    block = (
        "<!--social-cards:meta-->\n" + "\n".join(tags) + "\n<!--/social-cards:meta-->\n"
    )
    if "</head>" not in document:
        raise ValueError("social cards: page has no </head> anchor")
    document = document.replace("</head>", "\n" + block + "</head>", 1)

    if patch_jsonld_image:
        document = re.sub(
            r'("image"\s*:\s*)"https://seiche\.info/(?:og2?\.png|share/cards/[^\"]+\.png)"',
            lambda match: match.group(1) + json.dumps(image_url),
            document,
        )
    return document


def _view_page(spec: CardSpec, image_url: str, *, open_url: str) -> str:
    meta = "\n".join(
        f'<meta {attribute}="{html.escape(key, quote=True)}" content="{html.escape(value, quote=True)}">'
        for attribute, key, value in _meta_tags(spec, image_url)
    )
    metrics = "".join(
        "<div><dt>{}</dt><dd>{}</dd></div>".format(
            html.escape(metric.label), html.escape(metric.value)
        )
        for metric in spec.metrics
    )
    jsonld = {
        "@context": "https://schema.org",
        "@type": "Dataset" if spec.kind in {"series", "money-markets"} else "WebPage",
        "name": spec.title,
        "description": spec.description,
        "url": spec.canonical_url,
        "image": image_url,
        "dateModified": spec.generated_at or spec.as_of,
        "isAccessibleForFree": True,
        "publisher": {"@type": "Organization", "name": "Seiche", "url": SITE},
    }
    jsonld_text = json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(spec.title)} · Seiche</title>
<meta name="description" content="{html.escape(spec.description, quote=True)}">
<link rel="canonical" href="{html.escape(spec.canonical_url, quote=True)}">
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta name="theme-color" content="#05060c">
{meta}
<script type="application/ld+json">{jsonld_text}</script>
<style>
:root{{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#05060c;color:#eeeeF6}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 70% 0,#18132f 0,transparent 42%),#05060c;line-height:1.55}}
main,footer{{width:min(1000px,calc(100% - 36px));margin:auto}}main{{padding:42px 0 30px}}nav{{font:600 12px ui-monospace,monospace;letter-spacing:.16em;color:#bbb0fe}}
h1{{font-size:clamp(34px,7vw,64px);line-height:1.02;letter-spacing:-.035em;margin:42px 0 18px;max-width:17ch}}
.lede{{font-size:19px;color:#b0b4c7;max-width:72ch}}.status{{display:inline-block;border:1px solid #766cae;border-radius:999px;padding:5px 12px;color:#c9c0ff;font:12px ui-monospace,monospace}}
img{{display:block;width:100%;height:auto;margin:32px 0;border:1px solid #292c40;border-radius:16px;box-shadow:0 28px 90px #0008}}
dl{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}dl div{{background:#0d0f1a;border:1px solid #25283a;border-radius:12px;padding:15px}}
dt{{font:11px ui-monospace,monospace;color:#858ba3;letter-spacing:.08em}}dd{{margin:6px 0 0;font-size:18px}}.boundary{{border-left:3px solid #ffc988;padding:12px 16px;color:#bbbfd0}}
a{{color:#bbb0fe}}footer{{border-top:1px solid #25283a;padding:24px 0 42px;color:#858ba3;font-size:13px}}
</style>
</head>
<body>
<main>
<nav><a href="/">SEICHE</a> / {html.escape(spec.eyebrow.upper())}</nav>
<p class="status">{html.escape(spec.status.upper())}</p>
<h1>{html.escape(spec.title)}</h1>
<p class="lede">{html.escape(spec.description)}</p>
<img src="{html.escape(image_url, quote=True)}" width="1200" height="630" alt="{html.escape(spec.image_alt, quote=True)}">
<dl><div><dt>CURRENT READING</dt><dd>{html.escape(_clean(spec.value, "not available"))} {html.escape(_clean(spec.unit))}</dd></div>{metrics}<div><dt>{html.escape(_clean(spec.as_of_label, "EVIDENCE AS OF"))}</dt><dd>{html.escape(_clean(spec.as_of, "not available"))}</dd></div></dl>
<p class="boundary">{html.escape(_clean(spec.rights, "Research context, not investment advice. Missing evidence is not a zero."))}</p>
<p><a href="{html.escape(open_url, quote=True)}">Open the live evidence surface →</a></p>
</main>
<footer>Generated from the sealed Seiche publication snapshot at {html.escape(_clean(spec.generated_at, "clock unavailable"))}. No collection or model fitting occurs while this page is read.</footer>
</body>
</html>
"""


def _board_spec(
    snapshot: Mapping[str, Any], *, canonical_url: str, identifier: str
) -> CardSpec:
    engines = (
        snapshot.get("engines") if isinstance(snapshot.get("engines"), Mapping) else {}
    )
    composite = (
        engines.get("composite")
        if isinstance(engines.get("composite"), Mapping)
        else {}
    )
    deep = snapshot.get("deep") if isinstance(snapshot.get("deep"), Mapping) else {}
    tell = deep.get("tell") if isinstance(deep.get("tell"), Mapping) else {}
    stacker = deep.get("stacker") if isinstance(deep.get("stacker"), Mapping) else {}
    faults = snapshot.get("faults") if isinstance(snapshot.get("faults"), list) else []
    stale_inputs = _stale_input_count(snapshot)
    value = composite.get("value")
    regime = _clean(composite.get("regime"), "UNAVAILABLE").upper()
    status = "UNAVAILABLE" if value is None else regime
    if faults:
        status = (
            f"{status} · {len(faults)} SOURCE FAULT{'S' if len(faults) != 1 else ''}"
        )
    if stale_inputs:
        status += f" · {stale_inputs} STALE INPUT{'S' if stale_inputs != 1 else ''}"
    editorial = (
        snapshot.get("editorial")
        if isinstance(snapshot.get("editorial"), Mapping)
        else {}
    )
    description = _clean(
        editorial.get("thesis"),
        "The public dollar-funding board, with coverage, source faults and evidence clocks attached.",
    )
    return CardSpec(
        kind="board",
        identifier=identifier,
        canonical_url=canonical_url,
        eyebrow="live funding board",
        title=f"Dollar funding stress: {regime}",
        description=description,
        status=status,
        value=_display_number(value, 1),
        unit="out of 100",
        metrics=tuple(
            [
                CardMetric(
                    "coverage",
                    f"{_display_number(composite.get('coverage_pct'), 1)}%",
                ),
                CardMetric("source faults", str(len(faults))),
                (
                    CardMetric("stale inputs", str(stale_inputs))
                    if stale_inputs
                    else CardMetric("the Tell", _display_number(tell.get("tell"), 1))
                ),
                CardMetric(
                    "5bd event read",
                    _percent(stacker.get("p_now"))
                    if stacker.get("ok")
                    else "unavailable",
                ),
            ]
        ),
        as_of=_clean(snapshot.get("generated_at")) or None,
        as_of_label="SNAPSHOT GENERATED",
        generated_at=_clean(snapshot.get("generated_at")) or None,
        source="Seiche composite",
        rights="Point-in-time as published; research context, not investment advice.",
    )


def _world_reading(payload: Mapping[str, Any], selector: str) -> str:
    selected = (
        payload.get(selector) if isinstance(payload.get(selector), Mapping) else {}
    )
    candidates: list[object] = [selected.get("plain_language"), selected.get("reading")]
    headline = (
        selected.get("headline")
        if isinstance(selected.get("headline"), Mapping)
        else {}
    )
    candidates.append(headline.get("verdict"))
    risk = (
        selected.get("risk_context")
        if isinstance(selected.get("risk_context"), Mapping)
        else {}
    )
    comparison = (
        risk.get("market_vs_plumbing")
        if isinstance(risk.get("market_vs_plumbing"), Mapping)
        else {}
    )
    candidates.append(comparison.get("reading"))
    for candidate in candidates:
        if _clean(candidate):
            return _clean(candidate)
    if selector == "sources":
        return "Official publisher register"
    if selector == "methodology":
        return "Bounded context; not an executable market feed"
    return "Evidence coverage and known gaps"


def _world_spec(snapshot: Mapping[str, Any], selector: str) -> CardSpec:
    from seiche.markets.world import project_world_markets

    payload = project_world_markets(snapshot, selector=selector)
    status = _clean(payload.get("status"), "unavailable")
    coverage = (
        payload.get("coverage") if isinstance(payload.get("coverage"), Mapping) else {}
    )
    domains = (
        coverage.get("domains") if isinstance(coverage.get("domains"), list) else []
    )
    faults = snapshot.get("faults") if isinstance(snapshot.get("faults"), list) else []
    available = sum(
        isinstance(domain, Mapping) and domain.get("status") != "unavailable"
        for domain in domains
    )
    clocks = payload.get("clocks") if isinstance(payload.get("clocks"), Mapping) else {}
    title = _WORLD_LABELS[selector]
    reading = _world_reading(payload, selector)
    return CardSpec(
        kind="world-markets",
        identifier=selector.replace("_", "-"),
        canonical_url=f"{SITE}/views/world-markets/{selector.replace('_', '-')}/",
        eyebrow=f"world markets / {selector.replace('_', ' ')}",
        title=title,
        description=(
            f"{_sentence(reading)} Status, source-native clocks and explicit coverage gaps travel with this bounded projection."
        ),
        status=status,
        value=reading,
        unit="context only · no executable prices",
        metrics=(
            CardMetric("available domains", f"{available}/{len(domains) or 4}"),
            CardMetric("source faults", str(len(faults))),
            CardMetric(
                "history in response",
                "no" if payload.get("chart_history_included") is False else "declared",
            ),
        ),
        as_of=_clean(payload.get("as_of") or clocks.get("selected_evidence_as_of"))
        or None,
        generated_at=_clean(payload.get("generated_at") or snapshot.get("generated_at"))
        or None,
        source="Seiche world-markets v1",
        rights="Observed, derived, structural, restricted and unavailable remain distinct.",
    )


def _money_market_specs(site_dir: Path, snapshot: Mapping[str, Any]) -> list[CardSpec]:
    candidates = [
        site_dir / "money-markets" / "catalog.json",
        REPO_ROOT / "frontend" / "public" / "money-markets" / "catalog.json",
    ]
    catalog_path = next((path for path in candidates if path.exists()), None)
    if catalog_path is None:
        return []
    catalog = json.loads(catalog_path.read_text())
    market_rows = (
        catalog.get("markets") if isinstance(catalog.get("markets"), list) else []
    )
    snap = (
        catalog.get("snapshot") if isinstance(catalog.get("snapshot"), Mapping) else {}
    )
    generated_at = (
        _clean(snapshot.get("generated_at") or catalog.get("updated")) or None
    )
    specs = [
        CardSpec(
            kind="money-markets",
            identifier="overview",
            canonical_url=f"{SITE}/views/money-markets/overview/",
            eyebrow="global money-market atlas",
            title="Cash conditions across local clearing systems",
            description=_clean(
                catalog.get("description"),
                "A rights-aware map of registered money-market benchmarks.",
            ),
            status=_clean(snap.get("source_status"), "structural"),
            value=f"{_display_number(snap.get('registered_markets'))} markets",
            unit="local conventions · never one universal score",
            metrics=(
                CardMetric(
                    "raw live benchmarks",
                    _display_number(snap.get("raw_live_benchmarks")),
                ),
                CardMetric(
                    "derived context",
                    _display_number(snap.get("derived_context_benchmarks")),
                ),
                CardMetric(
                    "declared unavailable",
                    _display_number(snap.get("declared_unavailable_markets")),
                ),
            ),
            as_of=_clean(snap.get("observed_on") or catalog.get("updated")) or None,
            generated_at=generated_at,
            source="Seiche money-market coverage receipt",
            rights="Discovery is not live coverage; upstream rights remain attached.",
        )
    ]
    for row in market_rows:
        if not isinstance(row, Mapping) or not _clean(row.get("market_id")):
            continue
        market_id = _clean(row["market_id"])
        status = _clean(row.get("status"), "DECLARED_UNAVAILABLE")
        benchmark = _clean(row.get("benchmark"), "NO PUBLIC BENCHMARK")
        raw = row.get("raw_value_public") is True
        specs.append(
            CardSpec(
                kind="money-markets",
                identifier=market_id,
                canonical_url=f"{SITE}/views/money-markets/{market_id}/",
                eyebrow=f"money market / {market_id}",
                title=f"{_clean(row.get('display_name'), market_id)} cash market",
                description=(
                    f"{benchmark} is the registered local benchmark. Availability, rights and its native clock are shown without cross-market level ranking."
                ),
                status=status,
                value=benchmark,
                unit=_clean(row.get("currency"), "local convention"),
                metrics=(
                    CardMetric("public raw value", "yes" if raw else "no"),
                    CardMetric(
                        "rights", _clean(row.get("rights_status"), "not reported")
                    ),
                    CardMetric(
                        "evidence eligible",
                        "yes" if row.get("evidence_eligible") is True else "no",
                    ),
                ),
                as_of=_clean(row.get("as_of")) or None,
                generated_at=generated_at,
                source="Seiche registered market pack",
                rights=(
                    "Raw value may be shown under the registered source terms."
                    if raw
                    else "The raw value is withheld or unavailable; this is not a zero."
                ),
            )
        )
    return specs


def _series_specs(snapshot: Mapping[str, Any]) -> list[CardSpec]:
    headline = (
        snapshot.get("headline")
        if isinstance(snapshot.get("headline"), Mapping)
        else {}
    )
    specs: list[CardSpec] = []
    for identifier, raw in sorted(headline.items()):
        if not isinstance(raw, Mapping):
            continue
        value = raw.get("value")
        label, default_unit = _HEADLINE_LABELS.get(
            str(identifier),
            (_clean(identifier).replace("_", " ").title(), _clean(raw.get("unit"))),
        )
        unit = _clean(raw.get("unit"), default_unit)
        raw_status = _clean(raw.get("status"))
        stale = raw.get("fresh") is False or "stale" in raw_status.lower()
        status = (
            "STALE"
            if stale
            else raw_status or ("UNAVAILABLE" if value is None else "OBSERVED")
        )
        display = None if value is None else _display_number(value, 3)
        specs.append(
            CardSpec(
                kind="series",
                identifier=_slug(identifier),
                canonical_url=f"{SITE}/views/series/{_slug(identifier)}/",
                eyebrow=f"published series / {_clean(identifier)}",
                title=label,
                description=(
                    f"The current published {label} observation with its source clock and availability state."
                ),
                status=status,
                value=display,
                unit=unit or "native units",
                metrics=(
                    CardMetric("series id", _clean(identifier)),
                    CardMetric(
                        "freshness",
                        "stale"
                        if stale
                        else _clean(raw.get("freshness"), "as published"),
                    ),
                    CardMetric(
                        "source", _clean(raw.get("source"), "Seiche source ledger")
                    ),
                ),
                as_of=_clean(raw.get("asof") or raw.get("as_of")) or None,
                generated_at=_clean(snapshot.get("generated_at")) or None,
                source=_clean(raw.get("source"), "Seiche headline ledger"),
                rights=_clean(
                    raw.get("rights"),
                    "Public board observation; upstream terms remain attached.",
                ),
            )
        )
    return specs


def _tab_specs(snapshot: Mapping[str, Any]) -> list[CardSpec]:
    """Project every finite generic UI share surface from the sealed snapshot."""

    generated_at = _clean(snapshot.get("generated_at")) or None
    faults = snapshot.get("faults") if isinstance(snapshot.get("faults"), list) else []
    output: list[CardSpec] = []
    for identifier, definition in _TAB_DEFINITIONS.items():
        availability: list[bool | None] = []
        for path in definition.get("availability", ()):
            raw = _lookup_path(snapshot, str(path))
            if raw is None or raw == {} or raw == []:
                availability.append(None)
            elif isinstance(raw, Mapping) and raw.get("ok") is False:
                availability.append(False)
            else:
                availability.append(True)

        explicit_status = ""
        status_path = definition.get("status")
        if status_path:
            explicit_status = _clean(_lookup_path(snapshot, str(status_path)))
        available = sum(state is True for state in availability)
        unavailable = sum(state is False for state in availability)
        declared = len(availability)
        if identifier == "system":
            status = (
                "AVAILABLE"
                if not faults
                else f"{len(faults)} SOURCE FAULT{'S' if len(faults) != 1 else ''}"
            )
        elif explicit_status:
            status = explicit_status
        elif definition.get("literal_status") and available:
            status = _clean(definition["literal_status"])
        elif not available:
            status = "UNAVAILABLE"
        elif unavailable or available < declared:
            status = f"PARTIAL · {available}/{declared} INPUTS AVAILABLE"
        else:
            status = "AVAILABLE"

        primary_path, primary_kind = definition["primary"]
        primary = _format_tab_fact(
            _lookup_path(snapshot, str(primary_path)), str(primary_kind)
        )
        metrics: list[CardMetric] = []
        for label, path, kind in definition.get("metrics", ()):
            formatted = _format_tab_fact(_lookup_path(snapshot, str(path)), str(kind))
            if formatted:
                metrics.append(CardMetric(_clean(label), formatted))

        fallback_metrics = (
            CardMetric(
                "available inputs",
                f"{available}/{declared}" if declared else "not declared",
            ),
            CardMetric("source faults", str(len(faults))),
            CardMetric("stale published inputs", str(_stale_input_count(snapshot))),
        )
        labels = {metric.label for metric in metrics}
        for metric in fallback_metrics:
            if len(metrics) >= 3:
                break
            if metric.label not in labels:
                metrics.append(metric)
                labels.add(metric.label)

        as_of = None
        as_of_label = "EVIDENCE AS OF"
        for clock_path in definition.get("clocks", ()):
            candidate = _clean(_lookup_path(snapshot, str(clock_path)))
            if candidate:
                as_of = candidate
                if str(clock_path) == "generated_at":
                    as_of_label = "SNAPSHOT GENERATED"
                break
        if as_of is None:
            as_of = generated_at
            as_of_label = "SNAPSHOT GENERATED"

        output.append(
            CardSpec(
                kind="tab",
                identifier=identifier,
                canonical_url=f"{SITE}/views/tabs/{identifier}/",
                eyebrow=f"terminal view / {identifier.replace('-', ' ')}",
                title=_clean(definition["title"]),
                description=_clean(definition["description"]),
                status=status,
                value=primary or None,
                unit=_clean(definition.get("unit")) or None,
                metrics=tuple(metrics),
                as_of=as_of,
                as_of_label=as_of_label,
                generated_at=generated_at,
                source="Seiche sealed publication snapshot",
                rights=(
                    "Research context, not investment advice. Missing, stale, restricted "
                    "or unavailable inputs are never converted to zero."
                ),
            )
        )
    return output


def _dispatch_specs(site_dir: Path) -> list[tuple[CardSpec, Path]]:
    index_path = site_dir / "dispatches" / "index.json"
    if not index_path.exists():
        return []
    rows = json.loads(index_path.read_text())
    output: list[tuple[CardSpec, Path]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or not _clean(row.get("slug")):
            continue
        slug = _clean(row["slug"])
        date = _clean(row.get("date")) or None
        kind = "week ahead" if slug.endswith("week-ahead") else "daily dispatch"
        spec = CardSpec(
            kind="dispatch",
            identifier=slug,
            canonical_url=f"{SITE}/dispatches/{slug}",
            eyebrow=f"desk record / {kind}",
            title=_clean(row.get("title"), "Seiche dispatch"),
            description=_clean(
                row.get("summary"), "A dated Seiche market evidence record."
            ),
            status=_clean(row.get("tag"), "PUBLISHED"),
            value=date,
            unit="point-in-time desk record",
            metrics=(
                CardMetric("edition", kind),
                CardMetric("board regime", _clean(row.get("tag"), "not reported")),
                CardMetric("revision policy", "dated public record"),
            ),
            as_of=date,
            generated_at=date,
            source="Seiche desk",
            rights="Public dated analysis; not investment advice.",
        )
        output.append((spec, site_dir / "dispatches" / f"{slug}.html"))
    return output


def _article_specs(site_dir: Path) -> list[tuple[CardSpec, Path]]:
    index_path = site_dir / "articles" / "index.json"
    if not index_path.exists():
        return []
    rows = json.loads(index_path.read_text())
    output: list[tuple[CardSpec, Path]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping) or not _clean(row.get("slug")):
            continue
        slug = _clean(row["slug"])
        published = _clean(row.get("published_at") or row.get("date")) or None
        article_type = _clean(row.get("article_type"), "analysis").replace("_", " ")
        spec = CardSpec(
            kind="article",
            identifier=slug,
            canonical_url=f"{SITE}/articles/{slug}/",
            eyebrow=f"published analysis / {article_type}",
            title=_clean(row.get("headline"), "Seiche analysis"),
            description=_clean(
                row.get("dek"), "A dated Seiche market evidence analysis."
            ),
            status="PUBLISHED",
            value=_clean(row.get("evidence_as_of"), _clean(row.get("date"))),
            unit="evidence cut",
            metrics=(
                CardMetric("article type", article_type),
                CardMetric("words", _display_number(row.get("word_count"))),
                CardMetric(
                    "editorial class", _clean(row.get("editorial_class"), "desk brief")
                ),
            ),
            as_of=_clean(row.get("evidence_as_of") or row.get("date")) or None,
            generated_at=published,
            source="Seiche analysis desk",
            rights="Public dated analysis; claims remain bound to the published evidence cut.",
        )
        output.append((spec, site_dir / "articles" / slug / "index.html"))
    return output


def _write_view(site_dir: Path, spec: CardSpec, image_url: str, open_url: str) -> Path:
    path = spec.canonical_url.removeprefix(SITE).strip("/")
    destination = site_dir / path / "index.html"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_view_page(spec, image_url, open_url=open_url))
    return destination


def _append_sitemap(site_dir: Path, specs: Iterable[CardSpec]) -> None:
    path = site_dir / "sitemap.xml"
    if not path.exists():
        return
    document = path.read_text()
    urls = sorted({spec.canonical_url for spec in specs})
    additions = []
    for url in urls:
        if f"<loc>{html.escape(url)}</loc>" in document:
            continue
        additions.append(
            f"  <url><loc>{html.escape(url)}</loc><changefreq>daily</changefreq><priority>0.7</priority></url>"
        )
    if additions and "</urlset>" in document:
        document = document.replace(
            "</urlset>", "\n".join(additions) + "\n</urlset>", 1
        )
        path.write_text(document)


def _open_url(spec: CardSpec) -> str:
    if spec.kind == "board":
        return f"{SITE}/#board"
    if spec.kind == "world-markets":
        selector = spec.identifier.replace("-", "_")
        aliases = {
            "summary": "/markets/",
            "money_markets": "/money-markets/",
            "forex": "/markets/forex/",
            "capital_markets": "/markets/capital-markets/",
            "china_macro": "/markets/china-macro/",
        }
        return SITE + aliases.get(selector, "/markets/")
    if spec.kind == "money-markets":
        if spec.identifier == "overview":
            return f"{SITE}/money-markets/"
        return f"https://api.seiche.info/api/v2/markets/{spec.identifier}/overview"
    if spec.kind == "series":
        return f"{SITE}/#board"
    if spec.kind == "tab":
        fragments = {
            "fx-materials": "fx%C3%97materials",
            "oil-funding": "oil%C3%97funding",
        }
        return f"{SITE}/#{fragments.get(spec.identifier, spec.identifier)}"
    return spec.canonical_url


def build(site_dir: Path) -> dict[str, Any]:
    """Build every contextual card and return the written publication manifest."""

    site_dir = Path(site_dir)
    snapshot_path = site_dir / "data" / "overview.json"
    root_path = site_dir / "index.html"
    if not snapshot_path.exists() or not root_path.exists():
        raise SystemExit(
            "social cards: run after export_public.py and the Vite build; "
            "dist/data/overview.json and dist/index.html are required"
        )
    snapshot = json.loads(snapshot_path.read_text())
    if not isinstance(snapshot, Mapping):
        raise SystemExit("social cards: overview.json is not an object")

    root_spec = _board_spec(snapshot, canonical_url=f"{SITE}/", identifier="home")
    view_specs: list[CardSpec] = [
        _board_spec(
            snapshot,
            canonical_url=f"{SITE}/views/board/composite/",
            identifier="composite",
        )
    ]
    view_specs.extend(_world_spec(snapshot, selector) for selector in _WORLD_SELECTORS)
    view_specs.extend(_money_market_specs(site_dir, snapshot))
    view_specs.extend(_series_specs(snapshot))
    view_specs.extend(_tab_specs(snapshot))

    manifest_entries: list[dict[str, Any]] = []

    root_image, _root_file, root_digest = _emit_image(site_dir, root_spec)
    root_path.write_text(
        patch_page_metadata(root_path.read_text(), root_spec, root_image)
    )
    manifest_entries.append(
        {**asdict(root_spec), "image": root_image, "sha256_prefix": root_digest}
    )

    emitted: dict[tuple[str, str], tuple[str, str]] = {}
    for spec in view_specs:
        image_url, _image_file, digest = _emit_image(site_dir, spec)
        emitted[(spec.kind, spec.identifier)] = (image_url, digest)
        _write_view(site_dir, spec, image_url, _open_url(spec))
        manifest_entries.append(
            {**asdict(spec), "image": image_url, "sha256_prefix": digest}
        )

    # Upgrade the long-standing human aliases without changing their canonical
    # URLs. Sharing /markets/forex/ now shows forex rather than a fleet-wide logo.
    alias_specs = {
        site_dir / "markets" / "index.html": ("world-markets", "summary"),
        site_dir / "markets" / "forex" / "index.html": ("world-markets", "forex"),
        site_dir / "markets" / "capital-markets" / "index.html": (
            "world-markets",
            "capital-markets",
        ),
        site_dir / "markets" / "china-macro" / "index.html": (
            "world-markets",
            "china-macro",
        ),
        site_dir / "money-markets" / "index.html": ("money-markets", "overview"),
    }
    by_key = {(spec.kind, spec.identifier): spec for spec in view_specs}
    for page, key in alias_specs.items():
        if not page.exists() or key not in emitted or key not in by_key:
            continue
        base = by_key[key]
        alias_spec = CardSpec(
            **{
                **asdict(base),
                "canonical_url": SITE
                + "/"
                + page.relative_to(site_dir).parent.as_posix().strip("/")
                + "/",
                "metrics": base.metrics,
            }
        )
        image_url, _digest = emitted[key]
        page.write_text(patch_page_metadata(page.read_text(), alias_spec, image_url))

    for spec, page in [*_dispatch_specs(site_dir), *_article_specs(site_dir)]:
        image_url, _image_file, digest = _emit_image(site_dir, spec)
        if page.exists():
            page.write_text(
                patch_page_metadata(
                    page.read_text(),
                    spec,
                    image_url,
                    og_type="article",
                    patch_jsonld_image=True,
                )
            )
        manifest_entries.append(
            {**asdict(spec), "image": image_url, "sha256_prefix": digest}
        )

    _append_sitemap(site_dir, view_specs)
    manifest = {
        "schema": "seiche.social-cards.v1",
        "generated_at": snapshot.get("generated_at"),
        "image_contract": {
            "width": WIDTH,
            "height": HEIGHT,
            "type": "image/png",
            "content_addressed": True,
        },
        "request_time_collection": False,
        "request_time_model_fitting": False,
        "share_route_contract": {
            "covered": [
                "board composite",
                "published headline series",
                "registered money-market pack",
                "bounded world-market selector",
                "every finite generic public UI tab",
                "dispatch detail",
                "article detail",
            ],
            "fragment_only_gaps": [],
            "non_shareable_surfaces": {
                "CORPUS": "unbounded rights-aware dataset registry",
                "TIME MACHINE": "arbitrary request-time historical reconstruction",
                "ACCOUNT": "private viewer and credential state",
            },
            "reason": (
                "Every finite public share action resolves to a path-based card. "
                "Unbounded, request-time or private surfaces expose no share action."
            ),
        },
        "views": manifest_entries,
        "known_gap": (
            "The corpus registry is unbounded and served by a separate rights-aware service. "
            "This finite publisher does not pre-render one card per corpus dataset; a future "
            "cache-only corpus card service must preserve restricted, unavailable, stale and "
            "download=null states."
        ),
    }
    manifest_path = site_dir / "share" / "cards" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: python -m seiche.social_cards <built-site-dir>")
    manifest = build(Path(args[0]))
    print(
        "social cards: "
        f"{len(manifest['views'])} contextual views, "
        f"{manifest['image_contract']['width']}x{manifest['image_contract']['height']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
