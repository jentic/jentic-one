"""Unit tests for cancelling an actor's queued execution jobs on disable/archive/delete (#1674).

Covers the shared ``cancel_queued_executions_for_actor`` helper (audit only
when something was cancelled) and its wiring into ``AgentService.disable`` /
``archive`` and ``UserService.disable`` / ``delete``: each calls it in the
same transaction session as the status change, with the right actor kind and
cause, and a failure propagates instead of being swallowed. The SQL itself is
pinned by the integration suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.admin.services._support.actor_jobs import cancel_queued_executions_for_actor
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

_ACTOR_JOBS = "jentic_one.admin.services._support.actor_jobs"
_AGENT_SVC = "jentic_one.auth.services.agent_service"
_USER_SVC = "jentic_one.admin.services.user_service"


def _admin() -> Identity:
    return Identity(sub="usr_admin", email="admin@example.com", permissions=["org:admin"])


def _ctx_with_session() -> tuple[MagicMock, AsyncMock]:
    ctx = MagicMock()
    session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx, session


# -- helper ----------------------------------------------------------------


@pytest.mark.asyncio
@patch(f"{_ACTOR_JOBS}.record_audit", new_callable=AsyncMock)
@patch(f"{_ACTOR_JOBS}.JobRepository")
async def test_helper_records_no_audit_when_nothing_queued(
    mock_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    mock_repo.cancel_queued_executions_for_actor = AsyncMock(return_value=[])
    session = MagicMock()

    count = await cancel_queued_executions_for_actor(
        session,
        actor_id="agnt_x",
        target_type=AuditTargetType.AGENT,
        cause="was disabled",
        identity=_admin(),
    )

    assert count == 0
    mock_repo.cancel_queued_executions_for_actor.assert_awaited_once_with(session, "agnt_x")
    mock_audit.assert_not_awaited()


@pytest.mark.asyncio
@patch(f"{_ACTOR_JOBS}.record_audit", new_callable=AsyncMock)
@patch(f"{_ACTOR_JOBS}.JobRepository")
async def test_helper_records_one_update_audit_with_the_count(
    mock_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    mock_repo.cancel_queued_executions_for_actor = AsyncMock(return_value=["job_a", "job_b"])
    session = MagicMock()
    identity = _admin()

    count = await cancel_queued_executions_for_actor(
        session,
        actor_id="usr_target",
        target_type=AuditTargetType.USER,
        cause="was deleted",
        identity=identity,
    )

    assert count == 2
    mock_audit.assert_awaited_once_with(
        session,
        action=AuditAction.UPDATE,
        target_type=AuditTargetType.USER,
        target_id="usr_target",
        actor_type=identity.actor_type,
        actor_id="usr_admin",
        after={"cancelled_queued_execution_jobs": 2, "job_ids": ["job_a", "job_b"]},
        reason="queued execution jobs cancelled: user was deleted",
        origin=identity.origin.value,
    )


# -- AgentService ----------------------------------------------------------


@pytest.fixture()
def agent_cancel() -> Iterator[AsyncMock]:
    with patch(f"{_AGENT_SVC}.cancel_queued_executions_for_actor", new_callable=AsyncMock) as m:
        m.return_value = 0
        yield m


@pytest.mark.asyncio
@patch(f"{_AGENT_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_AGENT_SVC}.AgentRepository")
async def test_agent_disable_cancels_queued_jobs_in_the_disable_transaction(
    mock_repo: MagicMock, mock_audit: AsyncMock, agent_cancel: AsyncMock
) -> None:
    ctx, session = _ctx_with_session()
    mock_repo.update_status = AsyncMock()
    identity = _admin()
    svc = AgentService(ctx)

    with patch.object(AgentService, "_check_transition", new_callable=AsyncMock):
        await svc.disable("agnt_x", identity=identity)

    mock_repo.update_status.assert_awaited_once_with(session, "agnt_x", ActorStatus.DISABLED)
    agent_cancel.assert_awaited_once_with(
        session,
        actor_id="agnt_x",
        target_type=AuditTargetType.AGENT,
        cause="was disabled",
        identity=identity,
    )


@pytest.mark.asyncio
@patch(f"{_AGENT_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_AGENT_SVC}.AgentRepository")
async def test_agent_disable_propagates_a_cancel_failure(
    mock_repo: MagicMock, mock_audit: AsyncMock, agent_cancel: AsyncMock
) -> None:
    """Not best-effort: a failed sweep fails the disable, so its transaction rolls back."""
    ctx, _session = _ctx_with_session()
    mock_repo.update_status = AsyncMock()
    agent_cancel.side_effect = RuntimeError("boom")

    with (
        patch.object(AgentService, "_check_transition", new_callable=AsyncMock),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await AgentService(ctx).disable("agnt_x", identity=_admin())

    mock_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_enable_does_not_touch_jobs(agent_cancel: AsyncMock) -> None:
    ctx, _session = _ctx_with_session()
    with (
        patch.object(AgentService, "_check_transition", new_callable=AsyncMock),
        patch(f"{_AGENT_SVC}.AgentRepository") as mock_repo,
        patch(f"{_AGENT_SVC}.record_audit", new_callable=AsyncMock),
    ):
        mock_repo.update_status = AsyncMock()
        await AgentService(ctx).enable("agnt_x", identity=_admin())

    agent_cancel.assert_not_awaited()


@pytest.mark.asyncio
@patch(f"{_AGENT_SVC}.revoke_active_grants_for_agent", new_callable=AsyncMock)
@patch(f"{_AGENT_SVC}.AgentCredentialBindingRepository")
@patch(f"{_AGENT_SVC}.ActorPermissionGrantRepository")
@patch(f"{_AGENT_SVC}.record_audit", new_callable=AsyncMock)
@patch(f"{_AGENT_SVC}.AgentRepository")
async def test_agent_archive_cancels_queued_jobs_in_the_archive_transaction(
    mock_repo: MagicMock,
    mock_audit: AsyncMock,
    mock_grants: MagicMock,
    mock_bindings: MagicMock,
    mock_revoke: AsyncMock,
    agent_cancel: AsyncMock,
) -> None:
    ctx, session = _ctx_with_session()
    agent = MagicMock(id="agnt_x", status=ActorStatus.ACTIVE, owner_id="usr_owner")
    agent.name = "x"
    mock_repo.archive = AsyncMock()
    mock_grants.list_for_actor = AsyncMock(return_value=[])
    mock_grants.revoke_all = AsyncMock()
    mock_bindings.delete_for_agent = AsyncMock()
    identity = _admin()

    with patch.object(AgentService, "_load_owned_agent", AsyncMock(return_value=agent)):
        await AgentService(ctx).archive("agnt_x", identity=identity)

    mock_repo.archive.assert_awaited_once_with(session, "agnt_x")
    agent_cancel.assert_awaited_once_with(
        session,
        actor_id="agnt_x",
        target_type=AuditTargetType.AGENT,
        cause="was archived",
        identity=identity,
    )


# -- UserService -----------------------------------------------------------


@pytest.fixture()
def user_deps() -> Iterator[dict[str, MagicMock]]:
    with (
        patch(f"{_USER_SVC}.UserRepository") as users,
        patch(f"{_USER_SVC}.UserPermissionGrantRepository") as grants,
        patch(f"{_USER_SVC}.ExternalIdentityRepository") as external,
        patch(f"{_USER_SVC}.AuditRepository") as audit,
        patch(f"{_USER_SVC}.ensure_can_manage"),
        patch(f"{_USER_SVC}.ensure_not_last_active_admin", new_callable=AsyncMock),
        patch(f"{_USER_SVC}.cancel_queued_executions_for_actor", new_callable=AsyncMock) as cancel,
    ):
        users.get_by_id = AsyncMock(return_value=MagicMock(id="usr_target"))
        users.disable = AsyncMock()
        users.enable = AsyncMock()
        users.update = AsyncMock()
        grants.get_permission_sets = AsyncMock(return_value={})
        external.delete_for_user = AsyncMock()
        audit.record = AsyncMock()
        cancel.return_value = 0
        yield {"users": users, "audit": audit, "cancel": cancel}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "cause"), [("disable", "was disabled"), ("delete", "was deleted")]
)
async def test_user_lifecycle_cancels_queued_jobs_in_the_same_transaction(
    user_deps: dict[str, MagicMock], method: str, cause: str
) -> None:
    ctx, session = _ctx_with_session()
    identity = _admin()

    await getattr(UserService(ctx), method)("usr_target", identity=identity)

    user_deps["cancel"].assert_awaited_once_with(
        session,
        actor_id="usr_target",
        target_type=AuditTargetType.USER,
        cause=cause,
        identity=identity,
    )


@pytest.mark.asyncio
async def test_user_disable_propagates_a_cancel_failure(user_deps: dict[str, MagicMock]) -> None:
    ctx, _session = _ctx_with_session()
    user_deps["cancel"].side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await UserService(ctx).disable("usr_target", identity=_admin())

    user_deps["audit"].record.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_enable_does_not_touch_jobs(user_deps: dict[str, MagicMock]) -> None:
    ctx, _session = _ctx_with_session()

    await UserService(ctx).enable("usr_target", identity=_admin())

    user_deps["cancel"].assert_not_awaited()
