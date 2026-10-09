"""Host pinning: a credential only ever goes to the hosts the approver saw.

Extends ``test_connect_session_api_targets.py`` (which covers the basic
``servers_changed`` end, the held re-import and the enum-less host variable)
with the variants an attacker would try: widening the host set instead of
replacing it, widening a host variable's enum, the HTTP shape of the failure,
and reserved header names in every spelling.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectedConfirmResult,
)
from jentic_one.control.services.integrations.errors import (
    ReservedAuthFieldError,
    ServersChangedError,
    UnpinnedServerHostError,
)
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    API_BODY,
    KEY_SCHEME,
    OWNER,
    RULES,
    client,
    confirm_key,
    connect,
    digest,
    import_spec,
    is_bound,
    key_variant,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_CANARY = "sk_pin_" + "P" * 24
_ORIGINAL = [{"url": "https://api.vault.example"}]
_REGION = {
    "url": "https://{region}.vault.example",
    "variables": {"region": {"default": "eu", "enum": ["eu", "us"]}},
}


async def _no_secret_stored(ctx: Context, credential_id: str) -> bool:
    async with ctx.control_db.session() as session:
        row = (
            await session.execute(
                select(CustomerAPIKey).where(CustomerAPIKey.credential_id == credential_id)
            )
        ).first()
    return row is None


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # Replaced, widened by an extra server, and widened through an enum.
        (_ORIGINAL, [{"url": "https://evil.example"}]),
        (_ORIGINAL, [*_ORIGINAL, {"url": "https://evil.example"}]),
        (
            [_REGION],
            [
                {
                    **_REGION,
                    "variables": {"region": {"default": "eu", "enum": ["eu", "us", "evil"]}},
                }
            ],
        ),
        # Same host, plaintext scheme.
        (_ORIGINAL, [{"url": "http://api.vault.example"}]),
    ],
    ids=["replaced", "added", "enum-widened", "downgraded-to-http"],
)
async def test_servers_changed_between_create_and_confirm_fails_the_session(
    env: Context, before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> None:
    await import_spec(env, KEY_SCHEME, servers=before)
    created = await connect(env)
    row = await session_row(env, created.session_id)
    assert row is not None
    review_digest = await digest(env, created.session_id)
    # An operator-approved re-import makes the new servers current.
    await import_spec(env, KEY_SCHEME, servers=after, approved=True)

    async with client(env, OWNER) as owner:
        refused = await owner.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json={
                "kind": "api_key",
                "key": _CANARY,
                "permission_rules": RULES,
                "expected_agent_id": AGENT_ID,
                "digest": review_digest,
            },
        )
    assert (refused.status_code, refused.json()["type"]) == (409, "servers_changed")
    assert _CANARY not in refused.text
    # Ended failed: the secret was never stored, nothing bound, the agent sees why.
    assert await session_row(env, created.session_id) is None
    assert await _no_secret_stored(env, row.credential_id)
    assert not await is_bound(env, AGENT_ID, row.credential_id)
    status = await svc(env).get_status(
        created.session_id, poll_token=created.poll_token, identity=AGENT
    )
    assert (status.status, status.error_code) == ("failed", "servers_changed")
    # A retry with the same (now stale) review cannot resurrect it.
    with pytest.raises(Exception):  # noqa: B017 — any refusal; the session is gone
        await confirm_key(env, created.session_id, _CANARY)


@pytest.mark.parametrize(
    "after",
    [
        [{"url": "https://evil.example"}],
        [*_ORIGINAL, {"url": "https://evil.example"}],
        [{"url": "http://api.vault.example"}],
    ],
    ids=["replaced", "added", "downgraded-to-http"],
)
async def test_unapproved_host_change_during_an_open_manual_session_is_held(
    env: Context, after: list[dict[str, Any]]
) -> None:
    """No credential is bound yet — the open session alone makes the guard hold."""
    await import_spec(env, KEY_SCHEME, servers=_ORIGINAL)
    created = await connect(env)
    held = await import_spec(env, KEY_SCHEME, servers=after)
    assert held["held_for_review"] is True
    review = await svc(env).get_review_data(created.session_id, poll_token=None, identity=OWNER)
    assert review.pinned_hosts == ["https://api.vault.example"]
    result = await confirm_key(env, created.session_id, _CANARY)
    assert isinstance(result, ConnectedConfirmResult)
    # After connect the guard keeps holding: the credential is now bound.
    again = await import_spec(env, KEY_SCHEME, servers=after)
    assert again["held_for_review"] is True


async def test_unpinned_host_variable_appearing_after_create_fails_the_confirm(
    env: Context,
) -> None:
    """A re-import that moves the host into an enum-less variable ends the session."""
    await import_spec(env, KEY_SCHEME, servers=_ORIGINAL)
    created = await connect(env)
    review_digest = await digest(env, created.session_id)
    await import_spec(
        env,
        KEY_SCHEME,
        servers=[
            {"url": "https://{tenant}.vault.example", "variables": {"tenant": {"default": "api"}}}
        ],
        approved=True,
    )
    with pytest.raises(ServersChangedError):
        await svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=key_variant(_CANARY, review_digest),
            identity=OWNER,
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://{tenant}.vault.example",
        "https://api.vault.example:{port}",
        "{scheme}://api.vault.example",
        "https://{user}@api.vault.example",
        "{base}/v1",
    ],
)
async def test_any_host_position_variable_without_enum_is_refused(env: Context, url: str) -> None:
    name = url[url.index("{") + 1 : url.index("}")]
    defaults = {"port": "8443", "scheme": "https", "base": "https://api.vault.example"}
    default = defaults.get(name, "x")
    await import_spec(
        env, KEY_SCHEME, servers=[{"url": url, "variables": {name: {"default": default}}}]
    )
    with pytest.raises(UnpinnedServerHostError):
        await connect(env)


async def test_a_path_only_variable_does_not_need_an_enum(env: Context) -> None:
    await import_spec(
        env,
        KEY_SCHEME,
        servers=[{"url": "https://api.vault.example/{v}", "variables": {"v": {"default": "v1"}}}],
    )
    row = await session_row(env, (await connect(env)).session_id)
    assert row is not None and row.pinned_hosts == ["https://api.vault.example"]


@pytest.mark.parametrize(
    "header",
    [
        "authorization",
        "AUTHORIZATION",
        " Authorization ",
        "Proxy-Authorization",
        "host",
        "cookie",
        "Content-Length",
        "Transfer-Encoding",
        "Connection",
        "Keep-Alive",
        "Upgrade",
        "TE",
        "Trailer",
        "Forwarded",
        "X-Real-IP",
        "Via",
        "x-forwarded-host",
        "X-Forwarded-Anything",
        "jentic-credential-id",
        "Jentic-Revision",
        "X-Jentic-Api-Key",
        "traceparent",
        "tracestate",
    ],
)
async def test_api_key_in_a_reserved_header_is_refused(env: Context, header: str) -> None:
    await import_spec(env, {"key": {"type": "apiKey", "in": "header", "name": header}})
    with pytest.raises(ReservedAuthFieldError):
        await connect(env)


async def test_reserved_header_refusal_over_http(env: Context) -> None:
    await import_spec(env, {"key": {"type": "apiKey", "in": "header", "name": "Cookie"}})
    async with client(env, AGENT) as agent:
        refused = await agent.post("/integrations:connect", json=API_BODY)
    assert refused.status_code in (409, 422), refused.text
    assert refused.json()["type"] == "reserved_auth_field"
