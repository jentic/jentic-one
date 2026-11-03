"""Unit tests for registry-surface dynamic query scoping."""

from __future__ import annotations

import pytest
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.registry.core.schema.notes import Note
from jentic_one.registry.scoping.filters import build_access_filters
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType


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
    filters = build_access_filters(identity, Note)
    assert filters == []


def test_user_identity_returns_created_by_filter() -> None:
    identity = _identity(sub="user_42", permissions=["apis:read"])
    filters = build_access_filters(identity, Note)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "user_42" in sql
    assert "created_by" in sql


def test_empty_sub_raises() -> None:
    identity = _identity(sub="", permissions=[])
    with pytest.raises(ValueError, match="empty sub"):
        build_access_filters(identity, Note)


def test_non_admin_always_scoped() -> None:
    identity = _identity(sub="agent_1", actor_type=ActorType.AGENT)
    filters = build_access_filters(identity, Note)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_1" in sql


def _sql(filters: list[ColumnElement[bool]]) -> str:
    assert len(filters) == 1
    return str(filters[0].compile(compile_kwargs={"literal_binds": True}))


def test_owned_agent_ids_widen_the_read_filter() -> None:
    identity = _identity(sub="user_42")
    sql = _sql(build_access_filters(identity, Note, owned_agent_ids=["agt_a", "agt_b"]))
    assert "notes.created_by = 'user_42'" in sql
    assert " OR " in sql
    assert "notes.created_by IN ('agt_a', 'agt_b')" in sql


@pytest.mark.parametrize("owned", [None, []])
def test_no_owned_agents_stays_author_only(owned: list[str] | None) -> None:
    sql = _sql(build_access_filters(_identity(sub="user_42"), Note, owned_agent_ids=owned))
    assert sql == "notes.created_by = 'user_42'"


def test_write_filter_is_author_only() -> None:
    sql = _sql(build_access_filters(_identity(sub="user_42"), Note, for_write=True))
    assert sql == "notes.created_by = 'user_42'"


def test_write_filter_rejects_owned_agent_widening() -> None:
    with pytest.raises(ValueError, match="read-only visibility widening"):
        build_access_filters(
            _identity(sub="user_42"), Note, owned_agent_ids=["agt_a"], for_write=True
        )


def test_admin_unrestricted_even_with_owned_agents() -> None:
    identity = _identity(permissions=["org:admin"])
    assert build_access_filters(identity, Note, owned_agent_ids=["agt_a"]) == []
    assert build_access_filters(identity, Note, for_write=True) == []


def test_owner_delegation_does_not_widen_notes() -> None:
    identity = _identity(
        sub="agt_a",
        actor_type=ActorType.AGENT,
        permissions=["owner:resources:read", "owner:agents:read", "owner:credentials:read"],
        parent_actor_id="user_42",
    )
    assert _sql(build_access_filters(identity, Note)) == "notes.created_by = 'agt_a'"
