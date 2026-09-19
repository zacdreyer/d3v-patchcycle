"""Audit reproductions for fail-closed persistence and configuration."""

import json
import os

import pytest

from patchcycle.config import load_config, parse_config
from patchcycle.errors import ConfigError, StateError
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_execution_lock_refuses_writable_file(tmp_path):
    from patchcycle.errors import LockHeldError
    from patchcycle.lock import ExecutionLock

    path = tmp_path / "unsafe.lock"
    path.write_text("operator data")
    path.chmod(0o666)
    with pytest.raises(LockHeldError), ExecutionLock(path):
        pass
    assert path.read_text() == "operator data"


def test_execution_lock_creates_missing_runtime_directory(tmp_path):
    from patchcycle.lock import ExecutionLock

    path = tmp_path / "runtime" / "nested" / "lock"
    with ExecutionLock(path):
        assert path.is_file()
    assert path.read_text().startswith("pid=")


def test_directory_sync_open_failure_is_not_silently_successful(tmp_path, monkeypatch):
    from patchcycle import state_store

    monkeypatch.setattr(state_store.os, "name", "posix")

    def denied(*args, **kwargs):
        raise PermissionError("directory unavailable")

    monkeypatch.setattr(state_store.os, "open", denied)
    with pytest.raises(PermissionError):
        state_store._fsync_dir(tmp_path)


def test_corruption_blocks_subsequent_load(tmp_path):
    store = StateStore(tmp_path)
    store.state_file.write_text("{broken")
    with pytest.raises(StateError):
        store.load()
    with pytest.raises(StateError):
        store.load()


@pytest.mark.parametrize("run_id", ["../escape", "/absolute", "", "a/b", "a\\b"])
def test_archive_identifier_cannot_escape_history(tmp_path, run_id):
    store = StateStore(tmp_path)
    with pytest.raises(StateError):
        store.archive(CycleState(run_id, State.COMPLETED, "host"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("updates_applied", "false"),
        ("packages_updated", True),
        ("host", []),
        ("error", []),
        ("schema_version", False),
    ],
)
def test_state_schema_rejects_coerced_values(tmp_path, field, value):
    store = StateStore(tmp_path)
    data = CycleState("run-1", State.PRECHECK, "host").to_dict()
    data[field] = value
    store.state_file.write_text(json.dumps(data))
    with pytest.raises(StateError):
        store.load()


@pytest.mark.parametrize("table", ["maintenance", "updates", "reboot", "health", "logging"])
def test_non_table_config_raises_config_error(table):
    with pytest.raises(ConfigError):
        parse_config({table: 17})


def test_monthly_boolean_is_not_day_number():
    with pytest.raises(ConfigError):
        parse_config({"maintenance": {"schedule": "monthly", "day": True}})


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_world_readable_config_refused(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("")
    config.chmod(0o644)
    with pytest.raises(ConfigError):
        load_config(config)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_world_writable_hook_not_executed(tmp_path):
    from patchcycle.config import HooksConfig
    from patchcycle.hooks import HookRunner

    marker = tmp_path / "executed"
    hook = tmp_path / "hook"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    hook.chmod(0o777)
    result = HookRunner(HooksConfig()).run_all(((str(hook),),), "before_upgrade")
    assert not result[0].ok
    assert not marker.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_symlinked_history_directory_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    store = StateStore(tmp_path / "state")
    store.state_dir.mkdir()
    store.history_dir.symlink_to(target, target_is_directory=True)
    with pytest.raises(StateError):
        store.archive(CycleState("run-1", State.COMPLETED, "host"))
    assert list(target.iterdir()) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("reboot_before_updates", "false"),
        ("initial_reboot_completed", 1),
        ("verify_passed", "true"),
        ("boot_id_before", 123),
        ("host", {"hostname": []}),
        ("notification_status", {"smtp": False}),
        ("reboot_reasons", "not an array"),
        ("health_results", [{"ok": "false"}]),
        ("updates_available", [{"name": "pkg", "held": "false"}]),
    ],
)
def test_nested_recovery_schema_rejects_wrong_types(field, value):
    data = CycleState("run", State.PRECHECK, "host").to_dict()
    data[field] = value
    with pytest.raises(StateError):
        CycleState.from_dict(data)


@pytest.mark.skipif(os.name != "posix", reason="POSIX ancestry protections")
def test_private_config_under_untrusted_parent_is_refused(tmp_path):
    from patchcycle.secure_io import read_private

    untrusted = tmp_path / "untrusted"
    untrusted.mkdir(mode=0o777)
    untrusted.chmod(0o777)
    private = untrusted / "private"
    private.mkdir(mode=0o700)
    config = private / "config.toml"
    config.write_text("")
    with pytest.raises(OSError):
        read_private(config)


@pytest.mark.parametrize(
    "body",
    [
        '[[health.tcp]]\nhost="localhost"\n',
        "[[health.tcp]]\nport=443\n",
        '[[health.http]]\nurl="https:///missing-host"\n',
        '[notifications.webhook]\nenabled=true\nurl="https://user:password@example.com"\n',
        '[notifications.webhook]\nenabled=true\nurl="https://[broken"\n',
    ],
)
def test_incomplete_health_or_credential_url_is_config_error(tmp_path, body):
    from patchcycle.config import load_config
    from patchcycle.errors import ConfigError

    path = tmp_path / "config.toml"
    path.write_text(body)
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable ancestry")
@pytest.mark.parametrize("unsafe", ["writable", "non-root-owned"])
def test_hook_under_untrusted_parent_is_not_executed(tmp_path, unsafe):
    from patchcycle.config import HooksConfig
    from patchcycle.health import _default_run_command
    from patchcycle.hooks import HookRunner, validate_hook_paths

    parent = tmp_path / "untrusted"
    parent.mkdir()
    marker = tmp_path / "executed"
    script = parent / "hook"
    script.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    script.chmod(0o755)
    if unsafe == "writable":
        parent.chmod(0o777)
    else:
        os.chown(parent, 65534, 65534)
    argv = (str(script),)
    assert validate_hook_paths((argv,))
    result = HookRunner(HooksConfig()).run_all((argv,), "before_upgrade")[0]
    assert not result.ok
    assert "untrusted parent directory" in result.stderr_tail
    code, detail = _default_run_command(argv, 5)
    assert code == -1
    assert "untrusted parent directory" in detail
    assert not marker.exists()


def test_legacy_state_migrates_without_inventing_boot_proof():
    data = CycleState("legacy", State.REBOOTING, "host").to_dict()
    data["schema_version"] = 1
    data.pop("boot_id_after", None)
    restored = CycleState.from_dict(data)
    assert restored.schema_version == 2
    assert restored.boot_id_after is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("health_results", [{"kind": "service"}]),
        ("updates_available", [{"name": "incomplete"}]),
        ("error", {"kind": "incomplete"}),
    ],
)
def test_incomplete_nested_records_are_quarantined(tmp_path, field, value):
    store = StateStore(tmp_path)
    data = CycleState("incomplete", State.NOTIFYING, "host").to_dict()
    data[field] = value
    store.state_file.write_text(json.dumps(data))
    with pytest.raises(StateError):
        store.load()
    with pytest.raises(StateError):
        store.load()
