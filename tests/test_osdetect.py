"""Tests for OS detection (research: os-release(5); product spec FR-01/FR-02)."""

from __future__ import annotations

import platform

import pytest

from patchcycle.errors import UnsupportedPlatformError
from patchcycle.models import OsIdentity
from patchcycle.osdetect import OsDetector, parse_os_release
from patchcycle.providers import register, select_provider
from patchcycle.providers.base import UpdateProvider

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures" / "os-release"


def load_fixture(name: str) -> dict[str, str]:
    return parse_os_release((FIXTURES / name).read_text())


class TestParseOsRelease:
    def test_ubuntu_fields(self):
        fields = load_fixture("ubuntu-24.04")
        assert fields["ID"] == "ubuntu"
        assert fields["ID_LIKE"] == "debian"
        assert fields["VERSION_ID"] == "24.04"
        assert fields["VERSION_CODENAME"] == "noble"
        assert fields["PRETTY_NAME"] == "Ubuntu 24.04.2 LTS"

    def test_comments_blank_lines_and_single_quotes(self):
        fields = load_fixture("opensuse-leap-15.6")
        assert fields["ID"] == "opensuse-leap"
        assert fields["ID_LIKE"] == "suse opensuse"
        assert "#" not in fields

    def test_shell_escapes_and_duplicate_keys_later_wins(self):
        fields = load_fixture("malformed")
        assert fields["PRETTY_NAME"] == 'Quoted "Name" With $pecial and `chars`'
        assert fields["NAME"] == "Single Quoted OS"
        # os-release(5): readers pick later entries on repeats
        assert fields["VERSION_ID"] == "2"
        # malformed line is skipped, not fatal
        assert "BOGUS" not in fields

    def test_empty_input(self):
        assert parse_os_release("") == {}


class TestOsDetector:
    def detect(self, fixture: str, **overrides) -> OsIdentity:
        fields = load_fixture(fixture)
        kwargs = {
            "system": "Linux",
            "kernel": "6.8.0-55-generic",
            "arch": "x86_64",
            "init": "systemd",
        }
        kwargs.update(overrides)
        return OsDetector(fields=fields, **kwargs).detect()

    def test_ubuntu(self):
        ident = self.detect("ubuntu-24.04")
        assert ident.family == "linux"
        assert ident.os_id == "ubuntu"
        assert ident.id_like == ("debian",)
        assert ident.version_id == "24.04"
        assert ident.pretty_name == "Ubuntu 24.04.2 LTS"
        assert ident.arch == "x86_64"
        assert ident.kernel == "6.8.0-55-generic"
        assert ident.init == "systemd"

    def test_debian_derivative_via_id_like(self):
        ident = self.detect("rocky-9")
        assert ident.os_id == "rocky"
        assert ident.id_like == ("rhel", "centos", "fedora")

    def test_rolling_release_missing_version_is_tolerated(self):
        fields = {"ID": "arch", "NAME": "Arch Linux", "PRETTY_NAME": "Arch Linux"}
        ident = OsDetector(
            fields=fields, system="Linux", kernel="6.10.0", arch="x86_64", init="systemd"
        ).detect()
        assert ident.os_id == "arch"
        assert ident.version_id == ""

    def test_macos_detection(self):
        ident = OsDetector(
            fields=None,
            system="Darwin",
            kernel="24.5.0",
            arch="arm64",
            init="launchd",
            sw_vers={"ProductName": "macOS", "ProductVersion": "15.5"},
        ).detect()
        assert ident.family == "macos"
        assert ident.os_id == "macos"
        assert ident.version_id == "15.5"
        assert ident.pretty_name == "macOS 15.5"

    def test_unknown_posix_family(self):
        ident = OsDetector(
            fields=None, system="FreeBSD", kernel="14.2", arch="amd64", init=""
        ).detect()
        assert ident.family == "unsupported:freebsd"
        assert "FreeBSD" in ident.pretty_name


