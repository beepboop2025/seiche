from types import SimpleNamespace

from seiche import funding_history as history


def test_public_history_obeys_rights_and_is_bounded(monkeypatch):
    keys = ["SOFR", "EFFR", "IORB", "TGCR", "UNRELATED"]
    rows = [dict(mnemonic=k, available=True, csv_restricted=None, json=f"/api/series/{k}") for k in keys]
    rows[1]["csv_restricted"] = True
    rows[2]["available"] = False
    monkeypatch.setattr(history.methodology, "series_index", lambda: {"series": rows})
    monkeypatch.setattr(history.methodology, "csv_restriction", lambda k: "restricted" if k == "TGCR" else None)
    loaded = []

    def load(key):
        loaded.append(key)

        def tail(n):
            assert n == 520
            return [["2026-09-29", 0.0]]

        return SimpleNamespace(provenance=lambda: {"mnemonic": key, "asof": "2026-09-29"}, tail_records=tail)

    monkeypatch.setattr(history.store, "load_series", load)
    result = history.public_histories("2026-10-01T08:25:52Z")
    assert loaded == ["SOFR"]
    assert result["generated_at"] == "2026-10-01T08:25:52Z"
    assert result["series"][0]["payload"]["points"] == [["2026-09-29", 0.0]]


def test_missing_history_is_not_published_as_zero(monkeypatch):
    monkeypatch.setattr(history.methodology, "series_index", lambda: {"series": [
        dict(mnemonic="SOFR", available=True, csv_restricted=None, json="/api/series/SOFR")
    ]})
    monkeypatch.setattr(history.methodology, "csv_restriction", lambda _: None)
    monkeypatch.setattr(history.store, "load_series", lambda _: None)
    assert history.public_histories("2026-10-01T08:25:52Z")["series"] == []
