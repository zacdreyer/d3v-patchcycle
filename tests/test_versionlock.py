"""Native versionlock listing cases: multiple rules, excludes and comparisons."""

import pytest

from patchcycle.errors import PreflightError
from patchcycle.models import UpdateInfo
from patchcycle.providers.versionlock import is_held

UPDATE = UpdateInfo("bash", "5.1-1", "5.2-1", arch="x86_64")


@pytest.mark.parametrize(
    "listing,held",
    [
        ("", False),
        ("bash-0:5.2-1.*\n", False),
        ("bash-0:5.1-1.*\nbash-0:5.2-1.*\n", False),
        ("!bash-0:5.2-1.*\n", True),
        ("!bash-0:5.1-1.*\n", False),
        ("# comment\nother-0:1-1.*\n", False),
        ("Package name: ba*\nevr = 5.2-1\n", False),
        ("Package name: bash\nevr != 5.2-1\n", True),
        ("Package name: bash\narch = aarch64\n", True),
        ("Package name: bash\nepoch = 0\narch = x86_64\n", False),
        ("Package name: bash\nevr = 5.1-1\nPackage name: bash\nevr = 5.2-1\n", False),
    ],
)
def test_native_listing_rules(listing, held):
    assert is_held(UPDATE, listing, lambda *_: 1) is held


@pytest.mark.parametrize("operator,held", [("<", True), ("<=", True), (">", False), (">=", False)])
def test_native_order_comparison(operator, held):
    calls = []

    def compare(left, right):
        calls.append((left, right))
        return 1  # native RPM says 5.2-1 is newer than 5.1-1

    assert is_held(UPDATE, f"Package name: bash\nevr {operator} 5.1-1\n", compare) is held
    assert calls == [("0:5.2-1", "0:5.1-1")]


def test_unrecognized_condition_fails_closed():
    with pytest.raises(PreflightError):
        is_held(UPDATE, "Package name: bash\nfuture-key = unknown\n", lambda *_: 1)
