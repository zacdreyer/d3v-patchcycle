"""CLI cycle-command dispatch tests with injected engine (L1 seams)."""

from __future__ import annotations

import pytest

from patchcycle import cli
from patchcycle.models import OsIdentity

LINUX_IDENTITY = OsIdentity(
    family="linux",
    os_id="ubuntu",
    id_like=("debian",),
    version_id="24.04",
    pretty_name="Ubuntu 24.04.2 LTS",
    arch="x86_64",
    kernel="6.8.0-55-generic",
    init="systemd",
)


class FakeEngine:
    def __init__(self, run_code=0, resume_code=0):
        self.run_code = run_code
        self.resume_code = resume_code
        self.ran = False

    def run(self, *, scheduled=False, force=False):
        self.ran = True
        return self.run_code

    def resume(self):
        return self.resume_code

    def dry_run(self):
        return {
            "provider": "apt",
            "os": "Ubuntu 24.04.2 LTS",
            "preflight_ok": True,
            "refresh_ok": True,
            "updates": [
                {
                    "name": "libc6",
                    "version_from": "1",
                    "version_to": "2",
                    "security": True,
                    "held": False,
                    "requires_reboot_hint": False,
                },
            ],
            "packages_pending": 1,
            "reboot_required": False,
            "reboot_reasons": [],
            "strategy": "safe",
            "reboot_policy": "when_required",
        }


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Wire the CLI to a fake OS/provider/engine and a temp config."""
    config = tmp_path / "config.toml"
    config.write_text(
        f'[paths]\nstate_dir = "{(tmp_path / "state").as_posix()}"\n'
        f'lock_file = "{(tmp_path / "run" / "lock").as_posix()}"\n'
    )
    engine = FakeEngine()
    monkeypatch.setattr(cli.OsDetector, "detect", lambda self: LINUX_IDENTITY)

    class FakeProvider:
        name = "apt"

        def __init__(self, identity):
            self.os_identity = identity

        def configure(self, config):
            self.configured_with = config

    monkeypatch.setattr(cli, "select_provider", lambda identity: FakeProvider(identity))
    monkeypatch.setattr(cli, "_build_engine", lambda *a, **k: engine)
    return config, engine


class TestDryRun:
    def test_dry_run_prints_plan(self, wired, capsys):
        config, engine = wired
        assert cli.main(["run", "--dry-run", "--config", str(config)]) == 0
        out = capsys.readouterr().out
        assert '"provider": "apt"' in out
        assert '"packages_pending": 1' in out
        assert engine.ran is False  # no cycle executed

    def test_updates_lists_packages(self, wired, capsys):
        config, _ = wired
        assert cli.main(["updates", "--config", str(config)]) == 0
        out = capsys.readouterr().out
        assert "libc6" in out
        assert "security" in out


class TestRunResume:
    def test_run_returns_engine_exit_code(self, wired):
        config, engine = wired
        engine.run_code = 6
        assert cli.main(["run", "--config", str(config)]) == 6
        assert engine.ran is True

    def test_resume_dispatches(self, wired):
        config, engine = wired
        engine.resume_code = 0
        assert cli.main(["resume", "--config", str(config)]) == 0

    def test_concurrent_run_exits_3(self, wired, monkeypatch, capsys):
        from patchcycle.errors import LockHeldError

        config, engine = wired

        class BusyLock:
            def __init__(self, path):
                pass

            def __enter__(self):
                raise LockHeldError("another PatchCycle instance is running")

            def __exit__(self, *a):
                return None

        monkeypatch.setattr(cli, "ExecutionLock", BusyLock)
        assert cli.main(["run", "--config", str(config)]) == 3
        assert engine.ran is False
        assert "another PatchCycle instance" in capsys.readouterr().err

    def test_invalid_config_exits_4(self, wired, capsys):
        config, _ = wired
        config.write_text('[reboot]\npolicy = "whenever"')
        assert cli.main(["run", "--config", str(config)]) == 4
        assert "reboot.policy" in capsys.readouterr().err
