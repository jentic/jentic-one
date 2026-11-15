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

from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
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
    OTHER_OWNER,
    OWNER,
    OWNER_ID,
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
    revision,
    svc,
)

pytestmark = pytest.mark.integration

# The approver's browser; its cookie value accompanies each callback below.
_BINDING = new_browser_binding()


async def _use_existing(
    ctx: Context,
    session_id: str,
    credential_id: str,
    *,
    rules: list[dict[str, object]] | None = None,
) -> Any:
    return await svc(ctx).confirm_variant(
        session_id,
        poll_token=None,
        variant=ExistingCredentialConfirm(
            credential_id=credential_id,
            checks=checks(
                await digest(ctx, session_id),
                revision_id=await revision(ctx, session_id),
                rules=rules,
            ),
        ),
        identity=OWNER,
        browser_binding=_BINDING.claim,
    )


def _state_of(authorize_url: str) -> str:
    return parse_qs(urlsplit(authorize_url).query)["state"][0]


def _token_endpoint(request: httpx.Request) -> httpx.Response:
    """Vendor token endpoint: every code exchanges for ``read write``."""
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

    with patch(
        "jentic_one.shared.http.idp_client.build_always_pinned_transport",
        return_value=httpx.MockTransport(_token_endpoint),
    ):
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


async def test_the_session_reads_as_an_edit_while_it_awaits_the_vendor(env: Context) -> None:
    """Taking the edit intent means the review reads the session as an edit.

    Without a baseline that read raised out of the app for the whole window
    the session awaits its callback, for the approver and the agent alike.
    """
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read", "write"])
    narrow = await existing_credential(env, oauth_scope="read")
    result = await _use_existing(env, created.session_id, narrow)
    assert isinstance(result, AuthCodeConfirmResult)

    review = await svc(env).get_review_data(created.session_id, poll_token=None, identity=OWNER)
    assert review.edit is not None
    # Nothing is bound yet, so the access it would change is empty.
    assert review.edit.current["rules"] == []
    assert review.edit.current["granted_scopes"] == ["read"]
    assert review.state == "polling"


@pytest.mark.parametrize("requested_rules", [[], RULES], ids=["zero-rules", "with-rules"])
async def test_edit_review_over_http_after_confirm_existing_credential(
    env: Context, requested_rules: list[dict[str, object]]
) -> None:
    """HTTP review of a session confirmed onto an existing credential returns valid edit access."""
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, rules=requested_rules, requested_scopes=["write"])
    narrow = await existing_credential(env, oauth_scope="read")
    result = await _use_existing(env, created.session_id, narrow)
    assert isinstance(result, AuthCodeConfirmResult)

    async with client(env, OWNER) as approver:
        approver_resp = await approver.get(f"/connect-sessions/{created.session_id}")
    async with client(env, OTHER_OWNER) as poll_reader:
        poll_resp = await poll_reader.get(
            f"/connect-sessions/{created.session_id}",
            params={"poll_token": created.poll_token},
        )

    async with env.control_db.transaction() as session:
        await ConnectSessionRepository.transition_state(
            session,
            created.session_id,
            to_state="connected",
            from_states=("polling",),
        )

    async with client(env, OWNER) as approver:
        terminal_resp = await approver.get(f"/connect-sessions/{created.session_id}")
    async with client(env, OTHER_OWNER) as poll_reader:
        terminal_poll_resp = await poll_reader.get(
            f"/connect-sessions/{created.session_id}",
            params={"poll_token": created.poll_token},
        )

    for resp, expected_state in (
        (approver_resp, "polling"),
        (poll_resp, "polling"),
        (terminal_resp, "connected"),
        (terminal_poll_resp, "connected"),
    ):
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == expected_state
        edit = body["edit"]
        assert edit is not None
        assert edit["widens_access"] is True
        assert edit["needs_vendor_consent"] is True
        access = edit["result"]
        assert access["target"] == "binding"
        assert access["scopes"] == ["read", "write"]
        assert access["scope_notes"] == {}
        assert access["suspended"] is False
        assert len(access["rules"]) == len(requested_rules)
        for rule in access["rules"]:
            assert rule["id"] is None
            assert rule["added_by"] == AGENT_ID


