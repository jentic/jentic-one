"""Top-level composition root for cross-surface wiring.

This module lives at the package root — outside any surface package — so it may
import multiple surfaces to wire them together. The architecture-boundary tests
only scan the surface packages (``broker``, ``registry``, ``admin``, ``control``,
``shared``, ``auth``); they intentionally do not constrain this composition layer.

Its jobs are injecting a concrete ``RegistryResolverProtocol`` (the registry's
in-process ``RegistryService``) onto the broker app, so the broker can resolve
upstream URLs to operations without importing ``jentic_one.registry`` — and
carrying the ``/mcp`` mount (``jentic_one.mcp``) onto control-plane app shapes
via the container seam (phase 3). Swapping an implementation later (e.g. an
HTTP-backed resolver) is a change here only — the surfaces are unaffected.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from fastapi import FastAPI

from jentic_one.mcp.installer import (
    install_mcp_challenge_placeholder,
    install_mcp_mount,
    mcp_lifespan,
)
from jentic_one.registry.services.inspect.registry_service import RegistryService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import ResolveResult, RevisionPinResult
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.web.container import AppContainer


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


def build_default_container(ctx: Context) -> AppContainer:
    """Assemble the default ``AppContainer`` for this process's surface set.

    The composition root's factory for the DI seam. A downstream package can
    provide its own ``build_container`` that starts here and adds its ``Broker`` /
    extra routers, then calls the same app factories with the resulting container.

    Control-plane shapes (``"control" in ctx.config.apps``) additionally carry
    the ``/mcp`` mount's installer + session-manager lifespan (phase-3 item 1;
    master §6 Q1 — the mount never rides the broker). The mount itself is
    request-time gated by ``server.mcp.enabled``, so carrying it on every
    eligible shape adds no observable surface while the flag is off.

    Shapes serving the auth surface WITHOUT control instead carry the 3a-4
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
