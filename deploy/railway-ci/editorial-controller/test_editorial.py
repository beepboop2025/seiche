"""Adversarial native boundaries and generated-edition contracts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import editorial
from isolation import drop_privileges, read_regular


class PolicyTests(unittest.TestCase):
    def test_exact_date_and_lane_paths(self):
        permits = editorial.output_allowed
        self.assertTrue(permits("frontend/public/articles/2026-09-08-public-funding.md", "daily", "2026-09-08"))
        self.assertTrue(permits("backend/seiche/dispatches/2026-09-08-week-ahead.desk.md", "weekly", "2026-09-08"))
        for name in ("frontend/public/articles/2026-09-07-public-funding.md", "backend/seiche/api.py",
                     "frontend/public/articles/2026-09-08-../foo.md", ".github/workflows/ci.yml",
                     "frontend/public/articles/2026-09-08-public-funding.js"):
            self.assertFalse(permits(name, "daily", "2026-09-08"))
        self.assertFalse(permits("frontend/public/articles/index.json", "weekly", "2026-09-08"))

    def test_writer_environment_has_no_ambient_credentials(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "dummy", "RAILWAY_TOKEN": "dummy", "TELEGRAM_BOT_TOKEN": "dummy"}):
            result = editorial.clean_env(Path("/tmp/isolated"))
        self.assertNotIn("GITHUB_TOKEN", result)
        self.assertNotIn("RAILWAY_TOKEN", result)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", result)

    def test_symlink_and_hardlink_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "file").write_text("private")
            (root / "link").symlink_to(root / "file")
            with self.assertRaises(OSError):
                read_regular(root / "link")
            os.link(root / "file", root / "hard")
            with self.assertRaises(ValueError):
                read_regular(root / "hard")

    def test_publication_flag_rejected_before_any_remote_operation(self):
        with mock.patch.dict(os.environ, {"EDITORIAL_APPLY": "1"}), mock.patch.object(os, "geteuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "publication is gated"):
                editorial.main()

    def test_previous_index_entry_cannot_be_changed(self):
        old = [{"slug": "2026-09-07-daily", "date": "2026-09-07", "title": "As published"}]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path = root / "frontend/public/dispatches/index.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([{**old[0], "title": "Changed history"}]))
            with mock.patch.object(editorial, "git", return_value=json.dumps(old).encode()):
                with self.assertRaisesRegex(ValueError, "previously published"):
                    editorial.preserve_archive(root, None, {}, "a" * 40, "weekly", "2026-09-08")

    def test_wrong_date_index_addition_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            path = root / "frontend/public/dispatches/index.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([{"slug": "2026-09-09-daily", "date": "2026-09-09"}]))
            with mock.patch.object(editorial, "git", return_value=b"[]"):
                with self.assertRaisesRegex(ValueError, "wrong date"):
                    editorial.preserve_archive(root, None, {}, "a" * 40, "weekly", "2026-09-08")

    def test_superseded_parent_cannot_create_proposal(self):
        with mock.patch.object(editorial, "git", return_value=("b" * 40 + " refs/heads/main\n").encode()):
            with self.assertRaisesRegex(RuntimeError, "superseded"):
                editorial.proposal(None, {}, "a" * 40, {}, None, "daily", "2026-09-08", None)

    def test_forecast_probability_and_resolved_outcomes_are_immutable(self):
        before = {"date": "2026-09-01", "p": 0.4, "realized": None}
        editorial.preserve_forecasts([before], [{**before, "realized": True}], "2026-09-08")
        for after in ({**before, "p": 0.6}, {**before, "realized": 1}):
            with self.assertRaises(ValueError):
                editorial.preserve_forecasts([before], [after], "2026-09-08")
        with self.assertRaises(ValueError):
            editorial.preserve_forecasts([{**before, "realized": True}], [{**before, "realized": False}], "2026-09-08")


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0, "native root/UID boundary requires Linux container")
class NativeTests(unittest.TestCase):
    def test_runtime_cache_can_exceed_sealed_output_limit(self):
        with tempfile.TemporaryDirectory() as name:
            runtime = Path(name)
            os.chown(runtime, 65532, 65532)
            script = """import pathlib,sqlite3,sys
