"""Unit tests for control-surface dynamic query scoping.

Theme-5 Phase 5b collapsed the filter builder to the credential/direct axis
(the toolkit models themselves were deleted with the tables in Phase 6b).
Visibility widening for agents
now comes exclusively from ``bound_credential_ids`` — the caller-resolved
direct ``agent_credential_bindings`` ids.
"""

from __future__ import annotations

import pytest
from sqlalchemy import ColumnElement, exists, select

from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.scoping import filters as scoping_filters
from jentic_one.control.scoping.filters import (
    _ACCESS_FILTER_PROVIDERS,
    build_access_filters,
    register_access_filter_provider,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import (
    OWNER_ACCESS_REQUESTS_READ,
    OWNER_CREDENTIALS_READ,
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


# --- Direct-binding visibility (issues #665 / #682, credential axis only) ---


def test_orphaned_agent_sees_directly_bound_credential_by_id() -> None:
    """An orphaned agent (owner None, no org:admin) can read a bound credential.

    The owner check still holds (created_by == sub), but the returned filter
    must additionally OR in an ``id IN (...)`` clause for the credentials the
    agent holds an active direct binding to, so a credential it doesn't own is
    still visible.
    """
    identity = _identity(sub="agent_123", permissions=[], actor_type=ActorType.AGENT)
    filters = build_access_filters(identity, Credential, bound_credential_ids=["cred_bound_1"])
    assert len(filters) == 1
    compiled = filters[0].compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled)
    assert "created_by" in sql
    assert "agent_123" in sql
    assert "credentials.credential_id IN" in sql or "credentials.id IN" in sql
    assert "cred_bound_1" in sql


def test_bound_credential_ids_none_leaves_owner_only_filter() -> None:
    """Without bound ids the filter is unchanged (plain owner scoping)."""
    identity = _identity(sub="agent_123", permissions=[], actor_type=ActorType.AGENT)
    filters = build_access_filters(identity, Credential, bound_credential_ids=None)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "created_by" in sql
    assert " IN " not in sql


def test_admin_ignores_bound_credential_ids() -> None:
    """org:admin is unrestricted regardless of bound ids."""
    identity = _identity(permissions=["org:admin"])
    assert build_access_filters(identity, Credential, bound_credential_ids=["cred_1"]) == []


# --- Read-only sharing seam (register_access_filter_provider) ---


def test_include_shared_invokes_registered_provider() -> None:
    """A registered provider's clause is OR-merged on the read path."""

    def _provider(identity: Identity, model: type) -> ColumnElement[bool] | None:
        if model is Credential:
            # A dummy EXISTS keyed on the caller — stands in for a share table.
            return exists(
                select(Credential.id).where(Credential.created_by == f"shared:{identity.sub}")
            )
        return None

    register_access_filter_provider(_provider)
    try:
        identity = _identity(sub="user_share", permissions=[])
        filters = build_access_filters(identity, Credential, include_shared=True)
        assert len(filters) == 1
        sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
        assert "created_by" in sql  # owner branch preserved
        assert "EXISTS" in sql
        assert "shared:user_share" in sql  # provider clause merged in
    finally:
        _ACCESS_FILTER_PROVIDERS.remove(_provider)


def test_include_shared_default_off_ignores_providers() -> None:
    """Without include_shared (the write path), providers are not consulted."""

    def _provider(identity: Identity, model: type) -> ColumnElement[bool] | None:
        return exists(select(Credential.id).where(Credential.created_by == "PROVIDER_MARKER"))

    register_access_filter_provider(_provider)
    try:
        identity = _identity(sub="user_share", permissions=[])
        filters = build_access_filters(identity, Credential)  # include_shared defaults False
        sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
        assert "PROVIDER_MARKER" not in sql
    finally:
        _ACCESS_FILTER_PROVIDERS.remove(_provider)


def test_no_providers_is_owner_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero providers yields the plain owner filter even with include_shared.

    The provider registry is pinned empty for the duration: this test is
    about the no-provider BASELINE, not the process state — under an
    overlay-loaded run (e.g. an extension's "OSS suite with the extension
    registered" harness) providers legitimately exist, and asserting on
    whatever happens to be registered would make the test flip there.
    """
    monkeypatch.setattr(scoping_filters, "_ACCESS_FILTER_PROVIDERS", [])
    identity = _identity(sub="user_share", permissions=[])
    filters = build_access_filters(identity, Credential, include_shared=True)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "created_by" in sql
    assert "EXISTS" not in sql


def test_admin_ignores_include_shared() -> None:
    """org:admin stays unrestricted even with include_shared."""
    identity = _identity(permissions=["org:admin"])
    assert build_access_filters(identity, Credential, include_shared=True) == []


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
