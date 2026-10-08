"""Integration tests for the run-time re-authorization of queued executions.

Seeds a direct agent→credential binding with an allow rule in the real admin
and control DBs, builds the worker's ``QueuedExecutionAuthorizer`` exactly as
the broker lifespan does, and asserts that a change made *after* enqueue —
agent suspended, execute scope revoked, binding suspended or removed,
credential deactivated, rule changed — is honoured when
the job runs, with the same problem type the sync execute route returns.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import structlog
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.broker.core.setup import build_queued_execution_authorizer
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.broker.services.execution import authorization
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.jobs.protocols import QueuedExecutionRequest
from jentic_one.shared.models import StoredCredentialType

pytestmark = pytest.mark.integration

_VENDOR = "acme.com"
_API_NAME = "pets-api"
_API_VERSION = "v1"
_URL = "https://api.acme.com/v1/pets"


@pytest.fixture()
async def clean_tables(integration_context: Context) -> AsyncGenerator[None, None]:
    ctx = integration_context

    async def _truncate() -> None:
        async with ctx.admin_db.session() as session:
            await session.execute(delete(AgentCredentialBinding))
            await session.execute(delete(ActorPermissionGrant))
            await session.execute(delete(Agent))
            await session.commit()
        async with ctx.control_db.session() as session:
            await session.execute(delete(AgentPermissionRule))
            await session.execute(delete(CustomerAPIKey))
            await session.execute(delete(Credential))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_bound_agent(ctx: Context) -> tuple[str, str]:
    """An active agent holding the execute scope, bound to one API-key
    credential with an allow-GET rule."""
    agent = Agent(name="queued-agent", registered_by="usr_owner", status="active")
    credential = Credential(
        type=StoredCredentialType.API_KEY,
        name="acme-key",
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
    )
    async with ctx.control_db.session() as session:
        session.add(credential)
        await session.flush()
        session.add(
            CustomerAPIKey(
                id=generate_ksuid("key"),
                credential_id=credential.id,
                encrypted_key=ctx.encryption.encrypt("SECRET"),  # pragma: allowlist secret
                location="header",
                field_name="X-Api-Key",
            )
        )
        await session.commit()
        credential_id = credential.id
    async with ctx.admin_db.session() as session:
        session.add(agent)
        await session.flush()
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"), agent_id=agent.id, credential_id=credential_id
            )
        )
        session.add(
            ActorPermissionGrant(
                actor_id=agent.id, actor_type="agent", permission=BROKER_EXECUTE_PERMISSION
            )
        )
        await session.commit()
        agent_id = agent.id
    async with ctx.control_db.session() as session:
        session.add(
            AgentPermissionRule(
                agent_id=agent_id,
                credential_id=credential_id,
                effect="allow",
                methods=["GET"],
                path=".*",
                sequence=1,
            )
        )
        await session.commit()
    return agent_id, credential_id


def _request(agent_id: str, credential_id: str, *, method: str = "GET") -> QueuedExecutionRequest:
    return QueuedExecutionRequest(
        actor_id=agent_id,
        actor_type="agent",
        method=method,
        upstream_url=_URL,
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        credential_id=credential_id,
    )


async def test_still_authorized_job_gets_the_current_boundary(
    integration_context: Context, clean_tables: None
) -> None:
    agent_id, credential_id = await _seed_bound_agent(integration_context)

    verdict = await build_queued_execution_authorizer(integration_context).authorize(
        _request(agent_id, credential_id)
    )

    assert verdict.allowed is True
    assert verdict.allowed_credential_ids == (credential_id,)
    assert verdict.credential_id == credential_id


async def test_agent_suspended_after_enqueue_is_denied(
    integration_context: Context, clean_tables: None
) -> None:
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    async with integration_context.admin_db.session() as session:
        await session.execute(update(Agent).where(Agent.id == agent_id).values(status="suspended"))
        await session.commit()

    verdict = await build_queued_execution_authorizer(integration_context).authorize(
        _request(agent_id, credential_id)
    )

    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["type"] == "unauthorized"
    assert verdict.problem["status"] == 401


async def test_execute_scope_revoked_after_enqueue_is_denied(
    integration_context: Context, clean_tables: None
) -> None:
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    async with integration_context.admin_db.session() as session:
        await session.execute(
            delete(ActorPermissionGrant).where(ActorPermissionGrant.actor_id == agent_id)
        )
        await session.commit()

    verdict = await build_queued_execution_authorizer(integration_context).authorize(
        _request(agent_id, credential_id)
    )

    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["type"] == "insufficient_scope"
    assert verdict.problem["status"] == 403
    assert verdict.allowed_credential_ids == ()


async def test_credential_deactivated_after_enqueue_is_denied(
    integration_context: Context, clean_tables: None
) -> None:
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    async with integration_context.control_db.session() as session:
        await session.execute(
            update(Credential).where(Credential.id == credential_id).values(active=False)
        )
        await session.commit()

    verdict = await build_queued_execution_authorizer(integration_context).authorize(
        _request(agent_id, credential_id)
    )

    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["status"] == 403
    assert verdict.allowed_credential_ids == ()


async def test_binding_suspended_after_enqueue_is_denied(
    integration_context: Context, clean_tables: None
) -> None:
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    async with integration_context.admin_db.session() as session:
        await session.execute(
            update(AgentCredentialBinding)
            .where(AgentCredentialBinding.agent_id == agent_id)
            .values(suspended=True)
        )
        await session.commit()

    verdict = await build_queued_execution_authorizer(integration_context).authorize(
        _request(agent_id, credential_id)
    )

    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["status"] == 403
    assert verdict.allowed_credential_ids == ()


async def test_binding_removed_after_enqueue_is_denied(
    integration_context: Context, clean_tables: None
) -> None:
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    async with integration_context.admin_db.session() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == agent_id)
        )
        await session.commit()

    verdict = await build_queued_execution_authorizer(integration_context).authorize(
        _request(agent_id, credential_id)
    )

    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["status"] == 403


async def test_rule_changed_after_enqueue_is_denied(
    integration_context: Context, clean_tables: None
) -> None:
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    authorizer = build_queued_execution_authorizer(integration_context)
    # Warm any cache the authorizer might hold; the re-check must still see the change.
    assert (await authorizer.authorize(_request(agent_id, credential_id))).allowed is True
    async with integration_context.control_db.session() as session:
        await session.execute(
            update(AgentPermissionRule)
            .where(AgentPermissionRule.agent_id == agent_id)
            .values(effect="deny")
        )
        await session.commit()

    verdict = await authorizer.authorize(_request(agent_id, credential_id))

    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["type"] == "action_denied"
    assert verdict.problem["status"] == 403


async def test_actor_status_resolver_reads_user_and_agent_rows(
    integration_context: Context, clean_tables: None
) -> None:
    ctx = integration_context
    agent_id, _ = await _seed_bound_agent(ctx)
    user = User(email="queued@example.com", first_name="Q", last_name="User", active=False)
    async with ctx.admin_db.session() as session:
        session.add(user)
        await session.commit()
        user_id = user.id
    resolver = ActorStatusResolver(ctx.admin_db)
    try:
        assert await resolver.is_active(actor_id=agent_id, actor_type="agent") is True
        assert await resolver.is_active(actor_id=user_id, actor_type="user") is False
        assert await resolver.is_active(actor_id="agt_missing", actor_type="agent") is False
        assert await resolver.is_active(actor_id=agent_id, actor_type="toolkit") is False
    finally:
        async with ctx.admin_db.session() as session:
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()


async def test_actor_status_resolver_refuses_retired_service_account_actors(
    integration_context: Context, clean_tables: None
) -> None:
    """Theme-8 Phase 4: a job queued under a (retired) service account can
    never run — no liveness row exists to vouch for it, and a leftover
    ``service_account`` grant row confers nothing."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        session.add(
            ActorPermissionGrant(
                actor_id="sva_queued",
                actor_type="service_account",
                permission=BROKER_EXECUTE_PERMISSION,
            )
        )
        await session.commit()
    resolver = ActorStatusResolver(ctx.admin_db)
    assert await resolver.is_active(actor_id="sva_queued", actor_type="service_account") is False
    assert not await resolver.holds_permission(
        actor_id="sva_queued", actor_type="service_account", permission=BROKER_EXECUTE_PERMISSION
    )


