"""import_api on the mount — the Go table tests, replayed against the port.

Mirrors ``cli/internal/cli/api/mcp_access_test.go``'s import coverage
(validation arms, scope gate, three-outcome tracking, failed-job arm) plus the
arms only the in-process port has: the duplicate-content short-circuit (the
worker requeues a duplicate ingest with backoff and dead-letters it, so the
handler matches ``job.error`` on every poll — including via
get_execution_result), and the promote-leg softness (``RevisionService.promote``
enforces no scopes in-process, so the handler soft-checks ``apis:write``
itself; every promote failure is a per-revision map entry, never a hard error).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.tools as tools_mod
from jentic_one.admin.services.schemas.jobs import JobResultView, JobView
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call, validate_api_id
from jentic_one.registry.ingest.exc import DuplicateRevisionError
from jentic_one.registry.services.errors import (
    CatalogEntryNotFoundError,
    OverlaySupersedeForbiddenError,
    RevisionStateConflictError,
)
from jentic_one.registry.services.import_service import (
    ALL_SOURCES_FAILED_PREFIX_TEMPLATE,
    SOURCE_FAILURE_PREFIX_TEMPLATE,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.jobs.worker import _ERROR_MAX_LEN
from jentic_one.shared.models import ActorType

_NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _real_duplicate_job_error() -> str:
    """``job.error`` exactly as the worker writes it for a duplicate ingest.

    The construction is the real pipeline, end to end: ``ImportHandler`` wraps
    the per-source failure with its own (imported, never hand-copied) wrapper
    templates around ``DuplicateRevisionError().message``
    (``import_service.py``), and the worker's requeue/terminal writes truncate
    it to ``_ERROR_MAX_LEN`` (``worker.py``). Fixtures use this — never a
    hand-crafted string — so a reword on either side (or a wrapper that grows
    past the truncation point) breaks the tests.
    """
    wrapped = (
        ALL_SOURCES_FAILED_PREFIX_TEMPLATE.format(count=1)
        + SOURCE_FAILURE_PREFIX_TEMPLATE.format(index=0)
        + DuplicateRevisionError().message
    )
    return wrapped[:_ERROR_MAX_LEN]


def _multi_source_duplicate_job_error() -> str:
    """A MULTI-source ``job.error``: source[0] duplicate, source[1] genuine.

    ``POST /apis`` enqueues multi-source ``JobKind.IMPORT`` jobs; when one
    source hits the duplicate but another genuinely fails, the whole job
    dead-letters with the duplicate fragment inside the truncated error. The
    remap must NOT fire on it — reporting already_imported would mask
    source[1]'s failure.
    """
    wrapped = (
        ALL_SOURCES_FAILED_PREFIX_TEMPLATE.format(count=2)
        + SOURCE_FAILURE_PREFIX_TEMPLATE.format(index=0)
        + DuplicateRevisionError().message
        + "; "
        + SOURCE_FAILURE_PREFIX_TEMPLATE.format(index=1)
        + "spec fetch failed"
    )
    return wrapped[:_ERROR_MAX_LEN]


#: the import-job result body the worker writes for one draft revision
#: (``import_service.py`` — ``{"revisions": [...]}``); the Go fixture's twin.
_DRAFT_REVISION = {
    "api": {"vendor": "googleapis.com", "name": "sheets", "version": "v4"},
    "revision_id": "rev_1",
    "superseded_revision_id": None,
    "state": "draft",
}


def _env(permissions: list[str]) -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(sub="agnt_1", permissions=permissions, actor_type=ActorType.AGENT),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
    )


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


def _job(status: str, *, error: str | None = None, kind: str = "import") -> JobView:
    return JobView(
        id="job_9", kind=kind, status=status, error=error, created_at=_NOW, updated_at=_NOW
    )


class _FakeCatalogService:
    """CatalogService stand-in: ``import_entry`` enqueues job_9 (or raises)."""

    filed: ClassVar[list[str]] = []
    import_error: ClassVar[Exception | None] = None

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def import_entry(self, api_id: str, identity: Identity) -> str:
        if _FakeCatalogService.import_error is not None:
            raise _FakeCatalogService.import_error
        _FakeCatalogService.filed.append(api_id)
        return "job_9"


class _FakeJobService:
    """JobService stand-in: serves a scripted status sequence for job_9."""

    statuses: ClassVar[list[JobView]] = []
    polls: ClassVar[int] = 0
    poll_error: ClassVar[Exception | None] = None

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def get_by_id(self, job_id: str) -> JobView:
        assert job_id == "job_9"
        if _FakeJobService.poll_error is not None:
            raise _FakeJobService.poll_error
        idx = min(_FakeJobService.polls, len(_FakeJobService.statuses) - 1)
        _FakeJobService.polls += 1
        return _FakeJobService.statuses[idx]


class _FakeJobResultService:
    """JobResultService stand-in: serves the import job's result body."""

    body: ClassVar[dict[str, Any]] = {}
    error: ClassVar[Exception | None] = None

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def get(self, job_id: str) -> JobResultView:
        if _FakeJobResultService.error is not None:
            raise _FakeJobResultService.error
        return JobResultView(
            id="jr_1",
            job_id=job_id,
            kind="import",
            body=_FakeJobResultService.body,
            created_at=_NOW,
        )


