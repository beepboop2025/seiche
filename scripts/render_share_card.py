#!/usr/bin/env python3
"""Render Seiche's neutral share card into frontend/public/og.png and og2.png.

The previous cards carried readings and claims the current release withholds (a frozen
"REGIME: STRAIN", "honest backtests", "forecasts sealed before the outcome") and named another
company. Pages, feeds and catalogs keep linking the same two URLs; this card states only what
Seiche is, in the site's own words. Routes with generated cards keep them (seiche.social_cards).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend/public"
MARK = PUBLIC / "icons/pwa-512.png"
WIDTH, HEIGHT = 1200, 630
FIELD, PAPER, MUTED, LINE = "#000000", "#f2f1ec", "#9da2ab", "#23272e"


def _font(size: int) -> ImageFont.ImageFont:
    # Pillow's embedded font: no machine-specific font substitution.
    return ImageFont.load_default(size=size)


def render() -> Image.Image:
    card = Image.new("RGB", (WIDTH, HEIGHT), FIELD)
    draw = ImageDraw.Draw(card)
    mark = Image.open(MARK).convert("RGBA").resize((120, 120), Image.Resampling.LANCZOS)
    card.paste(mark, (80, 80), mark)
    draw.text((228, 140), "Seiche", font=_font(72), fill=PAPER, anchor="lm")
    draw.text((80, 300), "Dollar funding and money-market research,", font=_font(48), fill=PAPER)
    draw.text((80, 364), "free, with source dates and coverage gaps.", font=_font(48), fill=PAPER)
    draw.line((80, 500, WIDTH - 80, 500), fill=LINE, width=2)
    draw.text((80, 530), "LIQUILENS PRIVATE LIMITED", font=_font(24), fill=MUTED)
    draw.text((WIDTH - 80, 530), "seiche.info", font=_font(24), fill=MUTED, anchor="ra")
    return card


if __name__ == "__main__":
    card = render()
    for name in ("og.png", "og2.png"):
        card.save(PUBLIC / name, format="PNG", optimize=True)
    print("Rendered frontend/public/og.png and og2.png")
