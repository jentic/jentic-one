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
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, update

from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.execution_approvals import ExecutionApproval
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.users import User
from jentic_one.broker.core.setup import build_queued_execution_authorizer
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.jobs.protocols import QueuedExecutionRequest
from jentic_one.shared.models import JobKind, JobStatus, StoredCredentialType

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


async def _set_rule_effect(ctx: Context, agent_id: str, effect: str) -> None:
    async with ctx.control_db.session() as session:
        await session.execute(
            update(AgentPermissionRule)
            .where(AgentPermissionRule.agent_id == agent_id)
            .values(effect=effect)
        )
        await session.commit()


async def _job_with_approval(ctx: Context, agent_id: str, state: str | None) -> str:
    """A job created by ``agent_id``, with an approval row in ``state`` (or none)."""
    async with ctx.admin_db.session() as session:
        job = Job(kind=JobKind.EXECUTION, status=JobStatus.RUNNING, created_by=agent_id)
        session.add(job)
        await session.flush()
        if state is not None:
            session.add(
                ExecutionApproval(
                    job_id=job.id,
                    agent_id=agent_id,
                    credential_id="cred_x",
                    api_vendor=_VENDOR,
                    api_name=_API_NAME,
                    api_version=_API_VERSION,
                    method="GET",
                    path="/v1/pets",
                    request_fingerprint=generate_ksuid("fp").ljust(64, "0"),
                    state=state,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        await session.commit()
        return str(job.id)


@pytest.fixture()
async def clean_jobs(integration_context: Context) -> AsyncGenerator[None, None]:
    yield
    async with integration_context.admin_db.session() as session:
        await session.execute(delete(Job))
        await session.commit()


async def test_require_approval_passes_only_for_the_approved_job(
    integration_context: Context, clean_tables: None, clean_jobs: None
) -> None:
    """A require-approval verdict at run time is honoured for the job a reviewer
    approved — and for no other job, pending or unapproved."""
    ctx = integration_context
    agent_id, credential_id = await _seed_bound_agent(ctx)
    await _set_rule_effect(ctx, agent_id, "require-approval")
    approved_job = await _job_with_approval(ctx, agent_id, "approved")
    pending_job = await _job_with_approval(ctx, agent_id, "pending")
    bare_job = await _job_with_approval(ctx, agent_id, None)
    authorizer = build_queued_execution_authorizer(ctx)

    allowed = await authorizer.authorize(
        replace(_request(agent_id, credential_id), job_id=approved_job)
    )
    assert allowed.allowed is True
    assert allowed.credential_id == credential_id

    for job_id in (pending_job, bare_job, None):
        verdict = await authorizer.authorize(
            replace(_request(agent_id, credential_id), job_id=job_id)
        )
        assert verdict.allowed is False
        assert verdict.problem is not None
        assert verdict.problem["type"] == "approval_required"
        assert verdict.problem["status"] == 403


async def test_approved_job_still_fails_when_the_rule_now_denies(
    integration_context: Context, clean_tables: None, clean_jobs: None
) -> None:
    """An approval only satisfies require-approval; a rule that now denies wins."""
    ctx = integration_context
    agent_id, credential_id = await _seed_bound_agent(ctx)
    await _set_rule_effect(ctx, agent_id, "deny")
    approved_job = await _job_with_approval(ctx, agent_id, "approved")

    verdict = await build_queued_execution_authorizer(ctx).authorize(
        replace(_request(agent_id, credential_id), job_id=approved_job)
    )
    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["type"] == "action_denied"
