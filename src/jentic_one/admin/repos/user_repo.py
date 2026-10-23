"""Repository for User CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, exists, select, update
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.services.errors import UserDeletedError, UserNotFoundError
from jentic_one.shared.db.keyset import apply_desc_keyset
from jentic_one.shared.models import AuthProvider, InviteState


def _invite_state_clause(invite_state: InviteState) -> ColumnElement[bool]:
    """Match the invite state as the read path derives it, not the stored column.

    ``expired`` is never persisted: it is a stored ``pending`` user with no
    unredeemed, unexpired invite token. Splitting ``pending`` on the same
    active-token predicate as ``InviteTokenRepository.user_ids_with_active_invite``
    (DB clock via ``now()``) keeps the filter consistent with the rendered
    state and keeps cursor pages exact.
    """
    if invite_state not in (InviteState.PENDING, InviteState.EXPIRED):
        return User.invite_state == invite_state
    # Correlated on ``user_id`` so each candidate user probes
    # ``ix_invite_tokens_user_id`` instead of materialising every active token.
    has_active_invite = exists().where(
        InviteToken.user_id == User.id,
        InviteToken.redeemed_at.is_(None),
        InviteToken.expires_at > sa_func.now(),
    )
    is_pending = User.invite_state == InviteState.PENDING
    if invite_state == InviteState.PENDING:
        return and_(is_pending, has_active_invite)
    return and_(is_pending, ~has_active_invite)


class UserRepository:
    """Data access layer for User entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        id: str | None = None,
        email: str,
        first_name: str,
        last_name: str,
        active: bool = True,
        auth_provider: str = AuthProvider.LOCAL,
        external_subject_id: str | None = None,
        must_change_password: bool = False,
        invite_state: InviteState = InviteState.PENDING,
        created_by: str,
    ) -> User:
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            active=active,
            auth_provider=auth_provider,
            external_subject_id=external_subject_id,
            must_change_password=must_change_password,
            invite_state=invite_state,
            created_by=created_by,
        )
        if id is not None:
            user.id = id
        session.add(user)
        await session.flush()
        return user

    @staticmethod
    async def count(session: AsyncSession) -> int:
        """Return the total number of users — used to detect first-run setup."""
        result = await session.execute(select(sa_func.count()).select_from(User))
        return int(result.scalar_one())

    @staticmethod
    async def lock_active_holders_of(session: AsyncSession, permission: str) -> set[str]:
        """Return the ids of active users directly granted ``permission``, row-locked.

        Takes ``FOR NO KEY UPDATE`` on the matching user and grant rows
        (ordered by user id so concurrent callers lock in the same order), so
        two transactions each removing a different holder serialise: the second
        waits for the first to commit and then no longer sees the holder it
        disabled or stripped. Like :meth:`get_by_id_for_update`, the lighter
        lock conflicts with another removal or user write but not with the
        key-share lock a foreign-key insert (such as reissuing an invite token)
        takes on ``users``. SQLite has no row locks and ignores the clause; its
        single-writer model already serialises the writes.
        """
        stmt = (
            select(User.id)
            .join(UserPermissionGrant, UserPermissionGrant.user_id == User.id)
            .where(UserPermissionGrant.permission == permission, User.active.is_(True))
            .order_by(User.id)
            .with_for_update(key_share=True)
        )
        result = await session.execute(stmt)
        return set(result.scalars().all())

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        user_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> User | None:
        if filters is None:
            return await session.get(User, user_id)
        stmt = select(User).where(User.id == user_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id_for_update(session: AsyncSession, user_id: str) -> User | None:
        """Read the user row under a row lock, refreshing any cached copy.

        Callers that guard a write on ``deleted_at`` lock the row first, so a
        concurrent delete either commits before the read (and is seen) or
        waits for this transaction. Lock ``users`` before ``invite_tokens``,
        as delete does, so the two never deadlock. The lock is Postgres'
        ``FOR NO KEY UPDATE`` (the one an ``UPDATE`` of non-key columns
        takes): it conflicts with delete's write but not with the key-share
        lock a foreign-key insert (a new invite token) takes on the row.
        SQLite ignores the clause; its single-writer transactions already
        serialise the writes.
        """
        stmt = (
            select(User)
            .where(User.id == user_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(session: AsyncSession, email: str) -> User | None:
        stmt = select(User).where(sa_func.lower(User.email) == email.lower())
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def _update_live(
        session: AsyncSession, user_id: str, values: dict[str, object], *, action: str
    ) -> User:
        """Write ``values`` to the user row only while it is not deleted.

        The ``deleted_at IS NULL`` condition is part of the ``UPDATE`` itself:
        on Postgres a write that waits on a concurrent delete's row lock
        re-checks it once the delete commits and matches nothing, so the
        guard holds without a lock taken up front. A missing row raises
        :class:`UserNotFoundError`, a deleted one :class:`UserDeletedError`.
        """
        if values:
            stmt = (
                update(User)
                .where(User.id == user_id, User.deleted_at.is_(None))
                .values(**values)
                .execution_options(synchronize_session="fetch")
            )
            result = await session.execute(stmt)
            matched = bool(result.rowcount)  # type: ignore[attr-defined]
        else:
            matched = False
        user = await session.get(User, user_id, populate_existing=True)
        if user is None:
            raise UserNotFoundError(user_id)
        if not matched and user.deleted_at is not None:
            raise UserDeletedError(action)
        return user

    @staticmethod
    async def update(
        session: AsyncSession,
        user_id: str,
        *,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        active: bool | None = None,
        auth_provider: str | None = None,
        external_subject_id: str | None = None,
        must_change_password: bool | None = None,
        invite_state: str | None = None,
    ) -> User:
        """Set the given fields; refuses a deleted row (see :meth:`_update_live`)."""
        fields: dict[str, object | None] = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "active": active,
            "auth_provider": auth_provider,
            "external_subject_id": external_subject_id,
            "must_change_password": must_change_password,
            "invite_state": invite_state,
        }
        values = {name: value for name, value in fields.items() if value is not None}
        return await UserRepository._update_live(session, user_id, values, action="update")

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor: tuple[datetime, str] | None = None,
        invite_state: InviteState | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[User]:
        """Users ordered by ``(created_at, id) DESC``, keyset-paginated after ``cursor``."""
        stmt = select(User).order_by(User.created_at.desc(), User.id.desc()).limit(limit)
        stmt = apply_desc_keyset(stmt, User.created_at, User.id, cursor)
        if invite_state is not None:
            stmt = stmt.where(_invite_state_clause(invite_state))
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def disable(session: AsyncSession, user_id: str) -> User:
        """Deactivate the user; refuses a deleted row (see :meth:`_update_live`)."""
        return await UserRepository._update_live(
            session, user_id, {"active": False}, action="disable"
        )

    @staticmethod
    async def revoke_login_sessions(session: AsyncSession, user_id: str) -> User:
        """Bump the user's session generation: every login JWT issued so far is refused.

        Login JWTs minted afterwards (a fresh login, or the re-mint a password
        change returns) carry the new generation and are unaffected. The
        increment is a single ``UPDATE … SET n = n + 1``, so concurrent bumps
        never collapse into one.
        """
        user = await session.get(User, user_id)
        if user is None:
            raise UserNotFoundError(user_id)
        user.session_generation = User.session_generation + 1
        await session.flush()
        await session.refresh(user, ["session_generation"])
        return user

    @staticmethod
    async def mark_deleted(session: AsyncSession, user_id: str, *, email: str) -> bool:
        """Tombstone the user: anonymize the email, deactivate, stamp ``deleted_at``.

        ``deleted_at`` is the durable delete marker. The write is conditional on
        it being unset, so an already-deleted row keeps its first timestamp,
        and a concurrent second delete (which waits on the first's row lock,
        then re-checks the condition) matches nothing. Returns whether this
        call marked the row.
        """
        stmt = (
            update(User)
            .where(User.id == user_id, User.deleted_at.is_(None))
            .values(email=email, active=False, deleted_at=datetime.now(UTC))
            .execution_options(synchronize_session="fetch")
        )
        result = await session.execute(stmt)
        return bool(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    async def enable(session: AsyncSession, user_id: str) -> User:
        """Reactivate the user; refuses a deleted row (see :meth:`_update_live`).

        A delete that commits while this write waits on its row lock leaves
        the row inactive: the conditional ``UPDATE`` matches nothing and
        raises :class:`UserDeletedError`.
        """
        return await UserRepository._update_live(
            session, user_id, {"active": True}, action="enable"
        )
