import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import tarfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "publisher", Path(__file__).with_name("publish.py")
)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublisherBoundaryTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0,
                         "Builder identity and ancestry are verified in the Linux image")
    def test_builder_home_supports_private_unprivileged_runtime(self):
        home = publisher.builder_home()
        code = """
import os, pathlib, stat, tempfile
home = pathlib.Path(os.environ['HOME'])
assert os.getuid() == os.getgid() == 10001
with tempfile.TemporaryDirectory(dir=home) as raw:
    path = pathlib.Path(raw)
    for directory in (path, *path.parents):
        info = directory.lstat()
        assert stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
        assert info.st_uid == 0 or (info.st_uid, info.st_gid) == (10001, 10001)
        assert not stat.S_IMODE(info.st_mode) & 0o022
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    (path / 'probe').write_text('private')
print('PRIVATE_RUNTIME_OK')
"""
        result = publisher.run([sys.executable, "-I", "-S", "-c", code], home,
                               publisher.clean_env({"HOME": str(home)}),
                               unprivileged=True, capture=True)
        self.assertEqual(result, "PRIVATE_RUNTIME_OK")

    @unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0,
                         "Real filesystem ownership is verified in the Linux image")
    def test_builder_home_rejects_untrusted_paths_and_permissions(self):
        home = publisher.builder_home()
        for parent in (Path('/tmp'), home):
            with tempfile.TemporaryDirectory(dir=parent) as raw:
                path = Path(raw)
                os.chown(path, 10001, 10001)
                if parent == Path('/tmp'):
                    with self.assertRaisesRegex(RuntimeError, "unsafe ancestry"):
                        publisher.builder_home(path)
                else:
                    self.assertEqual(publisher.builder_home(path), path)
                    path.chmod(0o770)
                    with self.assertRaisesRegex(RuntimeError, "unsafe ancestry"):
                        publisher.builder_home(path)
                    path.chmod(0o700)
                    link = path / 'linked-home'
                    link.symlink_to(home, target_is_directory=True)
                    with self.assertRaisesRegex(RuntimeError, "unsafe ancestry"):
                        publisher.builder_home(link)

    def test_engine_tests_cannot_read_or_mutate_durable_history(self):
        environment = {"PATH": "/bin", "GDELT_WEB_HISTORY_FILE": "/durable/history.json"}
        for name in ("Engine tests (publish gates on green)", "Install backend",
                     "Test and build frontend (snapshot baked into dist/)"):
            with self.subTest(step=name):
                selected = publisher.build_step_env(name, environment, Path("/build/history.json"))
                self.assertEqual(selected, {"PATH": "/bin"})
        for name in ("Seed GDELT WEB-NGRAM baseline when cache is cold",
                     "Run engines, export snapshot"):
            with self.subTest(step=name):
                selected = publisher.build_step_env(name, environment, Path("/build/history.json"))
                self.assertEqual(selected, {"PATH": "/bin", "GDELT_WEB_HISTORY_FILE": "/build/history.json"})
        self.assertEqual(environment["GDELT_WEB_HISTORY_FILE"], "/durable/history.json")

    @unittest.skipUnless(sys.platform == "linux", "System interpreter is verified in the Linux image")
    def test_system_python_supports_isolated_deployment_helpers(self):
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-c", "import json,ssl,datetime; print('SYSTEM_PYTHON_OK')"],
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/untrusted", "PYTHONHOME": "/untrusted"},
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "SYSTEM_PYTHON_OK")

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



class DeskProjectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.current = self.root / "current"
        self.current.mkdir()
        publisher.git(["init", "--quiet", "-b", "main"], self.current)
        self.runtime = "backend/seiche/engine.py"
        self.state = "backend/seiche/dispatches/state.json"
        self.old = "backend/seiche/dispatches/old.desk.md"
        self.new = "frontend/public/dispatches/new.json"
        for name, data in ((self.runtime, "SIGNED_ENGINE"), (self.state, '{"n":1}'),
                           (self.old, "old desk"), ("frontend/src/main.ts", "SIGNED_FRONTEND")):
            path = self.current / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        self.engine_sha = self.commit()
        (self.current / self.state).write_text('{"n":2}')
        (self.current / self.old).unlink()
        (self.current / self.new).parent.mkdir(parents=True)
        (self.current / self.new).write_text('{"desk":"new"}')
        (self.current / "README.md").write_text("public onboarding")
        self.current_sha = self.commit()
        self.backend, self.build = self.root / "backend", self.root / "build"
        for checkout in (self.backend, self.build):
            publisher.git(["clone", "--quiet", "--no-hardlinks", str(self.current), str(checkout)], self.root)
            publisher.git(["checkout", "--quiet", "--detach", self.engine_sha], checkout)

    def commit(self):
        publisher.git(["add", "-A"], self.current)
        publisher.git(["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                       "commit", "--quiet", "--no-gpg-sign", "-m", "fixture"], self.current)
        return publisher.git(["rev-parse", "HEAD"], self.current)

    def admission(self):
        payload = {"entries": publisher.desk_tree(self.current, self.current_sha), "deletePaths": [self.old]}
        digest = hashlib.sha256((json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
        return {"currentSourceSha": self.current_sha,
                "sourceEquivalence": {"backendReleaseSha": self.engine_sha},
                "deskOverlay": {**payload, "sha256": digest}}

    def test_projection_uses_exact_desk_blobs_but_keeps_signed_engine_and_frontend(self):
        proof = publisher.apply_desk_projection(self.current, self.backend, self.build, self.admission())
        self.assertEqual((self.build / self.state).read_text(), '{"n":2}')
        self.assertEqual((self.build / self.new).read_text(), '{"desk":"new"}')
        self.assertFalse((self.build / self.old).exists())
        self.assertFalse((self.build / "README.md").exists())
        self.assertEqual((self.build / self.runtime).read_text(), "SIGNED_ENGINE")
        self.assertEqual((self.build / "frontend/src/main.ts").read_text(), "SIGNED_FRONTEND")
        self.assertEqual(publisher.git(["rev-parse", "HEAD"], self.build), self.engine_sha)
        self.assertEqual((self.backend / self.state).read_text(), '{"n":1}')
        self.assertEqual(publisher.git(["status", "--porcelain"], self.backend), "")
        self.assertEqual(proof["publicationSourceSha"], self.current_sha)
        self.assertEqual(proof["engineSourceSha"], self.engine_sha)
        self.assertEqual(proof["fileCount"], 2)
        self.assertEqual(proof["deletedCount"], 1)
        self.assertNotEqual(publisher.git(["status", "--porcelain"], self.build), "")

    def test_overlay_digest_object_mode_deletion_and_path_changes_are_rejected_before_mutation(self):
        for kind in ("digest", "object", "mode", "delete", "path"):
            with self.subTest(kind=kind):
                admission = self.admission()
                overlay = admission["deskOverlay"]
                if kind == "digest":
                    overlay["sha256"] = "0" * 64
                elif kind == "delete":
                    overlay["deletePaths"] = [self.runtime]
                else:
                    overlay["entries"][0][kind] = {"object": "0" * 40, "mode": "100755", "path": self.runtime}[kind]
                with self.assertRaisesRegex(RuntimeError, "Desk overlay differs"):
                    publisher.apply_desk_projection(self.current, self.backend, self.build, admission)
                self.assertEqual(publisher.git(["status", "--porcelain"], self.build), "")

    def test_dirty_or_wrong_projection_base_is_rejected(self):
        (self.build / self.runtime).write_text("unapproved")
        with self.assertRaisesRegex(RuntimeError, "not pristine"):
            publisher.apply_desk_projection(self.current, self.backend, self.build, self.admission())
        publisher.git(["checkout", "--force", "--detach", self.current_sha], self.build)
        with self.assertRaisesRegex(RuntimeError, "not the signed engine"):
            publisher.apply_desk_projection(self.current, self.backend, self.build, self.admission())

    def test_current_tree_executable_or_symlink_desk_entry_cannot_be_overlaid(self):
        (self.current / self.state).chmod(0o755)
        self.current_sha = self.commit()
        with self.assertRaisesRegex(RuntimeError, "nonexecutable regular data"):
            publisher.desk_tree(self.current, self.current_sha)
        (self.current / self.state).unlink()
        (self.current / self.state).symlink_to("../../engine.py")
        self.current_sha = self.commit()
        with self.assertRaisesRegex(RuntimeError, "nonexecutable regular data"):
            publisher.desk_tree(self.current, self.current_sha)

    def test_oversized_blob_stops_before_reading_or_writing(self):
        admission = self.admission()
        real_git = publisher.git

        def git(args, cwd, env=None):
            return str(8 * 1024 * 1024 + 1) if args[:2] == ["cat-file", "-s"] else real_git(args, cwd, env)

        with patch.object(publisher, "git", side_effect=git):
            with self.assertRaisesRegex(RuntimeError, "byte bound"):
                publisher.apply_desk_projection(self.current, self.backend, self.build, admission)
        self.assertEqual(publisher.git(["status", "--porcelain"], self.build), "")

class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.backend = Path(temporary.name) / "backend"
        self.backend.mkdir()
        self.engine_sha = "a" * 40

    def test_runtime_identity_checks_use_only_original_release_module_and_clean_environment(self):
        gates = self.backend / "ops/release"
        gates.mkdir(parents=True)
        for name in ("verify_frontend_publication.py", "verify_catalog_publication.py"):
            (gates / name).write_text("verified source")
        with (patch.dict(os.environ, {"GH_TOKEN": "never-pass", "SITE_DEPLOY_KEY": "never-pass"}),
              patch.object(publisher, "run", return_value='{"backendRuntimeSubject":{"sha":"signed"}}') as run):
            result = publisher.verify_runtime_identity(self.backend, self.engine_sha)
        self.assertEqual(result["backendRuntimeSubject"]["sha"], "signed")
        command, cwd, env = run.call_args.args
        self.assertEqual(command[5:7], [str(self.backend), self.engine_sha])
        self.assertEqual(Path(command[7]).parent, self.backend.parent)
        self.assertFalse(Path(command[7]).exists())
        self.assertEqual(cwd, self.backend)
        self.assertIn("module.RuntimeReceipts", command[4])
        self.assertIn("verify_market_corpus_receipts", command[4])
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("SITE_DEPLOY_KEY", env)


class SourceEquivalenceBoundaryTests(unittest.TestCase):
    def admission(self):
        return {
            "schema": "seiche.publication-source-admission.v1",
            "currentSourceSha": "d" * 40,
            "sourceEquivalence": {"sourceSha": "c" * 40, "controllerSourceSha": "a" * 40,
                                  "backendReleaseSha": "b" * 40},
            "deskOverlay": {"sha256": "e" * 64},
            "equivalentInputManifest": {"sha256": "f" * 64},
        }

    def controller(self, root):
        bundle = root / "bundle"
        bundle.mkdir()
        (bundle / "controller-source.json").write_text(json.dumps({
            "sha": "a" * 40, "engineSourceSha": "b" * 40, "engineGateSha256": {},
        }))
        (bundle / "gate-sha256.json").write_text("{}")
        gates = root / "controller-source/ops/release"
        gates.mkdir(parents=True)
        for name in ("verify_frontend_publication.py", "verify_catalog_publication.py"):
            (gates / name).write_text("verified source")
        (gates / "__pycache__").mkdir()
        (gates / "__pycache__/untrusted.pyc").write_bytes(b"ignored stale bytecode")
        return bundle

    def test_admission_executes_pinned_controller_with_three_separate_subjects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.controller(root)
            def checked_import(command, cwd, env, capture=False):
                fresh = Path(command[-1])
                self.assertEqual(fresh.parent, root)
                self.assertEqual(fresh.stat().st_mode & 0o777, 0o700)
                self.assertEqual(sorted(path.name for path in fresh.iterdir()),
                                 ["verify_catalog_publication.py", "verify_frontend_publication.py"])
                self.assertEqual((fresh / "verify_frontend_publication.py").read_text(), "verified source")
                return json.dumps(self.admission())
            with (patch.object(publisher, "CONTROLLER", bundle),
                  patch.dict(os.environ, {"PUBLICATION_EQUIVALENCE_TAG": "publication-source-equivalence-" + "c" * 40,
                                          "SITE_DEPLOY_KEY": "never-pass", "CLOUDFLARE_API_TOKEN": "never-pass"}),
                  patch.object(publisher, "git"),
                  patch.object(publisher, "verify_gate_files") as pinned,
                  patch.object(publisher, "current_main", return_value="d" * 40),
                  patch.object(publisher, "run", side_effect=checked_import) as execute):
                controller, backend, result = publisher.equivalent_sources(root, root / "current", "d" * 40, "public-fingerprint")
            self.assertEqual(result, self.admission())
            self.assertEqual([call.args[0] for call in pinned.call_args_list], [controller, backend])
            command, cwd, env = execute.call_args.args
            self.assertEqual(command[:3], ["python", "-I", "-S"])
            self.assertEqual(cwd, controller)
            self.assertEqual(command[5:8], [str(root / "current"), str(controller), str(backend)])
            self.assertNotIn("SITE_DEPLOY_KEY", env)
            self.assertNotIn("CLOUDFLARE_API_TOKEN", env)
            self.assertEqual(len({root / "current", controller, backend}), 3)

    def test_pin_failure_never_executes_admission_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (patch.object(publisher, "CONTROLLER", self.controller(root)),
                  patch.dict(os.environ, {"PUBLICATION_EQUIVALENCE_TAG": "publication-source-equivalence-" + "c" * 40}),
                  patch.object(publisher, "git"),
                  patch.object(publisher, "verify_gate_files", side_effect=RuntimeError("changed verifier")),
                  patch.object(publisher, "run") as execute):
                with self.assertRaisesRegex(RuntimeError, "changed verifier"):
                    publisher.equivalent_sources(root, root / "current", "d" * 40, "pin")
            execute.assert_not_called()

    def test_invalid_signature_wrong_subject_or_advanced_main_never_admitted(self):
        for change in ("signature", "head", "controller", "engine", "receipt", "advanced"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                value = self.admission()
                if change == "head":
                    value["currentSourceSha"] = "0" * 40
                for field, kind in (("controllerSourceSha", "controller"), ("backendReleaseSha", "engine"), ("sourceSha", "receipt")):
                    if change == kind:
                        value["sourceEquivalence"][field] = "0" * 40
                with (patch.object(publisher, "CONTROLLER", self.controller(root)),
                      patch.dict(os.environ, {"PUBLICATION_EQUIVALENCE_TAG": "publication-source-equivalence-" + "c" * 40}),
                      patch.object(publisher, "git"),
                      patch.object(publisher, "verify_gate_files"),
                      patch.object(publisher, "current_main", return_value=("0" if change == "advanced" else "d") * 40),
                      patch.object(publisher, "run", return_value=json.dumps(value),
                                   side_effect=RuntimeError("bad signature") if change == "signature" else None)):
                    with self.assertRaises(RuntimeError):
                        publisher.equivalent_sources(root, root / "current", "d" * 40, "pin")

    def test_missing_engine_pin_cannot_fall_back_to_current_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.controller(root)
            (bundle / "controller-source.json").write_text(json.dumps({"sha": "a" * 40}))
            with (patch.object(publisher, "CONTROLLER", bundle),
                  patch.dict(os.environ, {"PUBLICATION_EQUIVALENCE_TAG": "publication-source-equivalence-" + "c" * 40}),
                  patch.object(publisher, "git") as clone):
                with self.assertRaisesRegex(RuntimeError, "independently pinned"):
                    publisher.equivalent_sources(root, root / "current", "d" * 40, "pin")
            clone.assert_not_called()

    def test_changed_gate_bytes_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {}
            for name in publisher.GATES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("reviewed")
                manifest[name] = hashlib.sha256(b"reviewed").hexdigest()
            publisher.verify_gate_files(root, manifest)
            first = root / publisher.GATES[0]
            first.write_text("changed")
            with self.assertRaises(RuntimeError):
                publisher.verify_gate_files(root, manifest)
            first.unlink()
            outside = root / "outside"
            outside.write_text("reviewed")
            first.symlink_to(outside)
            with self.assertRaises(RuntimeError):
                publisher.verify_gate_files(root, manifest)

    def test_current_main_and_mirror_checks_use_publication_head(self):
        with (patch.object(publisher, "current_main", return_value="d" * 40),
              patch.object(publisher, "git", return_value="e" * 40 + "\trefs/heads/main")):
            publisher.require_current_publication("d" * 40, Path("/mirror"), "e" * 40)
            with self.assertRaisesRegex(RuntimeError, "Source main advanced"):
                publisher.require_current_publication("b" * 40, Path("/mirror"), "e" * 40)
            with self.assertRaisesRegex(RuntimeError, "compare-and-swap"):
                publisher.require_current_publication("d" * 40, Path("/mirror"), "f" * 40)

    def test_identity_keeps_publication_controller_engine_and_renderer_distinct(self):
        value = publisher.publication_identity(self.admission(), "d" * 40, "b" * 40)
        self.assertEqual(value["publicationSourceSha"], "d" * 40)
        self.assertEqual(value["controllerSourceSha"], "a" * 40)
        self.assertEqual(value["engineSourceSha"], "b" * 40)
        self.assertEqual(value["rendererSourceSha"], "b" * 40)
        self.assertEqual(value["sourceEquivalenceReceipt"], "publication-source-equivalence-" + "c" * 40)
        self.assertEqual(publisher.publication_identity(None, "d" * 40, "d" * 40), {})


class SignedFrontendTests(unittest.TestCase):
    def gate(self):
        path = next((parent / "ops/release/frontend_site_proof.py"
                     for parent in Path(__file__).resolve().parents
                     if (parent / "ops/release/frontend_site_proof.py").is_file()), None)
        if path is None:
            self.skipTest("Original proof integration runs in actual-image repository qualification")
        spec = importlib.util.spec_from_file_location("original_frontend_proof", path)
        proof = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(proof)
        return proof, path

    def site(self, root):
        for name, text in {
            "index.html": '<script src="./assets/old.js"></script>',
            "assets/old.js": "old UI",
            "data/overview.json": '{"as_of":"2026-09-24","rate":4.2}',
            ".well-known/ai-catalog.json": '{"entries":[]}',
            "dispatches/current.html": "newly generated evidence",
        }.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def test_new_frontend_keeps_current_engine_data_and_old_assets(self):
        proof, _ = self.gate()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.site(root)
            before = proof.files(root)
            (root / "index.html").write_text('<script src="./assets/new.js"></script>')
            (root / "assets/new.js").write_text("new UI")
            result = publisher.seal_frontend_overlay(before, root, proof,
                source_sha="a" * 40, retired=[], editorial=None)
            self.assertEqual(result["changed"], ["assets/new.js", "index.html"])
            self.assertEqual(result["publicFiles"]["data/overview.json"], before["data/overview.json"])
            self.assertEqual(proof.files(root)["assets/old.js"], before["assets/old.js"])
            self.assertEqual(result["artifactKind"], "generated_engine_with_signed_frontend")
            self.assertNotIn("previousMirrorSha", result)

    def test_frontend_cannot_mutate_or_remove_generated_evidence(self):
        proof, _ = self.gate()
        for name, change in (("data/overview.json", "change"),
                             (".well-known/ai-catalog.json", "change"),
                             ("assets/old.js", "change"),
                             ("dispatches/current.html", "delete"),
                             ("dispatches/invented.html", "add")):
            with self.subTest(path=name, change=change), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                self.site(root)
                before = proof.files(root)
                if change == "delete":
                    (root / name).unlink()
                else:
                    (root / name).write_text("unauthorized")
                with self.assertRaisesRegex(RuntimeError, "generated engine evidence"):
                    publisher.seal_frontend_overlay(before, root, proof,
                        source_sha="a" * 40, retired=[], editorial=None)

    def test_signed_retirement_and_exact_editorial_policy_remain_bounded(self):
        proof, _ = self.gate()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.site(root)
            (root / "funding.json").write_text("retired personal manifest")
            (root / "_headers").write_text("/*\n  Content-Security-Policy: default-src 'self'; " +
                                          proof.EDITORIAL_CONNECT_BEFORE + "\n")
            before = proof.files(root)
            proof.retire(root, ["funding.json"])
            proof.allow_editorial(root, proof.EDITORIAL_ORIGIN)
            result = publisher.seal_frontend_overlay(before, root, proof,
                source_sha="a" * 40, retired=["funding.json"], editorial=proof.EDITORIAL_ORIGIN)
            self.assertEqual(result["absentPublicFiles"], ["funding.json"])
            self.assertIn(proof.EDITORIAL_ORIGIN, result["contentSecurityPolicy"])
            (root / "_headers").write_text((root / "_headers").read_text().replace(
                "default-src 'self'", "default-src *"))
            with self.assertRaisesRegex(RuntimeError, "another engine header"):
                publisher.seal_frontend_overlay(before, root, proof,
                    source_sha="a" * 40, retired=["funding.json"], editorial=proof.EDITORIAL_ORIGIN)

    def test_original_frontend_helper_is_hash_pinned(self):
        _, source = self.gate()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            name = "ops/release/frontend_site_proof.py"
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_bytes(source.read_bytes())
            (root / "controller-source.json").write_text(json.dumps({
                "engineGateSha256": {name: hashlib.sha256(source.read_bytes()).hexdigest()}}))
            with patch.object(publisher, "CONTROLLER", root):
                with publisher.original_frontend_proof(root) as proof:
                    self.assertEqual(proof.SCHEMA, "seiche.frontend-site-proof.v1")
                path.write_text("raise RuntimeError('untrusted')")
                with self.assertRaisesRegex(RuntimeError, "Original frontend proof changed"):
                    with publisher.original_frontend_proof(root):
                        self.fail("Changed helper was loaded")

    def archive(self, extra=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            member = tarfile.TarInfo("frontend/src/main.ts")
            body = b"signed source"
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
            if extra is not None:
                archive.addfile(extra, io.BytesIO(b"x" * extra.size) if extra.isfile() else None)
        return stream.getvalue()

    def test_archive_validation_precedes_every_write(self):
        for name, kind in (("frontend/../../outside", tarfile.REGTYPE),
                           ("/tmp/outside", tarfile.REGTYPE),
                           ("backend/runtime.py", tarfile.REGTYPE),
                           ("frontend/link", tarfile.SYMTYPE),
                           ("frontend/hardlink", tarfile.LNKTYPE),
                           ("frontend/src/main.ts", tarfile.REGTYPE)):
            with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as raw:
                extra = tarfile.TarInfo(name)
                extra.type = kind
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    extra.linkname = "/outside"
                destination = Path(raw)
                with self.assertRaisesRegex(RuntimeError, "Unsafe signed frontend archive"):
                    publisher.extract_frontend_archive(self.archive(extra), destination)
                self.assertEqual(list(destination.iterdir()), [])

    def test_build_archive_does_not_inherit_test_mutations(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            test, build = root / "tests", root / "build"
            test.mkdir(); build.mkdir()
            archive = self.archive()
            publisher.extract_frontend_archive(archive, test)
            (test / "frontend/src/main.ts").write_text("test side effect")
            publisher.extract_frontend_archive(archive, build)
            self.assertEqual((build / "frontend/src/main.ts").read_text(), "signed source")

    def test_separate_ui_build_never_copies_source_public_data_over_engine_output(self):
        proof, proof_path = self.gate()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            controller, backend, engine = (root / name for name in ("controller", "backend", "engine"))
            for directory in (controller, backend, engine):
                directory.mkdir()
            self.site(engine)
            publisher.git(["init", "--quiet", "-b", "main"], controller)
            source = controller / "frontend/src/main.ts"
            source.parent.mkdir(parents=True)
            source.write_text("signed UI")
            publisher.git(["add", "."], controller)
            publisher.git(["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                           "commit", "--quiet", "--no-gpg-sign", "-m", "fixture"], controller)
            sha = publisher.git(["rev-parse", "HEAD"], controller)
            helper = backend / "ops/release/frontend_site_proof.py"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(proof_path.read_bytes())
            (controller / "controller-source.json").write_text(json.dumps({"engineGateSha256": {
                "ops/release/frontend_site_proof.py": hashlib.sha256(helper.read_bytes()).hexdigest()}}))
            environments = []

            def run(command, cwd, environment, **options):
                if "--receipt-tag" in command:
                    self.assertEqual(Path(command[3]).parent, helper.parent)
                    return json.dumps({"frontendPublication": {"sourceSha": sha, "backendReleaseSha": "b" * 40}})
                if command[0] == "npm":
                    environments.append(environment)
                    self.assertTrue(options["unprivileged"])
                    if command[1:] == ["test"]:
                        (cwd / "src/main.ts").write_text("test mutation")
                        (Path(environment["HOME"]) / ".npmrc").write_text("test mutation")
                    if command[1:] == ["run", "build"]:
                        self.assertEqual((cwd / "src/main.ts").read_text(), "signed UI")
                        self.assertFalse((Path(environment["HOME"]) / ".npmrc").exists())
                        self.site(cwd / "dist")
                        (cwd / "dist/index.html").write_text('<script src="./assets/new.js"></script>')
                        (cwd / "dist/assets/new.js").write_text("compiled UI")
                        (cwd / "dist/data/overview.json").write_text("old source data")
                else:
                    self.assertEqual(command[:3], ["python", "-I", "-c"])
                    self.assertEqual(command[-2], str(backend / "backend"))

            admission = {"sourceEquivalence": {"controllerSourceSha": sha, "backendReleaseSha": "b" * 40}}
            with (patch.object(publisher, "CONTROLLER", controller),
                  patch.object(publisher, "run", side_effect=run),
                  patch.object(publisher.shutil, "chown"), patch.object(publisher, "quiesce_builder")):
                candidate, manifest = publisher.build_signed_frontend(
                    root, controller, backend, engine, admission, "test-fingerprint")
            self.assertEqual((candidate / "data/overview.json").read_bytes(), (engine / "data/overview.json").read_bytes())
            self.assertEqual((candidate / "dispatches/current.html").read_bytes(), (engine / "dispatches/current.html").read_bytes())
            self.assertIn("assets/new.js", manifest["publicFiles"])
            self.assertEqual(len({env["HOME"] for env in environments}), 2)


if __name__ == "__main__":
    unittest.main()
