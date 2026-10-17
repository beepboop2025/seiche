"""Docker and CI pip exports must follow the same resolved project dependencies."""

from pathlib import Path
import re
import tomllib

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
import pytest


ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
LOCK = tomllib.loads((ROOT / "uv.lock").read_text())


def _pip_entries(text):
    entries = []
    for line in text.replace("\\\n", " ").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line.split(" --hash=", 1)[0].strip())
        pins = list(requirement.specifier)
        assert len(pins) == 1 and pins[0].operator == "==", "pip exports must use exact pins"
        entries.append((canonicalize_name(requirement.name), pins[0].version,
                        set(re.findall(r"--hash=(sha256:[a-f0-9]{64})", line))))
    assert entries, "pip export must not be empty"
    return entries


@pytest.mark.parametrize("filename", ["requirements.lock", "requirements-test.lock"])
def test_pip_export_versions_and_hashes_match_uv_lock(filename):
    # Read all platform pins, not just packages installed on this test runner.
    resolved = {(canonicalize_name(package["name"]), package["version"]): package
                for package in LOCK["package"] if "registry" in package["source"]}
    entries = _pip_entries((ROOT / filename).read_text())
    for name, version, hashes in entries:
        assert (name, version) in resolved, f"{filename}: {name}=={version} differs from uv.lock; re-export both pip locks"
        package = resolved[name, version]
        artifacts = package.get("wheels", []) + ([package["sdist"]] if "sdist" in package else [])
        assert hashes == {artifact["hash"] for artifact in artifacts}, f"{filename}: {name} artifact hashes differ from uv.lock"

    dependencies = PROJECT["dependencies"]
    if filename == "requirements-test.lock":
        dependencies = [*dependencies, *PROJECT["optional-dependencies"]["dev"]]
    for dependency in map(Requirement, dependencies):
        versions = [version for name, version, _ in entries if name == canonicalize_name(dependency.name)]
        assert versions, f"{filename}: missing direct dependency {dependency.name}"
        assert all(version in dependency.specifier for version in versions), f"{filename}: {dependency} is not satisfied"


def test_uv_project_metadata_matches_declared_dependency_constraints():
    project = next(package for package in LOCK["package"] if package["name"] == PROJECT["name"])
    assert LOCK["requires-python"] == PROJECT["requires-python"]
    declared = [*PROJECT["dependencies"], *PROJECT["optional-dependencies"]["dev"]]
    for dependency in map(Requirement, declared):
        matches = [entry for entry in project["metadata"]["requires-dist"]
                   if canonicalize_name(entry["name"]) == canonicalize_name(dependency.name)]
        assert any(SpecifierSet(entry.get("specifier", "")) == dependency.specifier
                   and set(entry.get("extras", [])) == dependency.extras for entry in matches), (
            f"uv.lock metadata differs from {dependency}; run uv lock before re-exporting"
        )
