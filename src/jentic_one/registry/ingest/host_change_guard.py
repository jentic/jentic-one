"""Server-host change guard: hold host changes on credential-bound APIs for review.

A catalog re-import (``catalog:import``, a default agent scope) or a draft
promote (``apis:write``) replaces the revision an API is served from. When the
new revision declares a different set of server hosts and the API has stored
credentials bound to an agent, making it current would change where those
credentials are sent. That step needs an operator: a caller holding
``credentials:write`` (implied by ``org:admin``).

- **Catalog re-import** without the operator scope: the new revision is kept
  as a DRAFT (origin preserved) and the current revision stays live. An
  operator reviews and promotes it.
- **Promote** without the operator scope: refused with
  ``HostChangeRequiresOperatorError`` (403 ``host_change_requires_operator``).

``credentials:write`` is the gate because the decision is about where stored
credentials go, it is not a default agent scope, and ``apis:write`` cannot be
the gate since promote already requires it.
"""

from __future__ import annotations

import structlog

from jentic_one.registry.repos.credential_binding_presence_repo import (
    CredentialBindingPresenceRepository,
)
from jentic_one.shared.auth.permission_catalog import CREDENTIALS_WRITE
from jentic_one.shared.auth.permissions import has_effective_permission
from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

#: Scope that may make a server-host change current on a credential-bound API.
HOST_CHANGE_OPERATOR_SCOPE = CREDENTIALS_WRITE


def may_approve_host_change(permissions: list[str] | None) -> bool:
    """True when the caller may make a server-host change current."""
    return has_effective_permission(permissions or [], HOST_CHANGE_OPERATOR_SCOPE)


async def api_has_bound_credentials(ctx: Context, *, vendor: str, name: str, version: str) -> bool:
    """True when any credential covering the API is bound to an agent or toolkit.

    Fails closed: if this process cannot reach the control or admin database,
    or a lookup errors, the API is treated as bound so the change is held.
    """
    if not ctx.is_db_allowed("control"):
        return True
    try:
        async with ctx.control_db.session() as session:
            credential_ids = await CredentialBindingPresenceRepository.covering_credential_ids(
                session, vendor=vendor, name=name, version=version
            )
            if not credential_ids:
                return False
            if await CredentialBindingPresenceRepository.any_toolkit_binding(
                session, credential_ids=credential_ids
            ):
                return True
        if not ctx.is_db_allowed("admin"):
            return True
        async with ctx.admin_db.session() as session:
            return await CredentialBindingPresenceRepository.any_agent_binding(
                session, credential_ids=credential_ids
            )
    except Exception:
        logger.warning(
            "credential_binding_presence_check_failed",
            api_vendor=vendor,
            api_name=name,
            api_version=version,
            exc_info=True,
        )
        return True
