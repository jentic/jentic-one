"""Application context providing access to configuration and databases."""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import TYPE_CHECKING

import structlog

from jentic_one.shared.cache import AsyncTTLCache
from jentic_one.shared.config import AppConfig, load_config
from jentic_one.shared.crypto import EncryptionService
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.provider_config_store import load_provider_configs
from jentic_one.shared.release_check import ReleaseChecker
from jentic_one.shared.state import SharedStateBackend, build_state_backend

if TYPE_CHECKING:
    from jentic_one.control.services.credentials.providers.registry import ProviderRegistry
    from jentic_one.registry.services.catalog.manifest_builder import PreviewProjection
    from jentic_one.shared.broker.protocols import BrokerCacheInvalidatorProtocol
    from jentic_one.shared.telemetry.sink import TelemetrySink

logger = structlog.get_logger(__name__)

ALL_DB_NAMES: frozenset[str] = frozenset(("registry", "admin", "control"))


class Context:
    """Central context object holding config and database connections.

    Use as an async context manager for lifecycle management:

        async with Context(config) as ctx:
            async with ctx.registry_db.session() as session:
                result = await session.execute(text("SELECT 1"))

    Restrict access to specific databases:

        ctx = Context(config, allowed_dbs={"registry", "admin"})
        ctx.control_db  # raises RuntimeError
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        allowed_dbs: set[str] | None = None,
        refresh_providers_on_boot: bool = True,
    ) -> None:
        self._config = config if config is not None else load_config()
        # Whether startup() merges the DB-backed provider configs into the
        # provider registry. Only processes that resolve providers (control,
        # broker) need it; the composition root turns it off for the rest, which
        # may not even hold the keyset those configs' client secrets decrypt with.
        self._refresh_providers_on_boot = refresh_providers_on_boot
        self._allowed_dbs: frozenset[str] | None = (
            frozenset(allowed_dbs) if allowed_dbs is not None else None
        )
        self._encryption: EncryptionService | None = None
        self._providers: ProviderRegistry | None = None
        self._registry_db: DatabaseSession | None = None
        self._admin_db: DatabaseSession | None = None
        self._control_db: DatabaseSession | None = None
        self._update_sweep_lock: asyncio.Lock | None = None
        self._release_checker: ReleaseChecker | None = None
        self._catalog_preview_cache: AsyncTTLCache[str, PreviewProjection] | None = None
        self._state_backend: SharedStateBackend | None = None
        self._state_backend_closed = False
        # Product telemetry (issue #446). Resolved + owned by the lifespan when
        # telemetry is enabled; both stay None otherwise (the single consent gate).
        self._instance_id: str | None = None
        self._telemetry: TelemetrySink | None = None
        # In-process broker cache invalidators (#1837). Populated by
        # ``install_broker_auth`` when a broker app shares this Context.
        self._broker_cache_invalidators: list[BrokerCacheInvalidatorProtocol] = []

    def _is_allowed(self, name: str) -> bool:
        return self._allowed_dbs is None or name in self._allowed_dbs

    def is_db_allowed(self, name: str) -> bool:
        """Return True if the named database is permitted by the allowed_dbs filter."""
        return self._is_allowed(name)

    def has_db(self, name: str) -> bool:
        """Return True if the named database is allowed and has been initialised."""
        if not self._is_allowed(name):
            return False
        return getattr(self, f"_{name}_db") is not None

    @property
    def has_encryption_keyset(self) -> bool:
        """Whether a credentials encryption keyset is configured.

        The one predicate for "can this process encrypt": the broker files a
        held execution only when it is true, and the worker decrypts held
        payloads with :attr:`encryption` under the same condition.
        """
        return bool(self._config.credentials.encryption.entries)

    @property
    def encryption(self) -> EncryptionService:
        """Lazily-constructed encryption service.

        When a keyset is configured, :meth:`startup` touches this property so
        an invalid keyset fails at boot; without one, the ConfigError surfaces
        here at first credential use.
        """
        if self._encryption is None:
            self._encryption = EncryptionService(self._config.credentials.encryption)
        return self._encryption

    @property
    def providers(self) -> ProviderRegistry:
        """Lazily-built provider registry from credentials config.

        Built synchronously from YAML config only. Runtime (DB-backed) provider
        configs are merged in by :meth:`refresh_providers`, which the admin API
        calls after a write so a fresh process picks up DB config without a
        restart. ``get()`` stays synchronous (no DB access on the hot path).
        """
        if self._providers is None:
            from jentic_one.control.services.credentials.providers.registry import (
                ProviderRegistry,
            )

            self._providers = ProviderRegistry.from_config(self._config.credentials)
        return self._providers

    async def refresh_providers(self) -> ProviderRegistry:
        """Rebuild the provider registry from YAML config + runtime DB config.

        Reads runtime provider configs from the admin DB via the boundary-safe
        shared reader, decrypts known secret fields, then rebuilds the
        synchronous registry. Dynamic entries override YAML entries of the same
        name. The swapped-in registry is what subsequent ``providers`` access
        and ``providers.get(...)`` resolve against — no restart required.

        SINGLE-PROCESS ONLY. This rebuilds the registry on *this* process's
        Context. In the combined deployment (one process serving all surfaces)
        an admin PUT immediately affects the same in-memory registry that the
        control/broker paths resolve against. In a parts/multi-process topology
        (admin, control, registry as separate services — see
        ``deploy/helm/values/local-parts.yaml``) the control/broker processes
        do NOT share this Context: they pick up DB-backed provider config only
        at their own boot (and only if granted admin-DB access), not after an
        admin PUT in another process. Cross-process propagation (control reads
        the DB on discovery, or a notify/poll mechanism) is tracked as a
        follow-up. See the PR description's "Topology / propagation" note.
        """
        from jentic_one.control.services.credentials.providers.registry import ProviderRegistry

        async with self.admin_db.session() as session:
            stored = await load_provider_configs(session)

        decrypted = {name: self.decrypt_provider_config(cfg) for name, cfg in stored.items()}
        self._providers = ProviderRegistry.from_config_and_dynamic(
            self._config.credentials, decrypted
        )
        return self._providers

    def decrypt_provider_config(self, cfg: dict[str, object]) -> dict[str, object]:
        """Return a copy of a stored provider config with secret fields decrypted.

        Stored configs persist secret fields (currently ``client_secret``) as
        ciphertext; the registry needs plaintext to construct the provider.
        """
        result = dict(cfg)
        secret = result.get("client_secret")
        if isinstance(secret, str) and secret:
            result["client_secret"] = self.encryption.decrypt(secret)
        return result

    @property
    def config(self) -> AppConfig:
        """Read access to the resolved application configuration."""
        return self._config

    @property
    def instance_id(self) -> str | None:
        """Opaque per-deployment telemetry id, or None when telemetry is off."""
        return self._instance_id

    @instance_id.setter
    def instance_id(self, value: str | None) -> None:
        self._instance_id = value

    @property
    def telemetry(self) -> TelemetrySink | None:
        """Active telemetry sink handle, or None when telemetry is off/unwired."""
        return self._telemetry

    @telemetry.setter
    def telemetry(self, value: TelemetrySink | None) -> None:
        self._telemetry = value

    def register_broker_cache_invalidator(
        self, invalidator: BrokerCacheInvalidatorProtocol
    ) -> None:
        """Register an in-process broker cache invalidator on this Context (#1837).

        Called by ``install_broker_auth``. Idempotent for the same instance.
        """
        if invalidator not in self._broker_cache_invalidators:
            self._broker_cache_invalidators.append(invalidator)

    def invalidate_broker_caches(
        self,
        *,
        agent_id: str | None = None,
        credential_id: str | None = None,
        rule_set_id: str | None = None,
        token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Notify registered in-process broker caches to evict stale entries (#1837).

        ``user_id`` evicts the user's cached token and actor-status verdicts.
        ``agent_id`` evicts the same verdicts for the agent, plus its binding
        and rule cache entries.

        No-op when no broker cache is wired onto this ``Context`` (e.g. a
        control-plane process in a multi-process deployment, where the broker's
        short TTL bounds staleness).
        """
        for invalidator in list(self._broker_cache_invalidators):
            invalidator.invalidate(
                agent_id=agent_id,
                credential_id=credential_id,
                rule_set_id=rule_set_id,
                token=token,
                user_id=user_id,
            )

    @property
    def registry_db(self) -> DatabaseSession:
        """Database session manager for the registry schema."""
        if not self._is_allowed("registry"):
            raise RuntimeError("Access to 'registry' database is not allowed in this context")
        if self._registry_db is None:
            self._registry_db = DatabaseSession(self._config.databases.registry)
        return self._registry_db

    @property
    def admin_db(self) -> DatabaseSession:
        """Database session manager for the admin schema."""
        if not self._is_allowed("admin"):
            raise RuntimeError("Access to 'admin' database is not allowed in this context")
        if self._admin_db is None:
            self._admin_db = DatabaseSession(self._config.databases.admin)
        return self._admin_db

    @property
    def control_db(self) -> DatabaseSession:
        """Database session manager for the control schema."""
        if not self._is_allowed("control"):
            raise RuntimeError("Access to 'control' database is not allowed in this context")
        if self._control_db is None:
            self._control_db = DatabaseSession(self._config.databases.control)
        return self._control_db

    @property
    def update_sweep_lock(self) -> asyncio.Lock:
        """Process-wide guard so the catalog update-notify sweep never runs concurrently.

        The scanner (owned cadence) and the read-path ``trigger_update_notify_sweep``
        (``POST /catalog:refresh`` piggyback) can both fire a sweep in one process; without a
        guard, two in-flight sweeps can each read ``last_notified_digest=None`` for a
        first-time change and both emit a duplicate (now *actionable*)
        ``catalog.update_available`` event. Serializing on this lock collapses the in-process
        race to a single emit; ``CatalogService`` pairs it with a registry-DB advisory lock
        for the cross-process half (other replicas/workers). Lazily created on first access
        so it binds to the running loop.
        """
        if self._update_sweep_lock is None:
            self._update_sweep_lock = asyncio.Lock()
        return self._update_sweep_lock

    @property
    def release_checker(self) -> ReleaseChecker:
        """Process-wide cache for the "latest jentic-one release" lookup.

        Held on the Context so its in-memory TTL cache and single-flight lock are
        shared across every ``GET /system/version`` request on this process
        (rather than re-fetching per request). Lazily constructed on first access.
        See ``shared/release_check.py``.
        """
        if self._release_checker is None:
            self._release_checker = ReleaseChecker(self._config)
        return self._release_checker

    @property
    def catalog_preview_cache(self) -> AsyncTTLCache[str, PreviewProjection]:
        """Process-wide cache of catalog spec preview projections, keyed by spec URL.

        ``CatalogService`` is built per request, so the cache lives here to be
        shared across every ``GET /catalog/{id}/operations`` on this process.
        Sized by ``catalog.preview_cache_*``; lazily constructed on first access.
        It is per process only: ``POST /catalog:refresh`` clears this process's
        copy, while other workers or replicas keep theirs until the TTL expires.
        """
        if self._catalog_preview_cache is None:
            cfg = self._config.catalog
            # A zero TTL or entry bound disables caching, failures included; the
            # cache expresses that as a zero TTL because its store requires a
            # positive entry bound. Nothing is stored, so every call loads —
            # though concurrent previews of one spec URL still share a single
            # fetch, which is the single-flight coalescing, not the cache.
            disabled = cfg.preview_cache_max_entries <= 0 or cfg.preview_cache_ttl_seconds <= 0
            self._catalog_preview_cache = AsyncTTLCache(
                max_entries=max(1, cfg.preview_cache_max_entries),
                ttl_seconds=0 if disabled else cfg.preview_cache_ttl_seconds,
                failure_ttl_seconds=0 if disabled else cfg.preview_cache_failure_ttl_seconds,
            )
        return self._catalog_preview_cache

    @property
    def state_backend(self) -> SharedStateBackend:
        """The process's one shared-state backend, built from ``config.state``.

        Every surface this process serves (the auth limiters and single-use
        markers, the broker's rate limiter, circuit breaker, idempotency
        store and auth-failure counters, the control connect limiters, and
        the MCP session-event dedupe) shares this instance, so the
        memory backend's budgets cap the whole process. Built on first access,
        when the app is assembled, so an unknown or misconfigured backend
        fails before the process serves; the app lifespan closes it once,
        last (:meth:`close_state_backend`).
        """
        if self._state_backend is None:
            self._state_backend = build_state_backend(
                self._config.state, apps=frozenset(self._config.apps)
            )
        return self._state_backend

    async def startup(self) -> None:
        """Connect allowed databases (validating the encryption keyset first)."""
        # A configured-but-invalid keyset (wrong-length key, duplicate id,
        # dangling active_id) must fail boot loudly, not surface as scattered
        # per-credential errors at first use. Checked before the DB connects so
        # a failure leaves nothing to unwind. A config without a keyset still
        # boots — credential writes then fail with the lazy ConfigError below.
        if self._config.credentials.encryption.entries:
            _ = self.encryption
        connected: list[DatabaseSession] = []
        try:
            for name in ("registry", "admin", "control"):
                if self._is_allowed(name):
                    db = getattr(self, f"{name}_db")
                    await db.connect()
                    connected.append(db)
        except Exception:
            for db in reversed(connected):
                await db.close()
            raise
        # Pick up runtime (DB-backed) provider configs on boot so a fresh
        # process reflects DB state without a restart. Only meaningful where the
        # admin DB is available (the registry owner) and the process resolves
        # providers at all (refresh_providers_on_boot); any other process keeps
        # the YAML-only registry from the lazy `providers` property.
        #
        # Best-effort: a transient admin-DB blip at boot must not crash startup.
        # On failure we keep the lazy YAML-only registry and let the next admin
        # write (which calls refresh_providers explicitly) reconcile DB state.
        if (
            self._refresh_providers_on_boot
            and self._is_allowed("admin")
            and self._admin_db is not None
        ):
            try:
                await self.refresh_providers()
            except Exception as exc:
                logger.warning("provider_refresh_on_boot_failed", error=str(exc))

    async def shutdown(self) -> None:
        """Close all connected databases.

        The shared-state backend is closed separately, by
        :meth:`close_state_backend`, so the app lifespan can close it after
        every surface teardown that may still touch it.
        """
        for db in (self._control_db, self._admin_db, self._registry_db):
            if db is not None:
                await db.close()

    async def close_state_backend(self) -> None:
        """Close the process's shared-state backend, if built. Idempotent.

        Best-effort, like the other shutdown steps: a failing ``aclose()`` is
        logged, not raised, so it never masks the error that ended a lifespan
        (a failed startup, say). The closed instance stays in place; the
        process does not build a second one.
        """
        backend = self._state_backend
        if backend is None or self._state_backend_closed:
            return
        self._state_backend_closed = True
        try:
            await backend.aclose()
        except Exception:
            logger.warning(
                "state_backend_close_failed",
                backend=self._config.state.backend,
                exc_info=True,
            )

    async def __aenter__(self) -> Context:
        await self.startup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            await self.shutdown()
        finally:
            await self.close_state_backend()
