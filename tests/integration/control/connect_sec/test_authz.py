"""AuthZ: only a human approver acts on a session; nobody else can even tell it exists.

Extends ``test_connect_session_api_targets.py`` (agent can read but not confirm
the review, reject refused for agent / stranger / owner without
``agents:write``) with: agents holding ``org:admin`` (directly or inherited),
byte-identical refusals across the session routes, a token-free approval URL,
owner-scoped listing, and delegation that never widens it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from jentic_one.control.services.integrations.errors import (
    ConfirmationForbiddenError,
    InvalidPollTokenError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    DEFAULT_AGENT_PERMISSIONS,
    OWNER_CREDENTIALS_READ,
)
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.integration.control.connect_sec.support import (
    ADMIN,
    AGENT_ID,
    FOREIGN_AGENT_ID,
    KEY_SCHEME,
    OTHER_OWNER,
    OWNER,
    OWNER_ID,
    OWNER_NO_AGENTS_WRITE,
    RULES,
    SIBLING_AGENT_ID,
    TARGET,
    client,
    connect,
    digest,
    import_spec,
    key_variant,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_CANARY = "sk_authz_" + "A" * 24
_ALL = ["org:admin", "credentials:write", "agents:write", "credentials:connect"]

# An agent granted everything directly, and one inheriting it from its owner.
_ADMIN_AGENT = Identity(sub=AGENT_ID, permissions=_ALL, actor_type=ActorType.AGENT)
_INHERITING_AGENT = Identity(
    sub=AGENT_ID,
    permissions=list(DEFAULT_AGENT_PERMISSIONS),
    parent_permissions=_ALL,
    parent_actor_id=OWNER_ID,
    actor_type=ActorType.AGENT,
)
_ADMIN_SIBLING = Identity(sub=SIBLING_AGENT_ID, permissions=_ALL, actor_type=ActorType.AGENT)


@pytest.mark.parametrize("agent", [_ADMIN_AGENT, _INHERITING_AGENT, _ADMIN_SIBLING])
async def test_agents_never_confirm_or_reject_even_as_org_admin(
    env: Context, agent: Identity
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    reviewed = await digest(env, created.session_id)
    service = svc(env)
    # With the session's own poll token (the agent that asked) or without it.
    for token in (created.poll_token, None):
        with pytest.raises((ConfirmationForbiddenError, InvalidPollTokenError)):
            await service.confirm_variant(
                created.session_id,
                poll_token=token,
                variant=key_variant(_CANARY, reviewed),
                identity=agent,
            )
        with pytest.raises((ConfirmationForbiddenError, InvalidPollTokenError)):
            await service.confirm(
                created.session_id,
                poll_token=token,
                confirmed_scopes=[],
                permission_rules=RULES,
                identity=agent,
                expected_agent_id=AGENT_ID,
                digest=reviewed,
            )
    with pytest.raises(InvalidPollTokenError):
        await service.reject_session(created.session_id, identity=agent)
    # Without the token an agent is never an approver: no review, no status.
    with pytest.raises(InvalidPollTokenError):
        await service.get_review_data(created.session_id, poll_token=None, identity=agent)
    with pytest.raises(InvalidPollTokenError):
        await service.get_status(created.session_id, poll_token=None, identity=agent)
    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "created"


async def test_agent_confirm_over_http_is_403(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    body = {
        "kind": "api_key",
        "key": _CANARY,
        "permission_rules": RULES,
        "expected_agent_id": AGENT_ID,
        "digest": await digest(env, created.session_id),
    }
    async with client(env, _ADMIN_AGENT) as agent:
        with_token = await agent.post(
            f"/connect-sessions/{created.session_id}:confirm",
            params={"poll_token": created.poll_token},
            json=body,
        )
        rejected = await agent.post(f"/connect-sessions/{created.session_id}:reject")
    assert with_token.status_code == 403 and _CANARY not in with_token.text
    assert rejected.status_code == 403


def _normalised(body: str, session_id: str) -> str:
    """The body with the caller's own path echo (``instance``) normalised."""
    return body.replace(session_id, "<id>")


