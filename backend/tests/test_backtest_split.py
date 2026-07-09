"""The exogenous-vs-endogenous competence split, and the rigor tests
(threshold-free AUROC + permutation null)."""

import numpy as np
import pandas as pd

from seiche.config import EPISODE_CLASS, EPISODES
from seiche.engines.backtest import _class_split, _event_auroc, _significance


def test_every_episode_is_classified():
    assert set(EPISODE_CLASS) == set(EPISODES)          # no episode left untagged
    assert set(EPISODE_CLASS.values()) <= {"endogenous", "exogenous"}


def test_class_split_counts_recall_and_lead():
    rows = [
        {"date": "a", "class": "endogenous", "in_sample": True, "first_alert_lead_d": 42},
        {"date": "b", "class": "endogenous", "in_sample": True, "first_alert_lead_d": 30},
        {"date": "c", "class": "exogenous", "in_sample": True, "first_alert_lead_d": None},
        {"date": "d", "class": "exogenous", "in_sample": True, "first_alert_lead_d": None},
        {"date": "e", "class": "endogenous", "in_sample": False},   # OOS: excluded
    ]
    cs = _class_split(rows)
    assert cs["endogenous"] == {
        "n": 2, "caught": 2, "recall": 1.0, "median_lead_d": 36, "episodes": ["a", "b"]}
    assert cs["exogenous"]["n"] == 2 and cs["exogenous"]["caught"] == 0
    assert cs["exogenous"]["recall"] == 0.0
    assert cs["exogenous"]["median_lead_d"] is None
    assert "endogenous" in cs["reading"]


# ---- rigor: AUROC + permutation null -----------------------------------------

def _synthetic(alert_before: bool):
    """500 business days, 4 events. If alert_before, the signal spikes in the
    ~20 days before each event (skill); else it's random noise."""
    idx = pd.bdate_range("2020-01-01", periods=500)
    ev_pos = [100, 220, 340, 450]
    events = idx[ev_pos]
    if alert_before:
        pct = pd.Series(np.zeros(500), index=idx)
        for p in ev_pos:
            pct.iloc[p - 20:p] = 99.0
    else:
        pct = pd.Series(np.random.default_rng(3).uniform(0, 100, 500), index=idx)
    return pct, events


def test_skillful_signal_scores_high_and_is_significant():
    pct, events = _synthetic(alert_before=True)
    assert _event_auroc(pct, events) > 0.75          # clear threshold-free skill
    sig = _significance(pct, events, n_perm=500)
    assert sig["ok"] and sig["actual_recall"] == 1.0
    assert sig["p_value"] < 0.05                       # beats chance placement


def test_random_signal_is_weaker_and_not_significant():
    pct, events = _synthetic(alert_before=False)
    skill_au = _event_auroc(*_synthetic(alert_before=True))
    rand_au = _event_auroc(pct, events)
    assert rand_au < skill_au                          # noise scores below the real signal
    sig = _significance(pct, events, n_perm=500)
    assert sig["ok"] and sig["p_value"] > 0.05         # not distinguishable from chance
