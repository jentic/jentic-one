"""Application context providing access to configuration and databases."""

from __future__ import annotations

import asyncio
import time
from types import TracebackType
from typing import TYPE_CHECKING

import structlog

from jentic_one.shared.config import AppConfig, load_config
from jentic_one.shared.crypto import EncryptionService
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.provider_config_store import load_provider_configs
from jentic_one.shared.release_check import ReleaseChecker

if TYPE_CHECKING:
    from jentic_one.control.services.credentials.providers.base import CredentialProvider
    from jentic_one.control.services.credentials.providers.registry import ProviderRegistry
    from jentic_one.shared.telemetry.sink import TelemetrySink

logger = structlog.get_logger(__name__)

#: How long a provider replaced by a registry rebuild stays open before it is
#: closed. A request that resolved it just before the swap keeps using it, so
#: this outlasts a whole provider operation (a connect makes a few sequential
#: upstream calls, each under a 30 s HTTP timeout).
PROVIDER_RETIRE_GRACE_S = 120.0

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
        # Refresh single-flight: a refresh requested while another is running
        # waits for the lock, then is skipped if a rebuild that *started* after
        # it was requested has already completed (that rebuild read the DB after
        # the change that prompted the request had committed).
        self._provider_refresh_lock: asyncio.Lock | None = None
        self._provider_refresh_requested = 0
        self._provider_refresh_completed = 0
        # Providers replaced by a rebuild, with the monotonic time each batch
        # may be closed at; closed by the next rebuild after that, or at shutdown.
        self._retired_providers: list[tuple[float, list[CredentialProvider]]] = []
        self._provider_retire_grace_s = PROVIDER_RETIRE_GRACE_S
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
    def resolves_providers(self) -> bool:
        """Whether this process resolves credential providers (control, broker).

        Such a process merges the DB-backed provider configs at boot and keeps
        its registry in step with later writes (see :meth:`refresh_providers`).
        """
        return self._refresh_providers_on_boot

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
        """The process's provider registry: an in-memory lookup, never a DB read.

        Built lazily from YAML config. Runtime (DB-backed) provider configs are
        merged in by :meth:`refresh_providers` — at boot, after an admin write
        in this process, and whenever another process commits a change to the
        stored configs (the app factory subscribes the refresh to the
        ``InvalidationBus``). ``get()`` stays synchronous.
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
        shared reader, decrypts known secret fields, then builds a new registry
        (reusing every provider whose config is unchanged) and swaps it in.
        Dynamic entries override YAML entries of the same name. Subsequent
        ``providers.get(...)`` calls resolve against the new registry; a request
        already holding a replaced provider finishes on it, and the replaced
        provider is closed by the first refresh after
        :data:`PROVIDER_RETIRE_GRACE_S` has passed, or at :meth:`shutdown`.

        Concurrent calls are serialised. A call is answered without a rebuild of
        its own when a rebuild that started after the call was made has already
        completed: that rebuild read the stored configs after the caller's change.
        """
        if self._provider_refresh_lock is None:
            self._provider_refresh_lock = asyncio.Lock()
        self._provider_refresh_requested += 1
        requested = self._provider_refresh_requested
        async with self._provider_refresh_lock:
            if self._provider_refresh_completed >= requested:
                return self.providers
            started = self._provider_refresh_requested
            new = await self._build_providers()
            self._swap_providers(new)
            self._provider_refresh_completed = started
        await self._close_retired_providers()
        return new

    async def _build_providers(self) -> ProviderRegistry:
        from jentic_one.control.services.credentials.providers.registry import ProviderRegistry

        async with self.admin_db.session() as session:
            stored = await load_provider_configs(session)
        decrypted = {name: self.decrypt_provider_config(cfg) for name, cfg in stored.items()}
        return ProviderRegistry.from_config_and_dynamic(
            self._config.credentials, decrypted, previous=self._providers
        )

    def _swap_providers(self, new: ProviderRegistry) -> None:
        """Install ``new`` and queue the providers it replaced for a deferred close."""
        old, self._providers = self._providers, new
        if old is None:
            return
        replaced = old.superseded_by(new)
        if replaced:
            deadline = time.monotonic() + self._provider_retire_grace_s
            self._retired_providers.append((deadline, replaced))

    async def _close_retired_providers(self, *, force: bool = False) -> None:
        """Close retired providers whose grace has passed (all of them when ``force``)."""
        from jentic_one.control.services.credentials.providers.registry import aclose_providers

        now = time.monotonic()
        due = [batch for deadline, batch in self._retired_providers if force or deadline <= now]
        self._retired_providers = [
            entry for entry in self._retired_providers if not (force or entry[0] <= now)
        ]
        # Due batches leave the queue before any await, so a concurrent call
        # never closes the same batch twice. A cancelled close re-queues the
        # batches it had not reached, so shutdown still closes them.
        for index, batch in enumerate(due):
            try:
                await aclose_providers(batch)
            except BaseException:
                self._retired_providers.extend((now, rest) for rest in due[index + 1 :])
                raise

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
        # On failure we keep the lazy YAML-only registry; the next refresh (an
        # admin write here, or a change notification) reconciles DB state.
        if (
            self._refresh_providers_on_boot
            and self._is_allowed("admin")
            and self._admin_db is not None
        ):
            try:
                await self.refresh_providers()
            except Exception as exc:
                logger.warning(
                    "provider_refresh_on_boot_failed",
                    error_type=type(exc).__name__,
                    actionable_step=(
                        "Check that this process can read the admin database and holds "
                        "the credential keyset; the YAML-configured providers stay in "
                        "use until a later refresh succeeds."
                    ),
                )

    async def shutdown(self) -> None:
        """Close the provider registry's providers, then all connected databases."""
        from jentic_one.control.services.credentials.providers.registry import aclose_providers

        await self._close_retired_providers(force=True)
        if self._providers is not None:
            await aclose_providers(self._providers.list_all().values())
        # A later startup builds afresh: a rebuild must never carry over a
        # provider instance that has been closed.
        self._providers = None
        self._provider_refresh_lock = None
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
