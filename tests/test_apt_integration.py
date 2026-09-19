"""L2 integration: full engine cycles through the real AptProvider.

The package manager is replaced by a scripted runner with captured real-world
output fixtures; state, reboot, and engine logic are all real.
"""

from __future__ import annotations

import pytest
from tests.test_apt import (
    DPKG_KERNELS,
    POLICY_SECURITY,
    SIM_UPGRADE,
    FakeRunner,
)
from tests.test_engine import make_engine

from patchcycle.providers.apt import AptProvider
from patchcycle.subproc import CommandResult


class TransactionRunner(FakeRunner):
    """A successful transaction consumes its updates; verification sees none."""

    def __call__(self, argv, **kwargs):
        result = super().__call__(argv, **kwargs)
        if (
            result.exit_code == 0
            and "-s" not in argv
            and any(op in argv for op in ("upgrade", "dist-upgrade", "install"))
        ):
            self.results[("-s", "upgrade")] = CommandResult(0, "", "")
        return result


@pytest.fixture()
def apt(tmp_path, fake_bin, monkeypatch):
    """AptProvider wired to fixtures; returns (provider, runner)."""
    runner = TransactionRunner(
        {
            ("--version",): CommandResult(0, "apt 3.0.3\n", ""),
            ("--audit",): CommandResult(0, "", ""),
            ("update",): CommandResult(0, "Reading package lists... Done\n", ""),
            ("-s", "upgrade"): CommandResult(0, SIM_UPGRADE, ""),
            "policy": CommandResult(0, POLICY_SECURITY, ""),
            ("upgrade",): CommandResult(
                0, "2 upgraded, 0 newly installed: linux-image-6.8.0-60-generic.\n", ""
            ),
            ("check",): CommandResult(0, "", ""),
            ("-W",): CommandResult(0, DPKG_KERNELS, ""),
        }
    )
    from tests.test_engine import UBUNTU

    provider = AptProvider(
        UBUNTU,
        runner=runner,
        search_paths=(str(fake_bin),),
        state_root=tmp_path,
        timer_active=lambda unit: False,
    )
    return provider, runner


class TestFullCycleThroughApt:
    def test_upgrade_cycle_no_reboot(self, apt, tmp_path):
        provider, runner = apt
        # Running kernel matches newest installed -> no kernel reboot reason.
        provider.os_identity = __import__("dataclasses").replace(
            provider.os_identity, kernel="6.8.0-60-generic"
        )
        engine, store, env = make_engine(tmp_path, provider)
        assert engine.run() == 0
        cycle = store.history()[0]
        assert cycle.outcome == "success"
        assert cycle.updates_applied is True
        # Verify the real provider drove the engine's recorded facts.
        assert cycle.packages_pending == 2  # docker-ce held back, excluded
        apt_calls = [" ".join(argv) for argv, _ in runner.calls]
        assert any("apt-get" in c and "update" in c for c in apt_calls)
        assert any("dpkg" in c and "--audit" in c for c in apt_calls)

    def test_security_strategy_cycle(self, apt, tmp_path):
        provider, runner = apt
        provider.os_identity = __import__("dataclasses").replace(
            provider.os_identity, kernel="6.8.0-60-generic"
        )
        engine, store, _ = make_engine(
            tmp_path, provider, config_text='[updates]\nstrategy = "security"'
        )
        assert engine.run() == 0
        install_calls = [argv for argv, _ in runner.calls if "--only-upgrade" in argv]
        assert install_calls
        assert any(arg.startswith("libssl3t64=") for arg in install_calls[0])
        assert "libc6" not in install_calls[0]  # not a security update

    def test_interrupted_dpkg_blocks_before_any_change(self, apt, tmp_path):
        provider, runner = apt
        runner.results[("--audit",)] = CommandResult(
            0, "The following packages are only half configured:\n libc6\n", ""
        )
        engine, store, _ = make_engine(tmp_path, provider)
        assert engine.run() == 2
        cycle = store.history()[0]
        assert cycle.error["kind"] == "interrupted-transaction"
        # No refresh or upgrade was ever attempted (FR-S2 default policy).
        apt_calls = [" ".join(argv) for argv, _ in runner.calls]
        assert not any("update" in c and "apt-get" in c for c in apt_calls)
        assert not any(c.strip().endswith("upgrade") for c in apt_calls)
