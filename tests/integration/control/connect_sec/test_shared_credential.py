"""Shared credential: using an existing credential that lacks scopes, in one step.

Picking an existing OAuth credential whose grant lacks scopes the session
asks for runs a vendor consent for them on that credential's own client, and
the bind, rules and wider scopes land together with the grant. The review
lists every other agent bound to the credential, because the wider grant
reaches them too. Until the grant lands nothing changes: the agent is not
bound, and the agents already bound keep exactly what they had.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.connect_service import ConnectService
from jentic_one.control.services.credentials.schemas.connect import (
    AuthCodeChallenge,
    ConnectCallback,
    ConnectRequest,
)
from jentic_one.control.services.credentials.schemas.provision import ProvisionResult
from jentic_one.control.services.credentials.state import new_browser_binding
from jentic_one.control.services.integrations.connect_session_service import (
    AuthCodeConfirmResult,
    ExistingCredentialConfirm,
)
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    OAUTH_SCHEME,
    OWNER,
    SIBLING_AGENT_ID,
    bind,
    checks,
    connect,
    digest,
    existing_credential,
    import_spec,
    is_bound,
    revision,
    svc,
)

pytestmark = pytest.mark.integration

# The approver's browser; its cookie value accompanies each callback below.
_BINDING = new_browser_binding()


async def _use_existing(ctx: Context, session_id: str, credential_id: str) -> Any:
    return await svc(ctx).confirm_variant(
        session_id,
        poll_token=None,
        variant=ExistingCredentialConfirm(
            credential_id=credential_id,
            checks=checks(
                await digest(ctx, session_id), revision_id=await revision(ctx, session_id)
            ),
        ),
        identity=OWNER,
        browser_binding=_BINDING.claim,
    )


def _state_of(authorize_url: str) -> str:
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


class _TokenEndpoint:
    """httpx.AsyncClient stand-in: every code exchanges for ``read write``."""

    async def __aenter__(self) -> _TokenEndpoint:
        return self

    async def __aexit__(self, *a: object) -> bool:
        return False

    async def post(self, *args: object, **kwargs: object) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": "wider-access", "expires_in": 3600, "scope": "read write"}
        )


async def _granted(ctx: Context, credential_id: str) -> str | None:
    async with ctx.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
    return token.scope if token is not None else None


async def test_a_narrow_credential_is_used_in_one_step_and_lands_with_the_grant(
    env: Context,
) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read", "write"])
    narrow = await existing_credential(env, oauth_scope="read")
    await bind(env, SIBLING_AGENT_ID, narrow)

    review = await svc(env).get_review_data(created.session_id, poll_token=None, identity=OWNER)
    (view,) = review.existing_credentials
    assert (view.missing_scopes, view.needs_vendor_consent, view.can_bind) == (
        ["write"],
        True,
        True,
    )
    assert [a.agent_id for a in view.other_bound_agents] == [SIBLING_AGENT_ID]

    result = await _use_existing(env, created.session_id, narrow)
    assert isinstance(result, AuthCodeConfirmResult)
    assert result.authorize_url.startswith("https://auth.vault.example/authorize?")
    # Nothing has changed yet: not bound, and the sibling keeps its grant.
    assert not await is_bound(env, AGENT_ID, narrow)
    assert await _granted(env, narrow) == "read"
    status = await svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=AGENT
    )
    assert status.status == "pending"

    with patch("httpx.AsyncClient", return_value=_TokenEndpoint()):
        landed = await svc(env).complete_from_callback(
            raw_state=_state_of(result.authorize_url),
            code="vendor-code",
            browser_binding=_BINDING.value,
        )
    assert (landed.status, landed.credential_id) == ("connected", narrow)
    assert await is_bound(env, AGENT_ID, narrow)
    assert await _granted(env, narrow) == "read write"
    status = await svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=AGENT
    )
    assert (status.status, status.credential_id) == ("connected", narrow)


async def test_a_failed_consent_never_binds_or_widens(env: Context) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read", "write"])
    narrow = await existing_credential(env, oauth_scope="read")
    result = await _use_existing(env, created.session_id, narrow)
    assert isinstance(result, AuthCodeConfirmResult)
    await svc(env).mark_terminal_from_callback(
        raw_state=_state_of(result.authorize_url),
        error="access_denied",
        browser_binding=_BINDING.value,
    )
    assert not await is_bound(env, AGENT_ID, narrow)
    assert await _granted(env, narrow) == "read"
    status = await svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=AGENT
    )
    assert status.status == "failed"


@pytest.fixture()
def vendor_grants_everything(env: Context, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for the vendor's token endpoint: every code yields ``read write``."""
    exchanged: list[str] = []
    provider = env.providers.get("direct_oauth2")

    async def complete_connect(ctx: Context, *, state: Any, callback: Any) -> ProvisionResult:
        exchanged.append(state.credential_id)
        return ProvisionResult(access_token="wider-access", scope="read write")

    monkeypatch.setattr(provider, "complete_connect", complete_connect)
    return exchanged


async def test_plain_reconnect_of_a_shared_credential_is_unaffected(
    env: Context, vendor_grants_everything: list[str]
) -> None:
    """The owner's own reconnect of a shared credential needs no connect session."""
    narrow = await existing_credential(env, oauth_scope="read")
    await bind(env, AGENT_ID, narrow)
    await bind(env, SIBLING_AGENT_ID, narrow)
    challenge = await ConnectService(env).begin(
        narrow,
        ConnectRequest(scopes=["read"]),
        identity=OWNER,
        redirect_uri="https://app.example.com/credentials/oauth/callback",
        browser_binding=_BINDING.claim,
    )
    assert isinstance(challenge, AuthCodeChallenge)
    completed = await ConnectService(env).complete(
        challenge.state, ConnectCallback(code="vendor-code"), browser_binding=_BINDING.value
    )
    assert completed == narrow
