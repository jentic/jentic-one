"""Integration tests for UserRepository against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import UserRepository
from jentic_one.admin.services.errors import UserDeletedError, UserNotFoundError
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_users(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(User))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(User))
        await session.commit()


async def test_create_generates_ksuid(admin_db: DatabaseSession, clean_users: None) -> None:
    async with admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="alice@example.com",
            first_name="Alice",
            last_name="Smith",
            created_by="usr_test",
        )
        await session.commit()
        assert user.id.startswith("usr_")
        assert len(user.id) == 28


async def test_create_and_get_by_id(admin_db: DatabaseSession, clean_users: None) -> None:
    async with admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="bob@example.com",
            first_name="Bob",
            last_name="Jones",
            auth_provider="oidc",
            must_change_password=True,
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await session.commit()
        user_id = user.id

    async with admin_db.session() as session:
        loaded = await UserRepository.get_by_id(session, user_id)
        assert loaded is not None
        assert loaded.email == "bob@example.com"
        assert loaded.first_name == "Bob"
        assert loaded.last_name == "Jones"
        assert loaded.auth_provider == "oidc"
        assert loaded.must_change_password is True
        assert loaded.invite_state == "redeemed"
        assert loaded.active is True


async def test_get_by_email_case_insensitive(admin_db: DatabaseSession, clean_users: None) -> None:
    async with admin_db.session() as session:
        await UserRepository.create(
            session,
            email="Carol@Example.COM",
            first_name="Carol",
            last_name="White",
            created_by="usr_test",
        )
        await session.commit()

    async with admin_db.session() as session:
        found = await UserRepository.get_by_email(session, "carol@example.com")
        assert found is not None
        assert found.first_name == "Carol"


async def test_get_by_email_not_found(admin_db: DatabaseSession, clean_users: None) -> None:
    async with admin_db.session() as session:
        result = await UserRepository.get_by_email(session, "nobody@example.com")
        assert result is None


async def test_update_fields(admin_db: DatabaseSession, clean_users: None) -> None:
    async with admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="dave@example.com",
            first_name="Dave",
            last_name="Brown",
            created_by="usr_test",
        )
        await session.commit()
        user_id = user.id

    async with admin_db.session() as session:
        updated = await UserRepository.update(
            session, user_id, email="david@example.com", first_name="David"
        )
        await session.commit()
        assert updated.email == "david@example.com"
        assert updated.first_name == "David"
        assert updated.last_name == "Brown"


async def test_update_not_found(admin_db: DatabaseSession, clean_users: None) -> None:
    async with admin_db.session() as session:
        with pytest.raises(UserNotFoundError):
            await UserRepository.update(session, "usr_nonexistent000000000", email="x@x.com")


async def test_disable_and_enable(admin_db: DatabaseSession, clean_users: None) -> None:
    async with admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="eve@example.com",
            first_name="Eve",
            last_name="Green",
            created_by="usr_test",
        )
        await session.commit()
        user_id = user.id

    async with admin_db.session() as session:
        disabled = await UserRepository.disable(session, user_id)
        await session.commit()
        assert disabled.active is False

    async with admin_db.session() as session:
        enabled = await UserRepository.enable(session, user_id)
        await session.commit()
        assert enabled.active is True


@pytest.mark.parametrize("operation", ["enable", "disable", "update"])
async def test_writes_refuse_a_deleted_row(
    admin_db: DatabaseSession, clean_users: None, operation: str
) -> None:
    """``enable``, ``disable`` and ``update`` only write a row without ``deleted_at``."""
    async with admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="tomb@example.com",
            first_name="Tomb",
            last_name="Stone",
            created_by="usr_test",
        )
        await session.commit()
        user_id = user.id

    async with admin_db.session() as session:
        assert await UserRepository.mark_deleted(session, user_id, email=f"deleted-{user_id}@local")
        await session.commit()

    async with admin_db.session() as session:
        with pytest.raises(UserDeletedError, match=f"Cannot {operation} a deleted user"):
            if operation == "enable":
                await UserRepository.enable(session, user_id)
            elif operation == "disable":
                await UserRepository.disable(session, user_id)
            else:
                await UserRepository.update(session, user_id, email="tomb@example.com")
        await session.commit()

    async with admin_db.session() as session:
        row = await UserRepository.get_by_id(session, user_id)
    assert row is not None
    assert row.active is False
    assert row.email == f"deleted-{user_id}@local"


async def test_get_by_id_for_update_reads_the_row(
    admin_db: DatabaseSession, clean_users: None
) -> None:
    async with admin_db.transaction() as session:
        user = await UserRepository.create(
            session,
            email="locked@example.com",
            first_name="Lock",
            last_name="Ed",
            created_by="usr_test",
        )
        user_id = user.id

    async with admin_db.transaction() as session:
        locked = await UserRepository.get_by_id_for_update(session, user_id)
        assert locked is not None and locked.email == "locked@example.com"
        assert await UserRepository.get_by_id_for_update(session, "usr_absent") is None


async def test_list_all_with_limit(admin_db: DatabaseSession, clean_users: None) -> None:
    async with admin_db.session() as session:
        for i in range(5):
            await UserRepository.create(
                session,
                email=f"user{i}@example.com",
                first_name=f"User{i}",
                last_name="Test",
                created_by="usr_test",
            )
        await session.commit()

    async with admin_db.session() as session:
        page = await UserRepository.list_all(session, limit=3)
        assert len(page) == 3

        all_users = await UserRepository.list_all(session, limit=50)
        assert len(all_users) == 5