async def test_edit_review_and_real_terminal_routes_after_confirm_existing_credential(
    env: Context,
) -> None:
    """Confirming onto an existing credential over HTTP yields a valid edit review
    and revision history; reaching terminal state via HTTP ``:cancel`` or callback
    completion records the terminal outcome and cleans up the edit session row."""
    await import_spec(env, OAUTH_SCHEME)
    narrow = await existing_credential(env, oauth_scope="read")

    # 1. Confirm over HTTP and reach terminal state via the real ``POST ...:cancel`` route.
    cancelled_session = await connect(env, rules=RULES, requested_scopes=["write"])
    async with client(env, OWNER) as approver:
        confirm_resp = await approver.post(
            f"/connect-sessions/{cancelled_session.session_id}:confirm",
            json={
                "kind": "existing_credential",
                "credential_id": narrow,
                "permission_rules": RULES,
                "expected_agent_id": AGENT_ID,
                "digest": await digest(env, cancelled_session.session_id),
                "revision_id": await revision(env, cancelled_session.session_id),
            },
        )
        assert confirm_resp.status_code == 200, confirm_resp.text

        review_resp = await approver.get(f"/connect-sessions/{cancelled_session.session_id}")
        assert review_resp.status_code == 200, review_resp.text
        review_body = review_resp.json()
        assert review_body["state"] == "polling"
        assert review_body["edit"]["result"]["target"] == "binding"
        assert review_body["edit"]["result"]["scopes"] == ["read", "write"]
        assert [r["id"] for r in review_body["edit"]["result"]["rules"]] == [None]

        revisions_resp = await approver.get(
            f"/connect-sessions/{cancelled_session.session_id}/revisions"
        )
        assert revisions_resp.status_code == 200, revisions_resp.text
        assert len(revisions_resp.json()["data"]) == 1

        cancel_resp = await approver.post(
            f"/connect-sessions/{cancelled_session.session_id}:cancel"
        )
        assert cancel_resp.status_code == 204, cancel_resp.text

        status_resp = await approver.get(f"/connect-sessions/{cancelled_session.session_id}/status")
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["status"] == "failed"
        assert status_resp.json()["error_code"] == "cancelled"

        # ``_mark_terminal`` deletes ``intent="edit"`` session rows on terminal transition.
        after_cancel_review = await approver.get(
            f"/connect-sessions/{cancelled_session.session_id}"
        )
        assert after_cancel_review.status_code == 403

    # 2. Confirm a second session and complete vendor consent via ``complete_from_callback``.
    completed_session = await connect(env, rules=RULES, requested_scopes=["write"])
    result = await _use_existing(env, completed_session.session_id, narrow)
    assert isinstance(result, AuthCodeConfirmResult)

    with patch(
        "jentic_one.shared.http.idp_client.build_always_pinned_transport",
        return_value=httpx.MockTransport(_token_endpoint),
    ):
        landed = await svc(env).complete_from_callback(
            raw_state=_state_of(result.authorize_url),
            code="vendor-code",
            browser_binding=_BINDING.value,
        )
    assert (landed.status, landed.credential_id) == ("connected", narrow)

    async with client(env, OWNER) as approver:
        completed_status = await approver.get(
            f"/connect-sessions/{completed_session.session_id}/status"
        )
        assert completed_status.status_code == 200, completed_status.text
        assert completed_status.json()["status"] == "connected"
        assert completed_status.json()["credential_id"] == narrow


async def test_binding_change_during_vendor_consent_keeps_the_confirmed_edit_review(
    env: Context,
) -> None:
    """Mutating the binding during vendor consent leaves the confirmed ask intact."""
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, rules=RULES, requested_scopes=["read", "write"])
    narrow = await existing_credential(env, oauth_scope="read")
    result = await _use_existing(env, created.session_id, narrow)
    assert isinstance(result, AuthCodeConfirmResult)

    await bind(env, AGENT_ID, narrow)
    async with env.control_db.transaction() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session,
            AGENT_ID,
            narrow,
            [{"effect": "deny", "methods": ["DELETE"], "path": "/items", "match_mode": "exact"}],
            created_by=OWNER_ID,
        )

    async with client(env, OWNER) as approver:
        rebased = await approver.post(f"/connect-sessions/{created.session_id}:rebase")
        assert rebased.status_code == 409
        review_resp = await approver.get(f"/connect-sessions/{created.session_id}")

    assert review_resp.status_code == 200, review_resp.text
    body = review_resp.json()
    assert body["stale"] is False
    edit = body["edit"]
    assert edit["revision"]["number"] == 1
    assert [r["path"] for r in edit["result"]["rules"]] == ["/items"]
    assert [r["id"] for r in edit["result"]["rules"]] == [None]
    assert edit["result"]["scopes"] == ["read", "write"]


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
