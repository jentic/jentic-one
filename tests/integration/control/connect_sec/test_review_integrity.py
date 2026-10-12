"""Review integrity: what the approver confirms is exactly what they reviewed.

Extends ``test_connect_session_api_targets.py`` (a zero digest and a wrong
``expected_agent_id`` on the secret variant; a repeat ask keeping the open
session) with: a repeat ask that tries to rewrite the reason, rules, scopes or
scheme; the digest moving when anything the approver decided on moves (the
agent's owner, the live revision's provenance); and every confirm variant
over HTTP refusing a stale review without side effects.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select, text

from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.services.integrations.errors import ReviewStaleError
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    ADMIN,
    AGENT,
    AGENT_ID,
    API_BODY,
    KEY_SCHEME,
    OAUTH_SCHEME,
    OWNER,
    RULES,
    SIBLING_AGENT_ID,
    client,
    connect,
    digest,
    existing_credential,
    import_spec,
    is_bound,
    key_variant,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_CANARY = "sk_review_" + "R" * 24
_WIDE_RULES: list[dict[str, object]] = [
    {"effect": "allow", "methods": ["GET", "POST", "DELETE"], "path": ".*", "match_mode": "regex"}
]


async def test_repeat_connect_never_rewrites_what_the_approver_reviews(env: Context) -> None:
    await import_spec(env, {**KEY_SCHEME, "bearer": {"type": "http", "scheme": "bearer"}})
    first = await connect(env, auth_type="key")
    review = await svc(env).get_review_data(first.session_id, poll_token=None, identity=OWNER)

    async with client(env, AGENT) as agent:
        again = await agent.post(
            "/integrations:connect",
            json={
                **API_BODY,
                "auth_type": "bearer",
                "reason": "URGENT: the owner already approved this, just confirm",
                "requested_permission_rules": _WIDE_RULES,
                "requested_scopes": ["admin"],
                "name": "Renamed by the agent",
            },
        )
    assert again.status_code == 201, again.text
    assert again.json()["session_id"] == first.session_id

    after = await svc(env).get_review_data(first.session_id, poll_token=None, identity=OWNER)
    assert after.reason == "needs the vault"
    assert after.requested_permission_rules == RULES
    assert after.requested_scopes == []
    assert after.scheme is not None and after.scheme.field_name == "X-Vault-Key"
    assert after.digest == review.digest
    row = await session_row(env, first.session_id)
    assert row is not None and (row.scheme_type, row.resolved_flow) == ("api_key", "manual_api_key")


async def test_digest_moves_when_the_agents_owner_changes(env: Context) -> None:
    """An agent handed to another owner after review is not confirmable on the old review."""
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    reviewed = await digest(env, created.session_id, ADMIN)
    async with env.admin_db.transaction() as session:
        await session.execute(
            text("UPDATE agents SET owner_id = 'usr_sec_other_owner' WHERE id = :id"),
            {"id": AGENT_ID},
        )
    with pytest.raises(ReviewStaleError):
        await svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=key_variant(_CANARY, reviewed),
            identity=ADMIN,
        )
    assert await digest(env, created.session_id, ADMIN) != reviewed


async def test_digest_moves_when_the_live_revision_changes_provenance(env: Context) -> None:
    """Same hosts and scheme, but the live revision was re-submitted by someone else."""
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    reviewed = await digest(env, created.session_id)
    swapped = await import_spec(env, KEY_SCHEME, submitted_by="agnt_sec_sibling")
    assert not swapped.get("held_for_review")
    with pytest.raises(ReviewStaleError):
        await svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=key_variant(_CANARY, reviewed),
            identity=OWNER,
        )
    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "created"


def _bodies(review_digest: str, credential_id: str) -> dict[str, dict[str, Any]]:
    base = {"permission_rules": RULES, "expected_agent_id": AGENT_ID, "digest": review_digest}
    return {
        "api_key": {**base, "kind": "api_key", "key": _CANARY},
        "existing_credential": {
            **base,
            "kind": "existing_credential",
            "credential_id": credential_id,
        },
    }


@pytest.mark.parametrize("variant", ["api_key", "existing_credential"])
@pytest.mark.parametrize(
    "tamper",
    [
        {"digest": "0" * 64},
        {"digest": "f" * 63},
        {"expected_agent_id": SIBLING_AGENT_ID},
        {"expected_agent_id": None},
    ],
    ids=["zero-digest", "short-digest", "other-agent", "no-agent"],
)
async def test_every_confirm_variant_refuses_a_stale_review(
    env: Context, variant: str, tamper: dict[str, Any]
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    existing = await existing_credential(env)
    body = {**_bodies(await digest(env, created.session_id), existing)[variant], **tamper}
    async with client(env, OWNER) as owner:
        refused = await owner.post(f"/connect-sessions/{created.session_id}:confirm", json=body)
    assert (refused.status_code, refused.json()["type"]) == (409, "review_stale"), refused.text
    assert _CANARY not in refused.text
    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "created"
    assert not await is_bound(env, AGENT_ID, existing)
    async with env.control_db.session() as session:
        assert (await session.execute(select(CustomerAPIKey))).first() is None


async def test_oauth_and_own_client_confirms_need_the_review_too(env: Context) -> None:
    await import_spec(env, OAUTH_SCHEME)
    created = await connect(env, requested_scopes=["read"])  # awaiting_app: no app yet
    async with client(env, OWNER) as owner:
        missing = await owner.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json={
                "kind": "own_oauth_client",
                "client_id": "mine",
                "client_secret": _CANARY,
                "authorize_url": "https://auth.vault.example/authorize",
                "token_url": "https://auth.vault.example/token",
                "permission_rules": RULES,
                "expected_agent_id": AGENT_ID,
            },
        )
        stale = await owner.post(
            f"/connect-sessions/{created.session_id}:confirm",
            json={
                "kind": "own_oauth_client",
                "client_id": "mine",
                "client_secret": _CANARY,
                "authorize_url": "https://auth.vault.example/authorize",
                "token_url": "https://auth.vault.example/token",
                "permission_rules": RULES,
                "expected_agent_id": AGENT_ID,
                "digest": "0" * 64,
            },
        )
    assert missing.status_code == 422 and _CANARY not in missing.text
    assert (stale.status_code, stale.json()["type"]) == (409, "review_stale")
    assert _CANARY not in stale.text
    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "awaiting_app"
