"""Communiqué — the policy text read as plumbing data.

The FOMC statement is written to be parsed; every desk parses it. This
engine scores each statement with a DETERMINISTIC lexicon (fixed word lists
in config — reproducible forever, no model drift, vintage-safe), because a
scorer that changes under your feet cannot sit under a backtest. An LLM
reading is welcome as enrichment via the desk assistant; the NUMBERS come
from the lexicon.

Three scores per statement, each net counts per 1,000 words:
  hawk_score      hawkish minus dovish lexicon hits (policy direction)
  bs_tighten      balance-sheet tightening minus easing hits (QT/QE bias —
                  the score that matters for reserves)
  stress_score    funding/liquidity-stress vocabulary (the Fed naming the
                  problem is itself a confession — cf. Sep 2019 statements)

Deltas against the PREVIOUS statement are the signal (the market trades the
change, not the level); a rolling z puts them on the board. Context engine:
never weighted into the composite — text is narrative evidence, not
plumbing evidence.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from seiche.config import (
    COMMUNIQUE_LEXICON_BS_EASE,
    COMMUNIQUE_LEXICON_BS_TIGHT,
    COMMUNIQUE_LEXICON_DOVISH,
    COMMUNIQUE_LEXICON_HAWKISH,
    COMMUNIQUE_LEXICON_STRESS,
)


def _count(text: str, phrases: tuple[str, ...]) -> int:
    t = text.lower()
    return sum(len(re.findall(r"\b" + re.escape(p.lower()) + r"\b", t)) for p in phrases)


def score_text(text: str) -> dict:
    """Deterministic per-1,000-word lexicon scores for one statement."""
    words = max(len(text.split()), 1)
    k = 1000.0 / words
    return {
        "hawk_score": round((_count(text, COMMUNIQUE_LEXICON_HAWKISH)
                             - _count(text, COMMUNIQUE_LEXICON_DOVISH)) * k, 2),
        "bs_tighten": round((_count(text, COMMUNIQUE_LEXICON_BS_TIGHT)
                             - _count(text, COMMUNIQUE_LEXICON_BS_EASE)) * k, 2),
        "stress_score": round(_count(text, COMMUNIQUE_LEXICON_STRESS) * k, 2),
        "n_words": words,
    }


def analyze(texts: dict[str, str]) -> dict:
    """texts: decision date (YYYY-MM-DD) -> statement text."""
    if not texts:
        return {"ok": False, "reason": "no statements fetched (fedtext coverage 0 — see faults)"}
    rows = []
    for d in sorted(texts):
        rows.append({"date": d, **score_text(texts[d])})
    df = pd.DataFrame(rows)

    for col in ("hawk_score", "bs_tighten", "stress_score"):
        df[f"{col}_chg"] = df[col].diff().round(2)

    last = df.iloc[-1].to_dict()
    hist = df.iloc[:-1]
    flags = []
    for col, label in (
        ("stress_score", "funding-stress vocabulary"),
        ("bs_tighten", "balance-sheet-tightening language"),
    ):
        if len(hist) >= 6:
            med = float(hist[col].median())
            mad = float((hist[col] - med).abs().median()) * 1.4826
            # a perfectly quiet history degenerates the z (MAD=0) exactly when
            # the flag matters most — any positive break from flat is the news
            unusual = (
                (mad > 0 and (last[col] - med) / mad >= 2.0)
                or (mad == 0 and last[col] > med + 0.5)
            )
            if unusual:
                flags.append(f"{label} at {last[col]} vs median {med:.2f} — unusual for this Fed")

    return {
        "ok": True,
        "asof": df["date"].iloc[-1],
        "n_statements": len(df),
        "latest": {k: last[k] for k in (
            "date", "hawk_score", "bs_tighten", "stress_score",
            "hawk_score_chg", "bs_tighten_chg", "stress_score_chg")},
        "flags": flags,
        "series": df[["date", "hawk_score", "bs_tighten", "stress_score"]].to_dict("records"),
        "caveats": [
            "deterministic lexicon, not comprehension — sarcasm-proof, nuance-blind; word lists frozen in config",
            "statement text only (minutes lag 3 weeks and are out of scope v1)",
            "context engine: narrative is not plumbing evidence — never weighted into the composite",
        ],
        "method": (
            "per statement: net lexicon hits per 1,000 words (hawkish−dovish, "
            "BS-tighten−BS-ease, stress vocabulary); the CHANGE vs the previous "
            "statement is the signal; robust-z flags vs the statement's own history"
        ),
    }
