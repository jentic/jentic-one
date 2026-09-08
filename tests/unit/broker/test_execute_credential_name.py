"""Unit tests for Jentic-Credential-Name header propagation in the execute router."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.broker.core.exceptions import AmbiguousMatchError, InvalidCredentialNameError
from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialCandidate,
    CredentialNameNotFoundError,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType

_IDENTITY = Identity(
    sub="agent_42",
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    expires_at=datetime(2999, 1, 1, tzinfo=UTC),
    active=True,
)


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.config.broker.account_linking_base_url = None

    @asynccontextmanager
    async def _noop_transaction() -> Any:
        yield None

    ctx.admin_db.transaction = _noop_transaction
    return ctx


@pytest.mark.asyncio
async def test_credential_name_none_when_header_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Jentic-Credential-Name is absent, credential_name=None is forwarded."""
    captured: dict[str, Any] = {}

    original_resolve = AsyncMock(
        return_value=MagicMock(
            credential_id="cred_1",
            wire_type=MagicMock(value="api_key"),
            stored_type=MagicMock(value="API_KEY"),
            provider="stripe",
            server_variables=None,
            encrypted_secret="enc",
        )
    )

    def fake_resolver(ctx: Any) -> MagicMock:
        async def _resolve(*, api: Any, caller: str, credential_name: str | None = None) -> Any:
            captured["credential_name"] = credential_name
            return await original_resolve(api=api, caller=caller, credential_name=credential_name)

        return MagicMock(resolve=_resolve)

    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.CredentialResolver", fake_resolver
    )
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.inject_auth",
        lambda resolved, *, ctx, access_token=None: MagicMock(
            headers={}, query_params={}, cookies={}
        ),
    )
    audit = AsyncMock(return_value="evt_1")
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.emit_credential_access", audit
    )

    await CredentialService(_ctx()).inject(
        api_vendor="stripe",
        api_name="payments",
        api_version="v1",
        identity=_IDENTITY,
        credential_name=None,
    )

    assert captured["credential_name"] is None


@pytest.mark.asyncio
async def test_credential_name_forwarded_when_header_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Jentic-Credential-Name is set, the value is forwarded to the resolver."""
    captured: dict[str, Any] = {}

    original_resolve = AsyncMock(
        return_value=MagicMock(
            credential_id="cred_1",
            wire_type=MagicMock(value="api_key"),
            stored_type=MagicMock(value="API_KEY"),
            provider="stripe",
            server_variables=None,
            encrypted_secret="enc",
        )
    )

    def fake_resolver(ctx: Any) -> MagicMock:
        async def _resolve(*, api: Any, caller: str, credential_name: str | None = None) -> Any:
            captured["credential_name"] = credential_name
            return await original_resolve(api=api, caller=caller, credential_name=credential_name)

        return MagicMock(resolve=_resolve)

    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.CredentialResolver", fake_resolver
    )
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.inject_auth",
        lambda resolved, *, ctx, access_token=None: MagicMock(
            headers={}, query_params={}, cookies={}
        ),
    )
    audit = AsyncMock(return_value="evt_1")
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.emit_credential_access", audit
    )

    await CredentialService(_ctx()).inject(
        api_vendor="stripe",
        api_name="payments",
        api_version="v1",
        identity=_IDENTITY,
        credential_name="admin",
    )

    assert captured["credential_name"] == "admin"


@pytest.mark.asyncio
async def test_ambiguous_response_contains_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """409 from AmbiguousCredentialError includes distinguishable candidates in extra."""
    exc = AmbiguousCredentialError(
        "stripe",
        "payments",
        "v1",
        2,
        candidates=[
            CredentialCandidate(id="cred_1", name="shared", last4="ed_1"),
            CredentialCandidate(id="cred_2", name="shared", last4="ed_2"),
        ],
    )
    resolve = AsyncMock(side_effect=exc)
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.CredentialResolver",
        lambda ctx: MagicMock(resolve=resolve),
    )

    with pytest.raises(AmbiguousMatchError) as raised:
        await CredentialService(_ctx()).inject(
            api_vendor="stripe",
            api_name="payments",
            api_version="v1",
            identity=_IDENTITY,
        )

    candidates = raised.value.extra["candidates"]
    assert [c["id"] for c in candidates] == ["cred_1", "cred_2"]
    assert [c["last4"] for c in candidates] == ["ed_1", "ed_2"]
    assert {c["name"] for c in candidates} == {"shared"}


@pytest.mark.asyncio
async def test_invalid_credential_name_response_contains_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 from CredentialNameNotFoundError includes distinguishable candidates in extra."""
    exc = CredentialNameNotFoundError(
        "stripe",
        "payments",
        "v1",
        "nonexistent",
        [
            CredentialCandidate(id="cred_1", name="read-only", last4="ed_1"),
            CredentialCandidate(id="cred_2", name="admin", last4="ed_2"),
        ],
    )
    resolve = AsyncMock(side_effect=exc)
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.CredentialResolver",
        lambda ctx: MagicMock(resolve=resolve),
    )

    with pytest.raises(InvalidCredentialNameError) as raised:
        await CredentialService(_ctx()).inject(
            api_vendor="stripe",
            api_name="payments",
            api_version="v1",
            identity=_IDENTITY,
            credential_name="nonexistent",
        )

    candidates = raised.value.extra["candidates"]
    assert [c["name"] for c in candidates] == ["read-only", "admin"]
    assert raised.value.type == "credential_name_not_found"
