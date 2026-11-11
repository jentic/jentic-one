"""Integration tests for the AgentExplanation model and its repository against a real database."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.agent_explanations import AgentExplanation
from jentic_one.admin.repos import AgentExplanationRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_HELD = "held_execution"


@pytest.fixture()
async def clean_agent_explanations(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.transaction() as session:
        await session.execute(delete(AgentExplanation))
    yield
    async with admin_db.transaction() as session:
        await session.execute(delete(AgentExplanation))


async def test_create_round_trips_with_a_ksuid_and_audit_columns(
    admin_db: DatabaseSession, clean_agent_explanations: None
) -> None:
    async with admin_db.transaction() as session:
        row = await AgentExplanationRepository.create(
            session, subject_type=_HELD, subject_id="held_a", agent_id="agnt_a", text="because"
        )
        assert row is not None
        row_id = row.id
    async with admin_db.session() as session:
        stored = (
            await session.execute(select(AgentExplanation).where(AgentExplanation.id == row_id))
        ).scalar_one()
    assert stored.id.startswith("aex")
    assert (stored.subject_type, stored.subject_id, stored.agent_id, stored.text) == (
        _HELD,
        "held_a",
        "agnt_a",
        "because",
    )
    assert stored.created_by == "agnt_a"
    assert stored.created_at is not None


async def test_a_second_explanation_of_a_subject_is_refused_and_the_transaction_survives(
    admin_db: DatabaseSession, clean_agent_explanations: None
) -> None:
    async with admin_db.transaction() as session:
        first = await AgentExplanationRepository.create(
            session, subject_type=_HELD, subject_id="held_a", agent_id="agnt_a", text="first"
        )
        second = await AgentExplanationRepository.create(
            session, subject_type=_HELD, subject_id="held_a", agent_id="agnt_a", text="second"
        )
        # The lost insert ran in a savepoint: the transaction carries on.
        other = await AgentExplanationRepository.create(
            session, subject_type=_HELD, subject_id="held_b", agent_id="agnt_a", text="other"
        )
    assert first is not None and second is None and other is not None
    async with admin_db.session() as session:
        assert await AgentExplanationRepository.get_text(session, _HELD, "held_a") == "first"
        texts = await AgentExplanationRepository.texts_for(
            session, _HELD, ["held_a", "held_b", "held_missing"]
        )
    assert texts == {"held_a": "first", "held_b": "other"}


async def test_reads_are_keyed_by_subject_type_too(
    admin_db: DatabaseSession, clean_agent_explanations: None
) -> None:
    async with admin_db.transaction() as session:
        await AgentExplanationRepository.create(
            session, subject_type=_HELD, subject_id="x_1", agent_id="agnt_a", text="held"
        )
        await AgentExplanationRepository.create(
            session, subject_type="execution", subject_id="x_1", agent_id="agnt_a", text="run"
        )
    async with admin_db.session() as session:
        assert await AgentExplanationRepository.get_text(session, _HELD, "x_1") == "held"
        assert await AgentExplanationRepository.get_text(session, "execution", "x_1") == "run"
        assert await AgentExplanationRepository.texts_for(session, _HELD, []) == {}
