#!/usr/bin/env python3
"""Bind a protected recovery workflow to its signed, possibly older runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def verify(root: Path, environment: dict[str, str]) -> dict[str, str]:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments], text=True, timeout=30
        ).strip()

    controller = environment.get("GITHUB_SHA", "")
    runtime = environment.get("RECOVERY_SOURCE_SHA", "")
    requested = environment.get("REQUESTED_SOURCE_SHA", "")
    require(
        environment.get("GITHUB_REPOSITORY") == "beepboop2025/seiche"
        and environment.get("GITHUB_REF") == "refs/heads/main"
        and environment.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
        "manual recovery must run from the protected main branch",
    )
    require(
        re.fullmatch(r"[0-9a-f]{40}", controller) is not None
        and re.fullmatch(r"[0-9a-f]{40}", runtime) is not None,
        "exact workflow and runtime commits are required",
    )
    require(
        (not requested and runtime == controller) or requested == runtime,
        "a preceding runtime requires an explicit exact source",
    )
    require(git("rev-parse", "HEAD") == controller, "workflow checkout differs")
    require(
        not git("status", "--porcelain", "--untracked-files=no"),
        "workflow checkout has tracked changes",
    )
    require(
        git("ls-remote", "origin", "refs/heads/main")
        == controller + "\trefs/heads/main",
        "protected main changed since workflow dispatch",
    )
    subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", runtime, controller],
        check=True,
        timeout=30,
    )
    # Trust the reviewed controller's signer policy, never the requested older
    # checkout or its local Git configuration. Keep signature verification bound
    # to the exact immutable objects used above.
    signers = git("show", controller + ":ops/deploy/release-allowed-signers")
    require(0 < len(signers.encode()) <= 32768, "invalid release signer policy")
    with tempfile.TemporaryDirectory(prefix="seiche-recovery-signers-") as directory:
        policy = Path(directory) / "allowed-signers"
        policy.write_text(signers + "\n", encoding="utf-8")
        for commit in sorted({controller, runtime}):
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "gpg.ssh.allowedSignersFile=" + str(policy),
                    "verify-commit",
                    commit,
                ],
                check=True,
                timeout=30,
            )
    return {"workflow_source": controller, "runtime_source": runtime}


if __name__ == "__main__":
    print(
        json.dumps(
            verify(Path(os.environ["GITHUB_WORKSPACE"]), dict(os.environ)),
            sort_keys=True,
        )
    )
