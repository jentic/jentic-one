"""Unit tests for AgentRepository.set_approval owner_id assignment."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos.agent_repo import AgentRepository


def _make_agent(*, owner_id: str | None = None) -> Agent:
    agent = MagicMock(spec=Agent)
    agent.id = "agnt_test1"
    agent.name = "dcr-agent"
    agent.owner_id = owner_id
    agent.status = "pending"
    agent.approved_by = None
    agent.approved_at = None
    agent.registration_access_token_hash = "hash123"
    agent.rat_expires_at = None
    return agent


@pytest.mark.asyncio
async def test_set_approval_assigns_owner_id_when_none() -> None:
    """DCR agents (owner_id=None) get owner_id set to the approver."""
    agent = _make_agent(owner_id=None)
    session = AsyncMock()
    session.get = AsyncMock(return_value=agent)
    session.flush = AsyncMock()

    result = await AgentRepository.set_approval(session, "agnt_test1", approved_by="usr_admin1")

    assert result.owner_id == "usr_admin1"
    assert result.approved_by == "usr_admin1"
    assert result.status == "active"


@pytest.mark.asyncio
async def test_set_approval_preserves_existing_owner_id() -> None:
    """Manually-created agents retain their original owner_id on approval."""
    agent = _make_agent(owner_id="usr_original_owner")
    session = AsyncMock()
    session.get = AsyncMock(return_value=agent)
    session.flush = AsyncMock()

    result = await AgentRepository.set_approval(session, "agnt_test1", approved_by="usr_admin1")

    assert result.owner_id == "usr_original_owner"
    assert result.approved_by == "usr_admin1"
