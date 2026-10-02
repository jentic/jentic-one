"""Web tests for the admin jobs router."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.repos import JobRepository, JobResultRepository
from jentic_one.admin.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, JobKind, JobStatus
from jentic_one.shared.web.deps import resolve_identity
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration


def test_list_success(authed_client: TestClient) -> None:
    resp = authed_client.get("/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "has_more" in data


def test_list_default_limit(authed_client: TestClient) -> None:
    resp = authed_client.get("/jobs")
    assert resp.status_code == 200


def test_list_without_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.get("/jobs")
    assert resp.status_code == 401


def test_get_not_found(authed_client: TestClient) -> None:
    resp = authed_client.get("/jobs/nonexistent-job-id")
    assert resp.status_code == 404


def test_get_result_not_found(authed_client: TestClient) -> None:
    resp = authed_client.get("/jobs/nonexistent-job-id/result")
    assert resp.status_code == 404 or resp.status_code == 409


def test_list_filter_by_status(authed_client: TestClient) -> None:
    resp = authed_client.get("/jobs?status=queued&status=running")
    assert resp.status_code == 200


def test_list_filter_from_to(authed_client: TestClient) -> None:
    resp = authed_client.get("/jobs?from=2026-01-01T00:00:00Z&to=2026-12-31T00:00:00Z")
    assert resp.status_code == 200


def test_list_limit_max_100(authed_client: TestClient) -> None:
    resp = authed_client.get("/jobs?limit=101")
    assert resp.status_code == 422


# --- Owner scoping (real DB, identity overridden at resolve_identity) ---

_SCOPED_OWNER = "usr_webjobs_owner"
_SCOPED_OTHER = "agnt_webjobs_other"


def _scoped_client(ctx: Context, identity: Identity) -> TestClient:
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan

    async def _override(_: object = None) -> Identity:
        return identity

    app.dependency_overrides[resolve_identity] = _override
    return TestClient(app)


@pytest.fixture()
async def owner_job_ids(web_context: Context) -> AsyncGenerator[tuple[str, str], None]:
    """A completed and a queued job, both created by ``_SCOPED_OWNER``."""
    async with web_context.admin_db.session() as session:
        done = await JobRepository.create(
            session, kind=JobKind.EXECUTION, status=JobStatus.COMPLETED, created_by=_SCOPED_OWNER
        )
        await JobResultRepository.create(
            session,
            job_id=done.id,
            kind="execution",
            body={"owner": "only"},
            created_by=_SCOPED_OWNER,
        )
        queued = await JobRepository.create(
            session, kind=JobKind.EXECUTION, status=JobStatus.QUEUED, created_by=_SCOPED_OWNER
        )
        await session.commit()
        ids = (done.id, queued.id)
    yield ids
    async with web_context.admin_db.session() as session:
        await session.execute(delete(JobResult).where(JobResult.job_id.in_(ids)))
        await session.execute(delete(Job).where(Job.id.in_(ids)))
        await session.commit()


def test_other_actor_gets_404_and_empty_list(
    web_context: Context, owner_job_ids: tuple[str, str]
) -> None:
    done_id, queued_id = owner_job_ids
    other = Identity(
        sub=_SCOPED_OTHER,
        permissions=["jobs:read", "jobs:write"],
        actor_type=ActorType.AGENT,
        parent_actor_id="usr_webjobs_someone_else",
    )
    with _scoped_client(web_context, other) as tc:
        listed = {j["job_id"] for j in tc.get("/jobs?limit=100").json()["data"]}
        assert not listed & {done_id, queued_id}
        assert tc.get(f"/jobs/{done_id}").status_code == 404
        assert tc.get(f"/jobs/{done_id}/result").status_code == 404
        assert tc.post(f"/jobs/{queued_id}:cancel").status_code == 404

    owner = Identity(sub=_SCOPED_OWNER, permissions=["jobs:read", "jobs:write"])
    with _scoped_client(web_context, owner) as tc:
        listed = {j["job_id"] for j in tc.get("/jobs?limit=100").json()["data"]}
        assert {done_id, queued_id} <= listed
        assert tc.get(f"/jobs/{done_id}").status_code == 200
        result = tc.get(f"/jobs/{done_id}/result")
        assert result.status_code == 200
        assert result.json() == {"owner": "only"}
        cancelled = tc.post(f"/jobs/{queued_id}:cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"


def test_admin_sees_other_actors_jobs(
    authed_client: TestClient, owner_job_ids: tuple[str, str]
) -> None:
    done_id, _ = owner_job_ids
    assert authed_client.get(f"/jobs/{done_id}").status_code == 200
    assert authed_client.get(f"/jobs/{done_id}/result").status_code == 200
