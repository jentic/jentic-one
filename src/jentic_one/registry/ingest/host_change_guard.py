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
- **Pinning a draft** (``Jentic-Revision``) routes calls through it without a
  promote, so the same check applies: a draft whose hosts need review on a
  credential-bound API resolves only for the operator scope
  (``RegistryService.resolve_revision_pin``).

``credentials:write`` is the gate because the decision is about where stored
credentials go, it is not a default agent scope, and ``apis:write`` cannot be
the gate since promote already requires it.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from jentic_one.registry.core.server_hosts import hosts_from_servers, needs_review
from jentic_one.registry.repos.credential_binding_presence_repo import (
    CredentialBindingPresenceRepository,
)
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.repos.server_repo import ServerRepository
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
    """True when any credential covering the API is bound to an agent.

    An open connect session that targets the API counts as bound: the human
    approving it enters a secret for the hosts pinned when the session opened,
    so a host change during that window must be held for an operator too.

    Fails closed: if this process cannot reach the control or admin database,
    or a lookup errors, the API is treated as bound so the change is held.
    """
    if not ctx.is_db_allowed("control"):
        return True
    try:
        async with ctx.control_db.session() as session:
            if await CredentialBindingPresenceRepository.any_open_api_target_session(
                session, vendor=vendor, name=name, version=version
            ):
                return True
            credential_ids = await CredentialBindingPresenceRepository.covering_credential_ids(
                session, vendor=vendor, name=name, version=version
            )
        if not credential_ids:
            return False
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


async def pending_host_change(
    ctx: Context | None,
    session: Any,
    *,
    api_id: uuid.UUID,
    revision_id: uuid.UUID,
    vendor: str,
    name: str,
    version: str,
) -> tuple[list[str], list[str]] | None:
    """``(current_hosts, new_hosts)`` when serving ``revision_id`` needs an operator.

    The baseline is the API's current revision, else its most recently live one,
    so archiving the current revision first does not skip the check. When the API
    has never been live every origin of the revision is new. Review is needed when
    the host set changes or a host moves to plaintext ``http`` and the API has
    bound credentials (``api_has_bound_credentials``).
    Without a ``ctx`` the binding check cannot run, so the API counts as bound.
    """
    baseline = await ApiRevisionRepository.host_baseline_revision_id(
        session, api_id, exclude=revision_id
    )
    current = (
        hosts_from_servers(await ServerRepository.list_url_specs(session, baseline))
        if baseline is not None
        else frozenset()
    )
    new = hosts_from_servers(await ServerRepository.list_url_specs(session, revision_id))
    if not needs_review(current, new):
        return None
    if ctx is not None and not await api_has_bound_credentials(
        ctx, vendor=vendor, name=name, version=version
    ):
        return None
    return sorted(current), sorted(new)
