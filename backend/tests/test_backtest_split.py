"""The exogenous-vs-endogenous competence split in the PROOF backtest."""

from seiche.config import EPISODE_CLASS, EPISODES
from seiche.engines.backtest import _class_split


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
