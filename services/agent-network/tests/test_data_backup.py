import hashlib
import json
from pathlib import Path
import sqlite3

import pytest


def test_learning_and_wake_restore_with_matching_config(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "ops"))
    import backup
    import data_backup
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    learning, wake = root / "learning.sqlite", root / "wake.sqlite"
    for path in (learning, wake):
        path.touch(mode=0o600)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, body TEXT)")
            db.execute("INSERT INTO sample VALUES (1,'synthetic-only')")
    secret = "a" * 40
    with sqlite3.connect(wake) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO metadata VALUES ('identity',?)", (hashlib.sha256(secret.encode()).hexdigest(),))
    config = root / "wake-config.json"
    backup._write_json(config, {"database": str(wake), "identity_secret": secret})
    mapping = root / "mapping.json"
    backup._write_json(mapping, {"databases": {"learning": str(learning), "wake": str(wake)}, "files": {"wake-config": str(config)}})
    key = root / "key.json"
    backup.initialize_backup_key(key)
    result = data_backup.backup(mapping, key, root / "backups")
    assert b"synthetic-only" not in Path(result["bundle"]).read_bytes()
    receipt = data_backup.restore(result["bundle"], key, root / "restored")
    assert receipt["status"] == "restored_offline"
    for name in ("learning", "wake"):
        with sqlite3.connect(root / "restored" / (name + ".sqlite")) as db:
            assert db.execute("SELECT body FROM sample").fetchone()[0] == "synthetic-only"
    restored = json.loads((root / "restored/wake-config.json").read_text())
    assert restored["database"] == str(root / "restored/wake.sqlite")
    assert restored["identity_secret"] == secret
    with pytest.raises(ValueError, match="new offline"):
        data_backup.restore(result["bundle"], key, root / "restored")
    bad = root / "tampered.sanbackup"
    value = bytearray(Path(result["bundle"]).read_bytes())
    value[-1] ^= 1
    bad.write_bytes(value)
    bad.chmod(0o600)
    with pytest.raises(Exception):
        data_backup.restore(bad, key, root / "bad-restore")
    assert not (root / "bad-restore").exists()
