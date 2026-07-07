"""Rogue Wave — the tail law of the basin (POT/GPD extreme value theory).

PLACEHOLDER (v2.6 build in progress): the full engine — GPD fit by
probability-weighted moments on the declustered pop statistic, return
levels with bootstrap CIs, P(pop >= x within h) beyond the sample maximum —
lands in the next commit. Until then the engine reports itself down, the
house way: fail-loud, never a silent gap.
"""

from __future__ import annotations

import pandas as pd


def analyze(spread_bp: pd.Series) -> dict:
    return {"ok": False, "reason": "engine under construction (v2.6 build in progress)"}
