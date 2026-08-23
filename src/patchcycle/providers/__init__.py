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
# V1 note: the APT provider is implemented in Phase 3 and registered then.
_REGISTRY: list[tuple[type[UpdateProvider], frozenset[str], frozenset[str]]] = []


def register(
    provider_cls: type[UpdateProvider],
    *,
    ids: frozenset[str],
    id_like: frozenset[str] = frozenset(),
) -> None:
    """Register a provider for exact os-release IDs and ID_LIKE fallbacks."""
    _REGISTRY.append((provider_cls, ids, id_like))


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
