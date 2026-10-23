"""Integration tests for how the worker settles a failed import job.

Pins #1980: an import whose every source failed for a reason a retry cannot
change (unparseable content, an unresolvable identity, a malformed source
payload, a refused or unusable URL) ends ``failed`` on its first attempt with
one ``import.failed`` event. A failure that may clear on its own (a refused
connection, a timeout, an upstream 5xx/408/425/429, a duplicate-recovery step
that raised) keeps the requeue-with-backoff budget, also when it shares the job
with a deterministic failure.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import structlog.testing
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.errors import IngestJobError
from jentic_one.registry.repos.overlay_repo import OverlayRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.shared.config import WorkerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseUnavailableError
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


def _client_answering(
    status_code: int, body: str = "", headers: dict[str, str] | None = None
) -> AsyncMock:
    """An ``httpx.AsyncClient`` double whose every GET answers ``status_code``."""
    response = AsyncMock()
    response.status_code = status_code
    response.text = body
    response.content = body.encode()
    response.headers = headers or {}
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


_SINGLE_SOURCE_PREFIX = "all 1 import source(s) failed: source[0]: "


@pytest.mark.parametrize(
    ("client", "url", "expected_error"),
    [
        pytest.param(
            _client_answering(404),
            _URL_SOURCE["url"],
            f"non-success status 404 fetching {_URL_SOURCE['url']}",
            id="404",
        ),
        pytest.param(
            _client_answering(403),
            _URL_SOURCE["url"],
            f"non-success status 403 fetching {_URL_SOURCE['url']}",
            id="403",
        ),
        pytest.param(
            _client_answering(302, headers={"location": "https://api.example.com/again"}),
            _URL_SOURCE["url"],
            "too many redirects",
            id="too-many-redirects",
        ),
        pytest.param(
            _client_answering(200, headers={"content-length": str(10**12)}),
            _URL_SOURCE["url"],
            "response exceeds",
            id="size-cap",
        ),
        pytest.param(
            _client_answering(200, body="not valid json or yaml {{{{"),
            _URL_SOURCE["url"],
            _NOT_A_MAPPING,
            id="unparseable-content",
        ),
        pytest.param(
            _client_answering(200),
            "http://169.254.169.254/latest/meta-data",
            "unsafe URL rejected: ",
            id="unsafe-url",
        ),
    ],
)
async def test_deterministic_url_failure_fails_on_first_attempt(
    integration_context: Context,
    admin_db: DatabaseSession,
    clean_jobs: None,
    client: AsyncMock,
    url: str,
    expected_error: str,
) -> None:
    """A URL source refused, or whose fetched content is unusable, is terminal."""
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient", return_value=client):
        job = await _run_import_attempt(
            integration_context, admin_db, [{**_URL_SOURCE, "url": url}]
        )

    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert (job.error or "").startswith(_SINGLE_SOURCE_PREFIX + expected_error)
    assert await _event_types(admin_db, job.id) == [EventType.IMPORT_FAILED]


async def test_url_spec_without_vendor_fails_on_first_attempt(
    integration_context: Context, admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """Fetched content whose API identity cannot be resolved is terminal."""
    source = {"type": "url", "url": "https://api.example.com/openapi.json"}
    client = _client_answering(200, body=_SPEC_WITHOUT_VENDOR)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient", return_value=client):
        job = await _run_import_attempt(integration_context, admin_db, [source])

    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
    assert job.error == _SINGLE_SOURCE_PREFIX + "cannot resolve api_identifier: missing vendor"


@pytest.mark.parametrize("status_code", [500, 502, 408, 425, 429])
async def test_transient_upstream_status_is_requeued(
    integration_context: Context, admin_db: DatabaseSession, clean_jobs: None, status_code: int
) -> None:
    """An upstream status that signals a temporary condition keeps the retry budget."""
    client = _client_answering(status_code)
    with patch("jentic_one.registry.ingest.fetch.httpx.AsyncClient", return_value=client):
        job = await _run_import_attempt(integration_context, admin_db, [_URL_SOURCE])

    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert job.error == (
        f"{_SINGLE_SOURCE_PREFIX}non-success status {status_code} fetching {_URL_SOURCE['url']}"
    )
    assert await _event_types(admin_db, job.id) == []


# --- Duplicate-on-retry recovery that raises --------------------------------

_BASE = json.dumps(
    {
        "openapi": "3.1.0",
        "info": {"title": "Recovery Base", "version": "1.0.0"},
        "servers": [{"url": "https://old.recovery.example.com"}],
        "paths": {
            "/items": {
                "get": {"operationId": "listItems", "responses": {"200": {"description": "OK"}}}
            }
        },
    }
)
_OVERLAID = _BASE.replace("old.recovery", "new.recovery")
_RECOVERY_IDENTITY = {"vendor": "recovery-vendor", "api_name": "recovery-api", "version": "1.0.0"}


@pytest.fixture()
async def clean_registry(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with registry_db.session() as session:
            for model in (
                Overlay,
                OperationURLIndex,
                SecuritySchemeFlow,
                SecurityScheme,
                ServerVariable,
                Server,
                Operation,
                SpecFile,
            ):
                await session.execute(delete(model))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


def _inline(content: str, *, origin: str) -> dict[str, Any]:
    return {
        "type": "inline",
        "content": content,
        "filename": "openapi.json",
        "origin": origin,
        "source_url": "https://catalog.example.com/recovery.json",
        **_RECOVERY_IDENTITY,
    }


async def _raise_unavailable(*args: Any, **kwargs: Any) -> Any:
    raise DatabaseUnavailableError("database is locked")


async def test_overlay_link_recovery_error_keeps_the_job_retryable(
    integration_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate re-ingest whose overlay-link recovery raises is retried, not failed.

    The recovery is what turns the duplicate into a completed job; when it errors
    (a database blip), failing the job outright would leave the overlay unlinked
    for good, so the job must stay retryable and the retry must recover.
    """
    handler = ImportHandler(integration_context)
    await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={"sources": [_inline(_BASE, origin="catalog")]},
        created_by="usr_test",
    )
    async with registry_db.session() as session:
        api = (
            await session.execute(select(Api).where(Api.vendor == "recovery-vendor"))
        ).scalar_one()
        overlay = Overlay(api_id=api.id, document={}, status="pending", created_by="u")
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id

    payload = {"sources": [_inline(_OVERLAID, origin="overlay")], "overlay_id": overlay_id}
    first = await handler.execute(
        job_id=str(uuid.uuid4()), db=None, payload=payload, created_by="usr_test"
    )
    materialized = uuid.UUID(first.body["revisions"][0]["revision_id"])
    async with registry_db.session() as session:
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay_id)
            .values(confirmed_revision_id=None, superseded_revision_id=None)
        )
        await session.commit()

    # Fault injection at the recovery's link write only; the re-ingest itself
    # runs against the real database and hits the real duplicate.
    with monkeypatch.context() as patched:
        patched.setattr(OverlayRepository, "set_confirmed_revision", _raise_unavailable)
        with pytest.raises(IngestJobError) as exc_info:
            await handler.execute(
                job_id=str(uuid.uuid4()), db=None, payload=payload, created_by="usr_test"
            )
    assert type(exc_info.value) is IngestJobError
    assert "identical content already exists" in exc_info.value.message

    retried = await handler.execute(
        job_id=str(uuid.uuid4()), db=None, payload=payload, created_by="usr_test"
    )
    assert retried.body["revisions"] == []
    async with registry_db.session() as session:
        linked = (
            await session.execute(select(Overlay).where(Overlay.id == overlay_id))
        ).scalar_one()
        assert linked.confirmed_revision_id == materialized


