"""Unit tests for registry-surface dynamic query scoping."""

from __future__ import annotations

import pytest

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