async def _refusals(
    env: Context, identity: Identity, session_id: str, poll_token: str | None
) -> dict[str, tuple[int, str]]:
    params: dict[str, Any] = {"poll_token": poll_token} if poll_token is not None else {}
    confirm_body = {
        "kind": "api_key",
        "key": _CANARY,
        "permission_rules": RULES,
        "expected_agent_id": AGENT_ID,
        "digest": "0" * 64,
    }
    out: dict[str, tuple[int, str]] = {}
    async with client(env, identity) as http:
        responses = {
            "review": await http.get(f"/connect-sessions/{session_id}", params=params),
            "status": await http.get(f"/connect-sessions/{session_id}/status", params=params),
            "confirm": await http.post(
                f"/connect-sessions/{session_id}:confirm", params=params, json=confirm_body
            ),
            "cancel": await http.post(f"/connect-sessions/{session_id}:cancel", params=params),
            "reject": await http.post(f"/connect-sessions/{session_id}:reject"),
        }
    for route, response in responses.items():
        out[route] = (response.status_code, _normalised(response.text, session_id))
    return out


async def test_missing_session_wrong_token_and_non_owner_are_indistinguishable(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    missing = "cs_" + "0" * 24
    cases = {
        "missing-session": await _refusals(env, OTHER_OWNER, missing, None),
        "missing-session-owner": await _refusals(env, OWNER, missing, None),
        "wrong-token": await _refusals(env, OTHER_OWNER, created.session_id, "not-the-token"),
        "non-owner": await _refusals(env, OTHER_OWNER, created.session_id, None),
        "owner-without-agents-write": await _refusals(
            env, OWNER_NO_AGENTS_WRITE, created.session_id, None
        ),
    }
    baseline = cases["missing-session"]
    for route, (status, body) in baseline.items():
        assert status == 403, (route, body)
        assert json.loads(body)["type"] == "invalid_poll_token", (route, body)
        assert _CANARY not in body
    for name, refusals in cases.items():
        assert refusals == baseline, name
    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "created"


async def test_approval_url_and_every_read_are_free_of_the_poll_token(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    assert "poll_token" not in created.approval_url
    assert created.poll_token not in created.approval_url
    async with client(env, OWNER) as owner:
        review = await owner.get(f"/connect-sessions/{created.session_id}")
        listing = await owner.get("/connect-sessions")
    for response in (review, listing):
        assert response.status_code == 200, response.text
        assert created.poll_token not in response.text
        assert "poll_token" not in response.text


async def _session_ids(env: Context, identity: Identity) -> set[str]:
    page = await svc(env).list_all(identity=identity)
    return {s.session_id for s in page.data}


async def test_owner_lists_only_their_own_agents_sessions(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    mine = (await connect(env)).session_id
    sibling = (await connect(env, agent_id=SIBLING_AGENT_ID)).session_id
    foreign = (await connect(env, agent_id=FOREIGN_AGENT_ID)).session_id
    assert await _session_ids(env, OWNER) == {mine, sibling}
    assert await _session_ids(env, OTHER_OWNER) == {foreign}
    assert await _session_ids(env, ADMIN) >= {mine, sibling, foreign}
    # An owner without agents:write still only sees their own agents' asks.
    assert await _session_ids(env, OWNER_NO_AGENTS_WRITE) == {mine, sibling}
    # The other owner can't act on them either.
    with pytest.raises(InvalidPollTokenError):
        await svc(env).get_review_data(mine, poll_token=None, identity=OTHER_OWNER)


async def test_delegation_never_widens_to_sibling_agents(env: Context) -> None:
    """An agent reading with its owner's delegated scope sees the owner's sessions only."""
    await import_spec(env, KEY_SCHEME)
    own = (await connect(env)).session_id
    sibling = await connect(env, agent_id=SIBLING_AGENT_ID)
    owner_started = await svc(env).create_session(
        vendor_key="", agent_id=None, initiator_actor_id=OWNER_ID, api_target=TARGET
    )
    delegated = Identity(
        sub=AGENT_ID,
        permissions=[*DEFAULT_AGENT_PERMISSIONS, OWNER_CREDENTIALS_READ],
        parent_actor_id=OWNER_ID,
        actor_type=ActorType.AGENT,
    )
    visible = await _session_ids(env, delegated)
    # Its own ask and what its owner started (the documented delegation) ...
    assert {own, owner_started.session_id} <= visible
    # ... never another agent's, even one with the same owner.
    assert sibling.session_id not in visible
    # Nor can it reach the sibling's session through any other route.
    with pytest.raises(InvalidPollTokenError):
        await svc(env).get_review_data(sibling.session_id, poll_token=None, identity=delegated)
    with pytest.raises(InvalidPollTokenError):
        await svc(env).get_status(sibling.session_id, poll_token=None, identity=delegated)
    with pytest.raises(InvalidPollTokenError):
        await svc(env).cancel_session(sibling.session_id, poll_token=None, identity=delegated)
    # The sibling's token is not ours to use either: it only works for the session it names.
    with pytest.raises(InvalidPollTokenError):
        await svc(env).get_status(own, poll_token=sibling.poll_token, identity=delegated)
