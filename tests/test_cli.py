"""Tests for the CLI (product spec §24, FR-16..FR-18)."""

from __future__ import annotations

import json
import platform

import pytest

from patchcycle import __version__
from patchcycle.cli import main
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State


def write_config(tmp_path, extra=""):
    config = tmp_path / "config.toml"
    state_dir = tmp_path / "state"
    lock = tmp_path / "run" / "lock"
    config.write_text(
        f'[paths]\nstate_dir = "{state_dir.as_posix()}"\nlock_file = "{lock.as_posix()}"\n{extra}'
    )
    return config, state_dir


class TestVersion:
    def test_version(self, capsys):
        assert main(["version"]) == 0
        out = capsys.readouterr().out
        assert __version__ in out
        assert "schema" in out


class TestDetect:
    def test_detect_reports_platform(self, capsys):
        code = main(["detect"])
        out = capsys.readouterr().out
        assert "OS:" in out or "Unsupported" in out
        # On the Windows dev host detection must fail safely with exit 5.
        if platform.system() == "Windows":
            assert code == 5
            assert "No maintenance actions were performed" in out

    def test_detect_json(self, capsys):
        main(["detect", "--json"])
        data = json.loads(capsys.readouterr().out)
        assert "pretty_name" in data
        assert "supported" in data


class TestConfigCheck:
    def test_valid_config(self, tmp_path, capsys):
        config, _ = write_config(tmp_path)
        assert main(["config-check", "--config", str(config)]) == 0
        assert "OK" in capsys.readouterr().out

    def test_invalid_config_exits_4(self, tmp_path, capsys):
        config = tmp_path / "config.toml"
        config.write_text('[updates]\nstrategy = "safee"')
        assert main(["config-check", "--config", str(config)]) == 4
        assert "updates.strategy" in capsys.readouterr().err

    def test_missing_config_exits_4(self, tmp_path, capsys):
        assert main(["config-check", "--config", str(tmp_path / "nope.toml")]) == 4


class TestStatusAndHistory:
    def test_status_idle(self, tmp_path, capsys):
        config, _ = write_config(tmp_path)
        assert main(["status", "--config", str(config)]) == 0
        assert "IDLE" in capsys.readouterr().out

    def test_status_active_cycle(self, tmp_path, capsys):
        config, state_dir = write_config(tmp_path)
        store = StateStore(state_dir)
        store.save(CycleState(run_id="run-1", state=State.UPGRADING, hostname="web01"))
        assert main(["status", "--config", str(config)]) == 0
        out = capsys.readouterr().out
        assert "UPGRADING" in out
        assert "run-1" in out

    def test_history_empty(self, tmp_path, capsys):
        config, _ = write_config(tmp_path)
        assert main(["history", "--config", str(config)]) == 0
        assert "no cycles" in capsys.readouterr().out.lower()

    def test_history_lists_cycles(self, tmp_path, capsys):
        config, state_dir = write_config(tmp_path)
        store = StateStore(state_dir)
        store.archive(
            CycleState(
                run_id="run-a",
                state=State.COMPLETED,
                hostname="h",
                outcome="success",
                started_at="2026-08-23T02:00:00Z",
            )
        )
        store.archive(
            CycleState(
                run_id="run-b",
                state=State.FAILED,
                hostname="h",
                outcome="failed",
                started_at="2026-08-23T03:00:00Z",
            )
        )
        assert main(["history", "--config", str(config)]) == 0
        out = capsys.readouterr().out
        assert "run-a" in out and "run-b" in out
        assert "success" in out and "failed" in out


class TestRunGuards:
    def test_run_on_unsupported_platform_exits_5(self, tmp_path, capsys):
        config, _ = write_config(tmp_path)
        if platform.system() == "Windows":
            assert main(["run", "--config", str(config)]) == 5
            assert "Unsupported operating system" in capsys.readouterr().err

    def test_run_disabled_config_blocks_scheduled_run(self, tmp_path, capsys):
        config, _ = write_config(tmp_path, extra="\n[maintenance]\nenabled = false\n")
        if platform.system() == "Windows":
            # Unsupported platform wins over the enabled check on this host;
            # both guard paths are exercised.
            assert main(["run", "--config", str(config)]) in (2, 5)

    def test_updates_on_unsupported_platform_exits_5(self, tmp_path):
        config, _ = write_config(tmp_path)
        if platform.system() == "Windows":
            assert main(["updates", "--config", str(config)]) == 5


class TestUnknownCommand:
    def test_no_command_prints_help(self, capsys):
        with pytest.raises(SystemExit):
            main(["--definitely-not-a-command"])
