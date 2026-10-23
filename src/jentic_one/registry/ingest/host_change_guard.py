"""Server-host change guard: hold host changes on credential-bound APIs for review.

A catalog re-import (``catalog:import``, a default agent scope) or a draft
promote (``apis:write``) replaces the revision an API is served from. When the
new revision declares a different set of server hosts and the API has stored
credentials bound to an agent, making it current would change where those
credentials are sent. That step needs an approver (``may_make_host_change_current``):

- an organisation admin (``org:admin``), or
- a caller holding ``credentials:write`` that owns every credential covering
  the API and every credential an open connect session on the API is filling.
  An agent counts as owning what its owning user created.

Outcomes:

- **Catalog re-import** not enqueued by an organisation admin: the new revision
  is kept as a DRAFT (origin preserved) and the current revision stays live.
  An approver reviews and promotes it.
- **Promote** by a caller who is not an approver: refused with
  ``HostChangeRequiresOperatorError`` (403 ``host_change_requires_operator``).
- **Pinning a draft** (``Jentic-Revision``) routes calls through it without a
  promote, so the same check applies per call
  (``RegistryService.resolve_revision_pin``): a caller without
  ``credentials:write`` is refused, an organisation admin is not, and anyone
  else may use the draft only with a credential they own (the broker checks
  the credential it selects).

``credentials:write`` is required because the decision is about where stored
credentials go and it is not a default agent scope; ``apis:write`` cannot be
the gate since promote already requires it. Ownership is required on top so a
caller cannot move another user's credential to hosts that user never approved.
The 403 detail names the rule, never whether anyone has a bound credential or
an open session on the API.
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
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import CREDENTIALS_WRITE, ORG_ADMIN
from jentic_one.shared.auth.permissions import has_effective_permission
from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

#: Scope a caller other than an organisation admin needs, on top of owning the
#: API's credentials, to make a server-host change current.
HOST_CHANGE_OPERATOR_SCOPE = CREDENTIALS_WRITE


def is_host_change_admin(permissions: list[str] | None) -> bool:
    """True for an organisation admin, who may make any server-host change current."""
    return ORG_ADMIN in (permissions or [])


def holds_host_change_scope(permissions: list[str] | None) -> bool:
    """True when the caller holds ``credentials:write`` (implied by ``org:admin``)."""
    return has_effective_permission(permissions or [], HOST_CHANGE_OPERATOR_SCOPE)


def credential_owner_ids(identity: Identity) -> frozenset[str]:
    """The actor ids whose credentials count as the caller's own.

    The caller itself and, for an agent, its owning user (``parent_actor_id``).
    """
    return frozenset(i for i in (identity.sub, identity.parent_actor_id) if i)


async def owns_api_credentials(
    ctx: Context, identity: Identity, *, vendor: str, name: str, version: str
) -> bool:
    """True when the caller owns every credential the API's host change would move.

    That is every credential covering the API identity, bound or not, and the
    credential of every open connect session targeting it. Fails closed: if
    this process cannot reach the control database, or a lookup errors, the
    caller does not own them.
    """
    if not ctx.is_db_allowed("control"):
        return False
    try:
        async with ctx.control_db.session() as session:
            owners = await CredentialBindingPresenceRepository.covering_credential_owners(
                session, vendor=vendor, name=name, version=version
            )
            owners |= await CredentialBindingPresenceRepository.open_api_target_session_owners(
                session, vendor=vendor, name=name, version=version
            )
    except Exception:
        logger.warning(
            "credential_owner_check_failed",
            api_vendor=vendor,
            api_name=name,
            api_version=version,
            exc_info=True,
        )
        return False
    mine = credential_owner_ids(identity)
    return all(owner in mine for owner in owners)


async def may_make_host_change_current(
    ctx: Context | None, identity: Identity, *, vendor: str, name: str, version: str
) -> bool:
    """True when the caller may make a server-host change on the API current.

    An organisation admin always may. Otherwise the caller needs
    ``credentials:write`` and must own every credential the change would move
    (``owns_api_credentials``). Without a ``ctx`` ownership cannot be checked,
    so only an organisation admin may.
    """
    if is_host_change_admin(identity.permissions):
        return True
    if ctx is None or not holds_host_change_scope(identity.permissions):
        return False
    return await owns_api_credentials(ctx, identity, vendor=vendor, name=name, version=version)


async def api_has_bound_credentials(ctx: Context, *, vendor: str, name: str, version: str) -> bool:
    """True when any credential covering the API is bound to an agent.

    An open connect session that targets the API counts as bound: the human
    approving it enters a secret for the hosts pinned when the session opened,
    so a host change during that window must be held for an approver too.

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
    """``(current_hosts, new_hosts)`` when serving ``revision_id`` needs an approver.

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
