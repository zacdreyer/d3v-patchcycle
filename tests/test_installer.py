"""Tests for scheduling translation and the installer (FR-19..FR-21, ADR-0003)."""

from __future__ import annotations

from patchcycle.config import parse_config
from patchcycle.installer import (
    Installer,
    render_resume_service,
    render_service,
    render_timer,
    schedule_to_oncalendar,
)


def cfg(toml: str = ""):
    return parse_config(__import__("tomllib").loads(toml) if toml else {})


class TestScheduleTranslation:
    def test_weekly_default(self):
        assert schedule_to_oncalendar(cfg().maintenance) == "Sun *-*-* 02:00:00"

    def test_daily(self):
        c = cfg('[maintenance]\nschedule = "daily"\ntime = "03:30"')
        assert schedule_to_oncalendar(c.maintenance) == "*-*-* 03:30:00"

    def test_monthly(self):
        c = cfg('[maintenance]\nschedule = "monthly"\nday = 15\ntime = "01:00"')
        assert schedule_to_oncalendar(c.maintenance) == "*-*-15 01:00:00"

    def test_weekly_day_normalised(self):
        c = cfg('[maintenance]\nschedule = "weekly"\nday = "3"\ntime = "22:15"')
        assert schedule_to_oncalendar(c.maintenance) == "Wed *-*-* 22:15:00"

    def test_manual_has_no_calendar(self):
        c = cfg('[maintenance]\nschedule = "manual"')
        assert schedule_to_oncalendar(c.maintenance) is None


class TestUnitRendering:
    def test_timer_unit(self):
        c = cfg('[maintenance]\nrandom_delay = "10m"')
        unit = render_timer(c, executable="/opt/d3v-patchcycle/bin/d3v-patchcycle")
        assert "OnCalendar=Sun *-*-* 02:00:00" in unit
        assert "Persistent=true" in unit
        assert "RandomizedDelaySec=600" in unit
        assert "[Install]" in unit and "WantedBy=timers.target" in unit

    def test_service_unit_hardening(self):
        c = cfg()
        unit = render_service(c, executable="/opt/d3v-patchcycle/bin/d3v-patchcycle")
        assert "Type=oneshot" in unit
        assert 'ExecStart="/opt/d3v-patchcycle/bin/d3v-patchcycle" run --scheduled' in unit
        for directive in (
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectHome=true",
            "RestrictSUIDSGID=false",
            "EnvironmentFile=-/etc/d3v-patchcycle/environment",
        ):
            assert directive in unit

    def test_resume_service_unit(self):
        unit = render_resume_service(executable="/opt/d3v-patchcycle/bin/d3v-patchcycle")
        assert "Type=oneshot" in unit
        assert 'ExecStart="/opt/d3v-patchcycle/bin/d3v-patchcycle" resume' in unit
        assert "WantedBy=multi-user.target" in unit

    def test_units_do_not_reference_cron(self):
        c = cfg()
        for unit in (
            render_timer(c, "/bin/x"),
            render_service(c, "/bin/x"),
            render_resume_service("/bin/x"),
        ):
            assert "cron" not in unit.lower()


class FakeSystemd:
    """Records systemctl invocations; controllable systemd presence."""

    def __init__(self, present: bool = True, calendar_ok: bool = True):
        self.present = present
        self.calendar_ok = calendar_ok
        self.calls: list[list[str]] = []

    def __call__(self, *args: str) -> tuple[int, str]:
        self.calls.append(list(args))
        if not self.present:
            raise FileNotFoundError("systemctl not found")
        if args[:2] == ("analyze", "calendar"):
            return (0 if self.calendar_ok else 1), "next elapse: Sun 02:00"
        if args[:2] == ("analyze", "verify"):
            return 0, ""
        if args[0] == "is-enabled":
            return 0, "enabled\n"
        return 0, ""


