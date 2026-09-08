"""Provider registry: maps an OS identity to an update provider.

Support-honesty rule (ADR-0005): a provider is registered here only when its
integration tests pass on the claimed platforms. Detection never implies
support.
"""

from __future__ import annotations

from patchcycle.errors import UnsupportedPlatformError
from patchcycle.models import OsIdentity
from patchcycle.providers.base import UpdateProvider

# Registry entries are (provider_factory, supported_ids, supported_id_like).
_REGISTRY: list[tuple[type[UpdateProvider], frozenset[str], frozenset[str]]] = []
_RELEASE_VERSIONS = {
    "debian": frozenset({"12", "13"}),
    "ubuntu": frozenset({"22.04", "24.04"}),
    "rocky": frozenset({"9"}),
    "almalinux": frozenset({"9"}),
    "fedora": frozenset({"44"}),
}


def register(
    provider_cls: type[UpdateProvider],
    *,
    ids: frozenset[str],
    id_like: frozenset[str] = frozenset(),
) -> None:
    """Register a provider for exact os-release IDs and ID_LIKE fallbacks."""
    _REGISTRY.append((provider_cls, ids, id_like))


def registry_savepoint() -> int:
    """Test seam: snapshot the registry length for later restore."""
    return len(_REGISTRY)


def registry_restore(savepoint: int) -> None:
    """Test seam: drop registrations added after the savepoint."""
    del _REGISTRY[savepoint:]


def select_provider(os_identity: OsIdentity) -> UpdateProvider:
    """Select the provider for a detected OS, or fail safely.

    Raises:
        UnsupportedPlatformError: no registered provider claims this OS.
    """
    for provider_cls, ids, _id_like in _REGISTRY:
        if os_identity.os_id in ids:
            if provider_cls in (AptProvider, DnfProvider):
                if os_identity.arch not in ("x86_64", "amd64"):
                    continue
                version = os_identity.version_id
                if os_identity.os_id in ("rocky", "almalinux"):
                    version = version.split(".")[0]
                if version not in _RELEASE_VERSIONS.get(os_identity.os_id, frozenset()):
                    continue
            return provider_cls(os_identity)
    for related_id in os_identity.id_like:
        for provider_cls, _ids, id_like in _REGISTRY:
            if related_id in id_like:
                return provider_cls(os_identity)
    raise UnsupportedPlatformError(
        f"Unsupported operating system: {os_identity.pretty_name}. "
        "No maintenance actions were performed."
    )


# --- built-in provider registrations --------------------------------------
# APT provider: registered per the support-honesty rule (ADR-0005). L1/L2
# suites cover parsing/policy/recovery; the L3 container matrix is the
# release gate (docs/development/implementation-plan.md Phase 3).
from patchcycle.providers.apt import AptProvider  # noqa: E402

register(
    AptProvider,
    ids=frozenset({"debian", "ubuntu"}),
)

# DNF provider (Phase 8 / V1.1): RHEL family. Registered per the
# support-honesty rule; L3 container matrix is the release gate.
from patchcycle.providers.dnf import DnfProvider  # noqa: E402

register(
    DnfProvider,
    ids=frozenset({"rocky", "almalinux", "fedora"}),
)