p=pathlib.Path(sys.argv[1])/'cache.sqlite'
with sqlite3.connect(p) as c:
 c.execute('PRAGMA journal_mode=WAL')
 c.execute('CREATE TABLE blobs(body BLOB)')
 c.execute('INSERT INTO blobs VALUES (zeroblob(34603008))')
 c.commit()
 assert c.execute('SELECT length(body) FROM blobs').fetchone()[0]==34603008
 c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
"""
            result = subprocess.run([sys.executable, "-c", script, name],
                                    preexec_fn=drop_privileges, env=editorial.clean_env(runtime),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with self.assertRaisesRegex(ValueError, "oversized output"):
                read_regular(runtime / "cache.sqlite")

    def test_candidate_cannot_read_parent_key_environment_or_fd_or_modify_code(self):
        with tempfile.TemporaryDirectory() as private, tempfile.TemporaryDirectory() as public:
            root, visible = Path(private), Path(public)
            visible.chmod(0o755)
            secret = root / "publisher-key"
            secret.write_text("dummy-private-key")
            secret.chmod(0o600)
            code = visible / "controller.py"
            code.write_text("reviewed")
            code.chmod(0o444)
            with secret.open() as fd:
                os.set_inheritable(fd.fileno(), True)
                script = """import os,sys
for path,mode in [(sys.argv[1],'r'),('/proc/'+sys.argv[2]+'/environ','r'),('/proc/'+sys.argv[2]+'/fd/'+sys.argv[3],'r'),(sys.argv[4],'w')]:
 try:
  open(path,mode).close()
 except PermissionError: pass
 else: raise SystemExit('candidate crossed private boundary')
try: os.fstat(int(sys.argv[3]))
except OSError: pass
else: raise SystemExit('root fd inherited')
assert os.getuid()==65532
assert 'GITHUB_TOKEN' not in os.environ
"""
                result = subprocess.run([sys.executable, "-c", script, str(secret), str(os.getpid()), str(fd.fileno()), str(code)],
                                        preexec_fn=drop_privileges, env=editorial.clean_env(visible), close_fds=True,
                                        capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(code.read_text(), "reviewed")

    def test_existing_edition_and_outside_source_are_immutable(self):
        with tempfile.TemporaryDirectory() as name:
            work = Path(name)
            work.chmod(0o755)
            for directory in editorial.CONTENT_DIRS:
                (work / directory).mkdir(parents=True)
            paths = {"frontend/public/dispatches/2026-09-07-daily.md": "old", "backend/seiche/api.py": "code"}
            for path, body in paths.items():
                (work / path).write_text(body)
                (work / path).chmod(0o444)
            editorial.allow_outputs(work, paths, "daily", "2026-09-08")
            code = """import pathlib,sys
r=pathlib.Path(sys.argv[1])
for name in ['frontend/public/dispatches/2026-09-07-daily.md','backend/seiche/api.py']:
 try: (r/name).write_text('changed')
 except PermissionError: pass
 else: raise SystemExit('immutable source modified')
(r/'frontend/public/dispatches/2026-09-08-daily.md').write_text('current')
"""
            result = subprocess.run([sys.executable, "-c", code, str(work)], env=editorial.clean_env(work), preexec_fn=drop_privileges)
            self.assertEqual(result.returncode, 0)
            baseline = {path: editorial.digest(body.encode()) for path, body in paths.items()}
            # Empty content directories are normal in this synthetic fixture.
            with mock.patch.object(editorial, "CONTENT_DIRS", ()):
                for path in (work / "frontend/public/articles", work / "backend/seiche/dispatches"):
                    path.rmdir()
                changed = editorial.seal_outputs(work, baseline, "daily", "2026-09-08", allow_content=True)
            self.assertEqual(set(changed), {"frontend/public/dispatches/2026-09-08-daily.md"})


if __name__ == "__main__":
    unittest.main()
