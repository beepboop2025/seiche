"""Scuttlebutt — what the harbor is talking about.

The scuttlebutt was the ship's water cask where sailors traded rumor; the
word still means exactly that. This engine measures press ATTENTION on the
money-market topics this terminal watches (repo, MMFs, reserves, bills, Fed
facilities, the basis trade). Production uses GDELT's downloadable WEB-NGRAM
heartbeat after GDELT asked researchers to move off its overloaded legacy
search API during the 2026 Spanner migration. Each heartbeat is normalized by
the documents inside that same corpus; an explicit legacy run can still read
the older DOC normalized-daily series for comparison.

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
  tone_delta    legacy DOC mode only: recent mean GDELT tone minus baseline
                tone. WEB-NGRAM does not carry the DOC tone model, so tone is
                unavailable rather than synthesized.
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
    GDELT_WEB_MIN_BASELINE_N,
    GDELT_WEB_RECENT_N,
)


def _zscore(values: list[float], recent_d: int,
            min_baseline: int = SCUTTLEBUTT_MIN_BASELINE_D) -> float | None:
    """Recent-window mean vs the series' own earlier baseline. Pure."""
    if len(values) < min_baseline + recent_d:
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


def _delta(values: list[float], recent_d: int,
           min_baseline: int = SCUTTLEBUTT_MIN_BASELINE_D) -> float | None:
    """Recent-window mean minus baseline mean (for tone: negative = souring)."""
    if len(values) < min_baseline + recent_d:
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

    web_mode = blob.get("mode") == "web-ngrams"
    recent_n = GDELT_WEB_RECENT_N if web_mode else SCUTTLEBUTT_RECENT_D
    min_baseline = (GDELT_WEB_MIN_BASELINE_N if web_mode
                    else SCUTTLEBUTT_MIN_BASELINE_D)
    rows, flags, asof = [], [], ""
    for key, rec in topics_in.items():
        vol = [v for _, v in rec.get("volume", [])]
        tone = [v for _, v in rec.get("tone", [])]
        if rec.get("volume"):
            asof = max(asof, rec["volume"][-1][0])
        z = _zscore(vol, recent_n, min_baseline)
        tone_delta = _delta(tone, recent_n, min_baseline)
        rows.append({
            "key": key,
            "label": rec.get("label", key),
            "attention": _blend(z, tone_delta),
            "attention_z": None if z is None else round(z, 2),
            "tone_delta": None if tone_delta is None else round(tone_delta, 2),
            "mean_daily_pct": (round(sum(vol) / len(vol), 4)
                               if vol and not web_mode else None),
            "mean_sample_pct": round(sum(vol) / len(vol), 6) if vol else None,
            "current_share_pct": rec.get("current_share_pct"),
            "matched_documents": rec.get("matched_documents"),
            "sample_documents": rec.get("sample_documents"),
            "n_days": len(vol) if not web_mode else None,
            "n_samples": len(vol),
        })
        if z is not None and z >= SCUTTLEBUTT_Z_FLAG:
            flags.append(f"{rec.get('label', key)} chatter surging (z {z:.1f} vs own baseline)")
        if tone_delta is not None and tone_delta <= SCUTTLEBUTT_TONE_FLAG:
            flags.append(f"{rec.get('label', key)} coverage souring (tone {tone_delta:+.1f} vs baseline)")

    rows.sort(key=lambda r: (-(
        r["attention"] if r["attention"] is not None
        else r["current_share_pct"] if r["current_share_pct"] is not None
        else -1), r["key"]))
    n_samples = max((row["n_samples"] for row in rows), default=0)
    required_samples = min_baseline + recent_n
    baseline_ready = n_samples >= required_samples
    if web_mode:
        caveats = [
            "GDELT WEB-NGRAM document share — one downloadable heartbeat is scanned once for all frozen topics",
            "bulk feed exposes occurrence, not DOC-model tone; tone is left unavailable rather than imputed",
            "attention measures how loudly the press talks, not whether it is right",
            "context engine: narrative is not plumbing evidence — never weighted into the composite",
        ]
        method = (
            "per frozen topic phrase set: matched documents / all documents in each "
            f"GDELT WEB-NGRAM heartbeat; latest {recent_n} samples vs at least "
            f"{min_baseline} earlier samples (z); transport follows GDELT's "
            "2026 bulk-feed migration guidance"
        )
    else:
        caveats = [
            "GDELT normalized volume (share of ALL global coverage) — the global news cycle is divided out",
            "attention measures how loudly the press talks, not whether it is right",
            "context engine: narrative is not plumbing evidence — never weighted into the composite",
        ]
        method = (
            "per frozen topic query: recent-window mean coverage vs the topic's own "
            f"{SCUTTLEBUTT_MIN_BASELINE_D}d+ baseline (z), plus tone delta; 0-100 blend "
            "is presentation only (Correia-Luck-Verner / Cerchiello lineage)"
        )
    return {
        "ok": True,
        "asof": blob.get("asof") or asof or None,
        "source_mode": blob.get("mode") or "legacy-doc",
        "stale": bool(blob.get("stale")),
        "refresh_note": blob.get("refresh_note"),
        "baseline_ready": baseline_ready,
        "baseline_samples": n_samples,
        "baseline_required_samples": required_samples,
        "latest": {"loudest": rows[0]["label"], "loudest_attention": rows[0]["attention"],
                   "n_topics": len(rows), "n_flags": len(flags)},
        "topics": rows,
        "flags": flags,
        "caveats": caveats,
        "method": method,
    }
