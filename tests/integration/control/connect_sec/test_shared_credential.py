"""Shared credential: re-authorizing never widens another agent's access.

Re-authorize (incremental consent on a credential the approver already holds)
is offered only when no other agent is bound to it. Extends
``test_connect_session_api_targets.py`` (the review/confirm-time refusal) with
the consent-completion re-check: another agent bound while the approver is at
the vendor's consent screen makes the callback refuse the wider grant.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from jentic_one.control.repos.oauth_token_repo import OAuthTokenRepository
from jentic_one.control.services.credentials.connect_service import (
    ConnectFlowError,
    ConnectService,
)
from jentic_one.control.services.credentials.schemas.connect import (
    AuthCodeChallenge,
    ConnectCallback,
    ConnectRequest,
)
from jentic_one.control.services.credentials.schemas.provision import ProvisionResult
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectedConfirmResult,
    ExistingCredentialConfirm,
    ReauthorizeConfirmResult,
)
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT_ID,
    OAUTH_SCHEME,
    OWNER,
    RULES,
    SIBLING_AGENT_ID,
    bind,
    checks,
    client,
    connect,
    digest,
    existing_credential,
    import_spec,
    is_bound,
    svc,
)

pytestmark = pytest.mark.integration


async def _reauthorize(ctx: Context, session_id: str, credential_id: str) -> Any:
    return await svc(ctx).confirm_variant(
        session_id,
        poll_token=None,
        variant=ExistingCredentialConfirm(
            credential_id=credential_id,
            reauthorize=True,
            checks=checks(await digest(ctx, session_id)),
        ),
        identity=OWNER,
    )


def _state_of(authorize_url: str) -> str:
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


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


async def _granted(ctx: Context, credential_id: str) -> str | None:
    async with ctx.control_db.session() as session:
        token = await OAuthTokenRepository.get_by_credential(session, credential_id)
    return token.scope if token is not None else None


async def test_reauthorize_over_http_is_refused_while_another_agent_is_bound(
    env: Context,
) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read", "write"])
    narrow = await existing_credential(env, oauth_scope="read")
    await bind(env, SIBLING_AGENT_ID, narrow)
    async with client(env, OWNER) as owner:
        refused = await owner.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json={
                "kind": "reauthorize",
                "credential_id": narrow,
                "permission_rules": RULES,
                "expected_agent_id": AGENT_ID,
                "digest": await digest(env, created.session_id),
            },
        )
    assert (refused.status_code, refused.json()["type"]) == (409, "reauthorize_unavailable")
    assert not await is_bound(env, AGENT_ID, narrow)
    assert await _granted(env, narrow) == "read"


async def test_consent_completion_refuses_when_another_agent_was_bound_meanwhile(
    env: Context, vendor_grants_everything: list[str]
) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read", "write"])
    narrow = await existing_credential(env, oauth_scope="read")
    result = await _reauthorize(env, created.session_id, narrow)
    assert isinstance(result, ReauthorizeConfirmResult)

    # While the approver is on the vendor's consent screen, a sibling agent's
    # narrower ask binds the same credential (its grant already covers ``read``).
    sibling = await connect(env, agent_id=SIBLING_AGENT_ID, requested_scopes=["read"])
    bound = await svc(env).confirm_variant(
        sibling.session_id,
        poll_token=None,
        variant=ExistingCredentialConfirm(
            credential_id=narrow,
            reauthorize=False,
            checks=checks(
                await digest(env, sibling.session_id), expected_agent_id=SIBLING_AGENT_ID
            ),
        ),
        identity=OWNER,
    )
    assert isinstance(bound, ConnectedConfirmResult)
    assert await is_bound(env, SIBLING_AGENT_ID, narrow)

    with pytest.raises(ConnectFlowError):
        await ConnectService(env).complete(
            _state_of(result.authorize_url), ConnectCallback(code="vendor-code")
        )
    # The wider grant was never stored (nor the code even exchanged).
    assert await _granted(env, narrow) == "read"
    assert vendor_grants_everything == []


async def test_consent_completion_widens_when_the_agent_is_still_the_only_one(
    env: Context, vendor_grants_everything: list[str]
) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read", "write"])
    narrow = await existing_credential(env, oauth_scope="read")
    result = await _reauthorize(env, created.session_id, narrow)
    assert isinstance(result, ReauthorizeConfirmResult)
    assert (
        await ConnectService(env).complete(
            _state_of(result.authorize_url), ConnectCallback(code="vendor-code")
        )
        == narrow
    )
    assert await _granted(env, narrow) == "read write"


async def test_plain_reconnect_of_a_shared_credential_is_unaffected(
    env: Context, vendor_grants_everything: list[str]
) -> None:
    """The re-check rides only re-authorize states, not the owner's own reconnect."""
    narrow = await existing_credential(env, oauth_scope="read")
    await bind(env, AGENT_ID, narrow)
    await bind(env, SIBLING_AGENT_ID, narrow)
    challenge = await ConnectService(env).begin(
        narrow,
        ConnectRequest(scopes=["read"]),
        actor_id=OWNER.sub,
        actor_type=OWNER.actor_type,
        redirect_uri="https://app.example.com/credentials/oauth/callback",
    )
    assert isinstance(challenge, AuthCodeChallenge)
    completed = await ConnectService(env).complete(
        challenge.state, ConnectCallback(code="vendor-code")
    )
    assert completed == narrow
