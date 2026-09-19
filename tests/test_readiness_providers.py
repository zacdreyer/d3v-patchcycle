"""Native-lock and provider parsing regressions."""

import os
import subprocess
import sys

import pytest

from patchcycle.errors import PreflightError
from patchcycle.providers.apt import parse_policy_security, parse_simulation, probe_lock
from patchcycle.subproc import CommandResult
from test_dnf import FakeRunner, make_provider


@pytest.mark.parametrize(
    "os_id,version", [("fedora", "41"), ("debian", "11"), ("rhel", "9"), ("derivative", "1")]
)
def test_unvalidated_platform_is_refused(os_id, version):
    from patchcycle.errors import UnsupportedPlatformError
    from patchcycle.models import OsIdentity
    from patchcycle.providers import select_provider

    with pytest.raises(UnsupportedPlatformError):
        select_provider(
            OsIdentity(
                "linux", os_id=os_id, version_id=version, id_like=("debian", "rhel"), arch="x86_64"
            )
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX record locks")
def test_apt_probe_detects_dpkg_record_lock_in_other_process(tmp_path):
    lock = tmp_path / "lock"
    script = (
        "import fcntl,sys; f=open(sys.argv[1],'w'); "
        "fcntl.lockf(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read()"
    )
    proc = subprocess.Popen(  # noqa: S603 - controlled helper, no real package manager
        [sys.executable, "-c", script, str(lock)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "ready"
        assert probe_lock(lock)
    finally:
        proc.communicate("", timeout=5)


def test_new_dependency_in_simulation_is_not_lost():
    result = parse_simulation("Inst new-dependency (2.0 Debian:stable [amd64])\n")
    assert len(result) == 1
    assert result[0].version_from == ""


def test_native_apt_kept_back_names_without_versions_are_preserved():
    updates = parse_simulation(
        "The following packages have been kept back:\n  foo bar\n  baz\n"
        "0 upgraded, 0 newly installed, 0 to remove and 3 not upgraded.\n"
    )
    assert [update.name for update in updates] == ["foo", "bar", "baz"]
    assert all(update.held and update.version_to == "" for update in updates)


def test_old_security_version_does_not_classify_nonsecurity_candidate():
    policy = """example:
  Installed: 1.0
  Candidate: 3.0
  Version table:
     3.0 500
        500 http://repo stable-updates/main amd64 Packages
     2.0 500
        500 http://repo stable-security/main amd64 Packages
 *** 1.0 100
        100 /var/lib/dpkg/status
"""
    assert parse_policy_security(policy) == set()


def test_candidate_security_origin_can_be_second_origin():
    policy = """example:
  Installed: 1.0
  Candidate: 2.0
  Version table:
     2.0 500
        500 http://repo stable-updates/main amd64 Packages
        500 http://repo stable-security/main amd64 Packages
"""
    assert parse_policy_security(policy) == {"example"}


def test_dnf_unknown_nonzero_is_failure(fake_dnf_bin):
    runner = FakeRunner({"check-update": CommandResult(2, "", "unknown error")})
    provider = make_provider(runner, fake_dnf_bin)
    with pytest.raises(PreflightError):
        provider.list_updates("safe")


def test_debian_kernel_release_comes_from_package_name(tmp_path, fake_bin):
    from patchcycle.providers.apt import AptProvider
    from test_apt import DEBIAN
    from test_apt import FakeRunner as AptRunner

    runner = AptRunner(
        {
            ("-W",): CommandResult(
                0, "linux-image-6.12.43+deb13-amd64\t6.12.43-1\nlinux-image-amd64\t6.12.43-1\n", ""
            )
        }
    )
    provider = AptProvider(
        DEBIAN, runner=runner, search_paths=(str(fake_bin),), state_root=tmp_path
    )
    assert provider._newest_installed_kernel() == "6.12.43+deb13-amd64"


def test_dnf_kernel_uses_native_rpm_version_comparison(fake_dnf_bin):
    runner = FakeRunner(
        {
            "-q": CommandResult(0, "kernel-core-6.9.1-1.x86_64\nkernel-core-6.10.1-1.x86_64\n", ""),
            "--eval": CommandResult(0, "1", ""),
        }
    )
    provider = make_provider(runner, fake_dnf_bin)
    assert provider._newest_kernel() == "6.10.1-1.x86_64"
    assert any("rpm.vercmp" in " ".join(call[0]) for call in runner.calls)


def test_exact_provider_wins_over_earlier_family_fallback():
    from patchcycle.models import OsIdentity
    from patchcycle.providers import register, registry_restore, registry_savepoint, select_provider
    from test_osdetect import FakeProvider

    saved = registry_savepoint()

    class ExactProvider(FakeProvider):
        pass

    try:
        register(FakeProvider, ids=frozenset(), id_like=frozenset({"fixture-family"}))
        register(ExactProvider, ids=frozenset({"fixture-exact"}))
        identity = OsIdentity("linux", "fixture-exact", id_like=("fixture-family",))
        assert isinstance(select_provider(identity), ExactProvider)
    finally:
        registry_restore(saved)


def test_dnf_versionlocked_candidate_is_reported_held(fake_dnf_bin):
    runner = FakeRunner(
        {
            "check-update": CommandResult(100, "bash.x86_64 5.2-1 baseos\n", ""),
            "versionlock": CommandResult(0, "bash-0:5.1-1.*\n", ""),
        }
    )
    provider = make_provider(runner, fake_dnf_bin)
    assert provider.list_updates("safe")[0].held is True
    assert provider.verify().outstanding == 0


def test_dnf5_versionlocked_candidate_is_reported_held(fake_dnf_bin):
    runner = FakeRunner(
        {
            "check-update": CommandResult(100, "bash.x86_64 5.2-1 updates\n", ""),
            "versionlock": CommandResult(0, "Package name: bash\nevr = 5.1-1\n", ""),
        }
    )
    assert make_provider(runner, fake_dnf_bin).list_updates("safe")[0].held is True


def test_dnf_lock_failure_is_retryable_preflight(fake_dnf_bin):
    runner = FakeRunner({"check": CommandResult(200, "", "Failed to obtain the transaction lock")})
    provider = make_provider(runner, fake_dnf_bin)
    assert provider.preflight().kind == "pm-locked"
    assert all("--setopt=exit_on_lock=True" in call[0] for call in runner.calls)


def test_native_hidden_versionlock_is_reported_without_inventing_candidate(fake_dnf_bin):
    runner = FakeRunner(
        {
            "versionlock": CommandResult(0, "bash-0:5.1-1.*\n", ""),
            "-qa": CommandResult(0, "bash\t0:5.1-1\tx86_64\nother\t0:1-1\tx86_64\nbad\n", ""),
        }
    )
    updates = make_provider(runner, fake_dnf_bin).list_updates("security")
    assert len(updates) == 1
    assert updates[0].name == "bash" and updates[0].held
    assert updates[0].version_from == "0:5.1-1"
    assert updates[0].version_to == ""


@pytest.mark.parametrize("probe", ["versionlock", "-qa"])
def test_versionlock_probe_errors_refuse_unknown_hold_state(fake_dnf_bin, probe):
    results = {"versionlock": CommandResult(0, "bash-0:5.1-1.*\n", "")}
    results[probe] = CommandResult(1, "", "database unavailable")
    with pytest.raises(PreflightError):
        make_provider(FakeRunner(results), fake_dnf_bin).list_updates("safe")


@pytest.mark.parametrize(
    "message", ["No such command: versionlock", 'Unknown argument "versionlock"']
)
def test_absent_optional_versionlock_plugin_allows_discovery(fake_dnf_bin, message):
    runner = FakeRunner({"versionlock": CommandResult(1, "", message)})
    assert make_provider(runner, fake_dnf_bin).list_updates("safe") == []


def test_dnf_security_probe_failure_cannot_report_no_updates(fake_dnf_bin):
    runner = FakeRunner(
        {
            "check-update": CommandResult(100, "bash.x86_64 5.2-1 updates\n", ""),
            "updateinfo": CommandResult(1, "", "metadata unavailable"),
        }
    )
    with pytest.raises(PreflightError):
        make_provider(runner, fake_dnf_bin).list_updates("security")


def test_apt_audit_error_is_not_consistent(tmp_path):
    from test_apt import FakeRunner as AptRunner
    from test_apt import make_provider as make_apt

    provider = make_apt(
        AptRunner({"--audit": CommandResult(2, "", "database unavailable")}), state_root=tmp_path
    )
    assert not provider.verify().consistent


@pytest.mark.parametrize("exit_code", [0, 100])
def test_all_apt_repositories_failed_is_not_partial_success(tmp_path, exit_code):
    from test_apt import FakeRunner as AptRunner
    from test_apt import make_provider as make_apt

    runner = AptRunner(
        {
            "update": CommandResult(
                exit_code,
                "Reading package lists... Done\n",
                "W: Failed to fetch https://example.invalid/index\n",
            )
        }
    )
    assert not make_apt(runner, state_root=tmp_path).refresh().ok


def test_apt_security_probe_failure_cannot_report_no_updates(tmp_path):
    from test_apt import SIM_UPGRADE
    from test_apt import FakeRunner as AptRunner
    from test_apt import make_provider as make_apt

    runner = AptRunner(
        {
            "-s": CommandResult(0, SIM_UPGRADE, ""),
            "policy": CommandResult(1, "", "metadata unavailable"),
        }
    )
    with pytest.raises(PreflightError):
        make_apt(runner, state_root=tmp_path).list_updates("security")


def test_dnf5_security_table_and_hyphenated_package_name(fake_dnf_bin):
    runner = FakeRunner(
        {
            "check-update": CommandResult(100, "python-3-tools.noarch 2.0-1 updates\n", ""),
            "updateinfo": CommandResult(
                0,
                "Name Type Severity Package Issued\n"
                "PC-2026:1 security Important python-3-tools-0:2.0-1.noarch 2026-09-08\n",
                "",
            ),
        }
    )
    updates = make_provider(runner, fake_dnf_bin).list_updates("security")
    assert len(updates) == 1 and updates[0].security


def test_apt_security_apply_pins_classified_candidate(tmp_path):
    from patchcycle.models import UpdateInfo
    from test_apt import FakeRunner as AptRunner
    from test_apt import make_provider as make_apt

    runner = AptRunner()
    provider = make_apt(runner, state_root=tmp_path)
    assert provider.apply_updates(
        "security", [UpdateInfo("example", "1.0", "2.0", security=True)]
    ).ok
    assert "example=2.0" in runner.calls[0][0]