async def _set_rule(ctx: Context, agent_id: str, *, path: str, match_mode: str) -> None:
    async with ctx.control_db.session() as session:
        await session.execute(
            update(AgentPermissionRule)
            .where(AgentPermissionRule.agent_id == agent_id)
            .values(path=path, match_mode=match_mode)
        )
        await session.commit()


_BASE_PATH_URL = "http://localhost:18765/eu/widgets"


@pytest.mark.parametrize(
    ("rule_path", "relative_path", "allowed"),
    [
        # The spec-relative rule the UI authors allows the base-path call.
        ("/widgets", "/widgets", True),
        # A rule written against the full upstream path does not match.
        ("/eu/widgets", "/widgets", False),
        # A job enqueued without the relative path falls back to the
        # normalized full upstream path — never broader than that.
        ("/eu/widgets", None, True),
        ("/widgets", None, False),
    ],
)
async def test_rules_are_reauthorized_on_the_server_relative_path(
    integration_context: Context,
    clean_tables: None,
    rule_path: str,
    relative_path: str | None,
    allowed: bool,
) -> None:
    """Pins #1424: for an API whose server URL carries a base path
    (``http://{host}:18765/{region}``), binding rules match the spec-relative
    path, the same basis the rule editor, preview and ``permissions:test`` use."""
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    await _set_rule(integration_context, agent_id, path=rule_path, match_mode="prefix")
    authorizer = build_queued_execution_authorizer(integration_context)

    verdict = await authorizer.authorize(
        QueuedExecutionRequest(
            actor_id=agent_id,
            actor_type="agent",
            method="GET",
            upstream_url=_BASE_PATH_URL,
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            relative_path=relative_path,
            credential_id=credential_id,
        )
    )

    assert verdict.allowed is allowed
    if not allowed:
        assert verdict.problem is not None
        assert verdict.problem["type"] == "action_denied"


