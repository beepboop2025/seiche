import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "publisher", Path(__file__).with_name("publish.py")
)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublisherBoundaryTests(unittest.TestCase):
    def test_builder_environment_never_inherits_credentials(self):
        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_API_TOKEN": "test-only",
                "SITE_DEPLOY_KEY": "test-only",
                "GH_TOKEN": "test-only",
            },
        ):
            env = publisher.clean_env()
        self.assertFalse(
            any(
                key in env
                for key in ("CLOUDFLARE_API_TOKEN", "SITE_DEPLOY_KEY", "GH_TOKEN")
            )
        )
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "/dev/null")

    def test_symlink_cannot_enter_publication_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, destination = root / "source", root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "index.html").write_text("public")
            (source / "leak").symlink_to("/etc/passwd")
            with self.assertRaises(RuntimeError):
                publisher.copy_public_tree(source, destination)

    def test_hardlink_cannot_enter_publication_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, destination = root / "source", root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "index.html").write_text("public")
            os.link(source / "index.html", source / "duplicate")
            with self.assertRaises(RuntimeError):
                publisher.copy_public_tree(source, destination)

    def test_publication_root_and_ancestor_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            secret = root / "secret"
            secret.mkdir()
            (secret / "index.html").write_text("private")
            link = root / "linked"
            link.symlink_to(secret, target_is_directory=True)
            out = root / "out"
            out.mkdir()
            with self.assertRaises(RuntimeError):
                publisher.copy_public_tree(link, out)
            child = secret / "child"
            child.mkdir()
            (child / "index.html").write_text("private")
            with self.assertRaises(RuntimeError):
                publisher.copy_public_tree(link / "child", out)

    def test_history_rejects_parent_symlink_and_non_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            secret = root / "secret"
            secret.mkdir()
            (secret / "data.json").write_text('{"private":true}')
            link = root / "linked"
            link.symlink_to(secret, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                publisher.copy_history(link / "data.json", root / "out")
            (root / "bad.json").write_text("not JSON")
            with self.assertRaises(ValueError):
                publisher.copy_history(root / "bad.json", root / "out")

    @unittest.skipUnless(
        sys.platform == "linux" and os.geteuid() == 0,
        "Real UID confinement is verified in the Linux image build",
    )
    def test_detached_builder_cannot_mutate_candidate_after_quiescence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            root.chmod(0o777)
            ready, late = root / "ready", root / "late"
            code = (
                "import os,time,pathlib; os.setsid(); pathlib.Path(%r).write_text('ready'); time.sleep(5); pathlib.Path(%r).symlink_to('/etc/passwd')"
                % (str(ready), str(late))
            )
            child = subprocess.Popen(
                [
                    "setpriv",
                    "--reuid=10001",
                    "--regid=10001",
                    "--init-groups",
                    "--no-new-privs",
                    sys.executable,
                    "-c",
                    code,
                ]
            )
            try:
                for _ in range(100):
                    if ready.exists():
                        break
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                # Reap our direct child while production PID1 reaps orphaned descendants.
                import threading

                reaper = threading.Thread(target=child.wait)
                reaper.start()
                publisher.quiesce_builder()
                reaper.join(timeout=2)
                self.assertIsNotNone(child.poll())
                self.assertFalse(late.is_symlink())
                self.assertEqual(
                    subprocess.run(
                        ["pgrep", "-u", "10001"], capture_output=True
                    ).returncode,
                    1,
                )
            finally:
                child.kill() if child.poll() is None else None
                child.wait()

    def test_git_metadata_never_enters_static_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, destination = root / "source", root / "destination"
            source.mkdir()
            destination.mkdir()
            (source / ".git").mkdir()
            (source / ".git/config").write_text("private transport settings")
            (source / "index.html").write_text("public")
            publisher.copy_public_tree(source, destination)
            self.assertEqual(
                [path.name for path in destination.iterdir()], ["index.html"]
            )

    def test_sealed_candidate_stays_inside_release_root_without_replacing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            trusted, prepared = root / "trusted", root / "builder"
            trusted.mkdir()
            prepared.mkdir()
            source = trusted / "frontend/public/.well-known/ai-catalog.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"entries":[]}')
            catalog = prepared / ".well-known/ai-catalog.json"
            catalog.parent.mkdir()
            catalog.write_bytes(source.read_bytes())
            (prepared / "index.html").write_text("public")
            candidate = publisher.seal_candidate(prepared, trusted)
            self.assertEqual(candidate.parent, trusted)
            self.assertEqual(candidate.stat().st_mode & 0o777, 0o700)
            self.assertEqual(source.read_bytes(), b'{"entries":[]}')
            self.assertEqual((candidate / ".well-known/ai-catalog.json").read_bytes(), source.read_bytes())
            # The sealed artifact is independent of subsequent builder writes.
            catalog.write_text('{"untrusted":"late change"}')
            self.assertEqual((candidate / ".well-known/ai-catalog.json").read_bytes(), source.read_bytes())

    def test_original_catalog_gate_accepts_sealed_path_and_rejects_external_or_changed_bytes(self):
        candidates = (
            parent / "ops/release/verify_catalog_publication.py"
            for parent in Path(__file__).resolve().parents
        )
        gate = next((path for path in candidates if path.is_file()), None)
        if gate is None:
            self.skipTest("Original release verifier integration runs from the repository checkout")
        spec = importlib.util.spec_from_file_location("original_catalog_gate", gate)
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            trusted, prepared = root / "trusted", root / "builder"
            trusted.mkdir()
            prepared.mkdir()
            source = trusted / verifier.AI_CATALOG_PATH
            source.parent.mkdir(parents=True)
            source.write_text('{"entries":[]}')
            catalog = prepared / ".well-known/ai-catalog.json"
            catalog.parent.mkdir()
            catalog.write_bytes(source.read_bytes())
            (prepared / "index.html").write_text("public")
            with self.assertRaisesRegex(verifier.PublicationGateError, "escapes the release root"):
                verifier.verify_published_catalog(trusted, catalog)
            candidate = publisher.seal_candidate(prepared, trusted)
            sealed_catalog = candidate / ".well-known/ai-catalog.json"
            verifier.verify_published_catalog(trusted, sealed_catalog)
            sealed_catalog.write_text('{"entries":[{"modified":true}]}')
            with self.assertRaisesRegex(verifier.PublicationGateError, "bytes differ from source"):
                verifier.verify_published_catalog(trusted, sealed_catalog)


if __name__ == "__main__":
    unittest.main()