class _FakeRevisionService:
    """RevisionService stand-in: records promote calls (or raises)."""

    promotes: ClassVar[list[tuple[str, str, str, str]]] = []
    promote_error: ClassVar[Exception | None] = None

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def promote(
        self, vendor: str, name: str, version: str, revision_id: str, *, identity: Identity
    ) -> Any:
        if _FakeRevisionService.promote_error is not None:
            raise _FakeRevisionService.promote_error
        _FakeRevisionService.promotes.append((vendor, name, version, revision_id))
        return MagicMock()


@pytest.fixture()
def services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire the four service seams to fresh fakes and shrink the poll cadence."""
    _FakeCatalogService.filed = []
    _FakeCatalogService.import_error = None
    _FakeJobService.statuses = []
    _FakeJobService.polls = 0
    _FakeJobService.poll_error = None
    _FakeJobResultService.body = {"revisions": [dict(_DRAFT_REVISION)]}
    _FakeJobResultService.error = None
    _FakeRevisionService.promotes = []
    _FakeRevisionService.promote_error = None
    monkeypatch.setattr(tools_mod, "CatalogService", _FakeCatalogService)
    monkeypatch.setattr(tools_mod, "JobService", _FakeJobService)
    monkeypatch.setattr(tools_mod, "JobResultService", _FakeJobResultService)
    monkeypatch.setattr(tools_mod, "RevisionService", _FakeRevisionService)
    monkeypatch.setattr(tools_mod, "_IMPORT_POLL_STEP_SECONDS", 0.001)
    monkeypatch.setattr(tools_mod, "_IMPORT_POLL_MAX_SECONDS", 0.002)


# ── validation arms (Go: MissingAPIID / TraversalAPIID) ──────────────────────


async def test_missing_api_id_is_invalid_params(services: None) -> None:
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_import_api(_env(["catalog:import", "jobs:read"]), {})
    assert "api_id" in str(err.value)
    assert "search_catalog" in str(err.value)


@pytest.mark.parametrize(
    "bad",
    [
        "../access-requests",
        "/catalog/x",
        "a//b",
        "a/./b",
        "googleapis.com/sheets/..",
        "googleapis.com/",
    ],
)
async def test_traversal_api_id_is_invalid_params(services: None, bad: str) -> None:
    """The Go ``validateAPIID`` contract: traversal-shaped ids are refused as
    a correctable protocol error before any service call."""
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_import_api(_env(["catalog:import", "jobs:read"]), {"api_id": bad})
    assert "search_catalog" in str(err.value)
    assert _FakeCatalogService.filed == []


def test_umbrella_api_id_with_literal_slash_stays_accepted() -> None:
    validate_api_id("googleapis.com/sheets")  # must not raise


# ── scope gate (Go: 403PointsAtRequestAccessForScope) ────────────────────────


async def test_missing_scope_is_broker_denied_with_the_pointer_served(
    services: None,
) -> None:
    """The same gate as POST /catalog/{api_id}:import (catalog:import). The
    handler raises the shared ``request_access`` pointer spelling (the
    contract — the pinned description names it); with request_access now in
    SERVED_TOOLS (PR B), #1254's lane-aware render filter lets it ride the
    wire — the pointer resurfaced with no handler change, exactly as PR A
    predicted."""
    result = await dispatch_tool_call(_env([]), "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert payload["next_tool"] == "request_access"  # served since PR B → rides (#1254)
    assert "catalog:import" in payload["error"]
    assert "catalog:import" in payload["actionable_step"]
    assert _FakeCatalogService.filed == []


async def test_missing_jobs_read_degrades_to_the_filed_envelope_without_polling(
    services: None,
) -> None:
    """In-process tracking rides the same jobs:read gate the Go client's poll
    leg does (GET /jobs/{id}). An identity with catalog:import but not
    jobs:read files successfully and gets the queued envelope — NOT an error
    (the filing succeeded) — and the job service is never touched. Both
    scopes ride DEFAULT_AGENT_SCOPES, so defaults are unaffected."""
    env = _env(["catalog:import"])  # no jobs:read
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error, "a missing poll scope degrades; the filing still succeeded"
    payload = _payload(result)
    assert payload["schema_version"] == "1"
    assert payload["job_id"] == "job_9"
    assert payload["status"] == "queued"
    assert "revisions" not in payload
    assert "promoted" not in payload
    assert _FakeCatalogService.filed == ["googleapis.com/sheets"]
    assert _FakeJobService.polls == 0, "no jobs:read → no job polling"


# ── three-outcome tracking (Go: CompletesAndPromotes / StillRunning / PollFailure) ──


async def test_completed_import_promotes_and_returns_the_go_envelope(services: None) -> None:
    """The happy path: track to completion, fetch the result, promote the
    draft revision live — {schema_version, job_id, status, revisions,
    promoted} with the instance stamp joined. Uses the ``id`` alias and an
    umbrella api_id with its literal slash, like the Go test."""
    _FakeJobService.statuses = [_job("running"), _job("completed")]
    env = _env(["catalog:import", "jobs:read", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"id": "googleapis.com/sheets"})
    assert not result.is_error, result.content
    payload = _payload(result)
    assert payload["schema_version"] == "1"
    assert payload["job_id"] == "job_9"
    assert payload["status"] == "completed"
    assert payload["revisions"] == [_DRAFT_REVISION]
    assert payload["promoted"] == {"rev_1": "live"}
    assert _FakeCatalogService.filed == ["googleapis.com/sheets"]
    assert _FakeRevisionService.promotes == [("googleapis.com", "sheets", "v4", "rev_1")]
    assert "instance" in payload


async def test_budget_lapse_returns_the_running_job_as_a_normal_result(
    services: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow import is not an error — the model converges by re-calling
    import_api (idempotent) or watching the job with get_execution_result."""
    monkeypatch.setattr(tools_mod, "_IMPORT_WAIT_BUDGET_SECONDS", 0.0)
    _FakeJobService.statuses = [_job("running")]
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error, result.content
    payload = _payload(result)
    assert payload["job_id"] == "job_9"
    assert payload["status"] == "running"
    assert "promoted" not in payload, "nothing completed, nothing may claim promotion"
    assert "revisions" not in payload


