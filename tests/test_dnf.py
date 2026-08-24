"""Tests for the DNF provider (provider-contract.md §3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from patchcycle.models import OsIdentity
from patchcycle.providers.dnf import (
    DnfProvider,
    parse_check_update,
    parse_kernel_versions,
)
from patchcycle.subproc import CommandResult

ROCKY = OsIdentity(
    family="linux",
    os_id="rocky",
    id_like=("rhel", "centos", "fedora"),
    version_id="9.5",
    pretty_name="Rocky Linux 9.5 (Blue Onyx)",
    arch="x86_64",
    kernel="5.14.0-503.14.1.el9_5.x86_64",
    init="systemd",
)

CHECK_UPDATE = """\
Last metadata expiration check: 0:01:23 ago on Sun 23 Aug 2026 02:00:00.

bind-libs.x86_64                    32:9.18.28-2.el9_5         baseos
bind-utils.x86_64                   32:9.18.28-2.el9_5         baseos
kernel-core.x86_64                  5.14.0-503.40.1.el9_5      baseos
openssl-libs.x86_64                 1:3.2.2-6.el9_5.1          appstream
Obsoletes:
something-old.noarch                1.0-1                      baseos
"""

CHECK_UPDATE_NONE = "Last metadata expiration check: 0:00:01 ago.\n\n"

UPDATEINFO_SECURITY = """\
Last metadata expiration check: 0:01:23 ago.
ELSA-2026-1234 Important/Sec. openssl-libs-1:3.2.2-6.el9_5.1.x86_64
ELSA-2026-1235 Moderate/Sec.  bind-libs-32:9.18.28-2.el9_5.x86_64
"""

RPM_KERNELS = """\
kernel-core-5.14.0-503.14.1.el9_5.x86_64
kernel-core-5.14.0-503.40.1.el9_5.x86_64
"""


class FakeRunner:
    def __init__(self, results=None, default=None):
        self.results = results or {}
        self.default = default or CommandResult(0, "", "")
        self.calls: list = []

    def __call__(self, argv, *, timeout, extra_env=None):
        self.calls.append((tuple(argv), extra_env))
        for pattern, result in self.results.items():
            if isinstance(pattern, str):
                if pattern in argv:
                    return result
            elif all(p in argv for p in pattern):
                return result
        return self.default


def make_provider(runner=None, fake_bin=None, tmp_path=None, **kwargs) -> DnfProvider:
    runner = runner or FakeRunner()
    if fake_bin is not None and "search_paths" not in kwargs:
        kwargs["search_paths"] = (str(fake_bin),)
    return DnfProvider(ROCKY, runner=runner, **kwargs)


class TestParseCheckUpdate:
    def test_parses_updates(self):
        updates = parse_check_update(CHECK_UPDATE)
        names = {u.name for u in updates}
        assert "bind-libs" in names
        assert "kernel-core" in names
        assert "openssl-libs" in names
        # Obsoletes block is not an update.
        assert "something-old" not in names

    def test_versions_and_repo(self):
        updates = {u.name: u for u in parse_check_update(CHECK_UPDATE)}
        assert updates["kernel-core"].version_to == "5.14.0-503.40.1.el9_5"
        assert updates["kernel-core"].version_from == ""  # dnf doesn't print it

    def test_empty_means_no_updates(self):
        assert parse_check_update(CHECK_UPDATE_NONE) == []

    def test_malformed_is_partial_not_crash(self):
        assert isinstance(parse_check_update("garbage\nlines\n123\n"), list)


class TestParseKernels:
    def test_newest_kernel(self):
        assert parse_kernel_versions(RPM_KERNELS) == "5.14.0-503.40.1.el9_5.x86_64"

    def test_empty(self):
        assert parse_kernel_versions("") is None


class TestExitCodeConventions:
    """The APT/DNF inversion must be handled per-provider (contract §3)."""

    def test_check_update_100_means_updates_available(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                "check-update": CommandResult(100, CHECK_UPDATE, ""),
                "updateinfo": CommandResult(0, UPDATEINFO_SECURITY, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        updates = provider.list_updates("safe")
        assert len(updates) == 4
        assert not any(u.held for u in updates)

    def test_check_update_0_means_none(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                "check-update": CommandResult(0, CHECK_UPDATE_NONE, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        assert provider.list_updates("safe") == []

    def test_check_update_1_is_error(self, fake_dnf_bin):
        from patchcycle.errors import PreflightError

        runner = FakeRunner(
            {
                "check-update": CommandResult(1, "", "Error: repo failed\n"),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        with pytest.raises(PreflightError, match="check-update failed"):
            provider.list_updates("safe")

    def test_security_classification(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                "check-update": CommandResult(100, CHECK_UPDATE, ""),
                "updateinfo": CommandResult(0, UPDATEINFO_SECURITY, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        updates = {u.name: u for u in provider.list_updates("safe")}
        assert updates["openssl-libs"].security is True
        assert updates["bind-libs"].security is True
        assert updates["kernel-core"].security is False


class TestStrategies:
    def test_security_filters(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                "check-update": CommandResult(100, CHECK_UPDATE, ""),
                "updateinfo": CommandResult(0, UPDATEINFO_SECURITY, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        updates = provider.list_updates("security")
        assert {u.name for u in updates} == {"openssl-libs", "bind-libs"}

    def test_apply_safe(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(0, "Upgraded:\n  kernel-core\n", ""),
                ("-q",): CommandResult(0, RPM_KERNELS, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        result = provider.apply_updates("safe", [])
        assert result.ok
        argv = runner.calls[0][0]
        assert "upgrade" in argv and "--refresh" in argv and "-y" in argv
        assert "--allowerasing" not in argv

    def test_apply_security(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("--security",): CommandResult(0, "Upgraded: 2\n", ""),
                ("-q",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        result = provider.apply_updates("security", [])
        assert result.ok
        assert "--security" in runner.calls[0][0]

    def test_apply_full_no_allowerasing_by_default(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(0, "Upgraded: 1\n", ""),
                ("-q",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        provider.apply_updates("full", [])
        assert "--allowerasing" not in runner.calls[0][0]

    def test_apply_full_allowerasing_when_configured(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(0, "Upgraded: 1\n", ""),
                ("-q",): CommandResult(0, "", ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin, allow_erasing=True)
        provider.apply_updates("full", [])
        assert "--allowerasing" in runner.calls[0][0]

    def test_apply_failure(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(1, "", "Error: transaction failed\n"),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        result = provider.apply_updates("safe", [])
        assert not result.ok
        assert "transaction failed" in result.detail

    def test_kernel_update_detected(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("upgrade",): CommandResult(0, "Upgraded:\n  kernel-core\n", ""),
                ("-q",): CommandResult(0, RPM_KERNELS, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        result = provider.apply_updates("safe", [])
        assert result.kernel_update is True
        assert result.expected_kernel == "5.14.0-503.40.1.el9_5.x86_64"


class TestRebootDetection:
    def test_needs_restarting_exit_1(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("needs-restarting", "-r"): CommandResult(1, "Reboot is required\n", ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        status = provider.reboot_required()
        assert status.required is True

    def test_needs_restarting_exit_0(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("needs-restarting", "-r"): CommandResult(0, "", ""),
                ("-q",): CommandResult(0, RPM_KERNELS, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        provider.os_identity = __import__("dataclasses").replace(
            ROCKY, kernel="5.14.0-503.40.1.el9_5.x86_64"
        )
        status = provider.reboot_required()
        assert status.required is False

    def test_plugin_missing_falls_back_to_kernel_check(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("needs-restarting", "-r"): CommandResult(
                    1, "", "No such command: needs-restarting\n"
                ),
                ("-q",): CommandResult(0, RPM_KERNELS, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        # Running kernel (503.14.1) != newest installed (503.40.1) -> required.
        status = provider.reboot_required()
        assert status.required is True
        assert any("kernel-not-running" in r for r in status.reasons)

    def test_probe_error_conservative(self, fake_dnf_bin):
        def boom(argv, *, timeout, extra_env=None):
            raise OSError("dnf exploded")

        provider = make_provider(boom, fake_dnf_bin)
        status = provider.reboot_required()
        assert status.required is True
        assert any(r.startswith("probe-error") for r in status.reasons)


class TestPreflightAndVerify:
    def test_healthy(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("--version",): CommandResult(0, "4.14.0\n", ""),
                ("check",): CommandResult(0, "", ""),
                ("needs-restarting", "-r"): CommandResult(0, "", ""),
                ("-q",): CommandResult(0, RPM_KERNELS, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        provider.os_identity = __import__("dataclasses").replace(
            ROCKY, kernel="5.14.0-503.40.1.el9_5.x86_64"
        )
        result = provider.preflight()
        assert result.ok is True

    def test_interrupted_transaction(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("--version",): CommandResult(0, "4.14.0\n", ""),
                ("check",): CommandResult(1, "", "Error: check discovered problems\n"),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        result = provider.preflight()
        assert not result.ok
        assert result.kind == "interrupted-transaction"

    def test_refresh(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                "makecache": CommandResult(0, "Metadata cache created.\n", ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        result = provider.refresh()
        assert result.ok
        argv = runner.calls[0][0]
        assert "makecache" in argv and "--refresh" in argv
        env = runner.calls[0][1]
        assert env["LC_ALL"] == "C"

    def test_verify_inconsistent(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("check",): CommandResult(1, "", "problem detected\n"),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        assert provider.verify().consistent is False

    def test_verify_outstanding(self, fake_dnf_bin):
        runner = FakeRunner(
            {
                ("check",): CommandResult(0, "", ""),
                "check-update": CommandResult(100, CHECK_UPDATE, ""),
                "updateinfo": CommandResult(0, UPDATEINFO_SECURITY, ""),
            }
        )
        provider = make_provider(runner, fake_dnf_bin)
        result = provider.verify()
        assert result.consistent is True
        assert result.outstanding == 4


class TestRegistration:
    @pytest.fixture(autouse=True)
    def _dnf_binaries(self, fake_dnf_bin, monkeypatch):
        monkeypatch.setattr("patchcycle.providers.dnf._DEFAULT_SEARCH_PATHS", (str(fake_dnf_bin),))

    def test_registered_for_rhel_family(self):
        from patchcycle.providers import select_provider

        ident = OsIdentity(
            family="linux",
            os_id="almalinux",
            id_like=("rhel", "centos", "fedora"),
            pretty_name="AlmaLinux 9.5",
        )
        provider = select_provider(ident)
        assert provider.name == "dnf"

    def test_fedora_by_id(self):
        from patchcycle.providers import select_provider

        ident = OsIdentity(family="linux", os_id="fedora", pretty_name="Fedora 41")
        assert select_provider(ident).name == "dnf"

    def test_no_dangerous_flags_in_source(self):
        src = (
            Path(__file__).parents[1] / "src" / "patchcycle" / "providers" / "dnf.py"
        ).read_text()
        for forbidden in ("shell=True", "--skip-broken", "tsflags=force"):
            assert forbidden not in src
