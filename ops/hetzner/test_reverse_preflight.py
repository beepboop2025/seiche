"""Offline tests for refusal boundaries; never signal or contact a database."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "reverse_preflight", Path(__file__).with_name("reverse_preflight.py")
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def row(pid, ppid, args, uid=10001):
    return {"pid": pid, "ppid": ppid, "pgid": pid, "session": pid,
            "start_ticks": pid * 100, "state": "S", "uid": [uid] * 4,
            "gid": [uid] * 4, "argv": args}


def processes():
    return {
        1: row(1, 0, ["/usr/bin/tini", "--", "python"], 0),
        7: row(7, 1, ["python", "-I", "-B", "-c", preflight.SUPERVISOR_CODE], 0),
        10: row(10, 7, ["/usr/bin/python", "-m", "seiche.cli", "market-worker",
                       "--poll-seconds", "30"]),
        11: row(11, 7, ["/usr/bin/python", "-m", "seiche.cli", "source-worker",
                       "--poll-seconds", "300"]),
        12: row(12, 7, ["/usr/bin/python", "-m", "uvicorn", "seiche.api:app",
                       "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]),
    }


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, database="seiche_test", readonly="on", visible=True):
        self.calls = []
        self.identity = (database, 180006, readonly, False, 10000, 77, visible)
        self.tables = [("public", "canonical_observations", "r", 8192, 4, False)]
        self.sessions = [
            (77, True, "client backend", "active", "2026-10-20 18:00:00+00", False, False, True),
            (80, False, "client backend", "idle", None, False, True, True),
        ]

    def execute(self, query):
        self.calls.append(query)
        if query == preflight.IDENTITY_SQL:
            return Result([self.identity])
        if query == preflight.TABLE_SQL:
            return Result(self.tables)
        if query == preflight.SESSION_SQL:
            return Result(self.sessions)
        if query == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return Result([])
        raise AssertionError("unexpected query")


class PreflightTests(unittest.TestCase):
    def test_exact_parent_rejects_other_replica_and_partial_identity(self):
        env = {**preflight.PARENT, "SEICHE_RAILWAY_STATEFUL_MODE": "production",
               "SEICHE_RAILWAY_APPLICATION_REQUEST_ID": "a" * 64}
        preflight.exact_identity(env)
        for key in preflight.PARENT:
            bad = {**env, key: "changed"}
            with self.subTest(key=key), self.assertRaises(preflight.PreflightError):
                preflight.exact_identity(bad)

    def test_process_stat_parentheses_and_spaces(self):
        fields = ["S", "1", "7", "7"] + ["0"] * 15 + ["12345"] + ["0"] * 10
        actual = preflight.parse_stat("7 (name (with) spaces) " + " ".join(fields))
        self.assertEqual(actual["start_ticks"], 12345)
        self.assertEqual(actual["ppid"], 1)

    def test_expected_process_tree_only_models_hold(self):
        selected, unsupported = preflight.topology(processes())
        self.assertEqual(set(selected), {"tini", "supervisor", "api", "market", "source"})
        self.assertEqual(unsupported, [])
        self.assertNotEqual(selected["supervisor"]["pgid"], selected["api"]["pgid"])

    def test_missing_duplicate_or_foreign_supervisor_fails(self):
        for variant in ("missing", "duplicate", "foreign_parent"):
            rows = processes()
            if variant == "missing":
                del rows[7]
            elif variant == "duplicate":
                rows[8] = {**rows[7], "pid": 8}
            else:
                rows[7]["ppid"] = 99
            with self.subTest(variant=variant), self.assertRaises(preflight.PreflightError):
                preflight.topology(rows)

    def test_wrong_uid_and_shared_process_group_fail(self):
        for field, value in (("uid", [0] * 4), ("pgid", 7)):
            rows = processes()
            rows[12][field] = value
            with self.subTest(field=field), self.assertRaises(preflight.PreflightError):
                preflight.topology(rows)

    def test_unexpected_writer_or_descendant_is_unsupported(self):
        rows = processes()
        rows[13] = row(13, 12, ["python", "unknown-writer.py"])
        _, unsupported = preflight.topology(rows)
        self.assertIn("additional_runtime_uid_process", unsupported)
        self.assertIn("runtime_descendant_present", unsupported)

    def test_known_children_do_not_hide_extra_export(self):
        rows = processes()
        rows[14] = row(14, 7, ["pg_dump", "--dbname=SECRET"], 0)
        _, unsupported = preflight.topology(rows)
        self.assertIn("unexpected_supervisor_child", unsupported)
        self.assertNotIn("SECRET", json.dumps(unsupported))

    def test_database_is_readonly_metadata_with_sessions_and_all_tables(self):
        connection = Connection()
        actual, unsupported = preflight.inspect_database(connection, "seiche_test")
        self.assertEqual(unsupported, [])
        self.assertEqual(connection.calls[0],
                         "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertTrue(actual["transaction_read_only"])
        self.assertFalse(actual["no_loss_proven"])
        self.assertEqual(len(actual["sessions"]), 2)
        self.assertEqual(actual["tables"][0]["table"], "canonical_observations")
        self.assertNotIn("query", preflight.SESSION_SQL.lower())
        self.assertNotIn("usename,", preflight.SESSION_SQL.lower())

    def test_database_identity_or_readonly_mismatch_fails(self):
        for connection in (Connection(database="other"), Connection(readonly="off")):
            with self.assertRaises(preflight.PreflightError):
                preflight.inspect_database(connection, "seiche_test")

    def test_database_visibility_and_foreign_tables_block_completion(self):
        connection = Connection(visible=False)
        connection.tables.append(("public", "external", "f", 0, -1, False))
        _, unsupported = preflight.inspect_database(connection, "seiche_test")
        self.assertIn("database_session_visibility_incomplete", unsupported)
        self.assertIn("foreign_table_or_row_security_requires_review", unsupported)

    def test_table_and_session_bounds_fail(self):
        for name, limit in (("tables", preflight.MAX_TABLES), ("sessions", preflight.MAX_SESSIONS)):
            connection = Connection()
            setattr(connection, name, getattr(connection, name) * (limit + 1))
            with self.subTest(name=name), self.assertRaises(preflight.PreflightError):
                preflight.inspect_database(connection, "seiche_test")

    def test_unsafe_identifiers_are_not_printed(self):
        connection = Connection()
        connection.tables[0] = ("public", "secret\nmalformed", "r", 1, 1, False)
        with self.assertRaisesRegex(preflight.PreflightError, "database_identifier_unsupported"):
            preflight.inspect_database(connection, "seiche_test")

    def test_output_process_inventory_excludes_commands_credentials_and_environment(self):
        value = row(12, 7, ["python", "--password=SECRET"])
        value["environment"] = {"DATABASE_URL": "SECRET"}
        safe = json.dumps(preflight.safe_process(value))
        self.assertNotIn("SECRET", safe)
        self.assertNotIn("argv", safe)

    def test_mutation_and_signal_audit_boundaries(self):
        for event, arguments in (
            ("os.kill", (1, 19)), ("os.killpg", (7, 19)),
            ("os.rename", ("a", "b", -1, -1)),
            ("open", ("/tmp/x", "wb", os.O_WRONLY | os.O_CREAT)),
            ("open", ("/tmp/x", None, os.O_RDWR)),
            ("subprocess.Popen", ("sh", ["sh", "-c", "true"], None, None)),
        ):
            with self.subTest(event=event), self.assertRaises(preflight.PreflightError):
                preflight.readonly_audit(event, arguments)
        preflight.readonly_audit("open", ("/proc/1/stat", "r", os.O_RDONLY))

    def test_subprocess_allowlist_rejects_git_mutations(self):
        preflight.readonly_audit("subprocess.Popen",
                                ("git", ["git", "-C", "/workspace", "rev-parse", "HEAD"], None, None))
        with self.assertRaises(preflight.PreflightError):
            preflight.readonly_audit("subprocess.Popen",
                                    ("git", ["git", "-C", "/workspace", "reset", "--hard"], None, None))

    def test_signature_verifier_uses_only_anonymous_descriptors(self):
        signature = "-----BEGIN SSH SIGNATURE-----\nZmFrZQ==\n-----END SSH SIGNATURE-----\n"

        def verify(argv, **kwargs):
            preflight.readonly_audit("subprocess.Popen", (argv[0], argv, None, None))
            self.assertEqual(kwargs["pass_fds"], (101, 102))
            self.assertIn("/proc/self/fd/101", argv)
            self.assertIn("/proc/self/fd/102", argv)
            self.assertEqual(kwargs["input"], b"message")
            return SimpleNamespace(returncode=0)

        with patch.object(preflight.os, "memfd_create", side_effect=[101, 102], create=True), \
             patch.object(preflight.os, "MFD_CLOEXEC", 1, create=True), \
             patch.object(preflight.os, "write", side_effect=lambda _fd, body: len(body)), \
             patch.object(preflight.os, "lseek"), \
             patch.object(preflight.os, "close") as close, \
             patch.object(preflight.subprocess, "run", side_effect=verify):
            preflight.verify_signature_memory(b"message", signature, "ssh-ed25519 fake")
        self.assertEqual(close.call_count, 2)

    def test_bad_signature_is_rejected_and_both_descriptors_close(self):
        signature = "-----BEGIN SSH SIGNATURE-----\nZmFrZQ==\n-----END SSH SIGNATURE-----\n"
        with patch.object(preflight.os, "memfd_create", side_effect=[101, 102], create=True), \
             patch.object(preflight.os, "MFD_CLOEXEC", 1, create=True), \
             patch.object(preflight.os, "write", side_effect=lambda _fd, body: len(body)), \
             patch.object(preflight.os, "lseek"), \
             patch.object(preflight.os, "close") as close, \
             patch.object(preflight.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(preflight.PreflightError, "signature_invalid"):
                preflight.verify_signature_memory(b"message", signature, "ssh-ed25519 fake")
        self.assertEqual(close.call_count, 2)

    def test_authority_pointer_change_mid_observation_is_rejected(self):
        body = b'{"state":"active"}\n'
        expected = preflight.hashlib.sha256(body).hexdigest()
        authority = {"request_id": "a" * 64, "request_sha256": expected,
                     "pointer_sha256": expected, "grant_sha256": expected,
                     "activation_sha256": expected}
        with patch.object(preflight, "read_bounded", return_value=body):
            preflight.recheck_authority(authority)
        with patch.object(preflight, "read_bounded",
                          side_effect=[body, b'{"state":"superseded"}\n']):
            with self.assertRaisesRegex(preflight.PreflightError, "authority_changed"):
                preflight.recheck_authority(authority)

    def test_cli_does_not_expose_exception_credentials(self):
        # Audit installation is isolated from the test runner.
        program = (
            "import importlib.util;"
            f"s=importlib.util.spec_from_file_location('p',{str(Path(preflight.__file__))!r});"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "m.run=lambda:(_ for _ in ()).throw(RuntimeError('postgres://SECRET'));"
            "raise SystemExit(m.main())"
        )
        completed = subprocess.run([sys.executable, "-B", "-c", program],
                                   text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn("SECRET", completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["hold"]["status"], "UNQUALIFIED")
        self.assertFalse(result["mutation_performed"])

    def test_live_driver_rejects_extra_arguments(self):
        completed = subprocess.run(
            [sys.executable, "-B", preflight.__file__, "--apply"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["error"], "arguments_not_supported")


if __name__ == "__main__":
    unittest.main()
