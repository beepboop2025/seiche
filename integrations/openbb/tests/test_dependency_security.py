"""Installed provider metadata must exclude known vulnerable dependencies."""

from importlib.metadata import requires, version

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version


def test_installed_package_enforces_patched_pyjwt():
    dependencies = {
        requirement.name.lower(): requirement
        for requirement in map(Requirement, requires("openbb-seiche") or [])
    }
    jwt = dependencies["pyjwt"]
    assert jwt.marker is None
    assert jwt.specifier == SpecifierSet(">=2.14,<3")
    assert Version("2.13.0") not in jwt.specifier
    assert Version("2.14.0") in jwt.specifier
    assert Version("3.0.0") not in jwt.specifier
    assert Version(version("PyJWT")) in jwt.specifier


@pytest.mark.parametrize(
    "package,vulnerable,patched",
    [
        ("certifi", "2026.7.22", "2026.10.23"),
        ("aiohttp", "3.14.3", "3.14.5"),
    ],
)
def test_installed_provider_excludes_vulnerable_http_dependencies(
    package, vulnerable, patched
):
    dependencies = {
        requirement.name.lower(): requirement
        for requirement in map(Requirement, requires("openbb-seiche") or [])
    }
    requirement = dependencies[package]
    assert requirement.marker is None
    assert Version(vulnerable) not in requirement.specifier
    assert Version(patched) in requirement.specifier
    assert Version(version(package)) in requirement.specifier
