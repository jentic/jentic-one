"""Self-identity read for fallback-resolved service-account callers (theme 8).

The service-account surface was removed in theme-8 Phase 2, but until the
Phase-4 drop an unmigrated ``sak_`` / ``jntc_live_`` key still resolves to an
``sva_`` identity through the resolver's SA-table fallback. ``GET /me`` and
the MCP ``me`` tool share this one read so both answer coherently for that
caller. It is strictly self-scoped — callers pass ``identity.sub`` — and never
imports the deleted ``ServiceAccountService``. Deleted in Phase 4.
"""

from __future__ import annotations

from jentic_one.admin.repos import LegacyServiceAccountReadRepository
from jentic_one.auth.services.errors import ActorNotFoundError
from jentic_one.auth.services.schemas.legacy_service_account import (
    LegacyServiceAccountIdentityView,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType


class LegacyServiceAccountIdentityService:
    """Read the caller's own retired service-account row + live grants."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def get_self(self, identity: Identity) -> LegacyServiceAccountIdentityView:
        """Return the caller's SA identity; raise ``ActorNotFoundError`` if gone.

        Self-scoped by construction: the only id read is ``identity.sub``, and
        only for a service-account identity.
        """
        if identity.actor_type != ActorType.SERVICE_ACCOUNT:
            raise ActorNotFoundError(identity.sub)
        async with self._ctx.admin_db.session() as session:
            row = await LegacyServiceAccountReadRepository.get_identity(session, identity.sub)
            if row is None:
                raise ActorNotFoundError(identity.sub)
            permissions = await LegacyServiceAccountReadRepository.list_permissions(
                session, identity.sub
            )
        return LegacyServiceAccountIdentityView(
            id=row.id,
            name=row.name,
            status=row.status,
            registered_by=row.registered_by,
            approved_by=row.approved_by,
            permissions=permissions,
        )
