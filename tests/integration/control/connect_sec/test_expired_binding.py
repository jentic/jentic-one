"""An expired binding stops a new connect, and a credential read, for the same agent and API.

A binding past its ``expires_at`` no longer authorizes calls, and only its
operator can renew it. A new connect session for an API that the expired
credential covers would hand the agent that access again beside it, routing
around the expiry, so it is refused with 409 ``connect_session_binding_expired``
— at ``:connect``, and again at confirm. A suspension on the same binding is
reported first. An expired binding also stops widening the agent's credential
reads, like a suspended one. A binding with a future expiry is still active.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import (
    SelfBindOverExpiredBindingError,
    SelfBindOverSuspendedBindingError,
)
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.effects_repo import EffectsRepository
from jentic_one.control.repos.prerequisite_repo import PrerequisiteRepository
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.integrations.connect_session_service import (
    ConfirmChecks,
    ExistingCredentialConfirm,
    SecretConfirm,
)
from jentic_one.control.services.integrations.errors import (
    BindingExpiredError,
    BindingSuspendedError,
)
from jentic_one.control.services.integrations.flow_handlers.manual import ApiKeySecret
from jentic_one.control.web.routers.credentials import binding_access_response
from jentic_one.shared.auth.expiry import binding_expired
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import DEFAULT_AGENT_PERMISSIONS
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    API_BODY,
    KEY_SCHEME,
    NAME,
    OWNER,
    OWNER_ID,
    RULES,
    TARGET,
    VERSION,
    bind,
    checks,
    client,
    connect,
    digest,
    existing_credential,
    import_spec,
    is_bound,
    revision,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_SECRET = "sk_expired_" + "C" * 20  # pragma: allowlist secret
_PAST = datetime.now(UTC) - timedelta(days=1)
_FUTURE = datetime.now(UTC) + timedelta(days=30)


async def _set_binding(
    ctx: Context,
    agent_id: str,
    credential_id: str,
    *,
    expires_at: datetime | None,
    suspended: bool = False,
) -> None:
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "UPDATE agent_credential_bindings SET expires_at = :e, suspended = :s"
                " WHERE agent_id = :a AND credential_id = :c"
            ),
            {"e": expires_at, "s": suspended, "a": agent_id, "c": credential_id},
        )


async def _bound(
    ctx: Context,
    *,
    expires_at: datetime | None = _PAST,
    suspended: bool = False,
    name: str = NAME,
) -> str:
    """A credential of the owner covering the API, bound to the agent with the given state."""
    credential_id = await existing_credential(ctx)
    if name != NAME:
        async with ctx.control_db.transaction() as session:
            credential = await session.get(Credential, credential_id)
            assert credential is not None
            credential.api_name = name
    await bind(ctx, AGENT_ID, credential_id)
    await _set_binding(ctx, AGENT_ID, credential_id, expires_at=expires_at, suspended=suspended)
    return credential_id


async def _counts(ctx: Context) -> tuple[int, int]:
    async with ctx.control_db.session() as session:
        sessions = await session.scalar(select(func.count()).select_from(ConnectSession))
        credentials = await session.scalar(select(func.count()).select_from(Credential))
    return int(sessions or 0), int(credentials or 0)


# ---- :connect ---------------------------------------------------------------


async def test_agent_connect_is_refused_while_its_binding_is_expired(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _bound(env)
    before = await _counts(env)

    with pytest.raises(BindingExpiredError):
        await connect(env)

    assert await _counts(env) == before


async def test_agent_connect_is_refused_while_its_only_limit_is_a_used_up_count(
    env: Context,
) -> None:
    """A single-use grant (the default under ``single_use_by_default``) has no time limit."""
    await import_spec(env, KEY_SCHEME)
    credential_id = await _bound(env, expires_at=None)
    async with env.admin_db.transaction() as session:
        await session.execute(
            text(
                "UPDATE agent_credential_bindings SET max_quota = 1, quota_remaining = 0"
                " WHERE agent_id = :a AND credential_id = :c"
            ),
            {"a": AGENT_ID, "c": credential_id},
        )
    before = await _counts(env)

    with pytest.raises(BindingExpiredError):
        await connect(env)

    assert await _counts(env) == before


async def test_expired_binding_connect_is_409_over_http(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _bound(env)

    async with client(env, AGENT) as agent:
        response = await agent.post(
            "/integrations:connect", json={**API_BODY, "reason": "needs the vault"}
        )

    assert response.status_code == 409, response.text
    assert response.json()["type"] == "connect_session_binding_expired"


async def test_owner_naming_the_agent_is_refused_too(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _bound(env)

    with pytest.raises(BindingExpiredError):
        await svc(env).create_session(
            vendor_key="", agent_id=AGENT_ID, identity=OWNER, api_target=TARGET
        )


async def test_suspended_and_expired_is_reported_as_suspended(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _bound(env, suspended=True)

    with pytest.raises(BindingSuspendedError):
        await connect(env)


async def test_suspension_on_one_binding_is_reported_before_expiry_on_another(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    await _bound(env)
    suspended = await existing_credential(env, name="Suspended")
    await bind(env, AGENT_ID, suspended)
    await _set_binding(env, AGENT_ID, suspended, expires_at=None, suspended=True)

    with pytest.raises(BindingSuspendedError):
        await connect(env)


async def test_future_expiry_does_not_block_a_connect(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _bound(env, expires_at=_FUTURE)

    created = await connect(env)

    assert await session_row(env, created.session_id) is not None


async def test_expired_binding_for_another_api_does_not_block_a_connect(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _bound(env, name="some-other-api")

    created = await connect(env)

    assert await session_row(env, created.session_id) is not None


# ---- confirm ----------------------------------------------------------------


async def test_late_bound_confirm_is_refused_while_the_binding_is_expired(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await svc(env).create_session(
        vendor_key="",
        agent_id=None,
        identity=OWNER,
        api_target=TARGET,
        requested_permission_rules=RULES,
    )
    await _bound(env)
    service = svc(env)
    review = await service.get_review_data(
        created.session_id, poll_token=created.poll_token, identity=OWNER
    )

    with pytest.raises(BindingExpiredError):
        await service.confirm_variant(
            created.session_id,
            poll_token=created.poll_token,
            variant=SecretConfirm(
                kind="api_key",
                secret=ApiKeySecret(key=SecretStr(_SECRET)),
                checks=ConfirmChecks(
                    permission_rules=RULES,
                    expected_agent_id=None,
                    digest=review.digest,
                    revision_id=review.revision_id,
                    agent_id=AGENT_ID,
                ),
            ),
            identity=OWNER,
        )

    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "created" and row.agent_id is None
    assert not await is_bound(env, AGENT_ID, row.credential_id)


# ---- repositories -----------------------------------------------------------


async def test_expired_credential_ids_list_only_unsuspended_expired_bindings(
    env: Context,
) -> None:
    expired = await _bound(env)
    await _bound(env, suspended=True)
    await _bound(env, expires_at=_FUTURE)
    await _bound(env, expires_at=None)

    async with env.admin_db.session() as session:
        ids = await EffectsRepository.list_expired_credential_ids(session, AGENT_ID)

    assert ids == [expired]


async def test_expiry_is_compared_against_the_given_now(env: Context) -> None:
    credential_id = await _bound(env, expires_at=_FUTURE)

    async with env.admin_db.session() as session:
        before = await EffectsRepository.list_expired_credential_ids(session, AGENT_ID)
        at = await EffectsRepository.list_expired_credential_ids(session, AGENT_ID, now=_FUTURE)
        readable_at = await PrerequisiteRepository.list_credential_ids_for_agent(
            session, agent_id=AGENT_ID, now=_FUTURE
        )

    assert before == []
    assert at == [credential_id]
    assert readable_at == []


# ---- credential reads -------------------------------------------------------


async def test_expired_binding_stops_widening_credential_reads(env: Context) -> None:
    expired = await _bound(env)
    active = await _bound(env, expires_at=_FUTURE)
    service = CredentialService(env)

    async with env.admin_db.session() as session:
        readable = await PrerequisiteRepository.list_credential_ids_for_agent(
            session, agent_id=AGENT_ID
        )
    assert readable == [active]

    assert (await service.get(active, identity=AGENT)).credential_id == active
    with pytest.raises(CredentialNotFoundError):
        await service.get(expired, identity=AGENT)


async def test_renewed_binding_widens_credential_reads_again(env: Context) -> None:
    credential_id = await _bound(env)
    await _set_binding(env, AGENT_ID, credential_id, expires_at=_FUTURE)

    view = await CredentialService(env).get(credential_id, identity=AGENT)

    assert view.credential_id == credential_id


# ---- show_access ------------------------------------------------------------

#: The agent as its token carries it: the owner-delegated read lets it see the
#: owner's credentials whatever its bindings, so an expired binding is still
#: shown rather than hidden, and must say it has expired.
_DELEGATED_AGENT = Identity(
    sub=AGENT_ID,
    permissions=list(DEFAULT_AGENT_PERMISSIONS),
    actor_type=ActorType.AGENT,
    parent_actor_id=OWNER_ID,
)


async def _show_access(env: Context, credential_id: str) -> dict[str, object]:
    """The binding access read as the route and the MCP tool both project it."""
    access = await CredentialService(env).get_binding_access(
        credential_id, AGENT_ID, identity=_DELEGATED_AGENT
    )
    return binding_access_response(access).model_dump(mode="json")


async def test_show_access_reports_an_expired_binding_as_expired(env: Context) -> None:
    credential_id = await _bound(env)

    body = await _show_access(env, credential_id)

    assert body["expired"] is True
    assert body["expired_reason"] == "end_date"
    assert body["expires_at"] is not None
    assert body["suspended"] is False


async def test_show_access_reports_a_live_binding_as_not_expired(env: Context) -> None:
    credential_id = await _bound(env, expires_at=_FUTURE)

    body = await _show_access(env, credential_id)

    assert body["expired"] is False
    assert body["expires_at"] is not None


# ---- an agent binding itself -----------------------------------------------

#: The agent with ``agents:write`` (not a default agent permission): it may
#: bind its owner's credential to itself, but not beside its own cut-off.
_SELF_BINDING_AGENT = Identity(
    sub=AGENT_ID,
    permissions=[*DEFAULT_AGENT_PERMISSIONS, "agents:write"],
    actor_type=ActorType.AGENT,
    parent_actor_id=OWNER_ID,
)


async def _self_bind(env: Context, credential_id: str, identity: Identity) -> None:
    await AgentService(env).bind_credential(
        AGENT_ID, credential_id=credential_id, identity=identity
    )


async def test_an_agent_cannot_bind_itself_beside_its_expired_binding(env: Context) -> None:
    await _bound(env)
    fresh = await existing_credential(env, name="Fresh")

    with pytest.raises(SelfBindOverExpiredBindingError):
        await _self_bind(env, fresh, _SELF_BINDING_AGENT)

    assert not await is_bound(env, AGENT_ID, fresh)


async def test_an_agent_self_bind_reports_suspension_before_expiry(env: Context) -> None:
    await _bound(env)
    suspended = await existing_credential(env, name="Suspended")
    await bind(env, AGENT_ID, suspended)
    await _set_binding(env, AGENT_ID, suspended, expires_at=None, suspended=True)
    fresh = await existing_credential(env, name="Fresh")

    with pytest.raises(SelfBindOverSuspendedBindingError):
        await _self_bind(env, fresh, _SELF_BINDING_AGENT)


async def test_an_agent_may_bind_itself_for_another_api(env: Context) -> None:
    await _bound(env, name="some-other-api")
    fresh = await existing_credential(env, name="Fresh")

    await _self_bind(env, fresh, _SELF_BINDING_AGENT)

    assert await is_bound(env, AGENT_ID, fresh)


async def test_the_owner_may_still_bind_the_agent_beside_an_expired_binding(
    env: Context,
) -> None:
    """The cut-off is the operator's to lift, so the operator's own bind is allowed."""
    await _bound(env)
    fresh = await existing_credential(env, name="Fresh")

    await _self_bind(env, fresh, OWNER)

    assert await is_bound(env, AGENT_ID, fresh)


# ---- confirming with an existing credential --------------------------------


async def _set_scope(env: Context, credential_id: str, *, version: str | None) -> None:
    async with env.control_db.transaction() as session:
        credential = await session.get(Credential, credential_id)
        assert credential is not None
        credential.api_version = version


async def test_a_broader_existing_credential_cannot_route_around_an_expired_sibling(
    env: Context,
) -> None:
    """The session asks for one version; the chosen credential serves every version.

    The agent's binding for another version of the API has expired, so the
    session opens (that binding doesn't cover the version asked for). The
    approver then picks an existing credential with no version pinned: it
    serves the expired version too, so binding it would hand that access back.
    """
    await import_spec(env, KEY_SCHEME)
    sibling = await _bound(env)
    await _set_scope(env, sibling, version="0.9.0")
    created = await connect(env)
    wide = await existing_credential(env, name="Every version")
    await _set_scope(env, wide, version=None)

    with pytest.raises(BindingExpiredError):
        await svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=ExistingCredentialConfirm(
                credential_id=wide,
                checks=checks(
                    await digest(env, created.session_id),
                    revision_id=await revision(env, created.session_id),
                ),
            ),
            identity=OWNER,
        )

    assert not await is_bound(env, AGENT_ID, wide)


async def test_an_existing_credential_for_another_version_still_binds(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    sibling = await _bound(env)
    await _set_scope(env, sibling, version="0.9.0")
    created = await connect(env)
    exact = await existing_credential(env, name="This version")
    await _set_scope(env, exact, version=VERSION)

    await svc(env).confirm_variant(
        created.session_id,
        poll_token=None,
        variant=ExistingCredentialConfirm(
            credential_id=exact,
            checks=checks(
                await digest(env, created.session_id),
                revision_id=await revision(env, created.session_id),
            ),
        ),
        identity=OWNER,
    )

    assert await is_bound(env, AGENT_ID, exact)


@pytest.mark.parametrize("direct", [False, True])
async def test_deadline_crossed_during_bind_rolls_back_the_insert(
    env: Context, monkeypatch: pytest.MonkeyPatch, direct: bool
) -> None:
    await import_spec(env, KEY_SCHEME)
    await _bound(env, expires_at=_FUTURE)
    fresh = await existing_credential(env, name="New")
    created = await connect(env)
    # The sibling is live during preflight and while the writer obtains its locks.
    # Advance the guard's clock across its deadline between the check and the insert's return.
    instants = iter((_FUTURE - timedelta(seconds=1), _FUTURE))

    def expired(at: datetime | str | None) -> bool:
        return binding_expired(at, now=next(instants))

    monkeypatch.setattr("jentic_one.shared.auth.binding_write.binding_expired", expired)
    if direct:
        with pytest.raises(SelfBindOverExpiredBindingError):
            await _self_bind(env, fresh, _SELF_BINDING_AGENT)
    else:
        with pytest.raises(BindingExpiredError):
            await svc(env).confirm_variant(
                created.session_id,
                poll_token=None,
                identity=OWNER,
                variant=ExistingCredentialConfirm(
                    credential_id=fresh,
                    checks=checks(
                        await digest(env, created.session_id),
                        revision_id=await revision(env, created.session_id),
                    ),
                ),
            )
    assert not await is_bound(env, AGENT_ID, fresh)


async def test_a_bind_waits_for_a_concurrent_suspension_of_its_sibling(env: Context) -> None:
    """The bind locks the agent's bindings, so a suspension in flight is seen, not raced."""
    if env.config.databases.admin.backend == "sqlite":
        pytest.skip("SQLite serialises writers; the race needs PostgreSQL row locking")
    sibling = await _bound(env, expires_at=_FUTURE)
    fresh = await existing_credential(env, name="Fresh")

    async with env.admin_db.transaction() as blocker:
        await blocker.execute(
            text(
                "UPDATE agent_credential_bindings SET suspended = true"
                " WHERE agent_id = :a AND credential_id = :c"
            ),
            {"a": AGENT_ID, "c": sibling},
        )
        writer = asyncio.create_task(_self_bind(env, fresh, _SELF_BINDING_AGENT))
        await asyncio.sleep(0.3)
        assert not writer.done()

    with pytest.raises(SelfBindOverSuspendedBindingError):
        await writer
    assert not await is_bound(env, AGENT_ID, fresh)
