"""Security test sweep mapping to threat-model.md T1–T14 (L7).

Each test class names the threats it verifies. Fixtures attempt malicious
inputs; the system must reject, ignore, or safely contain all of them.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import sys

import pytest

from patchcycle.config import parse_config
from patchcycle.errors import ConfigError, StateError
from patchcycle.hooks import HookRunner, validate_hook_paths
from patchcycle.logging_setup import JsonLinesFormatter
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State
from patchcycle.subproc import run_argv


class TestT1CommandInjection:
    """Malicious values must never reach a shell."""

    def test_subproc_rejects_string_commands(self):
        with pytest.raises(TypeError):
            run_argv("echo pwned", timeout=5)  # type: ignore[arg-type]

    def test_shell_metachars_in_package_names_are_inert(self, tmp_path):
        """A hostile 'package name' is just an argv element — no shell."""
        marker = tmp_path / "pwned"
        result = run_argv(
            [sys.executable, "-c", "import sys; sys.exit(0)", "; touch", str(marker)],
            timeout=5,
        )
        assert result.exit_code == 0
        assert not marker.exists()

    def test_hook_argv_never_shell_interpreted(self, tmp_path):
        runner = HookRunner.__new__(HookRunner)
        from patchcycle.config import HooksConfig

        runner.config = HooksConfig(timeout_s=10)
        results = runner._run_one(
            (sys.executable, "-c", "import sys;sys.exit(0)", "$(touch /tmp/pc-pwned)")
        )
        assert results.ok
        if os.name == "posix":
            assert not os.path.exists("/tmp/pc-pwned")

    def test_config_hook_with_metachars_in_name_rejected(self):
        with pytest.raises(ConfigError):
            parse_config({"hooks": {"before_upgrade": [["/bin/legit; rm -rf /"]]}})


class TestT2PathHijacking:
    def test_provider_binaries_come_from_fixed_paths(self, tmp_path):
        """Covered in test_apt: ambient PATH with planted binaries is ignored.
        Here: the search path itself is the only source."""
        from patchcycle.errors import PreflightError
        from patchcycle.providers.apt import AptProvider

        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(PreflightError, match="missing binaries"):
            AptProvider.__new__(AptProvider)._resolve_binaries((str(empty),))


class TestT3EnvironmentManipulation:
    def test_child_env_never_inherits_ambient(self, monkeypatch):
        monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")
        monkeypatch.setenv("PATCHCYCLE_AMBIENT", "leak")
        result = run_argv(
            [
                sys.executable,
                "-c",
                "import os,sys;sys.exit(0 if not os.environ.get('LD_PRELOAD') "
                "and not os.environ.get('PATCHCYCLE_AMBIENT') else 9)",
            ],
            timeout=5,
        )
        assert result.exit_code == 0

    def test_hook_env_is_minimal(self, monkeypatch):
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "canary-secret")
        from patchcycle.config import HooksConfig

        runner = HookRunner(HooksConfig(timeout_s=10))
        results = runner._run_one(
            (
                sys.executable,
                "-c",
                "import os,sys;sys.exit(0 if not os.environ.get('AWS_SECRET_ACCESS_KEY') else 9)",
            )
        )
        assert results.exit_code == 0


class TestT4MaliciousConfig:
    def test_unknown_top_level_section_rejected(self):
        with pytest.raises(ConfigError, match="unknown option"):
            parse_config({"evil": {"key": "value"}})

    def test_symlinked_config_refused_on_posix(self, tmp_path):
        if os.name != "posix":
            pytest.skip("POSIX-only")
        real = tmp_path / "real.toml"
        real.write_text("[maintenance]\n")
        link = tmp_path / "config.toml"
        link.symlink_to("/etc/passwd")
        from patchcycle.config import load_config

        with pytest.raises((ConfigError, OSError)):
            load_config(link)

    def test_hook_world_writable_flagged(self, tmp_path):
        if os.name != "posix":
            pytest.skip("POSIX-only")
        hook = tmp_path / "hook.sh"
        hook.write_text("#!/bin/sh\n")
        hook.chmod(stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
        problems = validate_hook_paths(((str(hook),),))
        assert any("writable" in p for p in problems)


class TestT5SecretLeakage:
    def test_canary_secret_redacted_from_all_log_paths(self, tmp_path):
        from patchcycle.logging_setup import configure_logging

        log_file = tmp_path / "log.jsonl"
        configure_logging(level="debug", log_file=log_file, secrets=["canary-pw-123"])
        log = logging.getLogger("patchcycle")
        log.info("connecting with password canary-pw-123 now")
        for handler in log.handlers:
            handler.flush()
        content = log_file.read_text()
        assert "canary-pw-123" not in content
        assert "REDACTED" in content

    def test_state_schema_has_no_secret_fields(self, tmp_path):
        store = StateStore(tmp_path)
        cycle = CycleState(run_id="r", state=State.PRECHECK, hostname="h")
        store.save(cycle)
        data = json.loads(store.state_file.read_text())
        serialized = json.dumps(data).lower()
        for word in ("password", "token", "secret", "credential"):
            assert word not in serialized


class TestT6StateTampering:
    def test_symlinked_state_refused(self, tmp_path):
        if os.name != "posix":
            pytest.skip("POSIX-only")
        store = StateStore(tmp_path)
        target = tmp_path / "elsewhere.json"
        target.write_text("{}")
        store.state_file.symlink_to(target)
        with pytest.raises(StateError):
            store.load()

    def test_bit_flipped_state_quarantined(self, tmp_path):
        store = StateStore(tmp_path)
        store.save(CycleState(run_id="r1", state=State.PRECHECK, hostname="h"))
        raw = bytearray(store.state_file.read_bytes())
        raw[len(raw) // 2] ^= 0xFF  # flip a byte mid-document
        store.state_file.write_bytes(bytes(raw))
        with pytest.raises(StateError):
            store.load()
        assert list(tmp_path.glob("state.json.corrupt.*"))
        assert not store.state_file.exists()  # quarantined, not left in place


class TestT13LogInjection:
    def test_newline_injection_stays_single_record(self):
        fmt = JsonLinesFormatter()
        record = logging.LogRecord(
            "patchcycle",
            logging.INFO,
            __file__,
            1,
            'pkg "evil"\n{"run_id": "forged", "outcome": "success"}',
            (),
            None,
        )
        out = fmt.format(record)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["run_id"] is None  # the forged field is just message text


class TestT14DuplicateNotifications:
    def test_terminal_cycle_cannot_be_renotified(self, tmp_path):
        """Terminal states have no outgoing transitions; resume is a no-op."""
        from tests.test_engine import ScriptedProvider, make_engine

        engine, store, _ = make_engine(tmp_path, ScriptedProvider(updates=[]))
        store.archive(CycleState(run_id="done", state=State.COMPLETED, hostname="h"))
        assert engine.resume() == 0
        # Still exactly one archived cycle; nothing re-driven.
        assert len(store.history()) == 1


class TestPackageNameValidation:
    """Package names flowing into argv must match the dpkg name grammar."""

    @pytest.mark.parametrize(
        "name",
        ["libc6", "libssl3t64", "linux-image-6.8.0-60-generic", "python3.12-minimal", "g++", "0ad"],
    )
    def test_valid_names_accepted(self, name):
        from patchcycle.providers.apt import _PACKAGE_NAME_RE

        assert _PACKAGE_NAME_RE.match(name)

    @pytest.mark.parametrize(
        "name",
        [
            "; rm -rf /",
            "$(evil)",
            "pkg;id",
            "pkg`id`",
            "pkg|id",
            "pkg>file",
            "-rf",
            "--option",
            "pkg name",
            "",
        ],
    )
    def test_hostile_names_rejected(self, name):
        from patchcycle.providers.apt import _PACKAGE_NAME_RE

        assert not _PACKAGE_NAME_RE.match(name)

    def test_security_strategy_drops_invalid_names(self, tmp_path):
        """Defence in depth: even a hostile parse result cannot reach argv."""
        from tests.test_apt import FakeRunner

        from patchcycle.models import UpdateInfo
        from patchcycle.providers.apt import AptProvider

        runner = FakeRunner()
        provider = AptProvider.__new__(AptProvider)
        provider.os_identity = None
        provider._runner = runner
        provider.binaries = {
            "apt-get": "/usr/bin/apt-get",
            "dpkg": "/usr/bin/dpkg",
            "dpkg-query": "/usr/bin/dpkg-query",
            "apt-cache": "/usr/bin/apt-cache",
        }
        provider._state_root = tmp_path
        provider._conf_policy = "keep_existing"
        provider._refresh_timeout = 10
        provider._upgrade_timeout = 10
        hostile = UpdateInfo(name="bad;name", version_from="1", version_to="2", security=True)
        result = provider.apply_updates("security", [hostile])
        assert result.ok is True
        assert result.packages_updated == 0  # dropped, not executed
        assert runner.calls == []  # no command was ever constructed
