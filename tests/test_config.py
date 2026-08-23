"""Tests for configuration loading/validation (docs/specifications/configuration.md)."""

from __future__ import annotations

import textwrap

import pytest

from patchcycle.config import Config, load_config
from patchcycle.errors import ConfigError

MINIMAL = """\
[maintenance]
"""

FULL = """\
[maintenance]
enabled = true
schedule = "weekly"
day = "sunday"
time = "02:00"
random_delay = "5m"
window_start = "23:00"
window_end = "04:00"

[updates]
strategy = "security"
repair_interrupted = true

[updates.config_files]
policy = "take_package"

[reboot]
policy = "notify_only"
existing_pending = "report_only"
allow_if_users_logged_in = true
user_wait_timeout = "45m"

[package_manager]
lock_timeout = "20m"
lock_poll_interval = "10s"
refresh_timeout = "5m"
upgrade_timeout = "90m"

[maintenance.estimates]
refresh = "3m"
per_package = "20s"
upgrade_min = "8m"
reboot_verify = "10m"

[notifications.email]
enabled = true
to = ["admin@example.com", "ops@example.com"]
from = "patchcycle@example.com"
smtp_host = "mail.example.com"
smtp_port = 587
smtp_starttls = true
smtp_username = "patchcycle"
smtp_password_env = "PATCHCYCLE_SMTP_PASSWORD"

[notifications.webhook]
enabled = true
url = "https://hooks.example.internal/patchcycle"
headers = { Authorization = "env:PATCHCYCLE_WEBHOOK_TOKEN" }
timeout = "20s"

[hooks]
before_upgrade = [["/usr/local/bin/pre", "--flag"]]
before_reboot = [["/usr/local/bin/pre-reboot"]]
after_reboot = []
after_upgrade = []
on_failure = [["/usr/local/bin/on-fail"]]
timeout = "2m"
failure_policy = "continue"

[[health.service]]
name = "nginx"
critical = true

[[health.http]]
url = "http://127.0.0.1/health"
expected_status = 204
timeout = "5s"
critical = false

[[health.tcp]]
host = "127.0.0.1"
port = 5432

[[health.command]]
argv = ["/usr/local/bin/check.sh", "-q"]

[logging]
level = "debug"

[paths]
state_dir = "/var/lib/custom-patchcycle"
lock_file = "/run/custom-patchcycle/lock"
"""


def write(tmp_path, text: str):
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(text))
    return path


class TestDefaults:
    def test_minimal_config_uses_conservative_defaults(self, tmp_path):
        cfg = load_config(write(tmp_path, MINIMAL))
        assert cfg.maintenance.enabled is True
        assert cfg.maintenance.schedule == "weekly"
        assert cfg.maintenance.day == "sunday"
        assert cfg.maintenance.time == "02:00"
        assert cfg.maintenance.random_delay_s == 300
        assert cfg.maintenance.window_start is None
        assert cfg.maintenance.window_end is None
        assert cfg.updates.strategy == "safe"
        assert cfg.updates.repair_interrupted is False
        assert cfg.updates.config_files_policy == "keep_existing"
        assert cfg.reboot.policy == "when_required"
        assert cfg.reboot.existing_pending == "reboot_first"
        assert cfg.reboot.allow_if_users_logged_in is False
        assert cfg.reboot.user_wait_timeout_s == 1800
        assert cfg.package_manager.lock_timeout_s == 900
        assert cfg.package_manager.lock_poll_interval_s == 15
        assert cfg.logging.level == "info"
        assert cfg.notifications.email.enabled is False
        assert cfg.notifications.webhook.enabled is False
        assert cfg.health_checks == ()
        assert cfg.hooks.before_upgrade == ()

    def test_full_config_parses(self, tmp_path):
        cfg = load_config(write(tmp_path, FULL))
        assert cfg.updates.strategy == "security"
        assert cfg.updates.config_files_policy == "take_package"
        assert cfg.reboot.policy == "notify_only"
        assert cfg.maintenance.window_start == 23 * 60
        assert cfg.maintenance.window_end == 4 * 60
        assert cfg.notifications.email.to == ("admin@example.com", "ops@example.com")
        assert cfg.notifications.email.smtp_port == 587
        # noqa: S105 - asserts an env var NAME, not a secret value
        assert cfg.notifications.email.smtp_password_env == "PATCHCYCLE_SMTP_PASSWORD"  # noqa: S105
        assert cfg.notifications.webhook.headers == (
            ("Authorization", "env:PATCHCYCLE_WEBHOOK_TOKEN"),
        )
        assert cfg.hooks.before_upgrade == (("/usr/local/bin/pre", "--flag"),)
        assert cfg.hooks.on_failure == (("/usr/local/bin/on-fail",),)
        assert len(cfg.health_checks) == 4
        kinds = [c.kind for c in cfg.health_checks]
        assert kinds == ["service", "http", "tcp", "command"]
        assert cfg.paths.state_dir == "/var/lib/custom-patchcycle"

    def test_estimates_defaults(self, tmp_path):
        cfg = load_config(write(tmp_path, MINIMAL))
        assert cfg.maintenance.estimates.refresh_s == 300
        assert cfg.maintenance.estimates.per_package_s == 30
        assert cfg.maintenance.estimates.upgrade_min_s == 600
        assert cfg.maintenance.estimates.reboot_verify_s == 900


