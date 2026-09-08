"""Application context providing access to configuration and databases."""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import TYPE_CHECKING

import structlog

from jentic_one.shared.config import AppConfig, load_config
from jentic_one.shared.crypto import EncryptionService
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.provider_config_store import load_provider_configs
from jentic_one.shared.release_check import ReleaseChecker

if TYPE_CHECKING:
    from jentic_one.control.services.credentials.providers.registry import ProviderRegistry
    from jentic_one.shared.telemetry.sink import TelemetrySink

logger = structlog.get_logger(__name__)

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
    ) -> None:
        self._config = config if config is not None else load_config()
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
        # Product telemetry (issue #446). Resolved + owned by the lifespan when
        # telemetry is enabled; both stay None otherwise (the single consent gate).
        self._instance_id: str | None = None
        self._telemetry: TelemetrySink | None = None

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
    def encryption(self) -> EncryptionService:
        """Lazily-constructed encryption service (fails fast if keyset is invalid)."""
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
        race to a single emit. Cross-replica overlap is still possible but self-heals: the
        losing replica's next sweep sees the persisted digest and won't re-fire. Lazily
        created on first access so it binds to the running loop.
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

    async def startup(self) -> None:
        """Connect allowed databases."""
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
        # admin DB is available (the registry owner); a surface without admin
        # access keeps the YAML-only registry from the lazy `providers` property.
        #
        # Best-effort: a transient admin-DB blip at boot must not crash startup.
        # On failure we keep the lazy YAML-only registry and let the next admin
        # write (which calls refresh_providers explicitly) reconcile DB state.
        if self._is_allowed("admin") and self._admin_db is not None:
            try:
                await self.refresh_providers()
            except Exception as exc:
                logger.warning("provider_refresh_on_boot_failed", error=str(exc))

    async def shutdown(self) -> None:
        """Close all connected databases."""
        for db in (self._control_db, self._admin_db, self._registry_db):
            if db is not None:
                await db.close()

    async def __aenter__(self) -> Context:
        await self.startup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.shutdown()
