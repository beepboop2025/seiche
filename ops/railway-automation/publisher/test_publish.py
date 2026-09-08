import importlib.util
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

    def test_absent_pinned_receipt_does_not_search_other_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Path(directory)
            (controller / "controller-source.json").write_text(
                json.dumps({"sha": "a" * 40})
            )
            with (
                patch.object(publisher, "CONTROLLER", controller),
                patch.object(publisher, "git", return_value="") as git_mock,
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "no receipt or pinned frontend ancestor"
                ):
                    publisher.select_publication_source(Path("/trusted"), "b" * 40, "")
            self.assertEqual(git_mock.call_count, 2)
            self.assertTrue(
                all(
                    call.args[0][:2] == ["tag", "--list"]
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


if __name__ == "__main__":
    unittest.main()
