"""Scheme from the spec only: neither the agent nor the approver picks where a key goes.

Extends ``test_connect_session_api_targets.py`` (``auth_type`` selection and the
service-level refusals) with the request-shape side: no request field can
carry a scheme, location, header name or host, and every refusal has its
documented HTTP status.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    API_BODY,
    KEY_SCHEME,
    OAUTH_SCHEME,
    OWNER,
    RULES,
    client,
    connect,
    digest,
    import_spec,
    session_row,
)

pytestmark = pytest.mark.integration

_CANARY = "sk_scheme_" + "S" * 24

_SMUGGLED: list[dict[str, Any]] = [
    {"scheme": "apiKey"},
    {"scheme_type": "api_key"},
    {"location": "query"},
    {"in": "query"},
    {"field_name": "X-Evil"},
    {"header": "X-Evil"},
    {"servers": [{"url": "https://evil.example"}]},
    {"hosts": ["https://evil.example"]},
    {"pinned_hosts": ["https://evil.example"]},
    {"base_url": "https://evil.example"},
    {"authorize_url": "https://evil.example/authorize"},
    {"token_url": "https://evil.example/token"},
    {"security_schemes": KEY_SCHEME},
]


@pytest.mark.parametrize("extra", _SMUGGLED, ids=lambda e: next(iter(e)))
async def test_connect_refuses_any_field_that_would_steer_the_scheme_or_hosts(
    env: Context, extra: dict[str, Any]
) -> None:
    await import_spec(env, KEY_SCHEME)
    async with client(env, AGENT) as agent:
        top = await agent.post("/integrations:connect", json={**API_BODY, **extra})
        nested = await agent.post(
            "/integrations:connect", json={"api": {**API_BODY["api"], **extra}}
        )
    assert top.status_code == 422, top.text
    assert nested.status_code == 422, nested.text
    async with env.control_db.session() as session:
        from_db = await session.execute(select(CustomerAPIKey))
    assert from_db.first() is None


@pytest.mark.parametrize("extra", _SMUGGLED, ids=lambda e: next(iter(e)))
async def test_secret_confirm_refuses_any_field_that_would_steer_the_scheme_or_hosts(
    env: Context, extra: dict[str, Any]
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    body = {
        "kind": "api_key",
        "key": _CANARY,
        "permission_rules": RULES,
        "expected_agent_id": AGENT_ID,
        "digest": await digest(env, created.session_id),
        **extra,
    }
    async with client(env, OWNER) as owner:
        refused = await owner.post(f"/connect-sessions/{created.session_id}:confirm", json=body)
    assert refused.status_code == 422, refused.text
    assert _CANARY not in refused.text
    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "created"
    assert (row.scheme_location, row.scheme_field_name) == ("header", "X-Vault-Key")


async def test_undeclared_auth_type_is_422_and_lists_only_usable_schemes(env: Context) -> None:
    await import_spec(
        env,
        {
            **KEY_SCHEME,
            "oidc": {"type": "openIdConnect", "openIdConnectUrl": "https://id.example/.wk"},
            "digest": {"type": "http", "scheme": "digest"},
        },
    )
    async with client(env, AGENT) as agent:
        for auth_type in ("oidc", "digest", "bearer", "oauth2", "X-Vault-Key", "apiKey"):
            refused = await agent.post(
                "/integrations:connect", json={**API_BODY, "auth_type": auth_type}
            )
            assert (refused.status_code, refused.json()["type"]) == (
                422,
                "auth_type_not_declared",
            ), auth_type
            assert refused.json()["options"] == ["key"]


async def test_ambiguous_ask_is_400_with_only_usable_options(env: Context) -> None:
    await import_spec(
        env,
        {
            **KEY_SCHEME,
            "bearer": {"type": "http", "scheme": "bearer"},
            "oidc": {"type": "openIdConnect", "openIdConnectUrl": "https://id.example/.wk"},
        },
    )
    async with client(env, AGENT) as agent:
        several = await agent.post("/integrations:connect", json=API_BODY)
    assert (several.status_code, several.json()["type"]) == (400, "auth_type_required")
    assert several.json()["options"] == ["bearer", "key"]


async def test_several_oauth_schemes_never_mix_with_a_static_one(env: Context) -> None:
    second_oauth = {"oauth_b": OAUTH_SCHEME["oauth"]}
    await import_spec(env, {**OAUTH_SCHEME, **second_oauth, **KEY_SCHEME})
    async with client(env, AGENT) as agent:
        several = await agent.post("/integrations:connect", json=API_BODY)
    assert (several.status_code, several.json()["type"]) == (400, "auth_type_required")


@pytest.mark.parametrize(
    "schemes",
    [
        {},
        {"oidc": {"type": "openIdConnect", "openIdConnectUrl": "https://id.example/.wk"}},
        {"digest": {"type": "http", "scheme": "digest"}},
        {"mtls": {"type": "mutualTLS"}},
        {"nameless": {"type": "apiKey", "in": "header"}},
    ],
    ids=["none", "oidc", "digest", "mtls", "nameless-key"],
)
async def test_no_usable_scheme_is_409(env: Context, schemes: dict[str, Any]) -> None:
    await import_spec(env, schemes)
    async with client(env, AGENT) as agent:
        refused = await agent.post("/integrations:connect", json=API_BODY)
        named = await agent.post("/integrations:connect", json={**API_BODY, "auth_type": "api_key"})
    assert (refused.status_code, refused.json()["type"]) == (409, "no_declared_scheme")
    assert (named.status_code, named.json()["type"]) == (409, "no_declared_scheme")


async def test_the_stored_injection_point_is_the_spec_one(env: Context) -> None:
    """Whatever the agent proposes, the session and the stored key use the spec's slot."""
    await import_spec(env, {"key": {"type": "apiKey", "in": "query", "name": "api_key"}})
    async with client(env, AGENT) as agent:
        created = await agent.post(
            "/integrations:connect", json={**API_BODY, "auth_type": "api_key"}
        )
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    async with client(env, OWNER) as owner:
        review = (await owner.get(f"/connect-sessions/{session_id}")).json()
        assert review["scheme"] == {"type": "api_key", "location": "query", "field_name": "api_key"}
        ok = await owner.post(
            f"/connect-sessions/{session_id}:confirm",
            json={
                "kind": "api_key",
                "key": _CANARY,
                "permission_rules": RULES,
                "expected_agent_id": AGENT_ID,
                "digest": review["digest"],
            },
        )
    assert ok.status_code == 200, ok.text
    async with env.control_db.session() as session:
        key_row = (await session.execute(select(CustomerAPIKey))).scalar_one()
    assert (key_row.location, key_row.field_name) == ("query", "api_key")
