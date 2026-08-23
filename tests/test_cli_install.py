"""Tests for install/uninstall/schedule CLI commands (FR-19..FR-21)."""

from __future__ import annotations

from patchcycle import cli
from patchcycle.installer import InstallResult


class FakeInstaller:
    def __init__(self, ok=True, detail=""):
        self.ok = ok
        self.detail = detail
        self.uninstalled = None

    def install(self):
        return InstallResult(self.ok, self.detail, ("unit:a",))

    def uninstall(self, *, purge=False):
        self.uninstalled = purge
        return InstallResult(self.ok, self.detail, ("unit:a",))


def make_config(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir = "{(tmp_path / "state").as_posix()}"\n'
        f'lock_file = "{(tmp_path / "run" / "lock").as_posix()}"\n'
    )
    return config


class TestInstallCommand:
    def test_install_success(self, tmp_path, monkeypatch, capsys):
        fake = FakeInstaller()
        monkeypatch.setattr(cli, "_make_installer", lambda cfg, executable: fake)
        config = make_config(tmp_path)
        assert cli.main(["install", "--config", str(config)]) == 0
        out = capsys.readouterr().out
        assert "installed" in out.lower()

    def test_install_failure_exit_2(self, tmp_path, monkeypatch, capsys):
        fake = FakeInstaller(ok=False, detail="systemd not available")
        monkeypatch.setattr(cli, "_make_installer", lambda cfg, executable: fake)
        config = make_config(tmp_path)
        assert cli.main(["install", "--config", str(config)]) == 2
        assert "systemd" in capsys.readouterr().err

    def test_install_invalid_config_exit_4(self, tmp_path, capsys):
        config = tmp_path / "config.toml"
        config.write_text('[updates]\nstrategy = "bogus"')
        assert cli.main(["install", "--config", str(config)]) == 4


class TestUninstallCommand:
    def test_uninstall_preserves_by_default(self, tmp_path, monkeypatch, capsys):
        fake = FakeInstaller()
        monkeypatch.setattr(cli, "_make_installer", lambda cfg, executable: fake)
        config = make_config(tmp_path)
        assert cli.main(["uninstall", "--config", str(config)]) == 0
        assert fake.uninstalled is False
        assert "preserved" in capsys.readouterr().out.lower()

    def test_uninstall_purge(self, tmp_path, monkeypatch):
        fake = FakeInstaller()
        monkeypatch.setattr(cli, "_make_installer", lambda cfg, executable: fake)
        config = make_config(tmp_path)
        assert cli.main(["uninstall", "--purge", "--config", str(config)]) == 0
        assert fake.uninstalled is True


class TestScheduleCommand:
    def test_schedule_describes_timer(self, tmp_path, monkeypatch, capsys):
        fake = FakeInstaller()
        monkeypatch.setattr(cli, "_make_installer", lambda cfg, executable: fake)
        config = make_config(tmp_path)
        assert cli.main(["schedule", "--config", str(config)]) == 0
        out = capsys.readouterr().out
        assert "Sun *-*-* 02:00:00" in out
        assert "Persistent=true" in out

    def test_schedule_manual(self, tmp_path, monkeypatch, capsys):
        fake = FakeInstaller()
        monkeypatch.setattr(cli, "_make_installer", lambda cfg, executable: fake)
        config = tmp_path / "config.toml"
        config.write_text('[maintenance]\nschedule = "manual"\n')
        assert cli.main(["schedule", "--config", str(config)]) == 0
        assert "manual" in capsys.readouterr().out.lower()
