"""Real-adapter and lifecycle audit regressions."""

from dataclasses import replace
from types import SimpleNamespace

from patchcycle import cli, installer
from test_cli_install import FakeInstaller
from test_installer import TestInstaller as InstallerFixture


def test_systemd_analyze_uses_its_own_executable(monkeypatch):
    calls = []
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda argv, **kw: (
            calls.append(argv) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    installer._real_systemd("analyze", "calendar", "daily")
    assert calls == [["/usr/bin/systemd-analyze", "calendar", "daily"]]


def test_fresh_install_can_create_missing_default_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DEFAULT_CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(cli, "_make_installer", lambda *args, **kw: FakeInstaller())
    assert cli.main(["install"]) == 0


def test_failed_enable_is_not_reported_as_success(tmp_path):
    inst, systemd = InstallerFixture().install(tmp_path)
    inst._systemd = lambda *args: (1, "enable denied") if args[0] == "enable" else systemd(*args)
    result = inst.install()
    assert not result.ok


def test_switch_to_manual_disables_and_removes_existing_timer(tmp_path):
    inst, systemd = InstallerFixture().install(tmp_path)
    assert inst.install().ok
    inst.config = replace(
        inst.config, maintenance=replace(inst.config.maintenance, schedule="manual")
    )
    assert inst.install().ok
    assert not (inst.unit_dir / installer.UNIT_TIMER).exists()
    assert ["disable", "--now", installer.UNIT_TIMER] in systemd.calls


def test_uninstall_refuses_pending_cycle(tmp_path):
    from patchcycle.state_store import CycleState, StateStore
    from patchcycle.states import State

    inst, systemd = InstallerFixture().install(tmp_path)
    assert inst.install().ok
    StateStore(inst.state_dir).save(CycleState("active", State.UPGRADING, "host"))
    systemd.calls.clear()
    assert not inst.uninstall(purge=True).ok
    assert (inst.unit_dir / installer.UNIT_RESUME).exists()
    assert not systemd.calls


def test_uninstall_reports_disable_failure_without_removing_units(tmp_path):
    inst, _ = InstallerFixture().install(tmp_path)
    assert inst.install().ok
    inst._systemd = lambda *args: (1, "denied")
    assert not inst.uninstall().ok
    assert (inst.unit_dir / installer.UNIT_RESUME).exists()


def test_custom_config_path_is_used_by_both_services(tmp_path):
    inst, _ = InstallerFixture().install(tmp_path)
    inst.config_path = inst.config_dir / "custom.toml"
    assert inst.install().ok
    assert inst.config_path.exists()
    for name in (installer.UNIT_SERVICE, installer.UNIT_RESUME):
        content = (inst.unit_dir / name).read_text()
        assert "--config" in content
        assert "custom.toml" in content


def test_purge_refuses_unrelated_files_in_custom_state_directory(tmp_path):
    inst, systemd = InstallerFixture().install(tmp_path)
    assert inst.install().ok
    unrelated = inst.state_dir / "other-application.key"
    unrelated.write_text("must survive")
    systemd.calls.clear()
    assert not inst.uninstall(purge=True).ok
    assert unrelated.read_text() == "must survive"
    assert not systemd.calls


def test_purge_refuses_foreign_history_json(tmp_path):
    inst, _ = InstallerFixture().install(tmp_path)
    assert inst.install().ok
    unrelated = inst.state_dir / "history" / "unrelated.json"
    unrelated.write_text('{"other_application":true}')
    assert not inst.uninstall(purge=True).ok
    assert unrelated.is_file()


def test_purge_accepts_valid_history_and_paired_report(tmp_path):
    from patchcycle.state_store import CycleState, StateStore
    from patchcycle.states import State

    inst, _ = InstallerFixture().install(tmp_path)
    assert inst.install().ok
    store = StateStore(inst.state_dir)
    store.archive(CycleState("complete", State.COMPLETED, "host", outcome="success"))
    (store.history_dir / "complete.report.txt").write_text("complete report")
    assert inst.uninstall(purge=True).ok
    assert not inst.state_dir.exists()