class TestInstaller:
    def install(self, tmp_path, config_toml: str = "", systemd=None):
        systemd = systemd or FakeSystemd()
        installer = Installer(
            config=cfg(config_toml),
            systemd=systemd,
            install_root=tmp_path / "opt",
            config_dir=tmp_path / "etc",
            state_dir=tmp_path / "varlib",
            unit_dir=tmp_path / "units",
            executable="d3v-patchcycle",
        )
        return installer, systemd

    def test_install_creates_layout_and_units(self, tmp_path):
        installer, systemd = self.install(tmp_path)
        result = installer.install()
        assert result.ok is True
        assert (tmp_path / "etc" / "config.toml").exists()
        assert (tmp_path / "varlib" / "history").is_dir()
        units = {p.name for p in (tmp_path / "units").iterdir()}
        assert units == {
            "d3v-patchcycle.timer",
            "d3v-patchcycle.service",
            "d3v-patchcycle-resume.service",
        }
        assert any(c[:2] == ["analyze", "verify"] for c in systemd.calls)
        assert any(c == ["daemon-reload"] for c in systemd.calls)
        assert any(
            c[:2] == ["enable", "--now"] and "d3v-patchcycle.timer" in c for c in systemd.calls
        )
        assert any(
            c[:1] == ["enable"] and "d3v-patchcycle-resume.service" in c for c in systemd.calls
        )

    def test_install_is_idempotent(self, tmp_path):
        installer, _ = self.install(tmp_path)
        assert installer.install().ok
        assert installer.install().ok
        # Second run: same content, no duplicates.
        timer = (tmp_path / "units" / "d3v-patchcycle.timer").read_text()
        assert timer.count("OnCalendar=") == 1

    def test_existing_config_never_overwritten(self, tmp_path):
        config_dir = tmp_path / "etc"
        config_dir.mkdir(parents=True)
        existing = config_dir / "config.toml"
        existing.write_text('[updates]\nstrategy = "security"\n')
        installer, _ = self.install(tmp_path)
        installer.install()
        assert 'strategy = "security"' in existing.read_text()
        # A .new reference is written when defaults differ.
        assert (config_dir / "config.toml.new").exists()

    def test_manual_schedule_installs_no_timer(self, tmp_path):
        installer, systemd = self.install(tmp_path, '[maintenance]\nschedule = "manual"')
        result = installer.install()
        assert result.ok
        units = {p.name for p in (tmp_path / "units").iterdir()}
        assert "d3v-patchcycle.timer" not in units
        assert "d3v-patchcycle-resume.service" in units

    def test_invalid_calendar_fails_install(self, tmp_path):
        installer, _ = self.install(tmp_path, systemd=FakeSystemd(calendar_ok=False))
        result = installer.install()
        assert not result.ok
        assert "calendar" in result.detail.lower()

    def test_no_systemd_fails_safely(self, tmp_path):
        installer, _ = self.install(tmp_path, systemd=FakeSystemd(present=False))
        result = installer.install()
        assert not result.ok
        assert "systemd" in result.detail.lower()

    def test_uninstall_removes_units_preserves_state(self, tmp_path):
        installer, systemd = self.install(tmp_path)
        installer.install()
        (tmp_path / "varlib" / "history" / "run-1.json").write_text("{}")
        assert installer.uninstall().ok
        assert not list((tmp_path / "units").glob("d3v-patchcycle*"))
        assert (tmp_path / "etc" / "config.toml").exists()
        assert (tmp_path / "varlib" / "history" / "run-1.json").exists()
        assert any("disable" in c for c in systemd.calls)

    def test_uninstall_purge_removes_config_and_state(self, tmp_path):
        installer, _ = self.install(tmp_path)
        installer.install()
        assert installer.uninstall(purge=True).ok
        assert not (tmp_path / "etc" / "config.toml").exists()
        assert not (tmp_path / "varlib").exists()

    def test_resume_unit_enabled_check_uses_systemd(self, tmp_path):
        """FR-S15 wiring: the same systemd seam the reboot controller uses."""
        systemd = FakeSystemd()
        installer, _ = self.install(tmp_path, systemd=systemd)
        installer.install()
        assert installer.resume_unit_enabled() is True
        systemd.present = False
        assert installer.resume_unit_enabled() is False
