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

import json
from datetime import UTC, datetime
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.tools as tools_mod
from jentic_one.admin.services.schemas.jobs import JobResultView, JobView
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call, validate_api_id
from jentic_one.registry.services.errors import (
    CatalogEntryNotFoundError,
    OverlaySupersedeForbiddenError,
    RevisionStateConflictError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType

_NOW = datetime(2026, 9, 10, tzinfo=UTC)

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
        await tools_mod.handle_import_api(_env(["catalog:import"]), {})
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
        await tools_mod.handle_import_api(_env(["catalog:import"]), {"api_id": bad})
    assert "search_catalog" in str(err.value)
    assert _FakeCatalogService.filed == []


def test_umbrella_api_id_with_literal_slash_stays_accepted() -> None:
    validate_api_id("googleapis.com/sheets")  # must not raise


# ── scope gate (Go: 403PointsAtRequestAccessForScope) ────────────────────────


async def test_missing_scope_is_broker_denied_pointing_at_request_access(
    services: None,
) -> None:
    """The same gate as POST /catalog/{api_id}:import (catalog:import); the
    403 points at request_access even though PR B hasn't landed yet — the
    pinned description already does, and the pointer is the contract."""
    result = await dispatch_tool_call(_env([]), "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert payload["next_tool"] == "request_access"
    assert "catalog:import" in payload["error"]
    assert "catalog:import" in payload["actionable_step"]
    assert _FakeCatalogService.filed == []


# ── three-outcome tracking (Go: CompletesAndPromotes / StillRunning / PollFailure) ──


async def test_completed_import_promotes_and_returns_the_go_envelope(services: None) -> None:
    """The happy path: track to completion, fetch the result, promote the
    draft revision live — {schema_version, job_id, status, revisions,
    promoted} with the instance stamp joined. Uses the ``id`` alias and an
    umbrella api_id with its literal slash, like the Go test."""
    _FakeJobService.statuses = [_job("running"), _job("completed")]
    env = _env(["catalog:import", "apis:write"])
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
    env = _env(["catalog:import"])
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
    env = _env(["catalog:import"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error, "a job-poll failure must never look like still-running"
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert payload["job_id"] == "job_9"
    assert payload["next_tool"] == "get_execution_result"
    assert "job store down" in payload["error"]


# ── failed-job arm (Go: FailedJobIsSoftError; DEAD_LETTER is terminal) ───────


@pytest.mark.parametrize("terminal", ["failed", "dead_letter", "cancelled"])
async def test_failed_job_is_internal_error_with_job_extras(services: None, terminal: str) -> None:
    _FakeJobService.statuses = [_job(terminal, error="spec fetch failed")]
    env = _env(["catalog:import"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert "spec fetch failed" in payload["error"]
    assert payload["job_id"] == "job_9"
    assert payload["job_status"] == terminal
    assert payload["next_tool"] == "search_catalog"


# ── duplicate-content short-circuit ──────────────────────────────────────────


async def test_duplicate_content_short_circuits_on_a_non_terminal_requeued_job(
    services: None,
) -> None:
    """The worker treats a duplicate ingest as retryable (backoff to
    DEAD_LETTER, ~30s+), so the handler matches the stable leading fragment of
    ``job.error`` on EVERY poll — including a non-terminal requeued state —
    and short-circuits instead of burning the wait budget."""
    duplicate_error = (
        "attempt 1/5 failed: all 1 import source(s) failed: source[0]: A revision with "
        "identical content already exists for this API"  # truncated at 128 chars
    )
    _FakeJobService.statuses = [_job("queued", error=duplicate_error)]
    env = _env(["catalog:import"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error, "already-present content is a convergence, not a failure"
    payload = _payload(result)
    assert payload["schema_version"] == "1"
    assert payload["job_id"] == "job_9"
    assert payload["status"] == "already_imported"
    assert "already present" in payload["note"]
    assert payload["next_tool"] == "search_apis"
    assert _FakeJobService.polls == 1, "the short-circuit must not keep polling"


async def test_duplicate_content_dead_letter_via_get_execution_result(services: None) -> None:
    """A duplicate import job polled later reports already_imported, never a
    scary dead_letter — the same detection, placed before the generic payload
    assembly in handle_get_execution_result."""
    _FakeJobService.statuses = [
        _job("dead_letter", error="… A revision with identical content already exists for …")
    ]
    env = _env(["jobs:read"])
    result = await dispatch_tool_call(env, "get_execution_result", {"job_id": "job_9"})
    assert not result.is_error
    payload = _payload(result)
    assert payload["status"] == "already_imported"
    assert payload["job_id"] == "job_9"
    assert payload["next_tool"] == "search_apis"


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


# ── promote-leg softness ─────────────────────────────────────────────────────


async def test_promote_without_apis_write_soft_fails_without_calling_the_service(
    services: None,
) -> None:
    """``RevisionService.promote`` enforces no scopes in-process — an
    unguarded call would be a capability escalation over REST. Missing
    ``apis:write`` becomes a per-revision map entry, and the service is never
    touched; the import itself still succeeds."""
    _FakeJobService.statuses = [_job("completed")]
    env = _env(["catalog:import"])  # no apis:write
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
    env = _env(["catalog:import", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error
    promoted = _payload(result)["promoted"]
    assert promoted["rev_1"].startswith("promote failed: ")
    assert "promote" in promoted["rev_1"]


async def test_non_draft_revisions_map_to_their_state_verbatim(services: None) -> None:
    """Catalog imports land IMPORTED (already live) on this backend — the
    promote leg is a runtime no-op that reports the state, exactly like Go's
    ``promoteRevisions`` skip."""
    _FakeJobService.statuses = [_job("completed")]
    _FakeJobResultService.body = {"revisions": [{**_DRAFT_REVISION, "state": "imported"}]}
    env = _env(["catalog:import", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert not result.is_error
    assert _payload(result)["promoted"] == {"rev_1": "imported"}
    assert _FakeRevisionService.promotes == []


# ── filing-time error arms ───────────────────────────────────────────────────


async def test_unknown_catalog_entry_is_resolve_failed(services: None) -> None:
    _FakeCatalogService.import_error = CatalogEntryNotFoundError("nope/nothing")
    env = _env(["catalog:import"])
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
    env = _env(["catalog:import"])
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
    env = _env(["catalog:import", "apis:write"])
    result = await dispatch_tool_call(env, "import_api", {"api_id": "googleapis.com/sheets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert payload["job_id"] == "job_9"
    assert payload["next_tool"] == "get_execution_result"
