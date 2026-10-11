import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from seiche_agent_network.crypto import generate_keys
from seiche_agent_network.store import Store

spec = importlib.util.spec_from_file_location("network_backup", Path(__file__).parents[1] / "ops" / "backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@pytest.fixture
def source(tmp_path):
    os.chmod(tmp_path, 0o700)
    keys = generate_keys()
    key_file = tmp_path / "keys.json"
    backup._write_json(key_file, keys)
    backup_key = tmp_path / "backup-key.json"
    backup.initialize_backup_key(backup_key)
    store = Store(tmp_path / "source.sqlite3", **keys)
    principals, tokens = [], []
    for label in ("Synthetic source", "Synthetic peer"):
        owner = store.authenticate(store.bootstrap_org(label)["owner_token"])
        token = store.register_agent(owner, label)["token"]
        tokens.append(token)
        principals.append(store.authenticate(token))
    invitation = store.invite_contact(principals[0], principals[1].agent_id)
    store.accept_contact(principals[1], invitation["id"])
    room = store.create_room(principals[0], "Synthetic backup proof", [principals[1].agent_id])
    store.accept_room(principals[1], room["id"])
    message = store.send_message(principals[0], room["id"], "question", {"text": "synthetic private backup evidence"}, "backup:test")
    yield dict(root=tmp_path, store=store, keys=keys, key_file=key_file, backup_key=backup_key, tokens=tokens, room=room, message=message)
    store.close()


def make_backup(source):
    return backup.backup(str(source["root"] / "source.sqlite3"), source["key_file"], source["backup_key"], source["root"] / "backups")


def test_encrypted_snapshot_restores_private_payload_credentials_and_chain(source):
    made = make_backup(source)
    blob = Path(made["bundle"]).read_bytes()
    assert b"synthetic private backup evidence" not in blob
    assert source["keys"]["encryption_key"].encode() not in blob
    assert source["tokens"][0].encode() not in blob
    target, keys = source["root"] / "restored.sqlite3", source["root"] / "restored-keys.json"
    result = backup.restore(str(target), made["bundle"], source["backup_key"], keys)
    assert result["status"] == "restored_offline"
    reopened = Store(target, **json.loads(keys.read_text()))
    try:
        p = reopened.authenticate(source["tokens"][1])
        message = reopened.poll_deliveries(p)["items"][0]["message"]
        assert message["id"] == source["message"]["id"]
        assert message["payload"]["text"] == "synthetic private backup evidence"
        assert reopened.verify_room(p, source["room"]["id"])["valid"]
    finally:
        reopened.close()
    assert (target.stat().st_mode & 0o777) == 0o600
    assert (keys.stat().st_mode & 0o777) == 0o600


def test_tampered_archive_and_wrong_key_never_create_restore_outputs(source):
    made = make_backup(source)
    bundle = Path(made["bundle"])
    data = bytearray(bundle.read_bytes())
    data[len(data) // 2] ^= 1
    corrupt = source["root"] / "corrupt.sanbackup"
    corrupt.write_bytes(data)
    corrupt.chmod(0o600)
    for chosen_key, chosen_bundle in ((source["backup_key"], corrupt),):
        with pytest.raises(Exception):
            backup.restore(str(source["root"] / "bad.sqlite3"), chosen_bundle, chosen_key, source["root"] / "bad-keys.json")
    assert not (source["root"] / "bad.sqlite3").exists()
    assert not (source["root"] / "bad-keys.json").exists()
    wrong = source["root"] / "wrong-backup-key.json"
    backup.initialize_backup_key(wrong)
    with pytest.raises(Exception):
        backup.restore(str(source["root"] / "bad.sqlite3"), bundle, wrong, source["root"] / "bad-keys.json")
    assert not (source["root"] / "bad.sqlite3").exists()


def test_mismatch_source_keys_existing_database_and_keys_are_refused(source):
    wrong = source["root"] / "wrong-service-keys.json"
    backup._write_json(wrong, generate_keys())
    with pytest.raises(ValueError):
        backup.backup(str(source["root"] / "source.sqlite3"), wrong, source["backup_key"], source["root"] / "backups")
    made = make_backup(source)
    before = (source["root"] / "source.sqlite3").read_bytes()
    with pytest.raises(ValueError):
        backup.restore(str(source["root"] / "source.sqlite3"), made["bundle"], source["backup_key"], source["root"] / "new-keys.json")
    assert before == (source["root"] / "source.sqlite3").read_bytes()
    with pytest.raises(ValueError):
        backup.restore(str(source["root"] / "new.sqlite3"), made["bundle"], source["backup_key"], source["key_file"])
    assert not (source["root"] / "new.sqlite3").exists()


def test_cli_output_does_not_print_database_credentials(source, capsys):
    config = source["root"] / "database.json"
    backup._write_json(config, {"database_url": "postgresql://user:do-not-print-this@127.0.0.1:1/db"})
    assert backup.main(["backup", "--database-config", str(config), "--keys-file", str(source["key_file"]),
                        "--backup-key-file", str(source["backup_key"]), "--destination", str(source["root"] / "backups")]) == 2
    output = capsys.readouterr()
    assert "do-not-print-this" not in output.out + output.err


def test_bounded_pruning_removes_only_matching_owned_expired_artifacts(source):
    made = make_backup(source)
    receipt = Path(made["receipt"])
    value = json.loads(receipt.read_text())
    value["created_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    receipt.write_text(json.dumps(value))
    unrelated = receipt.parent / "unrelated-document.json"
    unrelated.write_text("keep this")
    malformed = receipt.parent / "backup-20200101T000000Z-aaaaaaaaaaaa.json"
    malformed.write_text("incomplete")
    malformed.chmod(0o600)
    assert backup._prune(receipt.parent, 7, None) == 1
    assert not Path(made["bundle"]).exists()
    assert unrelated.read_text() == "keep this"
    assert malformed.exists()
