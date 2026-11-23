"""Which users an actor lookup may label for the caller.

Composes the per-model read filters (:func:`build_access_filters`) into the
user relations behind ``GET /actors/lookup``. Kept apart from ``filters.py``
because one relation reads a model that has no per-caller scoping
(``OAuthClient``: ``GET /admin/oauth-clients`` lists every client to any
``oauth-clients:read`` holder), and ``filters.py`` holds only owner-scoped
models.
"""

from __future__ import annotations

from sqlalchemy import exists, or_, select
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.scoping.filters import ORG_ADMIN, build_access_filters
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_READ,
    EVENTS_READ,
    OAUTH_CLIENTS_READ,
    compute_effective,
)
from jentic_one.shared.models import AuditReason, AuditTargetType

#: Audit reasons ``GET /agents/{id}/api-key/history`` returns.
_API_KEY_AUDIT_REASONS = (AuditReason.API_KEY_ROTATED, AuditReason.API_KEY_REVOKED)


def build_lookup_user_filters(identity: Identity) -> list[ColumnElement[bool]]:
    """Filter the ``users`` rows an actor lookup may label for the caller.

    ``org:admin`` -> no restriction. Anyone else may label the users it can
    already see by id elsewhere, each relation gated on the read permission of
    the route that shows it:

    - itself, always;
    - with ``agents:read`` (``GET /agents``): the owner, registrant, approver
      or denier of an agent it can see (the ``Agent`` filter);
    - with ``agents:read``, for an agent the caller owns
      (``GET /agents/{id}/api-key`` and ``.../api-key/history``, owner or
      ``org:admin``): the ``created_by`` of its API key, and the actor of an
      audit entry that rotated or revoked that key. The history route shows
      the agent's 50 most recent audit entries; the relation is not capped,
      so it also labels the actor of an older key event on the same agent;
    - with ``events:read`` (``GET /events``): the actor of an event it can see;
    - the decider of a held execution it can see (``GET /executions/holds``,
      which takes no permission; the ``HeldExecution`` filter);
    - the consenting user of an OAuth grant bound to an agent the caller owns
      (``GET /agents/{id}/oauth-grants``, owner or ``org:admin``, which takes
      no permission);
    - with ``oauth-clients:read`` (``GET /admin/oauth-grants``): the consenting
      user of an OAuth grant bound to the caller itself (an agent caller; the
      ``OAuthClientGrant`` filter);
    - with ``oauth-clients:read`` (``GET /admin/oauth-clients``, which lists
      every client): the creator of any OAuth client.

    Every column above is set by the server from the acting identity. An API
    revision's or connect review's ``submitted_by`` is deliberately not a
    relation: ``POST /apis:import`` lets an ``org:admin`` set it to any value
    (everyone else's is replaced with the caller), so labelling it would let an
    admin's import name any user it likes.

    The event and held-execution relations are written out rather than
    reusing the ``Event`` / ``HeldExecution`` filters. Of the event filter's
    branches only ``created_by`` can match while ``actor_id`` is another user,
    so that arm is ``actor_id == users.id`` with ``created_by`` the caller or
    one of the caller's agents (served by ``ix_events_created_by_actor``). The
    held-execution filter is ``agent_id`` the caller or one of its agents; it
    is split the same way so each half probes ``ix_held_executions_agent_state``.
    The grant relation likewise: of the ``OAuthClientGrant`` filter's
    branches, only a grant bound to the caller (an agent caller) or to one of
    its agents can name another consenting user, and each half probes
    ``ix_oauth_client_grants_agent_id``. The API key relations probe the
    caller's own agents (``agents.owner_id``), then ``ix_audit_actor`` and
    ``ix_agent_credentials_agent_id``.
    ``GET /audit`` adds nothing: a non-admin sees only entries whose actor is
    itself or one of its agents, so no other user. Only the API key history
    route shows another user's audit entries. Credential creators are
    resolved by the control surface (see ``ActorService.lookup``).

    Each arm is a correlated ``EXISTS`` probed per looked-up user, so the cost
    is bounded by the ids being looked up, not by how many rows the caller
    can see.
    """
    if ORG_ADMIN in identity.permissions:
        return []

    perms = compute_effective(set(identity.permissions))
    owned_agent_ids = select(Agent.id).where(Agent.owner_id == identity.sub)
    conditions: list[ColumnElement[bool]] = [*build_access_filters(identity, User)]
    if AGENTS_READ in perms:
        conditions.append(
            exists().where(
                or_(
                    Agent.owner_id == User.id,
                    Agent.registered_by == User.id,
                    Agent.approved_by == User.id,
                    Agent.denied_by == User.id,
                ),
                *build_access_filters(identity, Agent),
            )
        )
        conditions.append(
            exists().where(
                AgentCredential.agent_id.in_(owned_agent_ids),
                AgentCredential.created_by == User.id,
            )
        )
        conditions.append(
            exists().where(
                AuditEntry.actor_id == User.id,
                AuditEntry.target_type == AuditTargetType.AGENT,
                AuditEntry.target_id.in_(owned_agent_ids),
                AuditEntry.reason.in_(_API_KEY_AUDIT_REASONS),
            )
        )
    if EVENTS_READ in perms:
        conditions.append(
            exists().where(Event.created_by == identity.sub, Event.actor_id == User.id)
        )
        conditions.append(
            exists().where(Event.created_by.in_(owned_agent_ids), Event.actor_id == User.id)
        )
    conditions.append(
        exists().where(HeldExecution.agent_id == identity.sub, HeldExecution.decided_by == User.id)
    )
    conditions.append(
        exists().where(
            HeldExecution.agent_id.in_(owned_agent_ids), HeldExecution.decided_by == User.id
        )
    )
    conditions.append(
        exists().where(
            OAuthClientGrant.agent_id.in_(owned_agent_ids),
            OAuthClientGrant.user_id == User.id,
        )
    )
    if OAUTH_CLIENTS_READ in perms:
        conditions.append(
            exists().where(
                OAuthClientGrant.agent_id == identity.sub, OAuthClientGrant.user_id == User.id
            )
        )
        conditions.append(exists().where(OAuthClient.created_by == User.id))
    # Not a relation, on purpose: API revision and connect-review
    # ``submitted_by``. ``POST /apis:import`` lets an ``org:admin`` set it to any
    # value, so labelling it would resolve whichever user that import names.
    return [or_(*conditions)]
