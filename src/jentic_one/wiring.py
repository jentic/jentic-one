"""Top-level composition root for cross-surface wiring.

This module lives at the package root — outside any surface package — so it may
import multiple surfaces to wire them together. The architecture-boundary tests
only scan the surface packages (``broker``, ``registry``, ``admin``, ``control``,
``shared``, ``auth``); they intentionally do not constrain this composition layer.

Its jobs are injecting a concrete ``RegistryResolverProtocol`` (the registry's
in-process ``RegistryService``) onto the broker app, so the broker can resolve
upstream URLs to operations without importing ``jentic_one.registry``; a
``CatalogAutoImportProtocol`` onto the control-plane app so the connect flow can
auto-import a vendor's OpenAPI spec after a credential connects (broker requires
a registered API before it can route); and carrying the ``/mcp`` mount
(``jentic_one.mcp``) onto control-plane app shapes via the container seam.
Swapping an implementation later (e.g. an HTTP-backed resolver) is a change
here only — the surfaces are unaffected.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import structlog
from fastapi import FastAPI

from jentic_one.mcp.installer import (
    install_mcp_challenge_placeholder,
    install_mcp_mount,
    mcp_lifespan,
)
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.errors import CatalogEntryNotFoundError
from jentic_one.registry.services.inspect.registry_service import RegistryService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import ResolveResult, RevisionPinResult
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.actors import ActorType, actor_type_from_id
from jentic_one.shared.web.container import AppContainer

_logger = structlog.get_logger(__name__)


class InProcessRegistryResolver:
    """Registry-DB-backed ``RegistryResolverProtocol`` implementation.

    Opens a short-lived read-only session against the registry DB per call and
    delegates to the registry's ``RegistryService``. Holds the ``DatabaseSession``
    factory (not a live session) so it is safe to share across requests.
    """

    def __init__(self, registry_db: DatabaseSession) -> None:
        self._registry_db = registry_db

    async def resolve_operation(
        self, *, method: str, url: str, revision_id: uuid.UUID | None = None
    ) -> ResolveResult | None:
        async with self._registry_db.session() as session:
            return await RegistryService(session).resolve_operation(
                method=method, url=url, revision_id=revision_id
            )

    async def resolve_revision_pin(
        self,
        *,
        vendor: str,
        name: str,
        version: str,
        rev_label: str,
        identity: Identity,
    ) -> RevisionPinResult:
        async with self._registry_db.session() as session:
            return await RegistryService(session).resolve_revision_pin(
                vendor=vendor,
                name=name,
                version=version,
                rev_label=rev_label,
                identity=identity,
            )


def install_broker_registry_resolver(app: FastAPI, ctx: Context) -> None:
    """Inject the in-process registry resolver onto the broker app state."""
    app.state.broker_registry_resolver = InProcessRegistryResolver(ctx.registry_db)


class InProcessCatalogAutoImporter:
    """Registry-backed ``CatalogAutoImportProtocol`` implementation.

    Delegates to :class:`CatalogService`. Idempotent (skips when the entry is
    already registered) and best-effort (swallows every failure — the connect
    flow keeps working even if the catalog manifest is unreachable, the vendor
    slug isn't in the manifest, or the job store is temporarily wedged). The
    operator retains the manual ``POST /catalog/{api_id}:import`` escape hatch.

    Actor attribution: the ``initiator_actor_id`` is threaded through as the
    identity ``sub`` and its ``actor_type`` is derived from the id prefix
    (``usr_`` / ``agnt_`` / ``sva_``), so audit + job telemetry attribute the
    (re-)import to whoever finished the connect. The service method itself
    does not enforce ``catalog:import`` scope; the router does, and we do not
    go through the router.
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def ensure_imported(self, *, api_id: str, initiator_actor_id: str) -> str | None:
        try:
            svc = CatalogService(self._ctx)
            entry = await svc.get(api_id)
            if entry.registered:
                return None
            try:
                actor_type = actor_type_from_id(initiator_actor_id)
            except ValueError:
                actor_type = ActorType.USER
            identity = Identity(sub=initiator_actor_id, actor_type=actor_type)
            job_id = await svc.import_entry(api_id, identity)
            _logger.info(
                "catalog_auto_import.enqueued",
                api_id=api_id,
                job_id=job_id,
                initiator=initiator_actor_id,
            )
            return job_id
        except CatalogEntryNotFoundError:
            # The vendor's canonical slug isn't in the public catalog manifest.
            # Nothing to auto-import — the operator can wire a private registry
            # entry manually.
            _logger.info("catalog_auto_import.skipped.not_in_manifest", api_id=api_id)
            return None
        except Exception:
            _logger.warning("catalog_auto_import.failed", api_id=api_id, exc_info=True)
            return None

    async def current_version(self, *, api_id: str) -> str | None:
        """Return the imported api's current-revision version, or None.

        Direct lookup on the registry ``apis`` table by ``catalog_api_id``,
        gated on ``current_revision_id`` being set — otherwise the row
        exists but hasn't finished importing. Returns None on any error /
        no-match; the SPA polls until this becomes non-null.
        """
        try:
            from sqlalchemy import select

            from jentic_one.registry.core.schema.apis import Api

            async with self._ctx.registry_db.session() as session:
                stmt = (
                    select(Api.version)
                    .where(Api.catalog_api_id == api_id)
                    .where(Api.current_revision_id.is_not(None))
                    .limit(1)
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                return row if row else None
        except Exception:
            _logger.warning("catalog_current_version.failed", api_id=api_id, exc_info=True)
            return None


def install_control_catalog_auto_importer(app: FastAPI, ctx: Context) -> None:
    """Inject the catalog auto-importer onto the combined/control app state.

    Only meaningful when the process serves control AND has registry-DB access
    (the catalog reads live in the registry DB). The caller guards the call.
    """
    app.state.catalog_auto_importer = InProcessCatalogAutoImporter(ctx)


def build_default_container(ctx: Context) -> AppContainer:
    """Assemble the default ``AppContainer`` for this process's surface set.

    The composition root's factory for the DI seam. A downstream package can
    provide its own ``build_container`` that starts here and adds its ``Broker`` /
    extra routers, then calls the same app factories with the resulting container.

    Control-plane shapes (``"control" in ctx.config.apps``) additionally carry
    the ``/mcp`` mount's installer + session-manager lifespan
    (the mount never rides the broker). The mount itself is
    request-time gated by ``server.mcp.enabled``, so carrying it on every
    eligible shape adds no observable surface while the flag is off.

    Shapes serving the auth surface WITHOUT control instead carry the discovery
    challenge placeholder on ``/mcp``: they serve the RFC 8414/9728 discovery
    documents, so the ``resource_metadata`` pointers must keep landing on the
    discovery-chain 401 challenge (never a dangling 404) even though the real
    transport lives with control.
    """
    container = AppContainer.default(ctx)
    if "control" in ctx.config.apps:
        container = replace(
            container,
            extra_installers=(*container.extra_installers, install_mcp_mount),
            extra_lifespans=(*container.extra_lifespans, mcp_lifespan),
        )
    elif "auth" in ctx.config.apps:
        container = replace(
            container,
            extra_installers=(*container.extra_installers, install_mcp_challenge_placeholder),
        )
    return container
