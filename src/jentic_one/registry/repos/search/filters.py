"""Shared filter semantics every :class:`SearchStrategy` must apply.

Search results are restricted by three inputs, identically across strategies:

- **Active state** — without a pin, an API contributes only revisions whose
  state is in :data:`ACTIVE_REVISION_STATES`.
- **Revision pins** — ``revision_pins`` maps ``api_id -> revision_id``. A pinned
  API contributes *only* its pinned revision, whatever that revision's state;
  every unpinned API falls back to the active-state rule.
- **API filters** — when ``api_filters`` is non-empty, only revisions of those
  APIs are eligible.

A composite strategy (e.g. one fusing several child strategies) relies on every
child applying the *same* predicate, so the fused list can only contain
operations that predicate admits. Strategies built on SQLAlchemy Core apply
:func:`search_filter_clause`; a strategy that must hand-write SQL (e.g. against
a virtual table) builds its state predicate from :data:`ACTIVE_REVISION_STATES`
instead of restating the states.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.shared.models import ApiRevisionState

#: Revision states an unpinned search can surface. Mirrors the
#: ``ix_api_revisions_one_active`` partial unique index and ``is_current``: a
#: catalog import lands as IMPORTED and is searchable without a manual promote
#: to PUBLISHED; DRAFT and ARCHIVED revisions are never surfaced unless pinned.
ACTIVE_REVISION_STATES: tuple[ApiRevisionState, ...] = (
    ApiRevisionState.PUBLISHED,
    ApiRevisionState.IMPORTED,
)


def search_filter_clause(
    *,
    api_filters: list[uuid.UUID] | None = None,
    revision_pins: dict[uuid.UUID, uuid.UUID] | None = None,
) -> ColumnElement[bool]:
    """Return the pin/active-state/api-filter predicate over :class:`ApiRevision`.

    The statement it is applied to must join ``ApiRevision`` (typically on
    ``ApiRevision.id == Operation.revision_id``). Arguments take the same
    meaning as the matching :meth:`SearchStrategy.search_operations`
    parameters; ``None`` and empty collections both mean "no constraint".
    """
    active = ApiRevision.state.in_(ACTIVE_REVISION_STATES)
    pins = revision_pins or {}
    if pins:
        clause: ColumnElement[bool] = (
            ApiRevision.api_id.notin_(list(pins.keys())) & active
        ) | ApiRevision.id.in_(list(pins.values()))
    else:
        clause = active
    if api_filters:
        clause = and_(clause, ApiRevision.api_id.in_(api_filters))
    return clause
