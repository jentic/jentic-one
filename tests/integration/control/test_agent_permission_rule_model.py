"""Integration tests for the AgentPermissionRule model."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import StoredCredentialType

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_rules(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Empty ``agent_permission_rules`` and ``credentials`` before and after each test."""
    async with control_db.session() as session:
        await session.execute(delete(AgentPermissionRule))
        await session.execute(delete(Credential))
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(delete(AgentPermissionRule))
        await session.execute(delete(Credential))
        await session.commit()


async def _create_credential(control_db: DatabaseSession, cred_id: str = "cred_rule01") -> str:
    async with control_db.session() as session:
        session.add(
            Credential(
                id=cred_id,
                type=StoredCredentialType.API_KEY,
                name="Rule Test Credential",
                api_vendor="stripe",
                api_name="payments",
                api_version="v1",
            )
        )
        await session.commit()
    return cred_id


async def test_round_trip_defaults_and_id_prefix(
    control_db: DatabaseSession, clean_rules: None
) -> None:
    """A rule persists with an ``apr_`` id and correct column defaults."""
    cred_id = await _create_credential(control_db)
    async with control_db.session() as session:
        rule = AgentPermissionRule(
            agent_id="agt_test001",
            credential_id=cred_id,
            effect="allow",
            methods=["GET", "POST"],
            path="/v1/.*",
            sequence=0,
        )
        session.add(rule)
        await session.commit()
        rule_id = rule.id

    assert rule_id.startswith("apr_")
    async with control_db.session() as session:
        loaded = await session.get(AgentPermissionRule, rule_id)
        assert loaded is not None
        assert loaded.agent_id == "agt_test001"
        assert loaded.credential_id == cred_id
        assert loaded.effect == "allow"
        assert loaded.methods == ["GET", "POST"]
        assert loaded.path == "/v1/.*"
        assert loaded.match_mode == "regex"
        assert loaded.operations is None
        assert loaded.is_system is False
        assert loaded.sequence == 0


async def test_sequence_ordering_per_binding(
    control_db: DatabaseSession, clean_rules: None
) -> None:
    """Rules for one (agent, credential) binding read back in sequence order."""
    cred_id = await _create_credential(control_db)
    async with control_db.session() as session:
        for seq, effect in ((1, "deny"), (0, "allow"), (2, "deny")):
            session.add(
                AgentPermissionRule(
                    agent_id="agt_seq01",
                    credential_id=cred_id,
                    effect=effect,
                    path=".*",
                    match_mode="regex",
                    sequence=seq,
                )
            )
        await session.commit()

    async with control_db.session() as session:
        rows = (
            (
                await session.execute(
                    select(AgentPermissionRule)
                    .where(
                        AgentPermissionRule.agent_id == "agt_seq01",
                        AgentPermissionRule.credential_id == cred_id,
                    )
                    .order_by(AgentPermissionRule.sequence)
                )
            )
            .scalars()
            .all()
        )
        assert [r.sequence for r in rows] == [0, 1, 2]
        assert [r.effect for r in rows] == ["allow", "deny", "deny"]


async def test_cascade_on_credential_delete(control_db: DatabaseSession, clean_rules: None) -> None:
    """Deleting the credential cascades to its rules; other bindings are untouched."""
    kept = await _create_credential(control_db, "cred_keep01")
    dropped = await _create_credential(control_db, "cred_drop01")
    async with control_db.session() as session:
        for cred_id in (kept, dropped):
            session.add(
                AgentPermissionRule(
                    agent_id="agt_cascade",
                    credential_id=cred_id,
                    effect="allow",
                    path=".*",
                    sequence=0,
                )
            )
        await session.commit()

    async with control_db.session() as session:
        await session.execute(delete(Credential).where(Credential.id == dropped))
        await session.commit()

    async with control_db.session() as session:
        rows = (await session.execute(select(AgentPermissionRule))).scalars().all()
        assert [r.credential_id for r in rows] == [kept]
