"""Unit tests for control-surface dynamic query scoping."""

from __future__ import annotations

import pytest

from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import (
    OWNER_ACCESS_REQUESTS_READ,
    OWNER_CREDENTIALS_READ,
    OWNER_TOOLKITS_READ,
)


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


# --- Credential model tests ---


def test_admin_identity_returns_empty_filters() -> None:
    identity = _identity(permissions=["org:admin"])
    filters = build_access_filters(identity, Credential)
    assert filters == []


def test_user_identity_returns_created_by_filter() -> None:
    identity = _identity(sub="user_42", permissions=["credentials:read"])
    filters = build_access_filters(identity, Credential)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "user_42" in sql
    assert "created_by" in sql


def test_agent_with_delegation_scope_returns_or_filter() -> None:
    identity = _identity(
        sub="agent_1",
        permissions=[OWNER_CREDENTIALS_READ],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner",
    )
    filters = build_access_filters(identity, Credential)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_1" in sql
    assert "user_owner" in sql


def test_empty_sub_raises_value_error() -> None:
    identity = _identity(sub="", permissions=[])
    with pytest.raises(ValueError, match="empty sub"):
        build_access_filters(identity, Credential)


def test_unknown_model_raises_value_error() -> None:
    identity = _identity(sub="user_1", permissions=[])

    class FakeModel:
        pass

    with pytest.raises(ValueError, match="Unknown model"):
        build_access_filters(identity, FakeModel)


def test_agent_without_delegation_scope_returns_single_filter() -> None:
    identity = _identity(
        sub="agent_1",
        permissions=["credentials:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner",
    )
    filters = build_access_filters(identity, Credential)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_1" in sql
    assert "user_owner" not in sql


# --- Toolkit model tests ---


def test_toolkit_model_returns_created_by_filter() -> None:
    identity = _identity(sub="user_10", permissions=[])
    filters = build_access_filters(identity, Toolkit)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "user_10" in sql
    assert "created_by" in sql


def test_toolkit_with_delegation_scope_returns_or_filter() -> None:
    identity = _identity(
        sub="agent_2",
        permissions=[OWNER_TOOLKITS_READ],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_parent",
    )
    filters = build_access_filters(identity, Toolkit)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_2" in sql
    assert "user_parent" in sql


def test_child_model_returns_exists_filter() -> None:
    identity = _identity(sub="user_5", permissions=[])
    filters = build_access_filters(identity, ToolkitKey)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "EXISTS" in sql
    assert "user_5" in sql


def test_child_model_with_delegation_returns_or_in_exists() -> None:
    identity = _identity(
        sub="agent_3",
        permissions=[OWNER_TOOLKITS_READ],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_delegator",
    )
    filters = build_access_filters(identity, ToolkitKey)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "EXISTS" in sql
    assert "agent_3" in sql
    assert "user_delegator" in sql


# --- Bound-toolkit visibility (issues #665 / #682) ---


def test_orphaned_agent_sees_bound_toolkit_by_id() -> None:
    """An orphaned agent (owner_id=None, no org:admin) can read a bound toolkit.

    The owner check still holds (created_by == sub), but the returned filter must
    additionally OR in an ``id IN (...)`` clause for the toolkits the agent is
    bound to, so a toolkit it doesn't own is still visible.
    """
    identity = _identity(sub="agent_123", permissions=[], actor_type=ActorType.AGENT)
    filters = build_access_filters(identity, Toolkit, bound_toolkit_ids=["tk_bound_1"])
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "created_by" in sql
    assert "agent_123" in sql
    assert "toolkits.id IN" in sql
    assert "tk_bound_1" in sql


def test_bound_toolkit_ids_none_leaves_owner_only_filter() -> None:
    """Without bound ids the filter is unchanged (plain owner scoping)."""
    identity = _identity(sub="agent_123", permissions=[], actor_type=ActorType.AGENT)
    filters = build_access_filters(identity, Toolkit, bound_toolkit_ids=None)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "created_by" in sql
    assert " IN " not in sql


def test_admin_ignores_bound_toolkit_ids() -> None:
    """org:admin is unrestricted regardless of bound ids."""
    identity = _identity(permissions=["org:admin"])
    assert build_access_filters(identity, Toolkit, bound_toolkit_ids=["tk_1"]) == []


def test_orphaned_agent_sees_credential_bound_to_bound_toolkit() -> None:
    """A credential bound to a bound toolkit is visible via an EXISTS subquery."""
    identity = _identity(sub="agent_123", permissions=[], actor_type=ActorType.AGENT)
    filters = build_access_filters(identity, Credential, bound_toolkit_ids=["tk_bound_1"])
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "created_by" in sql
    assert "EXISTS" in sql
    assert "toolkit_credential_bindings" in sql
    assert "tk_bound_1" in sql


# --- AccessRequest model tests ---


def test_access_request_admin_returns_empty_filters() -> None:
    identity = _identity(permissions=["org:admin"])
    filters = build_access_filters(identity, AccessRequest)
    assert filters == []


def test_access_request_user_returns_two_sided_filter() -> None:
    identity = _identity(sub="user_77", permissions=["access-requests:read"])
    filters = build_access_filters(identity, AccessRequest)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "user_77" in sql
    assert "created_by" in sql
    assert "filer_owner_id" in sql


def test_access_request_agent_with_delegation_scope_returns_widened_filter() -> None:
    identity = _identity(
        sub="agent_2",
        permissions=[OWNER_ACCESS_REQUESTS_READ],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner_2",
    )
    filters = build_access_filters(identity, AccessRequest)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_2" in sql
    assert "user_owner_2" in sql
    assert "created_by" in sql
    assert "filer_owner_id" in sql


def test_access_request_agent_without_delegation_scope_returns_self_only() -> None:
    identity = _identity(
        sub="agent_3",
        permissions=["access-requests:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id="user_owner_3",
    )
    filters = build_access_filters(identity, AccessRequest)
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "agent_3" in sql
    assert "user_owner_3" not in sql
