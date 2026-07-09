"""The notary: the tamper-evident record ledger.

Pure hash-chain tests — no network, no Bitcoin. The OpenTimestamps anchor is an
optional dependency and is exercised only for its graceful-absence behaviour.
"""

import sqlite3

import pytest

from seiche import notary


@pytest.fixture()
def led(tmp_path, monkeypatch):
    monkeypatch.setattr(notary, "DB_PATH", tmp_path / "notary.sqlite")
    return notary


R1 = {"date": "2026-07-08", "value": 44.0, "regime": "STRAIN"}
R2 = {"date": "2026-07-09", "value": 45.7, "regime": "STRAIN"}
R3 = {"date": "2026-07-10", "value": 46.0, "regime": "STRAIN"}


# ---- digest ------------------------------------------------------------------

def test_digest_is_deterministic_regardless_of_key_order():
    a = notary.canonical_digest({"value": 46.0, "regime": "STRAIN", "date": "2026-07-10"})
    b = notary.canonical_digest({"date": "2026-07-10", "regime": "STRAIN", "value": 46.0})
    assert a == b == notary.canonical_digest(R3)


def test_digest_changes_when_content_changes():
    assert notary.canonical_digest(R2) != notary.canonical_digest(
        {**R2, "value": 45.8})


# ---- chain -------------------------------------------------------------------

def test_first_commit_links_to_genesis(led):
    c = led.commit("2026-07-08", R1)
    assert c["new"] is True
    assert c["prev_hash"] == led.GENESIS
    assert c["chain_hash"] == led.chain_hash(led.GENESIS, c["record_sha256"],
                                             c["utc"], "2026-07-08")


def test_chain_links_each_to_the_previous(led):
    a = led.commit("2026-07-08", R1)
    b = led.commit("2026-07-09", R2)
    assert b["prev_hash"] == a["chain_hash"]
    assert led.head() == b["chain_hash"]


def test_commit_is_idempotent_on_identical_content(led):
    a = led.commit("2026-07-09", R2)
    again = led.commit("2026-07-09", R2)
    assert again["new"] is False
    assert again["seq"] == a["seq"]
    assert len(led.entries()) == 1


def test_changed_reading_appends_a_new_link(led):
    led.commit("2026-07-09", R2)
    led.commit("2026-07-09", {**R2, "value": 45.9})   # intraday revision
    assert len(led.entries()) == 2                     # both states recorded


# ---- verification ------------------------------------------------------------

def test_verify_clean_chain(led):
    for d, r in [("2026-07-08", R1), ("2026-07-09", R2), ("2026-07-10", R3)]:
        led.commit(d, r)
    v = led.verify_chain()
    assert v["ok"] is True and v["n"] == 3


def test_verify_detects_a_tampered_reading(led):
    led.commit("2026-07-08", R1)
    led.commit("2026-07-09", R2)
    led.commit("2026-07-10", R3)
    # forge history: rewrite the middle reading's digest in place
    conn = sqlite3.connect(led.DB_PATH)
    conn.execute("UPDATE notary_log SET record_sha256=? WHERE seq=2",
                 (notary.canonical_digest({**R2, "value": 99.0}),))
    conn.commit()
    conn.close()
    v = led.verify_chain()
    assert v["ok"] is False and v["break_at"] == 2


def test_verify_detects_a_deleted_reading(led):
    led.commit("2026-07-08", R1)
    led.commit("2026-07-09", R2)
    led.commit("2026-07-10", R3)
    conn = sqlite3.connect(led.DB_PATH)
    conn.execute("DELETE FROM notary_log WHERE seq=2")   # try to erase a call
    conn.commit()
    conn.close()
    v = led.verify_chain()
    assert v["ok"] is False   # seq 3 no longer links to seq 1


# ---- OpenTimestamps (optional) ----------------------------------------------

def test_stamp_pending_is_safe_without_the_library(led, monkeypatch):
    monkeypatch.setattr(led, "ots_available", lambda: False)
    led.commit("2026-07-09", R2)
    r = led.stamp_pending()
    assert r["ok"] is False and "opentimestamps" in r["reason"]
    # the chain is unaffected — the reading is still committed, just unanchored
    assert led.entries()[0]["anchored"] is False
    assert led.verify_chain()["ok"] is True


# ---- public endpoints --------------------------------------------------------

def test_notary_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from seiche import api
    monkeypatch.setattr(notary, "DB_PATH", tmp_path / "n.sqlite")
    c = TestClient(api.app)
    notary.commit("2026-07-09", R2)
    r = c.get("/api/notary")
    assert r.status_code == 200 and r.json()["chain"]["ok"] is True
    assert r.json()["entries"]                                   # the committed reading shows
    assert c.get("/api/notary/proof/not-hex").status_code == 422
    assert c.get("/api/notary/proof/" + "a" * 64).status_code == 404   # valid form, no proof yet
