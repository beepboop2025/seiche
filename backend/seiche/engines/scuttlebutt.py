"""Scuttlebutt — what the harbor is talking about.

The scuttlebutt was the ship's water cask where sailors traded rumor; the
word still means exactly that. This engine measures press ATTENTION on the
money-market topics this terminal watches (repo, MMFs, reserves, bills, Fed
facilities, the basis trade) from GDELT's normalized daily coverage series.

The research lineage is the same one LiquiLens validated for institutions:
news attention identifies funding stress before official series can move
(Correia–Luck–Verner: newspaper coverage IS the run channel, 1863-1934;
Cerchiello et al.: news adds early warning beyond financials). Topic-level
attention is the macro version: September 2019 repo, March 2020, and the
Dec-2025 SRF record were all accompanied by coverage surges.

Per topic:
  attention_z   mean coverage over the recent window vs the topic's OWN
                prior baseline (z-score; normalized volume, so the global
                news cycle is already divided out)
  tone_delta    recent mean GDELT tone minus baseline tone (negative =
                the coverage is souring, not just growing)
  attention     0-100 presentation blend of the two

Context engine, same doctrine as Communiqué: narrative is not plumbing
evidence — never weighted into the composite. It tells you which pipe the
PRESS is staring at; the plumbing engines tell you which pipe is actually
straining. Divergence between the two is itself worth a look.
"""

from __future__ import annotations

import math

from seiche.config import (
    SCUTTLEBUTT_MIN_BASELINE_D,
    SCUTTLEBUTT_RECENT_D,
    SCUTTLEBUTT_TONE_FLAG,
    SCUTTLEBUTT_Z_FLAG,
)


def _zscore(values: list[float], recent_d: int) -> float | None:
    """Recent-window mean vs the series' own earlier baseline. Pure."""
    if len(values) < SCUTTLEBUTT_MIN_BASELINE_D + recent_d:
        return None
    base, recent = values[:-recent_d], values[-recent_d:]
    mu = sum(base) / len(base)
    var = sum((v - mu) ** 2 for v in base) / (len(base) - 1)
    sd = math.sqrt(var)
    recent_mu = sum(recent) / len(recent)
    if sd == 0:
        # flat baseline degenerates the z exactly when a break matters most —
        # cap it honestly instead of dividing by zero
        return 4.0 if recent_mu > mu else 0.0
    return (recent_mu - mu) / sd


def _delta(values: list[float], recent_d: int) -> float | None:
    """Recent-window mean minus baseline mean (for tone: negative = souring)."""
    if len(values) < SCUTTLEBUTT_MIN_BASELINE_D + recent_d:
        return None
    base, recent = values[:-recent_d], values[-recent_d:]
    return sum(recent) / len(recent) - sum(base) / len(base)


def _blend(z: float | None, tone_delta: float | None) -> float | None:
    """0-100 presentation blend; fixed squash ranges, missing parts drop out."""
    parts = []
    if z is not None:
        parts.append(max(0.0, min(1.0, z / 4.0)))            # z of 4+ saturates
    if tone_delta is not None:
        parts.append(max(0.0, min(1.0, -tone_delta / 4.0)))  # 4-point souring saturates
    return round(100.0 * sum(parts) / len(parts), 1) if parts else None


def analyze(blob: dict) -> dict:
    """blob: the gdelt source dict ({"topics": {key: {label, volume, tone}}})."""
    topics_in = (blob or {}).get("topics") or {}
    if not topics_in:
        return {"ok": False, "reason": "no GDELT topic series fetched (see faults)"}

    rows, flags, asof = [], [], ""
    for key, rec in topics_in.items():
        vol = [v for _, v in rec.get("volume", [])]
        tone = [v for _, v in rec.get("tone", [])]
        if rec.get("volume"):
            asof = max(asof, rec["volume"][-1][0])
        z = _zscore(vol, SCUTTLEBUTT_RECENT_D)
        tone_delta = _delta(tone, SCUTTLEBUTT_RECENT_D)
        rows.append({
            "key": key,
            "label": rec.get("label", key),
            "attention": _blend(z, tone_delta),
            "attention_z": None if z is None else round(z, 2),
            "tone_delta": None if tone_delta is None else round(tone_delta, 2),
            "mean_daily_pct": round(sum(vol) / len(vol), 4) if vol else None,
            "n_days": len(vol),
        })
        if z is not None and z >= SCUTTLEBUTT_Z_FLAG:
            flags.append(f"{rec.get('label', key)} chatter surging (z {z:.1f} vs own baseline)")
        if tone_delta is not None and tone_delta <= SCUTTLEBUTT_TONE_FLAG:
            flags.append(f"{rec.get('label', key)} coverage souring (tone {tone_delta:+.1f} vs baseline)")

    rows.sort(key=lambda r: (-(r["attention"] if r["attention"] is not None else -1), r["key"]))
    return {
        "ok": True,
        "asof": asof or None,
        "latest": {"loudest": rows[0]["label"], "loudest_attention": rows[0]["attention"],
                   "n_topics": len(rows), "n_flags": len(flags)},
        "topics": rows,
        "flags": flags,
        "caveats": [
            "GDELT normalized volume (share of ALL global coverage) — the global news cycle is divided out",
            "attention measures how loudly the press talks, not whether it is right",
            "context engine: narrative is not plumbing evidence — never weighted into the composite",
        ],
        "method": (
            "per frozen topic query: recent-window mean coverage vs the topic's own "
            f"{SCUTTLEBUTT_MIN_BASELINE_D}d+ baseline (z), plus tone delta; 0-100 blend "
            "is presentation only (Correia-Luck-Verner / Cerchiello lineage)"
        ),
    }
