"""Tests for atomic state persistence (ADR-0006; state-machine.md §6-7)."""

from __future__ import annotations

import json

import pytest

from patchcycle import STATE_SCHEMA_VERSION
from patchcycle.errors import StateError
from patchcycle.state_store import CycleState, StateStore
from patchcycle.states import State


@pytest.fixture()
def store(tmp_path):
    return StateStore(state_dir=tmp_path)


def make_cycle(**overrides) -> CycleState:
    base = {
        "run_id": "11111111-2222-3333-4444-555555555555",
        "state": State.PRECHECK,
        "hostname": "web01.example.com",
    }
    base.update(overrides)
    return CycleState(**base)


class TestRoundTrip:
    def test_save_and_load(self, store):
        cycle = make_cycle()
        store.save(cycle)
        loaded = store.load()
        assert loaded is not None
        assert loaded.run_id == cycle.run_id
        assert loaded.state is State.PRECHECK
        assert loaded.schema_version == STATE_SCHEMA_VERSION

    def test_missing_state_file_means_idle(self, store):
        assert store.load() is None

    def test_save_is_atomic_no_partial_files(self, store, monkeypatch):
        cycle = make_cycle()
        store.save(cycle)

        def boom(self, path, data):  # simulate power loss mid-write
            path.write_text(data[: len(data) // 2])
            raise OSError("simulated power loss")

        monkeypatch.setattr(StateStore, "_write_temp", boom)
        with pytest.raises(OSError):
            store.save(make_cycle(state=State.UPGRADING))
        # The previously committed state must be intact and readable.
        assert store.load().state is State.PRECHECK
        # No stray temp files remain visible as state.
        assert store.load().run_id == cycle.run_id

    def test_state_file_permissions(self, store):
        import os
        import stat

        store.save(make_cycle())
        mode = stat.S_IMODE(os.stat(store.state_file).st_mode)
        if os.name == "posix":
            assert mode == 0o600

    def test_transitions_recorded(self, store):
        cycle = make_cycle()
        cycle = cycle.record_transition(State.PRECHECK, State.REFRESHING, at="2026-08-23T02:01:00Z")
        store.save(cycle)
        loaded = store.load()
        assert loaded.transitions[-1]["to"] == "REFRESHING"


class TestCorruption:
    def test_corrupt_state_is_quarantined_and_refused(self, store):
        store.save(make_cycle())
        store.state_file.write_bytes(b'{"schema_version": 1, "state": "UPGR')  # truncated
        with pytest.raises(StateError) as excinfo:
            store.load()
        assert excinfo.value.error_kind == "state-corrupt"
        quarantined = list(store.state_dir.glob("state.json.corrupt.*"))
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes().startswith(b'{"schema_version"')

    def test_newer_schema_version_refused(self, store):
        cycle = make_cycle()
        store.save(cycle)
        data = json.loads(store.state_file.read_text())
        data["schema_version"] = STATE_SCHEMA_VERSION + 1
        store.state_file.write_text(json.dumps(data))
        with pytest.raises(StateError, match="schema"):
            store.load()

    def test_invalid_state_value_refused(self, store):
        store.save(make_cycle())
        data = json.loads(store.state_file.read_text())
        data["state"] = "TELEPORTING"
        store.state_file.write_text(json.dumps(data))
        with pytest.raises(StateError):
            store.load()

    @pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX symlink semantics")
    def test_symlinked_state_file_refused(self, store, tmp_path):
        target = tmp_path / "elsewhere.json"
        target.write_text("{}")
        store.state_file.symlink_to(target)
        with pytest.raises(StateError):
            store.load()


class TestHistory:
    def test_archive_moves_terminal_cycle_to_history(self, store):
        cycle = make_cycle(state=State.COMPLETED)
        store.save(cycle)
        store.archive(cycle)
        assert store.load() is None
        archived = store.state_dir / "history" / f"{cycle.run_id}.json"
        assert archived.exists()
        data = json.loads(archived.read_text())
        assert data["state"] == "COMPLETED"

    def test_history_lists_recent_cycles(self, store):
        for i in range(3):
            cycle = make_cycle(run_id=f"run-{i}", state=State.COMPLETED)
            store.archive(cycle)
        history = store.history()
        assert [h.run_id for h in history] == ["run-0", "run-1", "run-2"]

    def test_history_skips_corrupt_files_without_crashing(self, store):
        store.archive(make_cycle(run_id="good", state=State.COMPLETED))
        bad = store.state_dir / "history" / "bad.json"
        bad.write_text("not json")
        history = store.history()
        assert [h.run_id for h in history] == ["good"]
