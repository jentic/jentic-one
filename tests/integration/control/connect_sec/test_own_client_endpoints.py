"""Own OAuth client: the approver's client secret only goes where the approver said.

The API's spec may be agent- or user-submitted, so its declared
``authorizationUrl`` / ``tokenUrl`` are never a fallback for the approver's
own client: the approver enters both endpoints, and they pass the upstream
URL policy (https only, no private or metadata targets beyond what the
operator's ``control.egress`` allowlist names).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from jentic_one.control.repos.oauth_client_credential_repo import (
    OAuthClientCredentialRepository,
)
from jentic_one.control.services.integrations.connect_session_service import (
    AuthCodeConfirmResult,
    OwnClientConfirm,
)
from jentic_one.control.services.integrations.errors import OwnClientInvalidError
from jentic_one.control.services.integrations.flow_handlers.auth_code import (
    AuthCodeExchangeError,
    AuthCodeFlowHandler,
)
from jentic_one.shared.config import ControlEgressConfig
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT_ID,
    OWNER,
    RULES,
    checks,
    client,
    connect,
    digest,
    import_spec,
    revision,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_CLIENT_SECRET = "own-client-secret-" + "C" * 20
# An agent-submitted spec pointing the OAuth endpoints at a host it controls.
_HOSTILE_OAUTH = {
    "oauth": {
        "type": "oauth2",
        "flows": {
            "authorizationCode": {
                "authorizationUrl": "https://collector.attacker.example/authorize",
                "tokenUrl": "https://collector.attacker.example/token",
                "scopes": {"read": "Read"},
            }
        },
    }
}


def _body(review_digest: str, revision_id: str, **endpoints: Any) -> dict[str, Any]:
    return {
        "kind": "own_oauth_client",
        "client_id": "approver-client",
        "client_secret": _CLIENT_SECRET,
        "permission_rules": RULES,
        "expected_agent_id": AGENT_ID,
        "digest": review_digest,
        "revision_id": revision_id,
        "confirmed_scopes": ["read"],
        **endpoints,
    }


@pytest.mark.parametrize(
    "endpoints",
    [
        {},
        {"authorize_url": "https://idp.approver.example/authorize"},
        {"token_url": "https://idp.approver.example/token"},
        {"authorize_url": "", "token_url": ""},
        {"authorize_url": None, "token_url": None},
    ],
    ids=["neither", "no-token-url", "no-authorize-url", "blank", "null"],
)
async def test_spec_endpoints_are_never_a_fallback_for_the_approvers_client(
    env: Context, endpoints: dict[str, Any]
) -> None:
    await import_spec(env, _HOSTILE_OAUTH, origin="user", submitted_by=AGENT_ID)
    created = await connect(env, requested_scopes=["read"])
    async with client(env, OWNER) as owner:
        refused = await owner.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json=_body(
                await digest(env, created.session_id),
                await revision(env, created.session_id),
                **endpoints,
            ),
        )
    assert refused.status_code in (400, 422), refused.text
    assert _CLIENT_SECRET not in refused.text
    assert "attacker" not in refused.text
    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "awaiting_app"
    async with env.control_db.session() as session:
        assert (
            await OAuthClientCredentialRepository.get_by_credential(session, row.credential_id)
            is None
        )


async def test_service_refuses_missing_endpoints_too(env: Context) -> None:
    await import_spec(env, _HOSTILE_OAUTH, origin="user", submitted_by=AGENT_ID)
    created = await connect(env, requested_scopes=["read"])
    with pytest.raises(OwnClientInvalidError):
        await svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=OwnClientConfirm(
                client_id="approver-client",
                client_secret=SecretStr(_CLIENT_SECRET),
                authorize_url="",
                token_url="",
                confirmed_scopes=["read"],
                checks=checks(
                    await digest(env, created.session_id),
                    revision_id=await revision(env, created.session_id),
                ),
            ),
            identity=OWNER,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/token",
        "http://10.0.0.5/token",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/token",
        "http://[::1]/token",
        "file:///etc/passwd",
        "http://192.168.1.10/token",
        "http://idp.approver.example/token",
        "idp.approver.example/token",
    ],
)
async def test_approver_endpoints_must_pass_the_upstream_url_policy(env: Context, url: str) -> None:
    await import_spec(env, _HOSTILE_OAUTH)
    created = await connect(env, requested_scopes=["read"])
    async with client(env, OWNER) as owner:
        refused = await owner.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json=_body(
                await digest(env, created.session_id),
                await revision(env, created.session_id),
                authorize_url="https://idp.approver.example/authorize",
                token_url=url,
            ),
        )
    assert (refused.status_code, refused.json()["type"]) == (400, "own_oauth_client_invalid")
    assert _CLIENT_SECRET not in refused.text


async def test_approver_endpoints_are_the_ones_used(env: Context) -> None:
    await import_spec(env, _HOSTILE_OAUTH, origin="user", submitted_by=AGENT_ID)
    created = await connect(env, requested_scopes=["read"])
    result = await svc(env).confirm_variant(
        created.session_id,
        poll_token=None,
        variant=OwnClientConfirm(
            client_id="approver-client",
            client_secret=SecretStr(_CLIENT_SECRET),
            authorize_url="https://idp.approver.example/authorize",
            token_url="https://idp.approver.example/token",
            confirmed_scopes=["read"],
            checks=checks(
                await digest(env, created.session_id),
                revision_id=await revision(env, created.session_id),
            ),
        ),
        identity=OWNER,
    )
    assert isinstance(result, AuthCodeConfirmResult)
    assert result.authorize_url.startswith("https://idp.approver.example/authorize?")
    row = await session_row(env, created.session_id)
    assert row is not None
    async with env.control_db.session() as session:
        occ = await OAuthClientCredentialRepository.get_by_credential(session, row.credential_id)
    assert occ is not None
    assert (occ.authorize_url, occ.token_url) == (
        "https://idp.approver.example/authorize",
        "https://idp.approver.example/token",
    )


@pytest.mark.parametrize(
    ("broker_subnets", "control_subnets"),
    [
        ([], None),
        (["10.0.0.0/8"], []),
    ],
    ids=["strict-default", "control-override-stricter-than-broker"],
)
async def test_approvers_endpoints_follow_the_control_egress_policy_when_refused(
    env: Context,
    monkeypatch: pytest.MonkeyPatch,
    broker_subnets: list[str],
    control_subnets: list[str] | None,
) -> None:
    """Approver-entered endpoints are checked under ``control.egress``, as on ``POST /credentials``.

    With no allowlist, or a ``control.egress`` override narrower than
    ``broker.egress``, a private token endpoint is refused at confirm.
    """
    monkeypatch.setattr(env.config.broker.egress, "allowed_private_subnets", broker_subnets)
    monkeypatch.setattr(
        env.config.control, "egress", ControlEgressConfig(allowed_private_subnets=control_subnets)
    )
    await import_spec(env, _HOSTILE_OAUTH)
    created = await connect(env, requested_scopes=["read"])
    async with client(env, OWNER) as owner:
        refused = await owner.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json=_body(
                await digest(env, created.session_id),
                await revision(env, created.session_id),
                authorize_url="https://10.1.2.3/authorize",
                token_url="https://10.1.2.3/token",
            ),
        )
    assert (refused.status_code, refused.json()["type"]) == (400, "own_oauth_client_invalid")
    assert _CLIENT_SECRET not in refused.text


@pytest.mark.parametrize(
    ("broker_subnets", "control_subnets"),
    [
        (["10.0.0.0/8"], None),
        ([], ["10.0.0.0/8"]),
    ],
    ids=["inherited-from-broker", "control-override"],
)
async def test_allowlisted_in_network_idp_works_end_to_end_for_the_approvers_client(
    env: Context,
    monkeypatch: pytest.MonkeyPatch,
    broker_subnets: list[str],
    control_subnets: list[str] | None,
) -> None:
    """An allowlisted in-network IdP is accepted at confirm and reached, pinned, on the exchange.

    Narrowing ``control.egress`` afterwards refuses the exchange before any
    request carries the client secret.
    """
    monkeypatch.setattr(env.config.broker.egress, "allowed_private_subnets", broker_subnets)
    monkeypatch.setattr(
        env.config.control, "egress", ControlEgressConfig(allowed_private_subnets=control_subnets)
    )
    await import_spec(env, _HOSTILE_OAUTH, origin="user", submitted_by=AGENT_ID)
    created = await connect(env, requested_scopes=["read"])
    result = await svc(env).confirm_variant(
        created.session_id,
        poll_token=None,
        variant=OwnClientConfirm(
            client_id="approver-client",
            client_secret=SecretStr(_CLIENT_SECRET),
            authorize_url="https://10.1.2.3/authorize",
            token_url="https://10.1.2.3/token",
            confirmed_scopes=["read"],
            checks=checks(await digest(env, created.session_id)),
        ),
        identity=OWNER,
    )
    assert isinstance(result, AuthCodeConfirmResult)
    row = await session_row(env, created.session_id)
    assert row is not None

    sent: list[httpx.Request] = []

    def _token(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"access_token": "at-ok", "expires_in": 3600})

    with patch.object(
        httpx, "AsyncHTTPTransport", side_effect=lambda *a, **k: httpx.MockTransport(_token)
    ):
        tokens = await AuthCodeFlowHandler(env).complete_from_callback(row, code="the-code")
        assert tokens.access_token == "at-ok"
        assert [(r.url.host, r.url.path) for r in sent] == [("10.1.2.3", "/token")]

        monkeypatch.setattr(
            env.config.control, "egress", ControlEgressConfig(allowed_private_subnets=[])
        )
        with pytest.raises(AuthCodeExchangeError, match="unsafe upstream URL"):
            await AuthCodeFlowHandler(env).complete_from_callback(row, code="the-code")
    assert len(sent) == 1
