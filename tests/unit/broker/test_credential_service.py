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
    CredentialUndecryptableError,
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
from jentic_one.shared.crypto import DecryptionError
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from jentic_one.shared.models.events import EventSeverity

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
        api_vendor="stripe", api_name="charges", api_version="v1", identity=_IDENTITY
    )

    assert result.headers == {"Authorization": "injected"}
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


@pytest.mark.asyncio
async def test_decryption_error_maps_to_424_undecryptable_with_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored ciphertext that fails AES-GCM authentication (e.g. the encryption
    key was rotated under the same key id by a reinstall) must surface as a
    dedicated 424 with a prompt_human directive — not the raw 500 that
    unhandled DecryptionError produces (repro reported after `jenticctl update`
    + reinstall on v0.18.0)."""
    _patch_resolved(monkeypatch, _resolved())

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise DecryptionError("Decryption failed: authentication error")

    monkeypatch.setattr("jentic_one.broker.services.credentials.orchestrator.inject_auth", _boom)

    with pytest.raises(CredentialUndecryptableError) as exc:
        await CredentialService(_ctx()).inject(
            api_vendor="stripe", api_name="charges", api_version="v1", identity=_IDENTITY
        )

    err = exc.value
    assert err.type == "credential_undecryptable"
    # extra carries the opaque credential id + api vendor, never ciphertext.
    assert err.extra["credential_id"] == "cred_abc"
    assert err.extra["api_vendor"] == "stripe"
    assert err.directive is not None
    assert err.directive.strategy == "prompt_human"
    assert err.directive.parameters["credential_id"] == "cred_abc"
    # No secret material leaks into any surface.
    rendered = f"{err.detail} {err.directive.human_readable_instruction} {err.extra}"
    assert "authentication error" not in rendered
    assert "InvalidTag" not in rendered


@pytest.mark.asyncio
async def test_decryption_error_during_oauth_refresh_also_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OAuth refresh also touches encrypted material (encrypted_refresh_token /
    encrypted_client_secret via TokenRefresher). A DecryptionError there must
    map to the same 424 —     the orchestrator's single seam covers refresh + inject.
    """
    oauth_cred = ResolvedCredential(
        credential_id="cred_oauth",
        wire_type=CredentialType.OAUTH2,
        stored_type=StoredCredentialType.OAUTH2_CLIENT_CREDENTIALS,
        provider="google",
        encrypted_access_token="enc",  # pragma: allowlist secret
        encrypted_refresh_token="enc",  # pragma: allowlist secret
    )
    _patch_resolved(monkeypatch, oauth_cred)

    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.TokenRefresher",
        lambda ctx: MagicMock(
            ensure_fresh=AsyncMock(
                side_effect=DecryptionError("Decryption failed: authentication error")
            )
        ),
    )

    with pytest.raises(CredentialUndecryptableError) as exc:
        await CredentialService(_ctx()).inject(
            api_vendor="google", api_name="sheets", api_version="v4", identity=_IDENTITY
        )
    assert exc.value.type == "credential_undecryptable"


@pytest.mark.asyncio
async def test_decryption_error_emits_undecryptable_event_not_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Undecryptable path emits credential.undecryptable (WARNING) and skips
    the credential.accessed audit event — the access never actually happened."""
    _patch_resolved(monkeypatch, _resolved())

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise DecryptionError("boom")

    monkeypatch.setattr("jentic_one.broker.services.credentials.orchestrator.inject_auth", _boom)
    access = AsyncMock()
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.emit_credential_access", access
    )
    emit_evt = AsyncMock(return_value="evt_1")
    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.emit_event_best_effort", emit_evt
    )

    with pytest.raises(CredentialUndecryptableError):
        await CredentialService(_ctx()).inject(
            api_vendor="stripe", api_name="", api_version="", identity=_IDENTITY
        )

    access.assert_not_awaited()
    emit_evt.assert_awaited()
    kwargs = emit_evt.await_args.kwargs  # type: ignore[union-attr]
    assert kwargs["type"] == "credential.undecryptable"
    # Severity must be WARNING so the operator sees it in the events rail.
    assert kwargs["severity"] == EventSeverity.WARNING
