import importlib.util
import hashlib
import json
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

    def test_publication_root_and_parent_symlinks_cannot_disclose_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            private = root / "private"
            private.mkdir()
            (private / "index.html").write_text("private-controller-data")
            link = root / "link"
            link.symlink_to(private, target_is_directory=True)
            destination = root / "destination"
            destination.mkdir()
            for source in (link, link / "nested"):
                if source != link:
                    (private / "nested").mkdir()
                with self.assertRaises(RuntimeError):
                    publisher.copy_public_tree(source, destination, root)
            self.assertEqual(list(destination.iterdir()), [])

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

    def test_explicit_receipt_cannot_be_reinterpreted_as_an_ancestor(self):
        with self.assertRaisesRegex(RuntimeError, "current main exactly"):
            publisher.select_publication_source(
                Path("/trusted"), "a" * 40, "frontend-publication-" + "b" * 40
            )

    def test_pinned_ancestor_is_separate_from_current_desk_source(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Path(directory)
            ancestor, current = "a" * 40, "b" * 40
            (controller / "controller-source.json").write_text(
                json.dumps({"sha": ancestor})
            )
            admission = {
                "schema": "seiche.frontend-desk-descendant.v1",
                "receiptSourceSha": ancestor,
                "currentSourceSha": current,
                "purpose": "unchanged_frontend_only_no_desk_publication",
            }

            def git_result(args, root):
                if args[:2] == ["tag", "--list"]:
                    return (
                        args[2] if args[2] == "frontend-publication-" + ancestor else ""
                    )
                return ""

            with (
                patch.object(publisher, "CONTROLLER", controller),
                patch.object(publisher, "git", side_effect=git_result) as git_mock,
                patch.object(
                    publisher, "run", return_value=json.dumps(admission)
                ) as run_mock,
            ):
                result = publisher.select_publication_source(
                    Path("/trusted"), current, ""
                )
            self.assertEqual(
                result, (ancestor, "frontend-publication-" + ancestor, admission)
            )
            self.assertEqual(run_mock.call_args.args[0][:3], ["python", "-I", "-S"])
            git_mock.assert_called_with(
                ["checkout", "--quiet", "--detach", ancestor], Path("/trusted")
            )

    def test_application_desk_descendant_keeps_current_source_and_full_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Path(directory)
            ancestor, current = "a" * 40, "b" * 40
            (controller / "controller-source.json").write_text(
                json.dumps({"sha": ancestor})
            )
            admission = {
                "schema": "seiche.application-desk-descendant.v1",
                "controllerSourceSha": ancestor,
                "currentSourceSha": current,
                "purpose": "complete_application_publication",
            }
            with (
                patch.object(publisher, "CONTROLLER", controller),
                patch.object(publisher, "git", return_value="") as git_mock,
                patch.object(
                    publisher, "run", return_value=json.dumps(admission)
                ) as run_mock,
            ):
                result = publisher.select_publication_source(
                    Path("/trusted"), current, ""
                )
            self.assertEqual(result, (current, "", admission))
            command = run_mock.call_args.args[0]
            self.assertEqual(command[:3], ["python", "-I", "-S"])
            self.assertIn(
                "module._verify_generated_content_descendants(root,release=sys.argv[2],head=sys.argv[3])",
                command[4],
            )
            self.assertNotIn("signer_fingerprint", command[4])
            self.assertEqual(command[5:8], ["/trusted", ancestor, current])
            self.assertEqual(git_mock.call_count, 2)
            self.assertTrue(
                all(
                    call.args[0][:2] == ["tag", "--list"]
                    for call in git_mock.call_args_list
                )
            )

    def test_invalid_application_desk_history_never_selects_a_source(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Path(directory)
            (controller / "controller-source.json").write_text(
                json.dumps({"sha": "a" * 40})
            )
            for outcome in (
                "{}",
                RuntimeError(
                    "generated-content controller commit requires a pinned signer"
                ),
                RuntimeError("forbidden generated-content path"),
            ):
                with (
                    patch.object(publisher, "CONTROLLER", controller),
                    patch.object(publisher, "git", return_value="") as git_mock,
                    patch.object(
                        publisher,
                        "run",
                        side_effect=outcome if isinstance(outcome, Exception) else None,
                        return_value=outcome,
                    ),
                ):
                    with self.assertRaises(RuntimeError):
                        publisher.select_publication_source(
                            Path("/trusted"), "b" * 40, ""
                        )
                self.assertFalse(
                    any(
                        call.args[0][0] == "checkout"
                        for call in git_mock.call_args_list
                    )
                )

    def test_wrong_or_failed_desk_admission_never_selects_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Path(directory)
            (controller / "controller-source.json").write_text(
                json.dumps({"sha": "a" * 40})
            )

            def git_result(args, root):
                return (
                    args[2]
                    if args[:2] == ["tag", "--list"] and args[2].endswith("a" * 40)
                    else ""
                )

            for outcome in ("{}", RuntimeError("forbidden generated-content path")):
                with (
                    patch.object(publisher, "CONTROLLER", controller),
                    patch.object(publisher, "git", side_effect=git_result) as git_mock,
                    patch.object(
                        publisher,
                        "run",
                        side_effect=outcome if isinstance(outcome, Exception) else None,
                        return_value=outcome,
                    ),
                ):
                    with self.assertRaises(RuntimeError):
                        publisher.select_publication_source(
                            Path("/trusted"), "b" * 40, ""
                        )
                self.assertFalse(
                    any(
                        call.args[0][0] == "checkout"
                        for call in git_mock.call_args_list
                    )
                )

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



class AssemblerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).with_name("assemble.py")
        if not path.is_file():
            raise unittest.SkipTest("Assembler runs from the source checkout, not the controller image")
        spec = importlib.util.spec_from_file_location("publisher_assembler", path)
        cls.assembler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.assembler)

    def test_static_default_and_full_contexts_pin_exact_committed_blobs_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            publisher.git(["init", "--quiet", "-b", "main"], repo)
            files = list(self.assembler.FILES) + [
                ".github/workflows/publish-static.yml", ".github/workflows/publish.yml",
                "ops/requirements-social-cards.txt", "ops/railway-automation/publisher/github-known-hosts",
            ]
            for kind in ("publisher", "full-publisher"):
                files.extend("ops/railway-automation/" + kind + "/" + name
                             for name in ("Dockerfile", "publish.py", "test_publish.py"))
            for name in files:
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("committed " + name)
            publisher.git(["add", "-A"], repo)
            publisher.git(["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit",
                           "--quiet", "--no-gpg-sign", "-m", "fixtures"], repo)
            sha = publisher.git(["rev-parse", "HEAD"], repo)
            (repo / "ops/railway-automation/full-publisher/publish.py").write_text("uncommitted executable")
            static, full = root / "static", root / "full"
            self.assembler.assemble_context(repo, static)
            self.assembler.assemble_context(repo, full, kind="full", source=sha, engine_source=sha)
            self.assertEqual(json.loads((static / "controller-source.json").read_text()), {"sha": sha})
            full_identity = json.loads((full / "controller-source.json").read_text())
            self.assertEqual(full_identity["sha"], sha)
            self.assertEqual(full_identity["engineSourceSha"], sha)
            self.assertEqual((full / "publish.py").read_text(), "committed ops/railway-automation/full-publisher/publish.py")
            self.assertEqual((static / "publish.py").read_text(), "committed ops/railway-automation/publisher/publish.py")
            self.assertTrue((static / "publish-static.yml").is_file())
            self.assertTrue((full / "publish.yml").is_file())
            self.assertFalse((full / "publish-static.yml").exists())
            self.assertEqual(set(full_identity["engineGateSha256"]), set(self.assembler.FILES))
            self.assertEqual(set(json.loads((full / "gate-sha256.json").read_text())), set(self.assembler.FILES))
            (repo / "ops/railway-automation/publisher/publish.py").unlink()
            (repo / "ops/railway-automation/publisher/publish.py").symlink_to("README.md")
            publisher.git(["add", "-A"], repo)
            publisher.git(["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit",
                           "--quiet", "--no-gpg-sign", "-m", "unsafe fixture"], repo)
            with self.assertRaisesRegex(ValueError, "nonexecutable regular file"):
                self.assembler.assemble_context(repo, root / "unsafe")
            self.assertFalse((root / "unsafe").exists())


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


if __name__ == "__main__":
    unittest.main()
