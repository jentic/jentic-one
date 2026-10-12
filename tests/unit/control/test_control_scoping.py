"""Unit tests for control-surface dynamic query scoping.

Theme-5 Phase 5b collapsed the filter builder to the credential/direct axis
(the toolkit models themselves were deleted with the tables in Phase 6b).
Visibility widening for agents
now comes exclusively from ``bound_credential_ids`` — the caller-resolved
direct ``agent_credential_bindings`` ids. Theme 7 then removed the
``AccessRequest`` axis with the access-request feature. ``Credential`` and
``ConnectSession`` (initiator axis, reusing the credential delegation scope)
are the scoped models; both are also widened, read-only, to what the caller's
own agents asked for (``owned_agent_ids``).
"""

from __future__ import annotations

import pytest
from sqlalchemy import ColumnElement, exists, select

from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.connect_session_repo import LIVE_STATES
from jentic_one.control.scoping import filters as scoping_filters
from jentic_one.control.scoping.filters import (
    _ACCESS_FILTER_PROVIDERS,
    build_access_filters,
    register_access_filter_provider,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OWNER_CREDENTIALS_READ
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


# --- ConnectSession model tests (initiator axis, credential-read delegation) ---


def test_connect_session_admin_returns_empty_filters() -> None:
    identity = _identity(permissions=["org:admin"])
    assert build_access_filters(identity, ConnectSession) == []


def test_connect_session_user_returns_initiator_filter() -> None:
    identity = _identity(sub="usr_lister", permissions=["credentials:read"])
    filters = build_access_filters(identity, ConnectSession)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "initiator_actor_id" in sql
    assert "usr_lister" in sql


def test_connect_session_agent_with_credential_delegation_scope_returns_or_filter() -> None:
    """ConnectSession reuses OWNER_CREDENTIALS_READ — no dedicated scope exists."""
    identity = _identity(
        sub="agnt_lister",
        permissions=[OWNER_CREDENTIALS_READ],
        actor_type=ActorType.AGENT,
        parent_actor_id="usr_owner",
    )
    filters = build_access_filters(identity, ConnectSession)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "initiator_actor_id" in sql
    assert "agnt_lister" in sql
    assert "usr_owner" in sql


def test_connect_session_agent_without_delegation_scope_is_self_only() -> None:
    identity = _identity(
        sub="agnt_lister",
        permissions=["credentials:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id="usr_owner",
    )
    filters = build_access_filters(identity, ConnectSession)
    assert len(filters) == 1
    sql = str(filters[0].compile(compile_kwargs={"literal_binds": True}))
    assert "agnt_lister" in sql
    assert "usr_owner" not in sql


# --- Owned-agent axis (connect sessions an owned agent opened, read-only) ---


def _sql(clause: ColumnElement[bool]) -> str:
    return str(clause.compile(compile_kwargs={"literal_binds": True}))


def test_connect_session_owner_sees_sessions_of_owned_agents() -> None:
    identity = _identity(sub="usr_owner", permissions=["credentials:read"])
    filters = build_access_filters(identity, ConnectSession, owned_agent_ids=["agnt_a", "agnt_b"])
    assert len(filters) == 1
    sql = _sql(filters[0])
    assert "initiator_actor_id = 'usr_owner'" in sql
    assert "connect_sessions.agent_id IN ('agnt_a', 'agnt_b')" in sql


def test_credential_owner_sees_only_pending_credentials_of_open_owned_sessions() -> None:
    identity = _identity(sub="usr_owner", permissions=["credentials:read"])
    filters = build_access_filters(identity, Credential, owned_agent_ids=["agnt_a"])
    assert len(filters) == 1
    sql = _sql(filters[0])
    assert "credentials.created_by = 'usr_owner'" in sql
    assert "credentials.state = 'pending'" in sql
    assert "SELECT connect_sessions.credential_id" in sql
    assert "connect_sessions.agent_id IN ('agnt_a')" in sql
    assert "connect_sessions.state IN ('created', 'awaiting_app', 'polling')" in sql


def test_owned_agent_live_states_match_the_repository() -> None:
    assert scoping_filters._LIVE_SESSION_STATES == LIVE_STATES


def test_owned_agent_ids_empty_or_none_leaves_filters_unchanged() -> None:
    identity = _identity(sub="usr_owner", permissions=["credentials:read"])
    for model in (ConnectSession, Credential):
        base = _sql(build_access_filters(identity, model)[0])
        assert _sql(build_access_filters(identity, model, owned_agent_ids=None)[0]) == base
        assert _sql(build_access_filters(identity, model, owned_agent_ids=[])[0]) == base


def test_owned_agent_axis_is_not_routed_through_delegation() -> None:
    """A delegated agent's filter never gains the owned-agent clause on its own.

    The clause is driven only by the caller-resolved ``owned_agent_ids``; a
    delegated agent holding ``owner:credentials:read`` still sees just its own
    and its owner's rows by initiator / creator.
    """
    identity = _identity(
        sub="agnt_lister",
        permissions=[OWNER_CREDENTIALS_READ],
        actor_type=ActorType.AGENT,
        parent_actor_id="usr_owner",
    )
    for model in (ConnectSession, Credential):
        sql = _sql(build_access_filters(identity, model)[0])
        assert "agent_id IN" not in sql
        assert "connect_sessions.credential_id" not in sql


def test_admin_ignores_owned_agent_ids() -> None:
    identity = _identity(permissions=["org:admin"])
    assert build_access_filters(identity, ConnectSession, owned_agent_ids=["agnt_a"]) == []
    assert build_access_filters(identity, Credential, owned_agent_ids=["agnt_a"]) == []
