"""Repository for ExternalIdentity CRUD."""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.external_identities import ExternalIdentity


class ExternalIdentityRepository:
    """Data access layer for ExternalIdentity entities — flush-only, never commits."""

    @staticmethod
    async def get_by_issuer_subject(
        session: AsyncSession, issuer: str, external_subject: str
    ) -> ExternalIdentity | None:
        stmt = select(ExternalIdentity).where(
            ExternalIdentity.issuer == issuer,
            ExternalIdentity.external_subject == external_subject,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_unscoped(
        session: AsyncSession, provider: str, external_subject: str
    ) -> ExternalIdentity | None:
        """The oldest link for ``(provider, external_subject)`` that carries no real issuer.

        A link carries no real issuer when ``issuer`` is empty (a row written by
        a replica that does not set the column) or equals ``provider`` (the
        value the ``e5c9f3a7b802`` backfill gives rows of a provider without a
        well-known issuer). Neither value can be a configured issuer URL, so
        such a row was never keyed to any issuer.
        """
        stmt = (
            select(ExternalIdentity)
            .where(
                ExternalIdentity.provider == provider,
                ExternalIdentity.external_subject == external_subject,
                ExternalIdentity.issuer.in_(("", provider)),
            )
            .order_by(ExternalIdentity.created_at, ExternalIdentity.id)
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def has_link_under_other_issuer(
        session: AsyncSession, provider: str, issuer: str
    ) -> bool:
        """Whether any link of ``provider`` is keyed to a real issuer other than ``issuer``."""
        stmt = (
            select(ExternalIdentity.id)
            .where(
                ExternalIdentity.provider == provider,
                ExternalIdentity.issuer.not_in(("", provider, issuer)),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.first() is not None

    @staticmethod
    async def pin_issuer(session: AsyncSession, ext_id: ExternalIdentity, issuer: str) -> bool:
        """Key an unscoped link to ``issuer`` so later lookups match it by issuer.

        The update applies only while the row still carries the issuer it was
        read with, so of two concurrent first logins exactly one pins it.
        Returns whether this call pinned it.
        """
        stmt = (
            update(ExternalIdentity)
            .where(ExternalIdentity.id == ext_id.id, ExternalIdentity.issuer == ext_id.issuer)
            .values(issuer=issuer)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) == 1  # type: ignore[attr-defined]

    @staticmethod
    async def get_by_provider_subject(
        session: AsyncSession, provider: str, external_subject: str
    ) -> ExternalIdentity | None:
        stmt = select(ExternalIdentity).where(
            ExternalIdentity.provider == provider,
            ExternalIdentity.external_subject == external_subject,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        provider: str,
        external_subject: str,
        user_id: str,
        issuer: str | None = None,
        email: str | None = None,
        created_by: str,
    ) -> ExternalIdentity:
        resolved_issuer = (
            issuer
            if issuer is not None
            else ("https://accounts.google.com" if provider == "google" else provider)
        )
        ext_id = ExternalIdentity(
            provider=provider,
            issuer=resolved_issuer,
            external_subject=external_subject,
            user_id=user_id,
            email=email,
            created_by=created_by,
        )
        session.add(ext_id)
        await session.flush()
        return ext_id

    @staticmethod
    async def delete_for_user(session: AsyncSession, user_id: str) -> int:
        """Remove every external identity link for a user; returns the count removed."""
        stmt = delete(ExternalIdentity).where(ExternalIdentity.user_id == user_id)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