async def test_job_poll_failure_is_a_soft_error_never_still_running(services: None) -> None:
    """A failing poll is UNKNOWN state: reporting it as a clean non-terminal
    result would send the model into a re-import loop against a backend that
    de-duplicates nothing. The job_id rides the extras so the model can keep
    watching THIS job."""
    _FakeJobService.poll_error = RuntimeError("job store down")
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error, "a job-poll failure must never look like still-running"
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert payload["job_id"] == "job_9"
    assert payload["next_tool"] == "get_execution_result"
    assert "job store down" in payload["error"]


async def test_hung_poll_trips_the_hard_ceiling_and_maps_to_the_poll_failure_arm(
    services: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait budget only gates BETWEEN polls — a single hung poll would
    hold the ASGI request open indefinitely without the ``asyncio.timeout``
    ceiling. The lapse is UNKNOWN state: INTERNAL_ERROR with the job_id,
    never a clean "still running"."""
    monkeypatch.setattr(tools_mod, "_IMPORT_WAIT_BUDGET_SECONDS", 0.01)
    monkeypatch.setattr(tools_mod, "_IMPORT_WAIT_GRACE_SECONDS", 0.02)

    async def _hang(self: Any, job_id: str) -> JobView:
        await asyncio.Event().wait()  # never set — a poll that never returns
        raise AssertionError("unreachable")

    monkeypatch.setattr(_FakeJobService, "get_by_id", _hang)
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error, "a ceiling lapse must never look like still-running"
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert payload["job_id"] == "job_9"
    assert payload["next_tool"] == "get_execution_result"
    assert "timed out" in payload["error"]


async def test_hung_filing_trips_the_hard_ceiling_as_a_retryable_transport_lapse(
    services: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ceiling wraps the FILING leg too (its catalog read can lazily
    refresh the upstream manifest, bounded only by ingest.fetch_timeout_s).
    A lapse there has no job_id yet and whether the enqueue committed is
    unknowable: a retryable TRANSPORT_ERROR ("may or may not have been
    filed") — a re-import converges, so retrying is safe — never the
    INTERNAL_ERROR mid-poll arm and never a job_id it does not have."""
    monkeypatch.setattr(tools_mod, "_IMPORT_WAIT_BUDGET_SECONDS", 0.01)
    monkeypatch.setattr(tools_mod, "_IMPORT_WAIT_GRACE_SECONDS", 0.02)

    async def _hang(self: Any, api_id: str, identity: Identity) -> str:
        await asyncio.Event().wait()  # never set — a filing that never returns
        raise AssertionError("unreachable")

    monkeypatch.setattr(_FakeCatalogService, "import_entry", _hang)
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error, "a filing lapse must never look like a clean filing"
    payload = _payload(result)
    assert payload["error_code"] == "TRANSPORT_ERROR"
    assert "may or may not have been filed" in payload["error"]
    assert payload["retryable"] is True
    assert payload["next_tool"] == "search_catalog"
    assert "job_id" not in payload, "no job_id exists at filing time"


# ── failed-job arm (Go: FailedJobIsSoftError; DEAD_LETTER is terminal) ───────


@pytest.mark.parametrize("terminal", ["failed", "dead_letter", "cancelled"])
async def test_failed_job_is_internal_error_with_job_extras(services: None, terminal: str) -> None:
    _FakeJobService.statuses = [_job(terminal, error="spec fetch failed")]
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert "spec fetch failed" in payload["error"]
    assert payload["job_id"] == "job_9"
    assert payload["job_status"] == terminal
    assert payload["next_tool"] == "search_catalog"


# ── duplicate-content short-circuit ──────────────────────────────────────────


def test_duplicate_fragment_pins_the_real_worker_error_message() -> None:
    """Couples the remap's two keys to the real ``job.error``: the
    ``ImportHandler`` wrapper templates around
    ``DuplicateRevisionError().message``, truncated to the worker's
    ``_ERROR_MAX_LEN``. A reword of the exception message, a wrapper change
    that pushes the fragment past the truncation point, or a prefix drift
    that unkeys the single-source gate fails here first, not in production."""
    error = _real_duplicate_job_error()
    assert tools_mod._DUPLICATE_CONTENT_FRAGMENT in error
    assert error.startswith(tools_mod._SINGLE_SOURCE_IMPORT_FAILURE_PREFIX)


async def test_duplicate_content_short_circuits_on_a_non_terminal_requeued_job(
    services: None,
) -> None:
    """The worker treats a duplicate ingest as retryable (backoff to
    DEAD_LETTER, ~30s+), so the handler matches the stable leading fragment of
    ``job.error`` on EVERY non-completed poll — including a non-terminal
    requeued state — and short-circuits instead of burning the wait budget."""
    _FakeJobService.statuses = [_job("queued", error=_real_duplicate_job_error())]
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error, "already-present content is a convergence, not a failure"
    payload = _payload(result)
    assert payload["schema_version"] == "1"
    assert payload["job_id"] == "job_9"
    assert payload["status"] == "already_imported"
    assert "already present" in payload["note"]
    assert payload["next_tool"] == "search_apis"
    assert _FakeJobService.polls == 1, "the short-circuit must not keep polling"


async def test_completed_job_with_stale_duplicate_error_reports_the_completed_result(
    services: None,
) -> None:
    """The worker never clears ``job.error`` (requeue writes it; neither claim
    nor completion resets it), so an import that failed once with the
    duplicate message and succeeded on a later attempt carries the stale
    fragment forever. A COMPLETED job's result is always more honest than its
    residual error: the normal completed envelope — revisions AND the promote
    leg — never already_imported."""
    _FakeJobService.statuses = [_job("completed", error=_real_duplicate_job_error())]
    env = _env(["catalog:import", "jobs:read", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error, result.content
    payload = _payload(result)
    assert payload["status"] == "completed", "a stale requeue error must not mask completion"
    assert payload["revisions"] == [_DRAFT_REVISION]
    assert payload["promoted"] == {"rev_1": "live"}
    assert _FakeRevisionService.promotes == [("googleapis.com", "sheets", "v4", "rev_1")]


async def test_duplicate_content_dead_letter_via_get_execution_result(services: None) -> None:
    """A duplicate import job polled later reports already_imported, never a
    scary dead_letter — the same detection, placed before the generic payload
    assembly in handle_get_execution_result."""
    _FakeJobService.statuses = [_job("dead_letter", error=_real_duplicate_job_error())]
    env = _env(["jobs:read"])
    result = await dispatch_tool_call(env, "get_execution_result", {"job_id": "job_9"})
    assert not result.is_error
    payload = _payload(result)
    assert payload["status"] == "already_imported"
    assert payload["job_id"] == "job_9"
    assert payload["next_tool"] == "search_apis"


async def test_completed_job_with_stale_duplicate_error_polls_normally(services: None) -> None:
    """The COMPLETED exemption, on the poll side: a completed import job whose
    ``job.error`` still carries the duplicate fragment reports its normal
    completed payload (result attached), never already_imported."""
    _FakeJobService.statuses = [_job("completed", error=_real_duplicate_job_error())]
    env = _env(["jobs:read"])
    result = await dispatch_tool_call(env, "get_execution_result", {"job_id": "job_9"})
    assert not result.is_error
    payload = _payload(result)
    assert payload["status"] == "completed", "a stale requeue error must not mask completion"
    assert payload["kind"] == "import"
    assert payload["result"] == {"revisions": [_DRAFT_REVISION]}


async def test_execution_jobs_never_trip_the_duplicate_detection(services: None) -> None:
    """The short-circuit is keyed on kind=import: an execution job whose error
    happens to carry the fragment keeps the generic poll payload."""
    _FakeJobService.statuses = [
        _job("failed", error="identical content already exists", kind="execution")
    ]
    env = _env(["jobs:read"])
    result = await dispatch_tool_call(env, "get_execution_result", {"job_id": "job_9"})
    assert not result.is_error
    payload = _payload(result)
    assert payload["status"] == "failed"
    assert payload["kind"] == "execution"


async def test_multi_source_dead_letter_keeps_its_generic_payload_on_the_poll_side(
    services: None,
) -> None:
    """A 2-source import (POST /apis files those) where source[0] hit the
    duplicate but source[1] genuinely failed must NOT be remapped to
    already_imported — the remap is gated on the single-source wrapper, so
    the honest dead-letter payload (with source[1]'s failure) survives."""
    error = _multi_source_duplicate_job_error()
    assert tools_mod._DUPLICATE_CONTENT_FRAGMENT in error, "the masking premise"
    _FakeJobService.statuses = [_job("dead_letter", error=error)]
    env = _env(["jobs:read"])
    result = await dispatch_tool_call(env, "get_execution_result", {"job_id": "job_9"})
    assert not result.is_error
    payload = _payload(result)
    assert payload["status"] == "dead_letter", "a partial duplicate must not mask the failure"
    assert payload["error"] == error
    assert "note" not in payload


async def test_multi_source_dead_letter_never_short_circuits_the_import_tracker(
    services: None,
) -> None:
    """Defense in depth on import_api's own tracker: its filing leg
    (CatalogService.import_entry) always enqueues exactly one source, so a
    multi-source error should be unreachable there — but the same
    single-source gate rides the tracker, so a future multi-source filing
    would surface the honest failed-job arm, never already_imported."""
    _FakeJobService.statuses = [_job("dead_letter", error=_multi_source_duplicate_job_error())]
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error, "a partial duplicate is a failure, not a convergence"
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert payload["job_status"] == "dead_letter"
    # source[1]'s own text sits past the 128-char truncation point; what must
    # survive is the honest multi-source failure, never an already_imported remap.
    assert ALL_SOURCES_FAILED_PREFIX_TEMPLATE.format(count=2) in payload["error"]


# ── promote-leg softness ─────────────────────────────────────────────────────


async def test_promote_without_apis_write_soft_fails_without_calling_the_service(
    services: None,
) -> None:
    """``RevisionService.promote`` enforces no scopes in-process — an
    unguarded call would be a capability escalation over REST. Missing
    ``apis:write`` becomes a per-revision map entry, and the service is never
    touched; the import itself still succeeds."""
    _FakeJobService.statuses = [_job("completed")]
    env = _env(["catalog:import", "jobs:read"])  # no apis:write
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error, "a promote failure is never a hard error"
    payload = _payload(result)
    assert payload["promoted"] == {"rev_1": "promote failed: missing apis:write scope"}
    assert _FakeRevisionService.promotes == []


async def test_org_admin_implies_apis_write_via_the_implication_map(services: None) -> None:
    """The soft-check is ``has_effective_permission``, not a literal
    membership test: org:admin holders promote even though the literal scope
    string is absent from their grants."""
    _FakeJobService.statuses = [_job("completed")]
    env = _env(["org:admin"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error, result.content
    assert _payload(result)["promoted"] == {"rev_1": "live"}


async def test_promote_state_conflict_is_a_soft_map_entry(services: None) -> None:
    """A typed promote error (e.g. the revision is no longer DRAFT — the
    parity no-op path) degrades to a "promote failed: …" entry, never a hard
    error on the import result."""
    _FakeJobService.statuses = [_job("completed")]
    _FakeRevisionService.promote_error = RevisionStateConflictError(
        "rev_1", "imported", ["draft"], "promote"
    )
    env = _env(["catalog:import", "jobs:read", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error
    promoted = _payload(result)["promoted"]
    assert promoted["rev_1"].startswith("promote failed: ")
    assert "imported" in promoted["rev_1"], "the conflict's actual state must reach the map"
    assert "draft" in promoted["rev_1"], "…and the state promote expected"


async def test_non_draft_revisions_map_to_their_state_verbatim(services: None) -> None:
    """Catalog imports land IMPORTED (already live) on this backend — the
    promote leg is a runtime no-op that reports the state, exactly like Go's
    ``promoteRevisions`` skip."""
    _FakeJobService.statuses = [_job("completed")]
    _FakeJobResultService.body = {"revisions": [{**_DRAFT_REVISION, "state": "imported"}]}
    env = _env(["catalog:import", "jobs:read", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error
    assert _payload(result)["promoted"] == {"rev_1": "imported"}
    assert _FakeRevisionService.promotes == []


async def test_malformed_revision_entries_get_explicit_promote_failures(services: None) -> None:
    """A non-dict row or a revision_id-less dict never vanishes silently —
    each gets an explicit index-keyed "promote failed: malformed revision
    entry" map entry, and the promote service is never called for it."""
    _FakeJobService.statuses = [_job("completed")]
    _FakeJobResultService.body = {"revisions": ["not-a-dict", {"state": "draft"}]}
    env = _env(["catalog:import", "jobs:read", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error
    assert _payload(result)["promoted"] == {
        "revision[0]": "promote failed: malformed revision entry",
        "revision[1]": "promote failed: malformed revision entry",
    }
    assert _FakeRevisionService.promotes == []


# ── DB-gate refusal ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("blocked", ["registry", "admin"])
async def test_db_gate_refusal_is_a_soft_internal_error(services: None, blocked: str) -> None:
    """import_api needs BOTH the registry DB (the catalog) and the admin DB
    (the job rides it): a deployment shape missing either refuses softly —
    INTERNAL_ERROR, before any filing — never an unhandled crash. (The
    happy-path fixtures never exercise this arm: a MagicMock ctx is always
    truthy.)"""
    env = _env(["catalog:import", "jobs:read"])
    cast(MagicMock, env.ctx).is_db_allowed.side_effect = lambda db: db != blocked
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert "not available on this deployment" in payload["error"]
    assert _FakeCatalogService.filed == [], "the DB gate must refuse before filing"


# ── filing-time error arms ───────────────────────────────────────────────────


async def test_unknown_catalog_entry_is_resolve_failed(services: None) -> None:
    _FakeCatalogService.import_error = CatalogEntryNotFoundError("nope/nothing")
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "nope/nothing"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert payload["next_tool"] == "search_catalog"
    assert "nope/nothing" in payload["error"]


async def test_overlay_supersede_refusal_maps_to_broker_denied(services: None) -> None:
    """The in-process arm Go never sees distinctly: superseding a confirmed
    overlay needs overlays:confirm — mapped honestly, not folded into a
    generic import failure."""
    _FakeCatalogService.import_error = OverlaySupersedeForbiddenError(
        "googleapis.com/sheets", "ovl_1"
    )
    env = _env(["catalog:import", "jobs:read"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert "overlays:confirm" in payload["error"]
    assert "operator" in payload["actionable_step"]


async def test_result_fetch_failure_points_at_the_job_poll(services: None) -> None:
    """A completed job whose result fetch fails surfaces with the job_id —
    the model polls get_execution_result rather than re-importing blind."""
    _FakeJobService.statuses = [_job("completed")]
    _FakeJobResultService.error = RuntimeError("result store down")
    env = _env(["catalog:import", "jobs:read", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert payload["job_id"] == "job_9"
    assert payload["next_tool"] == "get_execution_result"
