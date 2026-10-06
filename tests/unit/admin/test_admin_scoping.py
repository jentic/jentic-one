"""Unit tests for admin-surface dynamic query scoping."""

from __future__ import annotations

import pytest

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import OWNER_AGENTS_READ


def _identity(
    sub: str = "user_1",
    permissions: list[str] | None = None,
    actor_type: ActorType = ActorType.USER,
    parent_actor_id: str | None = None,
) -> Identity:
    return Identity(
        sub=sub,
        email="test@example.com",
        permissions=permissions or [],
        actor_type=actor_type,
        parent_actor_id=parent_actor_id,
    )


def test_admin_identity_returns_empty_filters() -> None:
    identity = _identity(permissions=["org:admin"])
    filters = build_access_filters(identity, Agent)
    assert filters == []


def test_user_identity_returns_owner_filter() -> None:
    identity = _identity(sub="user_42", permissions=["agents:read"])
    filters = build_access_filters(identity, Agent)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "user_42" in sql
    assert "owner_id" in sql


def test_empty_sub_raises_value_error() -> None:
    identity = _identity(sub="", permissions=[])
    with pytest.raises(ValueError, match="empty sub"):
        build_access_filters(identity, Agent)


def test_unknown_model_raises_value_error() -> None:
    identity = _identity(sub="user_1", permissions=[])

    class FakeModel:
        pass

    with pytest.raises(ValueError, match="Unknown model"):
        build_access_filters(identity, FakeModel)


def test_agent_with_delegation_scope_returns_or_filter() -> None:
    identity = _identity(
        sub="agent_1",
        permissions=[OWNER_AGENTS_READ],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner",
    )
    filters = build_access_filters(identity, Agent)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_1" in sql
    assert "user_owner" in sql


def test_agent_without_delegation_scope_returns_single_filter() -> None:
    identity = _identity(
        sub="agent_1",
        permissions=["agents:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner",
    )
    filters = build_access_filters(identity, Agent)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_1" in sql
    assert "user_owner" not in sql


def test_agent_with_scope_but_no_parent_returns_single_filter() -> None:
    identity = _identity(
        sub="agent_1",
        permissions=[OWNER_AGENTS_READ],
        actor_type=ActorType.AGENT,
        parent_actor_id=None,
    )
    filters = build_access_filters(identity, Agent)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_1" in sql


def test_user_model_returns_self_scope_filter() -> None:
    identity = _identity(sub="usr_self", permissions=[])
    filters = build_access_filters(identity, User)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "usr_self" in sql


def test_job_model_scopes_to_creator_and_owned_agents() -> None:
    """Jobs: the creator, or the human owner of the creating agent."""
    identity = _identity(sub="usr_jobs", permissions=["jobs:read"])
    filters = build_access_filters(identity, Job)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "jobs.created_by = 'usr_jobs'" in sql
    assert "agents.owner_id = 'usr_jobs'" in sql


def test_job_model_never_delegates_to_parent_actor() -> None:
    """An agent does not inherit its owner's jobs, even with owner-read scopes."""
    identity = _identity(
        sub="agent_1",
        permissions=[OWNER_AGENTS_READ, "owner:resources:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner",
    )
    filters = build_access_filters(identity, Job)
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "agent_1" in sql
    assert "user_owner" not in sql


def test_job_model_admin_unrestricted() -> None:
    assert build_access_filters(_identity(permissions=["org:admin"]), Job) == []


def test_execution_record_scopes_to_actor_and_owned_agents() -> None:
    """Executions: the actor that ran it, or the human owner of the running agent."""
    identity = _identity(sub="usr_exec", permissions=["executions:read"])
    filters = build_access_filters(identity, ExecutionRecord)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "execution_records.actor_id = 'usr_exec'" in sql
    assert "agents.owner_id = 'usr_exec'" in sql


def test_execution_record_never_delegates_to_parent_actor() -> None:
    """An agent does not inherit its owner's executions, even with owner-read scopes."""
    identity = _identity(
        sub="agent_1",
        permissions=[OWNER_AGENTS_READ, "owner:resources:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner",
    )
    filters = build_access_filters(identity, ExecutionRecord)
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "agent_1" in sql
    assert "user_owner" not in sql


def test_execution_record_admin_unrestricted() -> None:
    assert build_access_filters(_identity(permissions=["org:admin"]), ExecutionRecord) == []


def test_event_scopes_to_actor_creator_and_owned_agents() -> None:
    """Events: the named actor or creator, or the human owner of that agent."""
    identity = _identity(sub="usr_evt", permissions=["events:read"])
    filters = build_access_filters(identity, Event)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "events.actor_id = 'usr_evt'" in sql
    assert "events.created_by = 'usr_evt'" in sql
    assert sql.count("agents.owner_id = 'usr_evt'") == 2


def test_event_never_delegates_to_parent_actor() -> None:
    """An agent does not inherit its owner's events, even with owner-read scopes."""
    identity = _identity(
        sub="agent_1",
        permissions=[OWNER_AGENTS_READ, "owner:resources:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner",
    )
    filters = build_access_filters(identity, Event)
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "agent_1" in sql
    assert "user_owner" not in sql


def test_event_admin_unrestricted() -> None:
    assert build_access_filters(_identity(permissions=["org:admin"]), Event) == []
