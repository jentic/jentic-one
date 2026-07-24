"""Unit tests for the broker ``CredentialService`` orchestrator (§02b).

Covers the credential-error → broker-domain-exception mapping (424/409/401/502)
and the 424 ``prompt_human`` agent directive (provisioning URL + intent id),
plus the empty-result shortcut. The per-credential-type ``InjectedAuth`` mapping
itself lives in ``test_injection.py`` (``inject_auth``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.broker.core.exceptions import (
    AmbiguousMatchError,
    CredentialNeedsReconnectError,
    CredentialNotProvisionedError,
    CredentialRefreshTransientError,
    ErrorOrigin,
)
from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    RefreshInvalidGrantError,
    RefreshTransientError,
)
from jentic_one.broker.services.credentials.errors import (
    CredentialNotProvisionedError as ResolveNotProvisioned,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType

_IDENTITY = Identity(
    sub="agent_42",
    actor_type=ActorType.AGENT,
    permissions=["execute"],
    expires_at=datetime(2999, 1, 1, tzinfo=UTC),
    active=True,
)


def _ctx(*, account_linking_base_url: str | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.config.broker.account_linking_base_url = account_linking_base_url

    @asynccontextmanager
    async def _noop_transaction() -> Any:
        yield None

    ctx.admin_db.transaction = _noop_transaction
    return ctx


def _resolved() -> ResolvedCredential:
    return ResolvedCredential(
        credential_id="cred_abc",
        name="abc",
        wire_type=CredentialType.API_KEY,
        stored_type=StoredCredentialType.API_KEY,
        provider="stripe",
        encrypted_secret="enc",  # pragma: allowlist secret
    )


def _patch_resolved(monkeypatch: pytest.MonkeyPatch, resolved: ResolvedCredential) -> None:
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.CredentialResolver",
        lambda ctx: MagicMock(resolve=AsyncMock(return_value=resolved)),
    )


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    resolve = AsyncMock(side_effect=exc)
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.CredentialResolver",
        lambda ctx: MagicMock(resolve=resolve),
    )


@pytest.mark.asyncio
async def test_empty_vendor_returns_empty_injection() -> None:
    result = await CredentialService(_ctx()).inject(
        api_vendor="", api_name="", api_version="", identity=_IDENTITY
    )
    assert result.headers == {}
    assert result.query_params == {}
    assert result.cookies == {}
    # No credential path attempted ⇒ no attribution to carry (#740).
    assert result.credential_id is None
    assert result.credential_name is None


@pytest.mark.asyncio
async def test_not_provisioned_maps_to_424_with_directive_and_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, ResolveNotProvisioned("stripe", "", ""))

    with pytest.raises(CredentialNotProvisionedError) as exc:
        await CredentialService(_ctx(account_linking_base_url="https://app.example.com/")).inject(
            api_vendor="stripe", api_name="", api_version="", identity=_IDENTITY
        )

    err = exc.value
    assert err.type == "credential_not_provisioned"
    assert err.directive is not None
    assert err.directive.strategy == "prompt_human"
    params: dict[str, Any] = err.directive.parameters
    assert params["vendor"] == "stripe"
    intent_id = params["intent_id"]
    assert intent_id.startswith("intent_")
    # intent id is echoed both in the directive and as a top-level extension member
    assert err.extra["intent_id"] == intent_id
    # provisioning url is non-secret and carries actor + intent for the host app
    assert params["provisioning_url"] == (
        f"https://app.example.com/connect/stripe?actor=agent_42&intent={intent_id}"
    )


@pytest.mark.asyncio
async def test_not_provisioned_without_base_url_keeps_directive_omits_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_resolver(monkeypatch, ResolveNotProvisioned("stripe", "", ""))

    with pytest.raises(CredentialNotProvisionedError) as exc:
        await CredentialService(_ctx(account_linking_base_url=None)).inject(
            api_vendor="stripe", api_name="", api_version="", identity=_IDENTITY
        )

    params = exc.value.directive.parameters  # type: ignore[union-attr]
    assert "provisioning_url" not in params
    assert params["intent_id"].startswith("intent_")


@pytest.mark.asyncio
async def test_ambiguous_maps_to_409(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolver(monkeypatch, AmbiguousCredentialError("stripe", "", "", 2))

    with pytest.raises(AmbiguousMatchError) as exc:
        await CredentialService(_ctx()).inject(
            api_vendor="stripe", api_name="", api_version="", identity=_IDENTITY
        )
    assert exc.value.type == "ambiguous_credential"


@pytest.mark.asyncio
async def test_invalid_grant_maps_to_401_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolver(monkeypatch, RefreshInvalidGrantError("cred_1"))

    with pytest.raises(CredentialNeedsReconnectError) as exc:
        await CredentialService(_ctx()).inject(
            api_vendor="stripe", api_name="", api_version="", identity=_IDENTITY
        )
    assert exc.value.type == "credential_needs_reconnect"
    assert exc.value.directive is not None
    assert exc.value.directive.strategy == "prompt_human"


@pytest.mark.asyncio
async def test_refresh_transient_maps_to_502_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolver(monkeypatch, RefreshTransientError("cred_1", "502 from idp"))

    with pytest.raises(CredentialRefreshTransientError) as exc:
        await CredentialService(_ctx()).inject(
            api_vendor="stripe", api_name="", api_version="", identity=_IDENTITY
        )
    assert exc.value.type == "refresh_transient_error"
    assert exc.value.origin == ErrorOrigin.UPSTREAM


@pytest.mark.asyncio
async def test_successful_inject_emits_one_audit_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful resolve/decrypt emits exactly one credential-access audit event."""
    _patch_resolved(monkeypatch, _resolved())
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.inject_auth",
        lambda resolved, *, ctx, access_token=None: MagicMock(
            headers={"Authorization": "injected"}, query_params={}, cookies={}
        ),
    )
    audit = AsyncMock(return_value="evt_1")
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.emit_credential_access", audit
    )

    result = await CredentialService(_ctx()).inject(
        api_vendor="stripe",
        api_name="charges",
        api_version="v1",
        identity=_IDENTITY,
        trace_id="trace_xyz",
    )

    assert result.headers == {"Authorization": "injected"}
    # #740: attribution follows the injection back through the caller so the
    # sync/streaming routers can stamp ``Jentic-Credential-*`` response headers
    # and persist attribution on ``execution_records`` without a second lookup.
    assert result.credential_id == "cred_abc"
    assert result.credential_name == "abc"
    audit.assert_awaited_once()
    assert audit.await_args is not None
    kwargs = audit.await_args.kwargs
    assert kwargs["actor_id"] == "agent_42"
    assert kwargs["actor_type"] == ActorType.AGENT.value
    assert kwargs["credential_id"] == "cred_abc"
    assert kwargs["provider"] == "stripe"
    assert kwargs["wire_type"] == CredentialType.API_KEY.value
    assert kwargs["api_vendor"] == "stripe"
    assert kwargs["api_name"] == "charges"
    assert kwargs["api_version"] == "v1"
    # #740: audit event joins to the triggering execution via trace_id.
    assert kwargs["trace_id"] == "trace_xyz"


@pytest.mark.asyncio
async def test_failed_resolution_emits_no_audit_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolution failure must not emit a credential-access audit event."""
    _patch_resolver(monkeypatch, ResolveNotProvisioned("stripe", "", ""))
    audit = AsyncMock()
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.emit_credential_access", audit
    )

    with pytest.raises(CredentialNotProvisionedError):
        await CredentialService(_ctx()).inject(
            api_vendor="stripe", api_name="", api_version="", identity=_IDENTITY
        )

    audit.assert_not_awaited()
