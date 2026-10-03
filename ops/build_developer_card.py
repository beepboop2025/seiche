"""Render versionless workbench previews; no prices, clocks or live claims.

Optional authoring command: python ops/build_developer_card.py
Requires Pillow 12.3.0. Committed PNGs are served directly; no deploy hook.
Pillow's embedded font keeps this independent of system fonts.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
CARDS = {
    "developers": {
        "label": "PUBLIC API + MCP",
        "title": ("Funding evidence.", "Inside your research tools."),
        "description": ("Connect public, read-only research interfaces.", "Keep source dates and coverage gaps with every result."),
        "steps": ("Connect", "Read", "Inspect sources", "Retain evidence"),
        "boundary": "RESEARCH DATA / EXPLICIT LIMITS / NO TRADE EXECUTION",
        "accent": "#b7a9ed",
    },
}


def render(kind):
    item = CARDS[kind]
    image = Image.new("RGB", (1200, 630), "#080e18")
    draw = ImageDraw.Draw(image)
    def text(x, y, value, size, color):
        draw.text((x, y), value, font=ImageFont.load_default(size=size), fill=color)
    draw.line((60, 96, 1140, 96), fill="#2b3e51", width=1)
    text(60, 44, "SEICHE", 25, "#ecf0f5")
    text(850, 48, "seiche.info", 18, "#a9b7c7")
    text(60, 129, item["label"], 18, item["accent"])
    for i, line in enumerate(item["title"]):
        text(60, 181 + 69 * i, line, 56, "#ecf0f5")
    for i, line in enumerate(item["description"]):
        text(62, 343 + 33 * i, line, 24, "#acbbcd")
    for i, label in enumerate(item["steps"]):
        x = 60 + i * 270
        draw.rounded_rectangle((x, 443, x + 251, 516), radius=10, fill="#101e2c", outline="#2b3e51")
        text(x + 16, 456, f"0{i + 1}", 15, item["accent"])
        text(x + 16, 479, label, 20, "#ecf0f5")
    text(60, 566, item["boundary"], 17, item["accent"])
    return image


def main():
    for kind in CARDS:
        render(kind).save(ROOT / "frontend" / "public" / f"og-{kind}.png", optimize=True)


if __name__ == "__main__":
    main()
