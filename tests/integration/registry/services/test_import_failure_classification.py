"""Integration tests for how the worker settles a failed import job.

Pins #1980: an import whose every source failed for a reason a retry cannot
change (unparseable content, an unresolvable identity, a malformed source
payload) ends ``failed`` on its first attempt with one ``import.failed`` event.
A failure that may clear on its own (a refused connection, an upstream 5xx)
keeps the requeue-with-backoff budget, also when it shares the job with a
deterministic failure.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.handlers import JobHandlerRegistry
from jentic_one.shared.jobs.worker import WorkerLoop
from jentic_one.shared.models import JobKind, JobStatus
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_ACTOR = "usr_import_failure_classification"

# No ``servers`` and no explicit vendor: the API identity cannot be resolved.
_SPEC_WITHOUT_VENDOR = json.dumps(
    {
        "openapi": "3.1.0",
        "info": {"title": "No Vendor", "version": "1.0.0"},
        "paths": {"/items": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }
)

# Parses as a YAML scalar, which is not a spec document.
_UNPARSEABLE = {"type": "inline", "content": "not valid json or yaml {{{{", "filename": "bad.json"}
_NOT_A_MAPPING = "spec content must be a mapping (object), not a scalar or list"

_URL_SOURCE = {
    "type": "url",
    "url": "https://api.example.com/openapi.json",
    "vendor": "retry-vendor",
    "api_name": "retry-api",
    "version": "1.0.0",
}


@pytest.fixture()
async def clean_jobs(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _clean() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(Event).where(Event.actor_id == _ACTOR))
            await session.execute(delete(JobResult))
            await session.execute(delete(Job))
            await session.commit()

    await _clean()
    yield
    await _clean()


async def _run_import_attempt(
    ctx: Context, admin_db: DatabaseSession, sources: list[dict[str, Any]]
) -> Job:
    """Queue an import job, run exactly one worker attempt, and return the row."""
    job = Job(
        kind=JobKind.IMPORT,
        status=JobStatus.QUEUED,
        payload={"sources": sources},
        created_by=_ACTOR,
        actor_type="user",
    )
    async with admin_db.session() as session:
        session.add(job)
        await session.commit()
        job_id = job.id

    registry = JobHandlerRegistry()
    registry.register(JobKind.IMPORT, ImportHandler(ctx))
    worker = WorkerLoop(admin_db, registry, worker_config=WorkerConfig(max_attempts=5))
    assert await worker._tick() is True

    async with admin_db.session() as session:
        return (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()


async def _event_types(admin_db: DatabaseSession, job_id: str) -> list[str]:
    async with admin_db.session() as session:
        rows = await session.execute(select(Event.type).where(Event.job_id == job_id))
        return [str(t) for t in rows.scalars().all()]


def _client_raising(exc: Exception) -> AsyncMock:
    """An ``httpx.AsyncClient`` double whose GET raises ``exc``."""
    client = AsyncMock()
    client.get = AsyncMock(side_effect=exc)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


def _client_answering(status_code: int) -> AsyncMock:
    """An ``httpx.AsyncClient`` double whose GET answers ``status_code`` with no body."""
    response = AsyncMock()
    response.status_code = status_code
    response.text = ""
    response.content = b""
    response.headers = {}
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.mark.parametrize(
    ("sources", "expected_error"),
    [
        pytest.param(
            [{"type": "inline", "content": _SPEC_WITHOUT_VENDOR, "filename": "openapi.json"}],
            "all 1 import source(s) failed: source[0]: "
            "cannot resolve api_identifier: missing vendor",
            id="missing-vendor",
        ),
        pytest.param(
            [_UNPARSEABLE],
            f"all 1 import source(s) failed: source[0]: {_NOT_A_MAPPING}",
            id="unparseable",
        ),
        pytest.param(
            [_UNPARSEABLE, _UNPARSEABLE],
            # ``job.error`` keeps the first 128 characters.
            f"all 2 import source(s) failed: source[0]: {_NOT_A_MAPPING}; "
            f"source[1]: {_NOT_A_MAPPING}"[:128],
            id="every-source-deterministic",
        ),
    ],
)
async def test_deterministic_import_failure_fails_on_first_attempt(
    integration_context: Context,
    admin_db: DatabaseSession,
    clean_jobs: None,
    sources: list[dict[str, Any]],
    expected_error: str,
) -> None:
    """A failure a retry cannot fix is terminal ``failed`` after one attempt."""
    job = await _run_import_attempt(integration_context, admin_db, sources)

    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert job.visible_at is None
    assert job.error == expected_error
    assert await _event_types(admin_db, job.id) == [EventType.IMPORT_FAILED]


async def test_malformed_source_payload_fails_on_first_attempt(
    integration_context: Context, admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A source the payload model rejects is terminal: the payload never changes."""
    job = await _run_import_attempt(
        integration_context, admin_db, [{"type": "carrier-pigeon", "content": "{}"}]
    )

    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert (job.error or "").startswith("all 1 import source(s) failed: source[0]: ")
    assert await _event_types(admin_db, job.id) == [EventType.IMPORT_FAILED]


@pytest.mark.parametrize(
    ("client", "sources"),
    [
        pytest.param(
            _client_raising(httpx.ConnectError("connection refused")),
            [_URL_SOURCE],
            id="connection-refused",
        ),
        pytest.param(_client_answering(503), [_URL_SOURCE], id="upstream-503"),
        pytest.param(
            _client_raising(httpx.ReadTimeout("timed out")),
            [_UNPARSEABLE, _URL_SOURCE],
            id="mixed-deterministic-and-transient",
        ),
    ],
)
async def test_transient_import_failure_is_requeued(
    integration_context: Context,
    admin_db: DatabaseSession,
    clean_jobs: None,
    client: AsyncMock,
    sources: list[dict[str, Any]],
) -> None:
    """A failure that may clear on its own keeps the retry budget."""
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient", return_value=client):
        job = await _run_import_attempt(integration_context, admin_db, sources)

    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert job.visible_at is not None
    assert (job.error or "").startswith(f"all {len(sources)} import source(s) failed: ")
    assert await _event_types(admin_db, job.id) == []