async def test_supersede_recovery_error_keeps_the_job_retryable(
    integration_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A supersede re-import whose duplicate recovery lookup raises is retried, not failed."""
    handler = ImportHandler(integration_context)
    await handler.execute(
        job_id=str(uuid.uuid4()),
        db=None,
        payload={"sources": [_inline(_BASE, origin="catalog")]},
        created_by="usr_test",
    )
    monkeypatch.setattr(
        ApiRevisionRepository, "current_revision_for_source_url", _raise_unavailable
    )
    payload = {
        "sources": [
            {
                **_inline(_BASE, origin="catalog"),
                "url": "https://catalog.example.com/recovery.json",
            }
        ],
        "supersede_overlay_id": "ovl_missing",
    }

    with pytest.raises(IngestJobError) as exc_info:
        await handler.execute(
            job_id=str(uuid.uuid4()), db=None, payload=payload, created_by="usr_test"
        )

    assert type(exc_info.value) is IngestJobError


async def test_one_active_slot_conflict_is_requeued_without_duplicate_wording(
    integration_context: Context,
    admin_db: DatabaseSession,
    registry_db: DatabaseSession,
    clean_jobs: None,
    clean_registry: None,
) -> None:
    """Losing the one-active revision slot is retryable and is not reported as a duplicate.

    The revision holding the slot carries different content, so the job must
    neither fail as "identical content already exists" nor match the /mcp
    duplicate detection. The competing active revision is set up as in
    ``test_create_revision_race``: a manual revision marked PUBLISHED, which a
    catalog import's origin-scoped archive does not touch.
    """
    handler = ImportHandler(integration_context)
    manual = {**_inline(_BASE, origin="catalog")}
    del manual["origin"]
    first = await handler.execute(
        job_id=str(uuid.uuid4()), db=None, payload={"sources": [manual]}, created_by="usr_test"
    )
    async with registry_db.session() as session:
        await session.execute(
            update(ApiRevision)
            .where(ApiRevision.id == uuid.UUID(first.body["revisions"][0]["revision_id"]))
            .values(state="published")
        )
        await session.commit()

    job = await _run_import_attempt(
        integration_context, admin_db, [_inline(_OVERLAID, origin="catalog")]
    )

    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1
    assert (
        job.error
        == _SINGLE_SOURCE_PREFIX + "another revision of this API version is already active"
    )
    assert "identical content" not in (job.error or "")


async def test_deterministic_pipeline_refusal_logs_nothing_at_error_level(
    integration_context: Context, admin_db: DatabaseSession, clean_jobs: None
) -> None:
    """A spec the pipeline refuses is a caller-fixable outcome: warnings, no error logs."""
    not_openapi = json.dumps({"info": {"title": "No Key", "version": "1"}, "paths": {}})
    source = {
        "type": "inline",
        "content": not_openapi,
        "filename": "openapi.json",
        **_RECOVERY_IDENTITY,
    }

    with structlog.testing.capture_logs() as logs:
        job = await _run_import_attempt(integration_context, admin_db, [source])

    assert job.status == JobStatus.FAILED
    assert job.error == _SINGLE_SOURCE_PREFIX + "Missing 'openapi' key in specification content"
    assert [e["event"] for e in logs if e["log_level"] == "error"] == []
    failure_events = [e["event"] for e in logs if e["log_level"] == "warning"]
    assert failure_events == [
        "stage_failed",
        "pipeline_failed",
        "import_source_failed",
        "job_failed_non_retryable",
    ]