class TestStrictSchema:
    @pytest.mark.parametrize(
        "bad_toml,fragment",
        [
            ('[maintenance]\nshcedule = "weekly"', "maintenance.shcedule"),
            ('[updates]\nstrategy = "safee"', "updates.strategy"),
            ("[bogus]\nx = 1", "bogus"),
            ('[reboot]\npolicy = "sometimes"', "reboot.policy"),
            ("[updates]\nstrategy = 42", "updates.strategy"),
        ],
    )
    def test_unknown_keys_and_bad_values_rejected(self, tmp_path, bad_toml, fragment):
        with pytest.raises(ConfigError) as excinfo:
            load_config(write(tmp_path, bad_toml))
        assert fragment in str(excinfo.value)

    def test_unknown_key_error_names_valid_keys(self, tmp_path):
        with pytest.raises(ConfigError) as excinfo:
            load_config(write(tmp_path, '[maintenance]\nshcedule = "weekly"'))
        assert "schedule" in str(excinfo.value)  # hint for the typo


class TestGrammar:
    @pytest.mark.parametrize(
        "text,seconds",
        [("90s", 90), ("15m", 900), ("2h", 7200), ("45", 45)],
    )
    def test_duration_grammar(self, tmp_path, text, seconds):
        cfg = load_config(write(tmp_path, f'[package_manager]\nlock_timeout = "{text}"'))
        assert cfg.package_manager.lock_timeout_s == seconds

    @pytest.mark.parametrize("text", ["-5m", "0s", "1d", "abc", "1m30s", ""])
    def test_duration_rejects_bad_values(self, tmp_path, text):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, f'[package_manager]\nlock_timeout = "{text}"'))

    @pytest.mark.parametrize(
        "day,expected",
        [("monday", "monday"), ("SUNDAY", "sunday"), ("3", "wednesday"), (3, "wednesday")],
    )
    def test_weekday_normalisation(self, tmp_path, day, expected):
        toml_day = f'"{day}"' if isinstance(day, str) else str(day)
        cfg = load_config(write(tmp_path, f'[maintenance]\nschedule = "weekly"\nday = {toml_day}'))
        assert cfg.maintenance.day == expected

    def test_bad_weekday_rejected(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, '[maintenance]\nschedule = "weekly"\nday = "funday"'))

    def test_monthly_day_range(self, tmp_path):
        cfg = load_config(write(tmp_path, '[maintenance]\nschedule = "monthly"\nday = 28'))
        assert cfg.maintenance.day == "28"
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, '[maintenance]\nschedule = "monthly"\nday = 29'))

    @pytest.mark.parametrize("t", ["24:00", "2:00", "aa:bb", "23:60"])
    def test_bad_time_rejected(self, tmp_path, t):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, f'[maintenance]\ntime = "{t}"'))


class TestSecretsAndWarnings:
    def test_plaintext_password_produces_warning(self, tmp_path):
        cfg = load_config(
            write(
                tmp_path,
                '[notifications.email]\nenabled = true\nto = ["a@b.c"]\nsmtp_password = "hunter2"',
            )
        )
        assert any("smtp_password" in w for w in cfg.warnings)

    def test_smtp_credentials_without_tls_warns(self, tmp_path):
        cfg = load_config(
            write(
                tmp_path,
                '[notifications.email]\nenabled = true\nto = ["a@b.c"]\n'
                'smtp_host = "mail.example.com"\nsmtp_username = "u"\n'
                'smtp_password_env = "P"',
            )
        )
        assert any("starttls" in w.lower() for w in cfg.warnings)

    def test_take_package_policy_warns(self, tmp_path):
        cfg = load_config(write(tmp_path, '[updates.config_files]\npolicy = "take_package"'))
        assert any("take_package" in w for w in cfg.warnings)

    def test_webhook_requires_https_off_loopback(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(
                write(
                    tmp_path,
                    '[notifications.webhook]\nenabled = true\nurl = "http://hooks.example.com/x"',
                )
            )

    def test_webhook_http_allowed_on_loopback(self, tmp_path):
        cfg = load_config(
            write(
                tmp_path,
                '[notifications.webhook]\nenabled = true\nurl = "http://127.0.0.1:8080/x"',
            )
        )
        assert cfg.notifications.webhook.url == "http://127.0.0.1:8080/x"

    def test_email_enabled_requires_to(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, "[notifications.email]\nenabled = true"))

    def test_hook_paths_must_be_absolute(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, '[hooks]\nbefore_upgrade = [["relative/path"]]'))


class TestPaths:
    def test_paths_must_be_absolute(self, tmp_path):
        with pytest.raises(ConfigError):
            load_config(write(tmp_path, '[paths]\nstate_dir = "relative/dir"'))

    def test_config_object_is_immutable(self, tmp_path):
        cfg = load_config(write(tmp_path, MINIMAL))
        assert isinstance(cfg, Config)
        with pytest.raises(AttributeError):
            cfg.updates.strategy = "full"  # type: ignore[misc]
