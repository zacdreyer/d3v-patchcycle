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
    for provider_cls, ids, id_like in _REGISTRY:
        if os_identity.os_id in ids or id_like.intersection(os_identity.id_like):
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
    id_like=frozenset({"debian"}),
)

# DNF provider (Phase 8 / V1.1): RHEL family. Registered per the
# support-honesty rule; L3 container matrix is the release gate.
from patchcycle.providers.dnf import DnfProvider  # noqa: E402

register(
    DnfProvider,
    ids=frozenset({"rhel", "rocky", "almalinux", "fedora", "centos"}),
    id_like=frozenset({"rhel", "fedora"}),
)
