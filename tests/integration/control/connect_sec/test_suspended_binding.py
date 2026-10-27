"""A suspended binding stops a new connect for the same agent and API.

Suspending an agent's binding is its operator's reversible cut-off. A new
connect session for an API that the suspended credential covers would hand
the agent that access again, so it is refused with 409
``connect_session_binding_suspended`` — at ``:connect``, and again at confirm
(an agent picked at confirm, or a suspension made after the session opened).
An active binding or no binding at all leaves the connect alone. The broker's
denial for the suspended case asks the operator and never suggests a connect.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text

from jentic_one.broker.core.errors import ActionDeniedError, CredentialBindingSuspendedError
from jentic_one.broker.repos.credential_binding_resolver import CredentialBindingResolver
from jentic_one.broker.services.execution.authorization import derive_credential_bindings
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.services.integrations.connect_session_service import (
    ConfirmChecks,
    SecretConfirm,
)
from jentic_one.control.services.integrations.errors import (
    AgentNotFoundError,
    BindingSuspendedError,
)
from jentic_one.control.services.integrations.flow_handlers.manual import ApiKeySecret
from jentic_one.shared.context import Context
from jentic_one.shared.schemas import APIReference
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    API_BODY,
    KEY_SCHEME,
    NAME,
    OTHER_OWNER,
    OWNER,
    RULES,
    TARGET,
    VENDOR,
    VERSION,
    bind,
    client,
    connect,
    digest,
    existing_credential,
    import_spec,
    is_bound,
    session_row,
    svc,
)

pytestmark = pytest.mark.integration

_SECRET = "sk_suspended_" + "B" * 20  # pragma: allowlist secret


async def _suspend(ctx: Context, agent_id: str, credential_id: str) -> None:
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "UPDATE agent_credential_bindings SET suspended = true"
                " WHERE agent_id = :a AND credential_id = :c"
            ),
            {"a": agent_id, "c": credential_id},
        )


async def _suspended_binding(ctx: Context, *, name: str = NAME) -> str:
    """A credential of the owner covering the API, bound to the agent and then suspended."""
    credential_id = await existing_credential(ctx)
    if name != NAME:
        async with ctx.control_db.transaction() as session:
            credential = await session.get(Credential, credential_id)
            assert credential is not None
            credential.api_name = name
    await bind(ctx, AGENT_ID, credential_id)
    await _suspend(ctx, AGENT_ID, credential_id)
    return credential_id


async def _counts(ctx: Context) -> tuple[int, int]:
    async with ctx.control_db.session() as session:
        sessions = await session.scalar(select(func.count()).select_from(ConnectSession))
        credentials = await session.scalar(select(func.count()).select_from(Credential))
    return int(sessions or 0), int(credentials or 0)


# ---- :connect ---------------------------------------------------------------


async def test_agent_connect_is_refused_while_its_binding_is_suspended(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _suspended_binding(env)
    before = await _counts(env)

    with pytest.raises(BindingSuspendedError):
        await connect(env)

    assert await _counts(env) == before


async def test_suspended_binding_connect_is_409_over_http(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _suspended_binding(env)

    async with client(env, AGENT) as agent:
        response = await agent.post(
            "/integrations:connect", json={**API_BODY, "reason": "needs the vault"}
        )

    assert response.status_code == 409, response.text
    assert response.json()["type"] == "connect_session_binding_suspended"


async def test_owner_naming_the_agent_is_refused_too(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _suspended_binding(env)

    with pytest.raises(BindingSuspendedError):
        await svc(env).create_session(
            vendor_key="", agent_id=AGENT_ID, identity=OWNER, api_target=TARGET
        )


async def test_another_owner_naming_the_agent_still_gets_not_found(env: Context) -> None:
    # Ownership is settled before the suspension is looked at.
    await import_spec(env, KEY_SCHEME)
    await _suspended_binding(env)

    with pytest.raises(AgentNotFoundError):
        await svc(env).create_session(
            vendor_key="", agent_id=AGENT_ID, identity=OTHER_OWNER, api_target=TARGET
        )


async def test_active_binding_does_not_block_a_connect(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    credential_id = await existing_credential(env)
    await bind(env, AGENT_ID, credential_id)

    created = await connect(env)

    assert await session_row(env, created.session_id) is not None


async def test_no_binding_does_not_block_a_connect(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await existing_credential(env)

    created = await connect(env)

    assert await session_row(env, created.session_id) is not None


async def test_suspended_binding_for_another_api_does_not_block_a_connect(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    await _suspended_binding(env, name="some-other-api")

    created = await connect(env)

    assert await session_row(env, created.session_id) is not None


# ---- confirm ----------------------------------------------------------------


async def _owner_session_without_agent(ctx: Context) -> tuple[str, str]:
    """A session the owner opened naming no agent: (session id, poll token)."""
    created = await svc(ctx).create_session(
        vendor_key="",
        agent_id=None,
        identity=OWNER,
        api_target=TARGET,
        requested_permission_rules=RULES,
    )
    return created.session_id, created.poll_token


async def _late_bind_confirm(ctx: Context, session_id: str, poll_token: str) -> object:
    service = svc(ctx)
    review = await service.get_review_data(session_id, poll_token=poll_token, identity=OWNER)
    return await service.confirm_variant(
        session_id,
        poll_token=poll_token,
        variant=SecretConfirm(
            kind="api_key",
            secret=ApiKeySecret(key=SecretStr(_SECRET)),
            checks=ConfirmChecks(
                permission_rules=RULES,
                expected_agent_id=None,
                digest=review.digest,
                agent_id=AGENT_ID,
            ),
        ),
        identity=OWNER,
    )


async def test_late_bound_confirm_is_refused_while_the_binding_is_suspended(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    session_id, poll_token = await _owner_session_without_agent(env)
    await _suspended_binding(env)

    with pytest.raises(BindingSuspendedError):
        await _late_bind_confirm(env, session_id, poll_token)

    row = await session_row(env, session_id)
    assert row is not None and row.state == "created" and row.agent_id is None
    assert not await is_bound(env, AGENT_ID, row.credential_id)


async def test_late_bound_confirm_proceeds_with_an_active_binding(env: Context) -> None:
    await import_spec(env, KEY_SCHEME)
    session_id, poll_token = await _owner_session_without_agent(env)
    credential_id = await existing_credential(env)
    await bind(env, AGENT_ID, credential_id)

    await _late_bind_confirm(env, session_id, poll_token)

    row = await session_row(env, session_id)
    assert row is not None and row.state == "connected"
    assert await is_bound(env, AGENT_ID, row.credential_id)


async def test_confirm_is_refused_when_the_binding_was_suspended_after_the_ask(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    created = await connect(env)
    await _suspended_binding(env)
    review = await digest(env, created.session_id)

    with pytest.raises(BindingSuspendedError):
        await svc(env).confirm_variant(
            created.session_id,
            poll_token=None,
            variant=SecretConfirm(
                kind="api_key",
                secret=ApiKeySecret(key=SecretStr(_SECRET)),
                checks=ConfirmChecks(
                    permission_rules=RULES, expected_agent_id=AGENT_ID, digest=review
                ),
            ),
            identity=OWNER,
        )

    row = await session_row(env, created.session_id)
    assert row is not None and row.state == "created"


# ---- broker directive -------------------------------------------------------

_API = APIReference(vendor=VENDOR, name=NAME, version=VERSION)


async def _broker_denial(ctx: Context) -> Exception:
    with pytest.raises((ActionDeniedError, CredentialBindingSuspendedError)) as raised:
        await derive_credential_bindings(
            deriver=CredentialBindingResolver(ctx.admin_db, ctx.control_db),
            identity=AGENT,
            api=_API,
            instance="/items",
            ctx=ctx,
            method="GET",
            path="/items",
        )
    return raised.value


async def test_broker_denial_for_a_suspended_binding_asks_the_operator_only(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    await _suspended_binding(env)

    denial = await _broker_denial(env)

    assert isinstance(denial, CredentialBindingSuspendedError)
    assert denial.type == "credential_binding_suspended"
    directive = denial.directive
    assert directive is not None
    for key in ("suggested_command", "connect", "provisioning_url", "suggested_rules"):
        assert key not in directive.parameters
    instruction = directive.human_readable_instruction
    assert "operator" in instruction
    assert "jentic connect" not in instruction
    assert "request_connection" not in instruction


async def test_broker_denial_without_any_binding_still_offers_the_connect(env: Context) -> None:
    # The contrast: with nothing suspended, the same deployment offers the connect.
    await import_spec(env, KEY_SCHEME)
    await existing_credential(env)

    denial = await _broker_denial(env)

    assert isinstance(denial, ActionDeniedError)
    assert denial.type == "no_credential_binding"
    assert denial.directive is not None
    assert "jentic connect" in denial.directive.human_readable_instruction


async def test_broker_ignores_a_suspended_binding_once_an_active_one_covers_the_api(
    env: Context,
) -> None:
    await import_spec(env, KEY_SCHEME)
    await _suspended_binding(env)
    active = await existing_credential(env, name="Active")
    await bind(env, AGENT_ID, active)

    derivation = await derive_credential_bindings(
        deriver=CredentialBindingResolver(env.admin_db, env.control_db),
        identity=AGENT,
        api=_API,
        instance="/items",
        ctx=env,
    )

    assert [c.credential_id for c in derivation.credentials] == [active]
    assert derivation.binding_suspended is False
