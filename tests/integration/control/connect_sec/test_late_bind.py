"""Late-binding an agent on confirm answers like naming it when the session is created.

A user who opens a session without an agent picks one on confirm. An agent
they do not own, and one that does not exist, get the same refusal as
``POST /integrations:connect`` gives for an unknown agent, and nothing is
written for either.

A session an agent started already names its agent, shown in the review, so
a token holder who does not own that agent is told the approval is not
theirs to make.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import Response
from sqlalchemy import text

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT_ID,
    API_BODY,
    FOREIGN_AGENT_ID,
    KEY_SCHEME,
    OTHER_OWNER,
    OWNER,
    RULES,
    TARGET,
    client,
    connect,
    import_spec,
    is_bound,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_MISSING_AGENT_ID = "agnt_sec_missing"
_CANARY = "sk_late_bind_" + "C" * 24
#: Headers that legitimately differ per request or with the echoed id length.
_PER_REQUEST_HEADERS = frozenset({"date", "content-length", "x-request-id", "traceparent"})


def _headers(resp: Response) -> dict[str, str]:
    return {k: v for k, v in resp.headers.items() if k.lower() not in _PER_REQUEST_HEADERS}


def _body(resp: Response, agent_id: str, *, drop_instance: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = resp.json()
    if drop_instance:
        body.pop("instance", None)
    normalised: dict[str, Any] = {
        k: v.replace(agent_id, "<id>") if isinstance(v, str) else v for k, v in body.items()
    }
    return normalised


async def _confirm(
    ctx: Context,
    session_id: str,
    poll_token: str,
    *,
    caller: Identity = OWNER,
    agent_id: str | None = None,
    expected_agent_id: str | None = None,
) -> Response:
    review = await svc(ctx).get_review_data(session_id, poll_token=poll_token, identity=caller)
    body: dict[str, Any] = {
        "kind": "api_key",
        "key": _CANARY,
        "permission_rules": RULES,
        "digest": review.digest,
    }
    if agent_id is not None:
        body["agent_id"] = agent_id
    if expected_agent_id is not None:
        body["expected_agent_id"] = expected_agent_id
    async with client(ctx, caller) as http:
        return await http.post(
            f"/connect-sessions/{session_id}:confirm",
            params={"poll_token": poll_token},
            json=body,
        )


async def _late_bind(ctx: Context, session_id: str, poll_token: str, agent_id: str) -> Response:
    return await _confirm(ctx, session_id, poll_token, agent_id=agent_id)


async def _rule_count(ctx: Context, agent_id: str) -> int:
    async with ctx.control_db.session() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM agent_permission_rules WHERE agent_id = :a"),
            {"a": agent_id},
        )
    return int(count or 0)


async def test_late_bound_foreign_agent_matches_a_missing_agent_and_the_create_path(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await svc(env).create_session(
        vendor_key="", agent_id=None, identity=OWNER, api_target=TARGET
    )

    foreign = await _late_bind(env, created.session_id, created.poll_token, FOREIGN_AGENT_ID)
    missing = await _late_bind(env, created.session_id, created.poll_token, _MISSING_AGENT_ID)

    assert foreign.status_code == 400, foreign.text
    assert foreign.json()["type"] == "connect_session_agent_not_found"
    assert foreign.status_code == missing.status_code
    assert _headers(foreign) == _headers(missing)
    assert _body(foreign, FOREIGN_AGENT_ID) == _body(missing, _MISSING_AGENT_ID)

    # The same refusal as naming an unknown agent when the session is created
    # (``instance`` is the request path, so it differs between the routes).
    async with client(env, OWNER) as owner:
        at_create = await owner.post(
            "/integrations:connect", json={**API_BODY, "agent_id": _MISSING_AGENT_ID}
        )
    assert at_create.status_code == missing.status_code
    assert _headers(at_create) == _headers(missing)
    assert _body(at_create, _MISSING_AGENT_ID, drop_instance=True) == _body(
        missing, _MISSING_AGENT_ID, drop_instance=True
    )

    # Nothing was written for the foreign agent, and the session is untouched.
    assert await _rule_count(env, FOREIGN_AGENT_ID) == 0
    row = await session_row(env, created.session_id)
    assert row is not None
    assert (row.state, row.agent_id) == ("created", None)
    assert _CANARY not in foreign.text

    # The owner still late-binds their own agent.
    own = await _late_bind(env, created.session_id, created.poll_token, AGENT_ID)
    assert own.status_code == 200, own.text
    row = await session_row(env, created.session_id)
    assert row is not None and row.agent_id == AGENT_ID
    assert row.credential_id is not None
    assert await is_bound(env, AGENT_ID, row.credential_id)


async def test_agent_started_session_refuses_a_token_holder_who_does_not_own_the_agent(
    env: Context,
) -> None:
    """The agent is already named on the session, so the refusal says the approval isn't theirs.

    The agent's owner still confirms it.
    """
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)

    refused = await _confirm(
        env,
        created.session_id,
        created.poll_token,
        caller=OTHER_OWNER,
        expected_agent_id=AGENT_ID,
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["type"] == "connect_session_confirmation_forbidden"
    assert _CANARY not in refused.text
    assert await _rule_count(env, AGENT_ID) == 0
    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "created"

    own = await _confirm(env, created.session_id, created.poll_token, expected_agent_id=AGENT_ID)
    assert own.status_code == 200, own.text
    row = await session_row(env, created.session_id)
    assert row is not None and row.credential_id is not None
    assert await is_bound(env, AGENT_ID, row.credential_id)
