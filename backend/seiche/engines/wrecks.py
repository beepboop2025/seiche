"""Wrecks — the crypto shipwrecks, read against the funding board.

Six labelled crypto stress episodes, each replayed point-in-time at T-21,
T-10, T-5, T-1 and T-0 business days before the anchor date, using the same
Time Machine as everything else (no lookahead). The honest question splits
two ways:

* EXTERNAL wrecks (COVID's Black Thursday, the SVB weekend, the 2025 tariff
  cascade) originated outside crypto. The question: was the dollar-funding
  board also under strain as crypto broke? That is the transmission
  evidence.
* CRYPTO-NATIVE wrecks (Terra, FTX, the Ethena carry unwind) originated
  inside crypto. The board SHOULD stay quiet — these did not run through the
  funding plumbing, and a board that fires on everything means nothing.
  Quiet here is specificity, not a miss.

Same anti-rigging rules as PROOF: the episode list and classes are fixed in
config (not fitted), replays are final-vintage (stated), and the numbers
publish whether or not they flatter.
"""

from __future__ import annotations

import pandas as pd

from seiche.config import (
    CRYPTO_EPISODE_APPROX,
    CRYPTO_EPISODE_CLASS,
    CRYPTO_EPISODES,
    WRECKS_BLOB_KEY,
    WRECKS_OFFSETS_BD,
)

REGIME_ORDER = {"CALM": 0, "EROSION": 1, "STRAIN": 2, "STRESS": 3}


def offset_dates(anchor: str, offsets: list[int] | None = None) -> list[tuple[int, str]]:
    """(offset_bd, ISO date) pairs for the replay ladder before `anchor`."""
    ts = pd.Timestamp(anchor)
    out = []
    for k in offsets or WRECKS_OFFSETS_BD:
        d = ts if k == 0 else ts - pd.tseries.offsets.BDay(k)
        out.append((k, d.date().isoformat()))
    return out


def _reading(cls: str, elevated: bool, peak: str | None) -> str:
    if peak is None:
        return "no board coverage in the window — reported, not filled in"
    if cls == "external":
        if elevated:
            return (f"the funding board was off CALM (peak {peak}) as this "
                    "broke — dollar-side strain coincident with the crypto "
                    "wreck, consistent with a shared channel")
        return ("the board stayed CALM: this shock reached crypto without "
                "visible funding-system strain — an honest non-signal")
    if elevated:
        return (f"the board read {peak} in the window — coincident dollar "
                "conditions; do not credit the board for a crypto-native "
                "failure it does not claim to see")
    return ("correctly quiet — this wreck was crypto-native, not plumbing; "
            "quiet is specificity")


def summarize(replays: dict[str, dict[int, dict | None]]) -> dict:
    """Pure summary over collected replays.

    `replays`: episode anchor date -> {offset_bd -> board read or None},
    where a board read is {"date", "value", "regime", "coverage_pct"}.
    """
    episodes = []
    for anchor, label in CRYPTO_EPISODES.items():
        cls = CRYPTO_EPISODE_CLASS[anchor]
        per = replays.get(anchor, {})
        board = []
        for k in WRECKS_OFFSETS_BD:
            read = per.get(k)
            row = {"offset_bd": k}
            row.update(read or {"date": None, "value": None, "regime": None})
            board.append(row)
        seen = [r for r in board if r.get("regime") in REGIME_ORDER]
        peak = (max((r["regime"] for r in seen), key=lambda g: REGIME_ORDER[g])
                if seen else None)
        elevated = peak is not None and REGIME_ORDER[peak] >= REGIME_ORDER["EROSION"]
        vals = {r["offset_bd"]: r.get("value") for r in board}
        first, last = vals.get(WRECKS_OFFSETS_BD[0]), vals.get(0)
        trend = (round(last - first, 1)
                 if first is not None and last is not None else None)
        episodes.append({
            "date": anchor,
            "episode": label,
            "class": cls,
            "date_approximate": anchor in CRYPTO_EPISODE_APPROX,
            "board": board,
            "peak_regime": peak,
            "board_elevated": elevated,
            "trend_21bd": trend,
            "reading": _reading(cls, elevated, peak),
        })

    ext = [e for e in episodes if e["class"] == "external" and e["peak_regime"]]
    nat = [e for e in episodes if e["class"] == "crypto_native" and e["peak_regime"]]
    summary = {
        "external_with_board_elevated": f"{sum(e['board_elevated'] for e in ext)}/{len(ext)}",
        "crypto_native_board_quiet": f"{sum(not e['board_elevated'] for e in nat)}/{len(nat)}",
        "reading": ("external wrecks test co-movement (dollar-side strain "
                    "should be visible); crypto-native wrecks test specificity "
                    "(the board should not be credited, and often was not "
                    "quiet by coincidence — the per-episode readings say "
                    "which). trend_21bd shows whether the board built into "
                    "the wreck or was merely at that level. Both columns "
                    "publish either way; this table claims context, not "
                    "leads."),
    }
    return {
        "episodes": episodes,
        "summary": summary,
        "offsets_bd": list(WRECKS_OFFSETS_BD),
        "caveats": [
            "replays are final-vintage; weekly aggregates are lightly revised vs what was on screens",
            "six episodes is a case table, not a statistic — no rate is claimed from it",
            "episode anchors and classes are fixed in config, not fitted to the outcome",
            "the Ethena anchor date is approximate: the unwind was gradual",
        ],
    }


async def collect(force: bool = False) -> dict:
    """Replay every episode ladder through the Time Machine and cache the
    summary blob. Individual replays are themselves blob-cached by assemble,
    so re-runs only pay for what is missing."""
    from seiche import assemble, store
    from seiche.sources.base import utcnow_iso

    if not force:
        cached = store.load_blob(WRECKS_BLOB_KEY)
        if cached is not None:
            return cached

    replays: dict[str, dict[int, dict | None]] = {}
    for anchor in CRYPTO_EPISODES:
        per: dict[int, dict | None] = {}
        for k, day in offset_dates(anchor):
            snap = await assemble.snapshot_asof(day)
            if not snap or snap.get("ok") is False:
                per[k] = None
                continue
            comp = (snap.get("engines") or {}).get("composite") or {}
            per[k] = {
                "date": day,
                "value": comp.get("value"),
                "regime": comp.get("regime"),
                "coverage_pct": comp.get("coverage_pct"),
            }
        replays[anchor] = per

    payload = summarize(replays)
    payload["generated_at"] = utcnow_iso()
    store.save_blob(WRECKS_BLOB_KEY, payload)
    return payload
