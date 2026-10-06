"""Integration tests for ActorScopeGrantRepository against real PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.repos import ActorScopeGrantRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_grants(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(ActorScopeGrant))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(ActorScopeGrant))
        await session.commit()


async def test_grant_and_list(admin_db: DatabaseSession, clean_grants: None) -> None:
    actor_id = "agnt_test000000000000000000"
    async with admin_db.session() as session:
        g1 = await ActorScopeGrantRepository.grant(
            session,
            actor_id=actor_id,
            actor_type="agent",
            scope="read:apis",
            granted_by="usr_admin000000000000000000",
            created_by="usr_test",
        )
        g2 = await ActorScopeGrantRepository.grant(
            session,
            actor_id=actor_id,
            actor_type="agent",
            scope="write:apis",
            created_by="usr_test",
        )
        await session.commit()
        assert g1.id.startswith("asg_")
        assert g2.id.startswith("asg_")

    async with admin_db.session() as session:
        grants = await ActorScopeGrantRepository.list_for_actor(session, actor_id)
        scopes = {g.scope for g in grants}
        assert scopes == {"read:apis", "write:apis"}
        assert all(g.actor_type == "agent" for g in grants)


async def test_revoke(admin_db: DatabaseSession, clean_grants: None) -> None:
    actor_id = "agnt_test00000000000000000"
    async with admin_db.session() as session:
        await ActorScopeGrantRepository.grant(
            session,
            actor_id=actor_id,
            actor_type="agent",
            scope="read:apis",
            created_by="usr_test",
        )
        await ActorScopeGrantRepository.grant(
            session,
            actor_id=actor_id,
            actor_type="agent",
            scope="write:apis",
            created_by="usr_test",
        )
        await session.commit()

    async with admin_db.session() as session:
        revoked = await ActorScopeGrantRepository.revoke(
            session, actor_id=actor_id, scope="read:apis"
        )
        await session.commit()
        assert revoked is True

    async with admin_db.session() as session:
        grants = await ActorScopeGrantRepository.list_for_actor(session, actor_id)
        assert len(grants) == 1
        assert grants[0].scope == "write:apis"


async def test_revoke_nonexistent(admin_db: DatabaseSession, clean_grants: None) -> None:
    async with admin_db.session() as session:
        revoked = await ActorScopeGrantRepository.revoke(
            session, actor_id="agnt_fake0000000000000000000", scope="nonexistent"
        )
        await session.commit()
        assert revoked is False


async def test_unique_constraint(admin_db: DatabaseSession, clean_grants: None) -> None:
    actor_id = "agnt_uniq000000000000000000"
    async with admin_db.session() as session:
        await ActorScopeGrantRepository.grant(
            session, actor_id=actor_id, actor_type="agent", scope="read:apis", created_by="usr_test"
        )
        await session.commit()

    async with admin_db.session() as session:
        with pytest.raises(IntegrityError):
            await ActorScopeGrantRepository.grant(
                session,
                actor_id=actor_id,
                actor_type="agent",
                scope="read:apis",
                created_by="usr_test",
            )


async def test_list_for_actor_filters_by_type(
    admin_db: DatabaseSession, clean_grants: None
) -> None:
    actor_id = "usr_multi00000000000000000"
    async with admin_db.session() as session:
        await ActorScopeGrantRepository.grant(
            session, actor_id=actor_id, actor_type="user", scope="read:apis", created_by="usr_test"
        )
        await ActorScopeGrantRepository.grant(
            session,
            actor_id=actor_id,
            actor_type="agent",
            scope="write:apis",
            created_by="usr_test",
        )
        await session.commit()

    async with admin_db.session() as session:
        user_grants = await ActorScopeGrantRepository.list_for_actor(
            session, actor_id, actor_type="user"
        )
        assert len(user_grants) == 1
        assert user_grants[0].scope == "read:apis"

        agent_grants = await ActorScopeGrantRepository.list_for_actor(
            session, actor_id, actor_type="agent"
        )
        assert len(agent_grants) == 1
        assert agent_grants[0].scope == "write:apis"

        all_grants = await ActorScopeGrantRepository.list_for_actor(session, actor_id)
        assert len(all_grants) == 2