class TestReadOsReleasePaths:
    def test_etc_wins_over_usr_lib(self, tmp_path):
        etc = tmp_path / "etc" / "os-release"
        usr = tmp_path / "usr" / "lib" / "os-release"
        etc.parent.mkdir(parents=True)
        usr.parent.mkdir(parents=True)
        etc.write_text("ID=etc-wins\n")
        usr.write_text("ID=usr-lib\n")
        from patchcycle.osdetect import read_os_release

        fields = read_os_release((etc, usr))
        assert fields == {"ID": "etc-wins"}  # never combined

    def test_fallback_to_usr_lib(self, tmp_path):
        usr = tmp_path / "usr" / "lib" / "os-release"
        usr.parent.mkdir(parents=True)
        usr.write_text("ID=fallback\n")
        from patchcycle.osdetect import read_os_release

        assert read_os_release((tmp_path / "missing", usr)) == {"ID": "fallback"}

    def test_all_missing_returns_none(self, tmp_path):
        from patchcycle.osdetect import read_os_release

        assert read_os_release((tmp_path / "a", tmp_path / "b")) is None

    def test_live_detection_returns_identity(self):
        from patchcycle.osdetect import OsDetector

        ident = OsDetector().detect()
        assert ident.family  # never crashes on the dev host
        assert ident.pretty_name


class FakeProvider(UpdateProvider):
    name = "fake"

    def __init__(self, os_identity: OsIdentity) -> None:
        super().__init__(os_identity)

    def preflight(self):  # pragma: no cover - registry test stub
        raise NotImplementedError

    def refresh(self):  # pragma: no cover
        raise NotImplementedError

    def list_updates(self, strategy):  # pragma: no cover
        raise NotImplementedError

    def apply_updates(self, strategy, updates):  # pragma: no cover
        raise NotImplementedError

    def reboot_required(self):  # pragma: no cover
        raise NotImplementedError

    def verify(self):  # pragma: no cover
        raise NotImplementedError


class TestProviderRegistry:
    @pytest.fixture(autouse=True)
    def _registry_scope(self):
        """Registry tests add fakes; restore built-in registrations after."""
        from patchcycle.providers import registry_restore, registry_savepoint

        savepoint = registry_savepoint()
        yield
        registry_restore(savepoint)

    def test_exact_id_match(self):
        register(FakeProvider, ids=frozenset({"tux-os"}))
        ident = OsDetector(
            fields={"ID": "tux-os", "PRETTY_NAME": "TuxOS 1"},
            system="Linux",
            kernel="k",
            arch="x86_64",
            init="systemd",
        ).detect()
        provider = select_provider(ident)
        assert isinstance(provider, FakeProvider)
        assert provider.os_identity.os_id == "tux-os"

    def test_id_like_fallback(self):
        register(FakeProvider, ids=frozenset({"tux"}), id_like=frozenset({"rhel"}))
        ident = OsDetector(
            fields=load_fixture("rocky-9"),
            system="Linux",
            kernel="k",
            arch="x86_64",
            init="systemd",
        ).detect()
        assert isinstance(select_provider(ident), FakeProvider)

    def test_unsupported_os_fails_safely(self):
        ident = OsDetector(
            fields=load_fixture("alpine-3.21"),
            system="Linux",
            kernel="k",
            arch="x86_64",
            init="openrc",
        ).detect()
        with pytest.raises(UnsupportedPlatformError) as excinfo:
            select_provider(ident)
        msg = str(excinfo.value)
        assert "Unsupported operating system" in msg
        assert "Alpine Linux v3.21" in msg
        assert "No maintenance actions were performed" in msg
        assert excinfo.value.exit_code == 5

    def test_real_platform_module_smoke(self):
        """The stdlib reader works on the dev host without crashing."""
        import contextlib

        if hasattr(platform, "freedesktop_os_release"):
            # Windows/macOS dev hosts may lack the files.
            with contextlib.suppress(OSError):
                platform.freedesktop_os_release()