async def test_encoded_path_is_denied_by_a_deny_rule_on_fallback(
    integration_context: Context, clean_tables: None
) -> None:
    """A percent-encoded spelling of a denied path cannot dodge a ``deny``
    rule: the fallback matches on the normalized path discovery resolved."""
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    async with integration_context.control_db.session() as session:
        session.add(
            AgentPermissionRule(
                agent_id=agent_id,
                credential_id=credential_id,
                effect="deny",
                methods=["GET"],
                path="/v1/admin",
                match_mode="prefix",
                sequence=0,
            )
        )
        await session.commit()
    authorizer = build_queued_execution_authorizer(integration_context)

    verdict = await authorizer.authorize(
        QueuedExecutionRequest(
            actor_id=agent_id,
            actor_type="agent",
            method="GET",
            upstream_url="https://api.acme.com/v1/%61dmin/users",
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            credential_id=credential_id,
        )
    )

    assert verdict.allowed is False


_DIVERGENCE_EVENT = "rule_written_with_server_base_path"


async def _set_rules(ctx: Context, agent_id: str, rules: list[tuple[str, str]]) -> None:
    """Replace the binding's rules with ``(effect, prefix path)`` pairs, in order."""
    async with ctx.control_db.session() as session:
        row = (
            await session.execute(
                select(AgentPermissionRule).where(AgentPermissionRule.agent_id == agent_id)
            )
        ).scalar_one()
        credential_id = row.credential_id
        await session.execute(
            delete(AgentPermissionRule).where(AgentPermissionRule.agent_id == agent_id)
        )
        for seq, (effect, path) in enumerate(rules):
            session.add(
                AgentPermissionRule(
                    agent_id=agent_id,
                    credential_id=credential_id,
                    effect=effect,
                    methods=["GET"],
                    path=path,
                    match_mode="prefix",
                    sequence=seq,
                )
            )
        await session.commit()


def _base_path_request(agent_id: str, credential_id: str) -> QueuedExecutionRequest:
    return QueuedExecutionRequest(
        actor_id=agent_id,
        actor_type="agent",
        method="GET",
        upstream_url="https://api.acme.com/api/v3/admin/users",
        api_vendor=_VENDOR,
        api_name=_API_NAME,
        api_version=_API_VERSION,
        relative_path="/admin/users",
        credential_id=credential_id,
    )


