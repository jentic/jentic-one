"""Short-TTL, single-flighted cache around direct-binding credential derivation.

The direct-binding twin of the pre-6b ``caching_toolkit_deriver`` (deleted with
the toolkit path): the cross-DB
``derive_credentials`` lookup (admin agent→credential bindings ∩ control
covering credentials) runs on every direct-path request — two DB hits per
request. Bindings change infrequently, so this wraps the authoritative resolver
in a short-TTL LRU keyed on ``(agent_id, vendor, name, version) →
CredentialDerivation``, expired on TTL and evicted in-process on binding and
credential mutations (#1837), and single-flighted so concurrent misses for one
key collapse to a single Admin+Control lookup.

This is a pure latency optimization layered *over* the authoritative DB lookup —
authorization correctness never depends on the cache, only its staleness is
bounded by the TTL. In a cluster the LRU is **per instance**, so a binding
granted/revoked/suspended via the binding API on another node only becomes
consistent here after the TTL lapses (acceptable for the read-mostly default; keep the TTL
short).
"""

from __future__ import annotations

import structlog

from jentic_one.shared.broker.protocols import CredentialDerivation, CredentialDeriverProtocol
from jentic_one.shared.cache import AsyncTTLCache

logger = structlog.get_logger(__name__)

DEFAULT_CREDENTIAL_CACHE_TTL_SECONDS = 30.0
# Bound the LRU so a high-cardinality spray of distinct (agent, api) tuples
# can't grow the cache without limit. Derivation keys are low-cardinality in
# practice (bounded by active agents times APIs), so this is generous headroom.
DEFAULT_MAX_CACHE_ENTRIES = 10_000


class CachingCredentialDeriver:
    """TTL-LRU + single-flight wrapper around a :class:`CredentialDeriverProtocol`.

    Implements ``CredentialDeriverProtocol`` itself so it drops in wherever the
    raw resolver is used. A hit within ``cache_ttl_seconds`` returns the cached
    result without touching the DB; concurrent misses for one key are coalesced
    into a single underlying ``derive_credentials`` call.

    The cached :class:`CredentialDerivation` is a frozen dataclass of frozen
    values, so it is returned directly (no defensive copy). Its ``api_served``
    and ``identity_mismatch`` are cached under the same TTL as the candidate
    list; those drive recovery guidance, never authorization, so bounded
    staleness is acceptable — the same argument that justifies caching the
    candidate list.
    """

    def __init__(
        self,
        inner: CredentialDeriverProtocol,
        *,
        cache_ttl_seconds: float = DEFAULT_CREDENTIAL_CACHE_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> None:
        self._inner = inner
        # The ``broker.credential_binding_cache.{hits,misses,evictions}`` counters,
        # on the ``broker`` meter they are published under.
        self._cache: AsyncTTLCache[str, CredentialDerivation] = AsyncTTLCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=max_entries,
            metrics_scope="broker",
            metrics_prefix="broker.credential_binding_cache",
        )

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        """Return the agent's derivation for the API, served from cache when fresh."""
        key = self._make_key(agent_id=agent_id, vendor=vendor, name=name, version=version)

        async def _load() -> CredentialDerivation:
            logger.debug("credential_binding_cache_miss", agent_id=agent_id, vendor=vendor)
            return await self._inner.derive_credentials(
                agent_id=agent_id, vendor=vendor, name=name, version=version
            )

        return await self._cache.get_or_load(key, _load)

    @staticmethod
    def _make_key(*, agent_id: str, vendor: str, name: str, version: str) -> str:
        # NUL-joined so component boundaries are unambiguous (no value contains it).
        return "\x00".join((agent_id, vendor, name, version))

    def invalidate_agent(self, agent_id: str) -> None:
        """Evict all cached derivations for ``agent_id`` (#1837)."""
        self._cache.invalidate_prefix(f"{agent_id}\x00")

    def invalidate_credential(self, credential_id: str) -> None:
        """Evict cached derivations when a credential is deleted or changed (#1837).

        A derivation entry's ``api_served`` and ``identity_mismatch`` fields can
        depend on any covering credential even when ``credential_id`` is not in
        ``entry.value.credentials``, so a credential mutation clears the cache.
        """
        self._cache.clear()

    def clear(self) -> None:
        """Drop all cached entries (useful for tests/operational invalidation)."""
        self._cache.clear()
