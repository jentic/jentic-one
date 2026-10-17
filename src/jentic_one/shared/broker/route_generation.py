"""Process-local generation counter for registry routing state.

The broker caches route discovery (METHOD+URL → operation) for a short TTL
(``jentic_one.broker.core.discovery_cache``). A registry write that changes
what a URL resolves to — a revision going live or leaving the live slot, an
API being deleted, a revision's URL-index rows being rewritten — makes every
route cached in **the process that commits it** stale at once, without the
registry importing the broker.

The registry repositories call :func:`mark_routes_changed` on the session that
performs such a write. When that session's outermost transaction commits, the
generation returned by :func:`route_generation` advances; caches stamp each
entry with the generation read before their lookup ran, so an entry filled
from pre-commit state is never served once the write is visible. A rollback of
the outermost transaction discards the mark. A SAVEPOINT (``begin_nested``)
neither bumps nor discards: its RELEASE does not make the write visible, and
rolling it back leaves any mark set earlier in the outer transaction in place
(a mark set inside a rolled-back savepoint survives too, which only costs one
spurious invalidation).

**Scope.** The counter is per process, and the broker always runs as its own
process (``python -m jentic_one`` refuses to bundle it with another surface),
while every routing write commits in the registry surface or the jobs worker.
So in a deployed topology this hook never fires in a broker: the broker sees a
promote, archive, import or delete only once ``broker.discovery_cache_ttl_s``
lapses. The hook keeps a resolver that shares a process with the writer (tests,
embedded use) fresh, and is the point a cross-process invalidation signal
attaches to.
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
    """Record that ``session`` holds a routing write; its outermost commit bumps the generation."""
    session.info[_ROUTES_CHANGED_KEY] = True


@event.listens_for(Session, "after_commit")
def _bump_on_commit(session: Session) -> None:
    # SQLAlchemy dispatches after_commit for a savepoint RELEASE too; the write
    # is visible to other connections only once the outermost transaction commits.
    if session.in_nested_transaction():
        return
    if session.info.pop(_ROUTES_CHANGED_KEY, False):
        bump_route_generation()


@event.listens_for(Session, "after_rollback")
def _discard_on_rollback(session: Session) -> None:
    # A savepoint rollback leaves the outer transaction (and its mark) alive.
    if session.in_nested_transaction():
        return
    session.info.pop(_ROUTES_CHANGED_KEY, None)
