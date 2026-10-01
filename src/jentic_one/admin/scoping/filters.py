"""Admin-surface access filter builder for dynamic query scoping."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.scopes import OWNER_AGENTS_READ

ORG_ADMIN = "org:admin"

_OWNER_MODELS: dict[type[Any], Any] = {
    Agent: Agent.owner_id,
    User: User.id,
    Job: Job.created_by,
    ExecutionRecord: ExecutionRecord.actor_id,
}

_ID_MODELS: dict[type[Any], Any] = {
    Agent: Agent.id,
}

_DELEGATION_SCOPES: dict[type[Any], str] = {
    Agent: OWNER_AGENTS_READ,
}

# Models whose owner column records the acting subject (a user *or* an agent).
# The human owner of an agent is accountable for what it does, so a row created
# by one of the caller's agents is visible to the caller as well.
_OWNED_AGENT_ACTOR_MODELS: frozenset[type[Any]] = frozenset({Job, ExecutionRecord})


def build_access_filters(identity: Identity, model: type[Any]) -> list[ColumnElement[bool]]:
    """Build SQLAlchemy filter expressions scoping queries to the caller's visibility.

    Rules (evaluated in order):
    1. org:admin -> no restriction (empty list).
    2. Agent with delegation scope + parent_actor_id -> OR filter (owner or delegator).
    3. Otherwise -> owner == self OR id == self (self-access for agents).
    4. Models in ``_OWNED_AGENT_ACTOR_MODELS`` (``Job``, ``ExecutionRecord``) additionally admit
       rows created by an agent the caller owns (``Agent.owner_id == sub``).

    Raises ValueError for an unknown model or empty sub.
    """
    if ORG_ADMIN in identity.permissions:
        return []

    if not identity.sub:
        raise ValueError("empty sub reached scoped read")

    if model in _OWNER_MODELS:
        col = _OWNER_MODELS[model]
        id_col = _ID_MODELS.get(model)
        delegation_scope = _DELEGATION_SCOPES.get(model)

        conditions = [col == identity.sub]
        if id_col is not None:
            conditions.append(id_col == identity.sub)

        if (
            delegation_scope is not None
            and delegation_scope in identity.permissions
            and identity.parent_actor_id is not None
        ):
            conditions.append(col == identity.parent_actor_id)

        if model in _OWNED_AGENT_ACTOR_MODELS:
            owned_agent_ids = select(Agent.id).where(Agent.owner_id == identity.sub)
            conditions.append(col.in_(owned_agent_ids))

        return [or_(*conditions)]

    raise ValueError(f"Unknown model for access scoping: {model.__name__}")
