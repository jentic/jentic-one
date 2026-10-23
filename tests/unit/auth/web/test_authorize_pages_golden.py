"""Golden snapshots of every server-rendered page in the authorize/consent flow.

Each case drives the real route through ``TestClient`` (collaborator services
stubbed, the state backend in memory) and compares the response body
byte-for-byte against ``golden/authorize_pages/<case>.html``. Signed flow
blobs carry ``iat`` and per-mint entropy, so they are normalised to
``<SIGNED>`` before the comparison; nothing else is.

The response security headers are pinned alongside the body, so a change to
the page markup, the inlined script, or the CSP/no-store posture fails here.

Regenerate after an intentional page change with
``JENTIC_UPDATE_GOLDEN=1 uv run pytest tests/unit/auth/web/test_authorize_pages_golden.py``
and review the diff of the golden files.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.authorize_service import AgentConsentOption, PendingAgentRef
from jentic_one.auth.web import deps, flow
from jentic_one.auth.web.routers import approval, authorize, consent, consent_agent
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.state.backend import MemoryStateBackend

_GOLDEN_DIR = Path(__file__).parent / "golden" / "authorize_pages"
_UPDATE = os.environ.get("JENTIC_UPDATE_GOLDEN") == "1"

_SECRET = "golden-pages-secret"  # pragma: allowlist secret
_CLIENT_ID = "oc_golden"
_REDIRECT_URI = "https://app.example.com/cb?tenant=a&x=<y>"
_HANDLE = "handle-golden"
_CODE_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # pragma: allowlist secret

#: A signed flow blob (``base64url(json).hexsig``), raw or URL-encoded.
_SIGNED_BLOB = re.compile(r"[A-Za-z0-9_\-]+(?:=|%3D)*\.[0-9a-f]{64}")

_EXPECTED_HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "pragma": "no-cache",
}


def _client_view(
    *,
    consent_model: str = "agent",
    approval_status: str = "approved",
    active: bool = True,
    allowed_scopes: list[str] | None = None,
) -> OAuthClientView:
    return OAuthClientView(
        id="oac_golden",
        client_id=_CLIENT_ID,
        name='Evil <App> & "Co"',
        description=None,
        redirect_uris=[_REDIRECT_URI, "https://other.example.org/cb", "cursor://callback"],
        allowed_scopes=allowed_scopes,
        active=active,
        require_consent=True,
        token_endpoint_auth_method="none",
        consent_model=consent_model,
        registration_source="dcr",
        software_id="com.example.<ide>",
        approval_status=approval_status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=None,
        created_by=None,
    )


def _authorize_svc(
    *,
    user_id: str | None = None,
    agents: list[AgentConsentOption] | None = None,
    has_any_agents: bool = False,
    pending: PendingAgentRef | None = None,
    can_create_active: bool = True,
) -> MagicMock:
    svc = MagicMock()
    svc.resolve_existing_user_id = AsyncMock(return_value=user_id)
    svc.list_consentable_agents = AsyncMock(return_value=agents or [])
    svc.owner_has_any_agents = AsyncMock(return_value=has_any_agents)
    svc.newest_pending_agent = AsyncMock(return_value=pending)
    svc.user_can_create_active_agent = AsyncMock(return_value=can_create_active)
    svc.get_authorize_redirect_url = MagicMock(return_value="https://idp.example.com/authorize")
    return svc


def _app(svc: MagicMock) -> tuple[TestClient, MemoryStateBackend, MagicMock]:
    app = FastAPI()
    app.include_router(approval.router)
    app.include_router(authorize.router)
    app.include_router(consent.router)
    app.include_router(consent_agent.router)
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com", platform_clients=[])
    ctx.config.admin.auth.jwt_secret.get_secret_value.return_value = _SECRET
    app.state.ctx = ctx
    backend = MemoryStateBackend()
    app.state.auth_state_backend = backend
    app.dependency_overrides[deps.get_authorize_service] = lambda: svc
    return TestClient(app, follow_redirects=False), backend, ctx


def _seed_handle(
    backend: MemoryStateBackend,
    *,
    subject: dict[str, object],
    scope: str = "openid apis:read",
) -> None:
    payload = json.dumps(
        {
            **subject,
            "redirect_uri": _REDIRECT_URI,
            "original_state": "st<1>",
            "client_id": _CLIENT_ID,
            "code_challenge": "challenge",
            "scope": scope,
            "nonce": "n&1",
            "client_name": 'Evil <App> & "Co"',
            "client_description": "Reads <b>things</b>",
            "user_email": "o'brien@example.com",
            "iat": int(time.time()),
        }
    ).encode()
    asyncio.run(backend.set(f"consent-handle:{_HANDLE}", payload, ttl_s=300.0))


_IDP_SUBJECT: dict[str, object] = {
    "claims": {
        "external_subject": "ext-golden",
        "email": "o'brien@example.com",
        "email_verified": True,
        "first_name": "Pat",
        "last_name": "O'Brien",
        "issuer": "https://idp.example.com",
    }
}
_LOCAL_SUBJECT: dict[str, object] = {"local_user_id": "usr_golden"}


@pytest.fixture()
def oauth_client_view() -> Iterator[MagicMock]:
    """Patch the cached-client lookup; tests set ``.return_value`` per case."""
    lookup = AsyncMock(return_value=_client_view())
    client_svc = MagicMock()
    client_svc.get_by_client_id = lookup
    with patch.object(flow, "OAuthClientService", return_value=client_svc):
        yield lookup


def _assert_golden(name: str, resp: Response, *, status_code: int = 200) -> None:
    assert resp.status_code == status_code
    for header, value in _EXPECTED_HEADERS.items():
        assert resp.headers.get(header) == value, header
    body = _SIGNED_BLOB.sub("<SIGNED>", resp.text)
    path = _GOLDEN_DIR / f"{name}.html"
    if _UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode("utf-8"))
    assert path.exists(), f"missing golden file {path}; regenerate with JENTIC_UPDATE_GOLDEN=1"
    assert body == path.read_bytes().decode("utf-8")


def test_invalid_handle_page(oauth_client_view: MagicMock) -> None:
    client, _backend, _ctx = _app(_authorize_svc())
    resp = client.get("/oauth/consent", params={"ch": "missing"})
    _assert_golden("invalid_handle", resp, status_code=400)


def test_user_consent_page(oauth_client_view: MagicMock) -> None:
    oauth_client_view.return_value = _client_view(consent_model="user")
    client, backend, _ctx = _app(_authorize_svc())
    _seed_handle(
        backend,
        subject=_IDP_SUBJECT,
        scope="openid email profile agents:read agents:write credentials:read apis:read odd:x",
    )
    resp = client.get("/oauth/consent", params={"ch": _HANDLE})
    _assert_golden("user_consent", resp)


@pytest.mark.parametrize(
    ("case", "agents"),
    [
        (
            "agent_picker",
            [
                AgentConsentOption(id="agnt_1", name="reader <1>", permissions=frozenset()),
                AgentConsentOption(
                    id="agnt_2",
                    name="writer & co",
                    permissions=frozenset({"apis:read", "agents:read"}),
                ),
            ],
        ),
        (
            "agent_picker_single",
            [AgentConsentOption(id="agnt_1", name="solo", permissions=frozenset({"apis:read"}))],
        ),
    ],
)
def test_agent_picker_page(
    oauth_client_view: MagicMock, case: str, agents: list[AgentConsentOption]
) -> None:
    oauth_client_view.return_value = _client_view(allowed_scopes=["apis:read", "agents:read"])
    client, backend, _ctx = _app(_authorize_svc(user_id="usr_golden", agents=agents))
    _seed_handle(backend, subject=_IDP_SUBJECT, scope="openid apis:read agents:read apis:write")
    resp = client.get("/oauth/consent", params={"ch": _HANDLE})
    _assert_golden(case, resp)


def test_no_agents_first_run_page(oauth_client_view: MagicMock) -> None:
    client, backend, _ctx = _app(_authorize_svc())
    _seed_handle(backend, subject={})
    resp = client.get("/oauth/consent", params={"ch": _HANDLE})
    _assert_golden("no_agents_first_run", resp)


def test_no_agents_unavailable_page(oauth_client_view: MagicMock) -> None:
    client, backend, _ctx = _app(_authorize_svc(has_any_agents=True))
    _seed_handle(backend, subject=_LOCAL_SUBJECT)
    resp = client.get("/oauth/consent", params={"ch": _HANDLE})
    _assert_golden("no_agents_unavailable", resp)


@pytest.mark.parametrize(
    ("case", "subject", "user_id", "can_create_active"),
    [
        ("agent_create_neutral", _IDP_SUBJECT, None, True),
        ("agent_create_active", _LOCAL_SUBJECT, None, True),
        ("agent_create_pending", _LOCAL_SUBJECT, None, False),
    ],
)
def test_agent_create_page(
    oauth_client_view: MagicMock,
    case: str,
    subject: dict[str, object],
    user_id: str | None,
    can_create_active: bool,
) -> None:
    svc = _authorize_svc(user_id=user_id, can_create_active=can_create_active)
    client, backend, _ctx = _app(svc)
    _seed_handle(backend, subject=subject)
    resp = client.get("/oauth/consent", params={"ch": _HANDLE})
    _assert_golden(case, resp)


def test_agent_create_validation_rerender_page(oauth_client_view: MagicMock) -> None:
    client, backend, ctx = _app(_authorize_svc(can_create_active=False))
    _seed_handle(backend, subject=_LOCAL_SUBJECT)
    create_state = flow.sign_payload(
        {
            "ch_digest": hashlib.sha256(_HANDLE.encode()).hexdigest(),
            "user_key": "local:usr_golden",
            "iat": str(int(time.time())),
        },
        flow.agent_create_signing_key(ctx),
        purpose="agent-create",
    )
    resp = client.post(
        "/oauth/consent/agent",
        data={"consent_token": _HANDLE, "create_state": create_state, "agent_name": "   "},
    )
    _assert_golden("agent_create_error", resp)


def test_agent_awaiting_page(oauth_client_view: MagicMock) -> None:
    svc = _authorize_svc(pending=PendingAgentRef(id="agnt_p", name="pending <bot>"))
    client, backend, _ctx = _app(svc)
    _seed_handle(backend, subject=_LOCAL_SUBJECT)
    resp = client.get("/oauth/consent", params={"ch": _HANDLE})
    _assert_golden("agent_awaiting", resp)


def test_approval_pending_page(oauth_client_view: MagicMock) -> None:
    oauth_client_view.return_value = _client_view(approval_status="pending", active=False)
    client, _backend, _ctx = _app(_authorize_svc())
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": _CLIENT_ID,
            "redirect_uri": _REDIRECT_URI,
            "code_challenge": _CODE_CHALLENGE,
            "code_challenge_method": "S256",
            "scope": "openid apis:read",
            "state": "st<1>",
            "nonce": "n&1",
        },
    )
    _assert_golden("approval_pending", resp)
