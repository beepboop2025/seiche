"""The installed integration must enforce the patched JWT dependency floor."""

from importlib.metadata import requires, version

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
