"""L2 integration: full engine cycles through the real DnfProvider."""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.test_dnf import (
    CHECK_UPDATE,
    ROCKY,
    RPM_KERNELS,
    UPDATEINFO_SECURITY,
    FakeRunner,
)
from tests.test_engine import make_engine

from patchcycle.providers.dnf import DnfProvider
from patchcycle.subproc import CommandResult


class TransactionRunner(FakeRunner):
    """Successful upgrade changes subsequent discovery results."""

    def __call__(self, argv, **kwargs):
        result = super().__call__(argv, **kwargs)
        if result.exit_code == 0 and "upgrade" in argv:
            self.results["check-update"] = CommandResult(0, "", "")
        return result


@pytest.fixture()
def dnf(tmp_path, fake_dnf_bin):
    runner = TransactionRunner(
        {
            ("--version",): CommandResult(0, "4.14.0\n", ""),
            ("check",): CommandResult(0, "", ""),
            "makecache": CommandResult(0, "Metadata cache created.\n", ""),
            "check-update": CommandResult(100, CHECK_UPDATE, ""),
            "updateinfo": CommandResult(0, UPDATEINFO_SECURITY, ""),
            ("upgrade",): CommandResult(0, "Upgraded: 4\n", ""),
            ("needs-restarting", "-r"): CommandResult(0, "", ""),
            ("-q",): CommandResult(0, RPM_KERNELS, ""),
        }
    )
    provider = DnfProvider(
        # Running kernel matches newest installed: no kernel reboot reason.
        replace(ROCKY, kernel="5.14.0-503.40.1.el9_5.x86_64"),
        runner=runner,
        search_paths=(str(fake_dnf_bin),),
        state_root=tmp_path,
    )
    return provider, runner


class TestFullCycleThroughDnf:
    def test_upgrade_cycle_no_reboot(self, dnf, tmp_path):
        provider, runner = dnf
        engine, store, _ = make_engine(tmp_path, provider)
        assert engine.run() == 0
        cycle = store.history()[0]
        assert cycle.outcome == "success"
        assert cycle.updates_applied is True
        assert cycle.packages_pending == 4
        calls = [" ".join(argv) for argv, _ in runner.calls]
        assert any("makecache" in c for c in calls)
        assert any("upgrade" in c and "--refresh" in c for c in calls)

    def test_security_strategy_cycle(self, dnf, tmp_path):
        provider, runner = dnf
        engine, store, _ = make_engine(
            tmp_path, provider, config_text='[updates]\nstrategy = "security"'
        )
        assert engine.run() == 0
        sec_calls = [argv for argv, _ in runner.calls if "--security" in argv]
        assert sec_calls  # dnf upgrade --security used

    def test_interrupted_transaction_blocks(self, dnf, tmp_path):
        provider, runner = dnf
        runner.results[("check",)] = CommandResult(1, "", "Error: broken deps\n")
        engine, store, _ = make_engine(tmp_path, provider)
        assert engine.run() == 2
        assert store.history()[0].error["kind"] == "interrupted-transaction"
        calls = [" ".join(argv) for argv, _ in runner.calls]
        assert not any("upgrade" in c for c in calls)

    def test_reboot_cycle_with_kernel(self, tmp_path, fake_dnf_bin):
        runner = TransactionRunner(
            {
                ("--version",): CommandResult(0, "4.14.0\n", ""),
                ("check",): CommandResult(0, "", ""),
                "makecache": CommandResult(0, "ok\n", ""),
                "check-update": CommandResult(100, CHECK_UPDATE, ""),
                "updateinfo": CommandResult(0, UPDATEINFO_SECURITY, ""),
                ("upgrade",): CommandResult(0, "Upgraded:\n  kernel-core\n", ""),
                ("needs-restarting", "-r"): CommandResult(1, "Reboot required\n", ""),
                ("-q",): CommandResult(0, RPM_KERNELS, ""),
            }
        )
        provider = DnfProvider(
            replace(ROCKY, kernel="5.14.0-503.14.1.el9_5.x86_64"),
            runner=runner,
            search_paths=(str(fake_dnf_bin),),
            state_root=tmp_path,
        )
        engine, store, env = make_engine(
            tmp_path, provider, config_text='[reboot]\nexisting_pending = "continue_then_reboot"'
        )

        original_reboot = env.reboot

        def reboot_new_kernel():
            original_reboot()
            env.kernel = "5.14.0-503.40.1.el9_5.x86_64"

        env.reboot = reboot_new_kernel
        engine.reboot.rebooter = env.reboot
        engine.reboot.kernel_reader = lambda: env.kernel
        assert engine.run() == 0
        cycle = store.history()[0]
        assert cycle.outcome == "success"
        assert cycle.reboot_required is True
        assert env.reboot_calls == 1