@pytest.fixture(autouse=True)
def _reset_divergence_log_window() -> None:
    authorization._divergence_last_logged.clear()


@pytest.mark.parametrize(
    ("rules", "allowed", "kind"),
    [
        # Upgrade trap, fail-closed: a base-path ``allow`` stops matching.
        ([("allow", "/api/v3/admin")], False, "legacy_allow_no_longer_matches"),
        # Upgrade trap, fail-OPEN: a base-path ``deny`` stops matching and a
        # broader ``allow`` now lets the call through. Must be flagged.
        (
            [("deny", "/api/v3/admin"), ("allow", "/")],
            True,
            "legacy_deny_no_longer_matches",
        ),
        # Correct spec-relative rules whose verdict merely differs on the
        # upstream string: not a base-path rule, so no warning.
        ([("deny", "/admin"), ("allow", "/")], False, None),
        # A denial no base-path reading explains stays quiet.
        ([("allow", "/gadgets")], False, None),
    ],
)
async def test_flags_rules_written_with_the_server_base_path(
    integration_context: Context,
    clean_tables: None,
    rules: list[tuple[str, str]],
    allowed: bool,
    kind: str | None,
) -> None:
    """A verdict that changed because a rule carries the server base path —
    either direction — is logged with the rule's position and pattern; the
    verdict itself is the spec-relative one."""
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    await _set_rules(integration_context, agent_id, rules)
    authorizer = build_queued_execution_authorizer(integration_context)

    with structlog.testing.capture_logs() as logs:
        verdict = await authorizer.authorize(_base_path_request(agent_id, credential_id))

    assert verdict.allowed is allowed
    flagged = [e for e in logs if e["event"] == _DIVERGENCE_EVENT]
    if kind is None:
        assert flagged == []
        return
    assert len(flagged) == 1
    entry = flagged[0]
    assert entry["log_level"] == "warning"
    assert entry["kind"] == kind
    assert entry["rule_position"] == 1
    assert entry["rule_path"] == "/api/v3/admin"
    assert entry["credential_id"] == credential_id
    assert "rewrite-rule-base-paths" in entry["actionable_step"]
    # Never the concrete request path (it can carry user data).
    assert "/admin/users" not in str(entry)


async def test_divergence_warning_is_rate_limited(
    integration_context: Context, clean_tables: None
) -> None:
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    await _set_rules(integration_context, agent_id, [("allow", "/api/v3/admin")])
    authorizer = build_queued_execution_authorizer(integration_context)

    with structlog.testing.capture_logs() as logs:
        for _ in range(5):
            await authorizer.authorize(_base_path_request(agent_id, credential_id))

    assert len([e for e in logs if e["event"] == _DIVERGENCE_EVENT]) == 1


@pytest.mark.parametrize(
    "upstream_url",
    [
        "https://api.acme.com/v1/admin%2F..%2Fpets",
        "https://api.acme.com/v1/admin%5C..%5Cpets",
        "https://api.acme.com/v1/admin/%2e%2e/pets",
    ],
)
async def test_encoded_traversal_is_denied(
    integration_context: Context, clean_tables: None, upstream_url: str
) -> None:
    """``deny /v1/admin`` + ``allow /``: a path that only resolves to an
    allowed path by decoding an escaped separator/dot segment is refused —
    upstream stacks disagree on what it means."""
    agent_id, credential_id = await _seed_bound_agent(integration_context)
    await _set_rules(integration_context, agent_id, [("deny", "/v1/admin"), ("allow", "/")])
    authorizer = build_queued_execution_authorizer(integration_context)

    verdict = await authorizer.authorize(
        QueuedExecutionRequest(
            actor_id=agent_id,
            actor_type="agent",
            method="GET",
            upstream_url=upstream_url,
            api_vendor=_VENDOR,
            api_name=_API_NAME,
            api_version=_API_VERSION,
            credential_id=credential_id,
        )
    )

    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["type"] == "action_denied"
