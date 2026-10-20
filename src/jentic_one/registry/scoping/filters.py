"""Registry-surface access filter builder for dynamic query scoping."""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.registry.core.schema.notes import Note
from jentic_one.shared.auth.identity import Identity

ORG_ADMIN = "org:admin"


def build_access_filters(
    identity: Identity,
    model: type[Note],
    *,
    owned_agent_ids: list[str] | None = None,
    for_write: bool = False,
) -> list[ColumnElement[bool]]:
    """Build SQLAlchemy filter expressions scoping queries to the caller's visibility.

    Rules (evaluated in order):
    1. org:admin → no restriction (empty list).
    2. Otherwise → created_by == self, OR-merged on reads with
       ``created_by IN owned_agent_ids``.

    ``owned_agent_ids`` (READ call sites only) lets a human read the notes
    their own agents wrote. Agents live in the admin DB, so the service
    resolves the ids there and passes them in; this module stays single-DB.
    The ids are keyed on the caller's own ``sub`` — owner delegation
    (``parent_actor_id``) never widens notes, so an agent sees only its own.

    ``for_write=True`` builds the filter for a mutation: created_by == self
    only, so an owner can read an agent's note but not change it. Passing
    ``owned_agent_ids`` with it is a programming error (ValueError).

    Raises ValueError for an unknown model or empty sub.
    """
    if for_write and owned_agent_ids:
        raise ValueError("read-only visibility widening passed to a write filter")

    if ORG_ADMIN in identity.permissions:
        return []

    if not identity.sub:
        raise ValueError("empty sub reached scoped read")

    if model is Note:
        owner_clause = Note.created_by == identity.sub
        if owned_agent_ids:
            return [or_(owner_clause, Note.created_by.in_(owned_agent_ids))]
        return [owner_clause]

    raise ValueError(f"no access filter defined for {model.__name__}")
