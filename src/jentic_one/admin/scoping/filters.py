"""Admin-surface access filter builder for dynamic query scoping."""

from __future__ import annotations

from typing import Any

from sqlalchemy import exists, or_, select
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.users import User
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_READ,
    EVENTS_READ,
    OWNER_AGENTS_READ,
    compute_effective,
)

ORG_ADMIN = "org:admin"

_OWNER_MODELS: dict[type[Any], Any] = {
    Agent: Agent.owner_id,
    User: User.id,
    Job: Job.created_by,
    ExecutionRecord: ExecutionRecord.actor_id,
    Event: Event.actor_id,
    AuditEntry: AuditEntry.actor_id,
    OAuthClientGrant: OAuthClientGrant.user_id,
    # HeldExecution rows are owned by the agent that created the hold.
    # The human owner of that agent can see their agent's approval rows too
    # (via the _OWNED_AGENT_ACTOR_MODELS check).
    HeldExecution: HeldExecution.agent_id,
}

# Extra subject columns matched the same way as the owner column. An event names
# what it is about in ``created_by``: the credential owner on credential use and
# health events, the agent on binding events, the consenting user on an OAuth
# grant. ``actor_id`` stays whoever acted. An OAuth grant is owned by the
# consenting user (``user_id``) and is about the agent it binds (``agent_id``).
_EXTRA_OWNER_COLUMNS: dict[type[Any], tuple[Any, ...]] = {
    Event: (Event.created_by,),
    OAuthClientGrant: (OAuthClientGrant.agent_id,),
}

_ID_MODELS: dict[type[Any], Any] = {
    Agent: Agent.id,
}

_DELEGATION_PERMISSIONS: dict[type[Any], str] = {
    Agent: OWNER_AGENTS_READ,
}

# Models whose owner column records the acting subject (a user *or* an agent).
# The human owner of an agent is accountable for what it does, so a row created
# by one of the caller's agents is visible to the caller as well. An audit entry
# is attributed to whoever acted (``actor_id``), so the audit log shows the caller
# their own changes plus those made by their agents. An OAuth grant bound to one
# of the caller's agents is visible to the caller even when someone else consented.
_OWNED_AGENT_ACTOR_MODELS: frozenset[type[Any]] = frozenset(
    {Job, ExecutionRecord, Event, AuditEntry, OAuthClientGrant, HeldExecution}
)


def build_access_filters(identity: Identity, model: type[Any]) -> list[ColumnElement[bool]]:
    """Build SQLAlchemy filter expressions scoping queries to the caller's visibility.

    Rules (evaluated in order):
    1. org:admin -> no restriction (empty list).
    2. Agent with delegation scope + parent_actor_id -> OR filter (owner or delegator).
    3. Otherwise -> owner == self OR id == self (self-access for agents).
    4. Models in ``_OWNED_AGENT_ACTOR_MODELS`` (``Job``, ``ExecutionRecord``, ``Event``,
       ``AuditEntry``, ``OAuthClientGrant``) additionally admit rows whose subject is an
       agent the caller owns (``Agent.owner_id == sub``).
    5. Columns in ``_EXTRA_OWNER_COLUMNS`` are matched like the owner column (self, and
       owned agents for models in ``_OWNED_AGENT_ACTOR_MODELS``). Rows with no matching
       subject (system events or audit entries with a NULL actor) are visible only to
       org:admin.

    Raises ValueError for an unknown model or empty sub.
    """
    if ORG_ADMIN in identity.permissions:
        return []

    if not identity.sub:
        raise ValueError("empty sub reached scoped read")

    if model in _OWNER_MODELS:
        col = _OWNER_MODELS[model]
        id_col = _ID_MODELS.get(model)
        delegation_permission = _DELEGATION_PERMISSIONS.get(model)

        subject_cols = (col, *_EXTRA_OWNER_COLUMNS.get(model, ()))
        conditions = [c == identity.sub for c in subject_cols]
        if id_col is not None:
            conditions.append(id_col == identity.sub)

        if (
            delegation_permission is not None
            and delegation_permission in identity.permissions
            and identity.parent_actor_id is not None
        ):
            conditions.append(col == identity.parent_actor_id)

        if model in _OWNED_AGENT_ACTOR_MODELS:
            owned_agent_ids = select(Agent.id).where(Agent.owner_id == identity.sub)
            conditions.extend(c.in_(owned_agent_ids) for c in subject_cols)

        return [or_(*conditions)]

    raise ValueError(f"Unknown model for access scoping: {model.__name__}")


def build_lookup_user_filters(identity: Identity) -> list[ColumnElement[bool]]:
    """Filter the ``users`` rows an actor lookup may label for the caller.

    ``org:admin`` -> no restriction. Anyone else may label the users it can
    already see by id elsewhere, each relation gated on the read permission of
    the route that shows it:

    - itself, always;
    - the owner or approver of an agent it can see (the ``Agent`` filter),
      with ``agents:read`` (``GET /agents``);
    - the actor of an event it can see, with ``events:read`` (``GET /events``).

    The event relation is written out rather than reusing
    ``build_access_filters(identity, Event)``: of that filter's branches only
    ``created_by`` can match while ``actor_id`` is another user, so the arm is
    ``actor_id == users.id`` with ``created_by`` the caller or one of the
    caller's agents. Both halves are served by ``ix_events_created_by_actor``.
    Audit entries add nothing: a non-admin sees only those whose actor is
    itself or one of its agents, so no other user. Credential creators are
    resolved by the control surface (see ``ActorService.lookup``).

    Each arm is a correlated ``EXISTS`` probed per looked-up user, so the cost
    is bounded by the ids being looked up, not by how many rows the caller
    can see.
    """
    if ORG_ADMIN in identity.permissions:
        return []

    perms = compute_effective(set(identity.permissions))
    conditions: list[ColumnElement[bool]] = [*build_access_filters(identity, User)]
    if AGENTS_READ in perms:
        agent_filters = build_access_filters(identity, Agent)
        conditions.append(exists().where(Agent.owner_id == User.id, *agent_filters))
        conditions.append(exists().where(Agent.approved_by == User.id, *agent_filters))
    if EVENTS_READ in perms:
        owned_agent_ids = select(Agent.id).where(Agent.owner_id == identity.sub)
        conditions.append(
            exists().where(Event.created_by == identity.sub, Event.actor_id == User.id)
        )
        conditions.append(
            exists().where(Event.created_by.in_(owned_agent_ids), Event.actor_id == User.id)
        )
    return [or_(*conditions)]
