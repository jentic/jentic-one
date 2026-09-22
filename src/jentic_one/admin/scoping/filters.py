"""Admin-surface access filter builder for dynamic query scoping."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OWNER_AGENTS_READ

ORG_ADMIN = "org:admin"

_OWNER_MODELS: dict[type[Any], Any] = {
    Agent: Agent.owner_id,
    User: User.id,
}

_ID_MODELS: dict[type[Any], Any] = {
    Agent: Agent.id,
}

_DELEGATION_SCOPES: dict[type[Any], str] = {
    Agent: OWNER_AGENTS_READ,
}


def build_access_filters(identity: Identity, model: type[Any]) -> list[ColumnElement[bool]]:
    """Build SQLAlchemy filter expressions scoping queries to the caller's visibility.

    Rules (evaluated in order):
    1. org:admin -> no restriction (empty list).
    2. Agent with delegation scope + parent_actor_id -> OR filter (owner or delegator).
    3. Otherwise -> owner == self OR id == self (self-access for agents).

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

        return [or_(*conditions)]

    raise ValueError(f"Unknown model for access scoping: {model.__name__}")
