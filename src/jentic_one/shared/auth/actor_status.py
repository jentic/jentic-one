"""The ``users`` row facts every credential path re-checks before trusting a user.

One primary-key read answers every question a user credential depends on: is
the account active, is it deleted (``users.deleted_at`` — delete is terminal,
so a deleted row is refused even if it reads active), and was the credential
issued under the account's current login-session generation
(``users.session_generation``, bumped on disable, delete and password
change/reset)? The login-JWT verifier and the broker's queued-work
re-authorizer both read through here, so a change to user liveness lands in
one place.

Raw SQL against the admin schema: ``shared`` (and the broker) may not import
the ``admin`` ORM.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Boolean, Integer, text
from sqlalchemy.exc import DisconnectionError, OperationalError

from jentic_one.shared.db import DatabaseSession, DatabaseUnavailableError
from jentic_one.shared.db.connectivity import is_connection_loss, redact_sql

#: The login-JWT claim carrying the user's session generation at mint time. A
#: token without it reads as generation ``0`` (the column's starting value).
SESSION_GENERATION_CLAIM = "sgen"

_USER_STATUS = text(
    "SELECT active, session_generation, deleted_at IS NOT NULL AS deleted"
    " FROM users WHERE id = :user_id"
).columns(active=Boolean, session_generation=Integer, deleted=Boolean)


@dataclass(frozen=True)
class UserStatus:
    """The liveness facts of an existing ``users`` row."""

    active: bool
    #: Login JWTs carrying any other generation are refused.
    session_generation: int
    #: ``deleted_at`` is set: no credential for this user is accepted.
    deleted: bool

    @property
    def accepts_credentials(self) -> bool:
        """Whether a credential for this user may authenticate at all."""
        return self.active and not self.deleted


async def read_user_status(admin_db: DatabaseSession, user_id: str) -> UserStatus | None:
    """The user's liveness facts, or ``None`` when no ``users`` row exists.

    A database that cannot answer raises :class:`DatabaseUnavailableError`
    (never a raw driver error), so callers can tell "the user is gone" from
    "we could not ask" — the first is a refusal, the second a retryable 503.
    Neither path admits the caller.
    """
    try:
        async with admin_db.session() as session:
            row = (await session.execute(_USER_STATUS, {"user_id": user_id})).one_or_none()
    except (OperationalError, DisconnectionError) as exc:
        raise DatabaseUnavailableError(redact_sql(str(exc))) from exc
    except Exception as exc:
        # An unreachable server can surface as a bare network error from the
        # driver's connect (asyncpg raises ``ConnectionRefusedError``).
        if is_connection_loss(exc):
            raise DatabaseUnavailableError(redact_sql(str(exc))) from exc
        raise
    if row is None:
        return None
    return UserStatus(
        active=bool(row.active),
        session_generation=int(row.session_generation),
        deleted=bool(row.deleted),
    )
