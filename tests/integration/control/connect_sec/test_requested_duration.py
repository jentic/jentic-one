"""A connect request can ask how long its access should last; the approver decides.

The ask is stored on the session and shown on the review page. The binding's
end date is the one the approver sends on confirm, worked out when they
confirm; a confirm without one binds with no expiry.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from jentic_one.control.services.integrations.connect_session_service import SecretConfirm
from jentic_one.control.services.integrations.errors import BindingExpiredError
from jentic_one.control.services.integrations.flow_handlers.manual import ApiKeySecret
from jentic_one.shared.auth.expiry import as_utc
from jentic_one.shared.auth.grant_limits import GrantPolicyLimitError
from jentic_one.shared.context import Context
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    API_BODY,
    KEY_SCHEME,
    OWNER,
    RULES,
    bind,
    checks,
    client,
    connect,
    digest,
    existing_credential,
    import_spec,
    revision,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_SECRET = "sk_duration_" + "D" * 20  # pragma: allowlist secret
_WEEK = 7 * 86_400


async def _binding_expiry(ctx: Context, credential_id: str) -> tuple[bool, datetime | None]:
    async with ctx.admin_db.session() as session:
        row = (
            await session.execute(
                text(
                    "SELECT expires_at FROM agent_credential_bindings"
                    " WHERE agent_id = :a AND credential_id = :c"
                ),
                {"a": AGENT_ID, "c": credential_id},
            )
        ).first()
    if row is None:
        return False, None
    return True, as_utc(row[0]) if row[0] is not None else None


async def _credential_of(ctx: Context, session_id: str) -> str:
    row = await session_row(ctx, session_id)
    assert row is not None
    return row.credential_id


async def _confirm(ctx: Context, session_id: str, expires_at: datetime | None) -> None:
    base = checks(await digest(ctx, session_id), revision_id=await revision(ctx, session_id))
    await svc(ctx).confirm_variant(
        session_id,
        poll_token=None,
        variant=SecretConfirm(
            kind="api_key",
            secret=ApiKeySecret(key=SecretStr(_SECRET)),
            checks=replace(base, expires_at=expires_at),
        ),
        identity=OWNER,
    )


async def test_the_requested_duration_is_shown_on_review(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env, requested_expires_in=_WEEK)

    review = await svc(env).get_review_data(created.session_id, poll_token=None, identity=OWNER)

    assert review.requested_expires_in == _WEEK


async def test_a_request_without_a_duration_asks_for_nothing(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)

    review = await svc(env).get_review_data(created.session_id, poll_token=None, identity=OWNER)

    assert review.requested_expires_in is None


async def test_confirm_binds_with_the_approvers_end_date(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env, requested_expires_in=_WEEK)
    # The approver shortens the week the agent asked for to three days.
    end = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)

    await _confirm(env, created.session_id, end)

    bound, expires_at = await _binding_expiry(env, await _credential_of(env, created.session_id))
    assert bound
    assert expires_at == end


async def test_confirm_without_an_end_date_binds_with_no_expiry(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env, requested_expires_in=_WEEK)

    await _confirm(env, created.session_id, None)

    credential_id = await _credential_of(env, created.session_id)
    assert await _binding_expiry(env, credential_id) == (True, None)


async def test_http_connect_stores_the_duration_and_refuses_a_bad_one(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    async with client(env, AGENT) as agent:
        refused = await agent.post(
            "/integrations:connect", json={**API_BODY, "requested_expires_in": "PT0S"}
        )
        assert refused.status_code == 422, refused.text
        created = await agent.post(
            "/integrations:connect", json={**API_BODY, "requested_expires_in": "P1W"}
        )
        assert created.status_code == 201, created.text
    async with client(env, OWNER) as owner:
        review = await owner.get(f"/connect-sessions/{created.json()['session_id']}")
    assert review.status_code == 200, review.text
    assert review.json()["requested_expires_in"] == "P7D"


async def test_http_confirm_refuses_an_end_date_in_the_past(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    session_id = created.session_id
    body = {
        "kind": "api_key",
        "key": _SECRET,
        "permission_rules": [],
        "digest": await digest(env, session_id),
        "revision_id": await revision(env, session_id),
        "expected_agent_id": AGENT_ID,
        "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }
    async with client(env, OWNER) as owner:
        response = await owner.post(f"/connect-sessions/{session_id}:confirm", json=body)
    assert response.status_code == 422, response.text
    assert "expires_at must be in the future" in response.text


async def test_http_confirm_applies_the_org_limits(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an org maximum, "No expiry" is refused; a body with no policy gets the default."""
    monkeypatch.setattr(env.config.grant_expiry, "default_expires_in", 86_400)
    monkeypatch.setattr(env.config.grant_expiry, "max_expires_in", 7 * 86_400)
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    session_id = created.session_id
    body = {
        "kind": "api_key",
        "key": _SECRET,
        "permission_rules": RULES,
        "digest": await digest(env, session_id),
        "revision_id": await revision(env, session_id),
        "expected_agent_id": AGENT_ID,
    }
    async with client(env, OWNER) as owner:
        refused = await owner.post(
            f"/connect-sessions/{session_id}:confirm", json={**body, "expires_at": None}
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["type"].endswith("grant_policy_limit_exceeded")
        confirmed = await owner.post(f"/connect-sessions/{session_id}:confirm", json=body)
    assert confirmed.status_code == 200, confirmed.text

    credential_id = await _credential_of(env, session_id)
    async with env.admin_db.session() as session:
        expires_at = (
            await session.execute(
                text(
                    "SELECT expires_at FROM agent_credential_bindings"
                    " WHERE agent_id = :a AND credential_id = :c"
                ),
                {"a": AGENT_ID, "c": credential_id},
            )
        ).scalar_one()
    left = as_utc(expires_at) - datetime.now(UTC)
    assert timedelta(hours=23) < left <= timedelta(days=1)


async def test_the_idle_limit_is_requested_and_set_on_confirm(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env, requested_max_idle_period=14 * 86_400)
    review = await svc(env).get_review_data(created.session_id, poll_token=None, identity=OWNER)
    assert review.requested_max_idle_period == 14 * 86_400

    base = checks(
        await digest(env, created.session_id), revision_id=await revision(env, created.session_id)
    )
    await svc(env).confirm_variant(
        created.session_id,
        poll_token=None,
        variant=SecretConfirm(
            kind="api_key",
            secret=ApiKeySecret(key=SecretStr(_SECRET)),
            checks=replace(base, max_idle_period=7 * 86_400, idle_warning=True, max_quota=1),
        ),
        identity=OWNER,
    )

    credential_id = await _credential_of(env, created.session_id)
    async with env.admin_db.session() as session:
        limit, renewed, warning, count, left = (
            await session.execute(
                text(
                    "SELECT max_idle_period, renewed_at, idle_warning, max_quota, quota_remaining"
                    " FROM agent_credential_bindings WHERE agent_id = :a AND credential_id = :c"
                ),
                {"a": AGENT_ID, "c": credential_id},
            )
        ).one()
    assert limit == 7 * 86_400
    assert renewed is not None
    assert bool(warning) is True
    assert (count, left) == (1, 1)


async def test_http_refuses_an_idle_limit_under_a_day(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    async with client(env, AGENT) as agent:
        refused = await agent.post(
            "/integrations:connect", json={**API_BODY, "requested_max_idle_period": "PT1H"}
        )
    assert refused.status_code == 422, refused.text


async def test_a_binding_is_never_created_already_ended(env: Context) -> None:
    """Consent can land after the end date the approver chose: nothing is bound."""
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    gone = datetime.now(UTC) - timedelta(minutes=1)

    with pytest.raises(BindingExpiredError):
        await _confirm(env, created.session_id, gone)

    credential_id = await _credential_of(env, created.session_id)
    assert await _binding_expiry(env, credential_id) == (False, None)


async def test_the_service_refuses_a_grant_past_the_org_maximum_whoever_confirms(
    env: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enforced where the agent is bound, so a caller other than the HTTP route can't bypass it."""
    monkeypatch.setattr(env.config.grant_expiry, "max_expires_in", 7 * 86_400)
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)  # the session carries its own agent

    with pytest.raises(GrantPolicyLimitError):
        await _confirm(env, created.session_id, None)  # "No expiry" under a maximum

    credential_id = await _credential_of(env, created.session_id)
    assert await _binding_expiry(env, credential_id) == (False, None)
    # Within the maximum, the same confirm binds.
    await _confirm(env, created.session_id, datetime.now(UTC) + timedelta(days=3))
    assert (await _binding_expiry(env, credential_id))[0] is True


async def test_the_review_reports_the_agents_own_binding_to_a_credential_it_already_holds(
    env: Context,
) -> None:
    """Binding it again creates nothing, so the review says what limits it keeps."""
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    held = await existing_credential(env, name="Held")
    fresh = await existing_credential(env, name="Fresh")
    await bind(env, AGENT_ID, held)
    end = (datetime.now(UTC) + timedelta(days=9)).replace(microsecond=0)
    async with env.admin_db.transaction() as session:
        await session.execute(
            text(
                "UPDATE agent_credential_bindings SET expires_at = :end, max_idle_period = :idle,"
                " max_quota = 3, quota_remaining = 2 WHERE agent_id = :a AND credential_id = :c"
            ),
            {"end": end, "idle": 14 * 86_400, "a": AGENT_ID, "c": held},
        )

    review = await svc(env).get_review_data(created.session_id, poll_token=None, identity=OWNER)

    by_id = {view.credential_id: view for view in review.existing_credentials}
    own = by_id[held].agent_binding
    assert own is not None
    assert as_utc(own.expires_at) == end  # type: ignore[arg-type]
    assert (own.max_idle_period, own.max_quota, own.quota_remaining) == (14 * 86_400, 3, 2)
    assert (own.suspended, own.ended) == (False, False)
    assert by_id[fresh].agent_binding is None


async def test_http_review_carries_the_agents_own_binding_as_iso_8601(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    held = await existing_credential(env, name="Held")
    await bind(env, AGENT_ID, held)
    async with env.admin_db.transaction() as session:
        await session.execute(
            text(
                "UPDATE agent_credential_bindings SET max_idle_period = :idle, max_quota = 1,"
                " quota_remaining = 1 WHERE agent_id = :a AND credential_id = :c"
            ),
            {"idle": 14 * 86_400, "a": AGENT_ID, "c": held},
        )

    async with client(env, OWNER) as owner:
        response = await owner.get(f"/connect-sessions/{created.session_id}")

    assert response.status_code == 200, response.text
    (entry,) = [c for c in response.json()["existing_credentials"] if c["credential_id"] == held]
    assert entry["agent_binding"] == {
        "expires_at": None,
        "max_idle_period": "P14D",
        "max_quota": 1,
        "quota_remaining": 1,
        "suspended": False,
        "expired": False,
    }
