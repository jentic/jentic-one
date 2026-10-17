"""Process-local generation counter for registry routing state.

The broker caches route discovery (METHOD+URL → operation) for a short TTL
(``jentic_one.broker.core.discovery_cache``). A registry write that changes
what a URL resolves to — a revision going live or leaving the live slot, an
API being deleted, a revision's URL-index rows being rewritten — must make
every cached route in **this process** stale as soon as it commits, without the
registry importing the broker.

The registry repositories call :func:`mark_routes_changed` on the session that
performs such a write. When that session commits, the generation returned by
:func:`route_generation` advances; caches stamp each entry with the generation
read before their lookup ran, so an entry filled from pre-commit state is never
served once the write is visible. A rolled-back session discards its mark.

The counter is per process. Other processes and workers (the broker always
runs as its own process) see a registry write only once their cache TTL lapses.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

_ROUTES_CHANGED_KEY = "jentic_one.routes_changed"

_generation = 0


def route_generation() -> int:
    """The current routing generation; it advances on every routing-change commit."""
    return _generation


def bump_route_generation() -> None:
    """Advance the routing generation, invalidating every route cached in this process."""
    global _generation
    _generation += 1


def mark_routes_changed(session: AsyncSession | Session) -> None:
    """Record that ``session`` holds a routing write; its commit bumps the generation."""
    session.info[_ROUTES_CHANGED_KEY] = True


@event.listens_for(Session, "after_commit")
def _bump_on_commit(session: Session) -> None:
    if session.info.pop(_ROUTES_CHANGED_KEY, False):
        bump_route_generation()


@event.listens_for(Session, "after_rollback")
def _discard_on_rollback(session: Session) -> None:
    session.info.pop(_ROUTES_CHANGED_KEY, None)
