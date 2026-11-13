"""Tool handlers for the mounted MCP app — the pinned tool surface, in-process.

Each handler is the Python twin of the Go stdio server's handler for the same
tool (``cli/internal/cli/api/mcp_tools.go`` / ``mcp_discovery.go`` /
``mcp_catalog.go`` / ``mcp_execute.go`` / ``mcp_request_connection.go``): the
same argument normalization
(aliases + coercions), the same envelope keys, and the same coded soft-error
mapping — the golden contract tests replay identical tool calls against both
implementations. Where the Go server calls REST routes, these handlers call
the owning services **in-process** (registry search/inspect/catalog, admin
jobs, auth identity, connect sessions); the execute family proxies to the
broker server-side
(the broker stays MCP-free).

Permission enforcement mirrors the REST routes fronted: the same
``required_permissions`` the routers declare, checked against the resolved
identity through the same ``compute_effective`` expansion + ``org:admin``
bypass ``get_current_identity`` applies. A permission failure maps exactly like
the Go client's wire 403 (``mcpCoded``): NOT_AUTHENTICATED with the get_started
pointer — except ``search_catalog`` and ``import_api``, whose 403s are
missing-permission facts routed to the operator (BROKER_DENIED with an
ask-your-operator step, the Go special case).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid as uuid_mod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import mcp.types as mcp_types
import structlog
from jentic.problem_details import Forbidden, Unauthorized
from mcp.shared.exceptions import MCPError

from jentic_one.admin.services.errors import (
    HeldExecutionNotFoundError,
    HeldExecutionResultNotReadyError,
    JobNotCompletedError,
    JobNotFoundError,
)
from jentic_one.admin.services.held_execution_service import HeldExecutionService
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.schemas.jobs import JobResultView, JobView
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.web.routers.identity import _resolve_agent, _resolve_user
from jentic_one.mcp import approvals
from jentic_one.mcp import execute as ex
from jentic_one.mcp.access import (
    _ambiguous_vendor_actionable as _ambiguous_vendor_actionable,
)
from jentic_one.mcp.access import (
    handle_amend_request as handle_amend_request,
)
from jentic_one.mcp.access import (
    handle_inspect_api as handle_inspect_api,
)
from jentic_one.mcp.access import (
    handle_request_access_change as handle_request_access_change,
)
from jentic_one.mcp.access import (
    handle_request_connection as handle_request_connection,
)
from jentic_one.mcp.access import (
    handle_show_access as handle_show_access,
)
from jentic_one.mcp.call_context import (
    _INVALID_PARAMS,
)
from jentic_one.mcp.call_context import (
    CallEnv as CallEnv,
)
from jentic_one.mcp.call_context import (
    Handler as Handler,
)
from jentic_one.mcp.call_context import (
    ParamSpec as ParamSpec,
)
from jentic_one.mcp.call_context import (
    ToolCallResult as ToolCallResult,
)
from jentic_one.mcp.call_context import (
    invalid_params as invalid_params,
)
from jentic_one.mcp.call_context import (
    normalize_tool_args as normalize_tool_args,
)
from jentic_one.mcp.call_context import (
    require_password_current as require_password_current,
)
from jentic_one.mcp.call_context import (
    require_permissions as require_permissions,
)
from jentic_one.mcp.envelopes import (
    CODE_BROKER_DENIED,
    CODE_INTERNAL_ERROR,
    CODE_MISSING_ARGUMENT,
    CODE_NOT_AUTHENTICATED,
    CODE_RESOLVE_FAILED,
    CODE_TRANSPORT_ERROR,
    SCHEMA_VERSION,
    ToolError,
    soft_error_result,
    tool_result,
)
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.errors import (
    ArchivedRevisionPinError,
    CatalogEntryNotFoundError,
    CatalogUnavailableError,
    InvalidApiFilterError,
    OverlaySupersedeForbiddenError,
    SearchUnavailableError,
)
from jentic_one.registry.services.import_service import (
    ALL_SOURCES_FAILED_PREFIX_TEMPLATE,
    SOURCE_FAILURE_PREFIX_TEMPLATE,
)
from jentic_one.registry.services.inspect.models import SUMMARY_LOAD_OPTIONS
from jentic_one.registry.services.inspect.service import InspectService
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.registry.services.search_service import SearchService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permissions import has_effective_permission
from jentic_one.shared.context import Context
from jentic_one.shared.discovery.errors import OperationNotFoundError
from jentic_one.shared.jobs.hold import (
    HELD_AGENT_DIRECTIVE,
    STILL_HELD_AGENT_DIRECTIVE,
    review_page_path,
)
from jentic_one.shared.pagination import InvalidCursorError, InvalidSearchCursorError
from jentic_one.shared.redaction import redact_value

logger = structlog.get_logger()

_OPERATION_ID_SPEC = ParamSpec("operation_id", "string", ("id", "uuid"))


def _parse_method_url(target: str) -> tuple[str, str] | None:
    """``METHOD:https://…`` / ``METHOD https://…`` (Go: ``parseMethodURL``)."""
    stripped = target.strip()
    if " " in stripped:
        first, rest = stripped.split(" ", 1)
    elif ":" in stripped:
        first, rest = stripped.split(":", 1)
    else:
        return None
    rest = rest.strip()
    if not rest.startswith(("http://", "https://")):
        return None
    method = first.upper()
    # The full method set OpenAPI ingestion accepts (Go: ``parseMethodURL``) —
    # a discovered TRACE operation's contract is readable; execute refuses it.
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}:
        return None
    return method, rest


# ── whoami ────────────────────────────────────────────────────────────────────


async def handle_whoami(env: CallEnv, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    """GET /me passthrough (Go: ``handleWhoami``), resolved in-process."""
    request: Any = _StubRequest("/mcp")
    sub = env.identity.sub
    try:
        if sub.startswith("usr_"):
            me: Any = await _resolve_user(request, env.identity, UserService(env.ctx))
        elif sub.startswith("agnt_"):
            me = await _resolve_agent(request, env.identity, AgentService(env.ctx))
        else:
            raise ToolError(
                CODE_NOT_AUTHENTICATED,
                "unrecognised actor type in token subject",
            )
    except (Unauthorized, Forbidden) as exc:
        raise ToolError(
            CODE_NOT_AUTHENTICATED,
            "the control plane rejected this agent's credentials "
            f"({getattr(exc, 'detail', exc)}) — the identity may have been revoked "
            "or disabled",
            # Lane-aware prose (#1327 deferred work): the stdio taxonomy
            # points this code at get_started, unserved here — and whoami
            # itself just failed, so no served tool can help. Re-root the
            # recovery at the operator.
            actionable="relay this to your human operator: this connection's "
            "credentials and the agent's status are managed in the Jentic One "
            "dashboard",
        ) from None
    payload = me.model_dump(mode="json")
    payload["schema_version"] = SCHEMA_VERSION
    return tool_result(env.ctx, payload)


class _StubRequest:
    """The minimal ``Request`` shim the /me resolvers read (``url.path`` only)."""

    def __init__(self, path: str) -> None:
        self.url = type("_URL", (), {"path": path})()


# ── search_apis ───────────────────────────────────────────────────────────────

_SEARCH_APIS_PARAMS = [
    ParamSpec("query", "string"),
    ParamSpec("apis", "string_list", ("api",)),
    ParamSpec("limit", "int"),
    ParamSpec("cursor", "string", ("next_cursor",)),
]


async def handle_search_apis(env: CallEnv, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    """POST /search in-process (Go: ``handleSearchAPIs``) — same envelope."""
    args = normalize_tool_args(arguments, _SEARCH_APIS_PARAMS)
    query = args.get("query", "")
    if not query:
        raise invalid_params(
            'search_apis requires a non-empty "query" string, e.g. {"query": "create github issue"}'
        )
    limit = args.get("limit", 0)
    if limit and not 1 <= limit <= 100:
        raise invalid_params(f"limit must be between 1 and 100, got {limit}")
    require_permissions(env.identity, ["apis:read"])
    _require_db(env.ctx, "registry", "search")

    try:
        page = await SearchService(env.ctx).search(
            query=query,
            apis=args.get("apis"),
            revision_pins=None,
            limit=int(limit) if limit else 10,
            cursor=args.get("cursor") or None,
        )
    except SearchUnavailableError as exc:
        raise ToolError(CODE_INTERNAL_ERROR, str(exc)) from None
    except (InvalidSearchCursorError, InvalidApiFilterError, ArchivedRevisionPinError) as exc:
        raise invalid_params(str(exc)) from None

    hits = [
        {
            "type": "operation",
            "api": {
                "vendor": r.api.vendor,
                "name": r.api.name,
                "version": r.api.version,
                "host": r.api.host or "",
            },
            "operation_id": r.operation_id,
            "method": r.method,
            "url": r.url,
            "target": r.target,
            "name": r.name or "",
            "description": r.description or "",
            "relevance_score": r.relevance_score,
            "_links": {"inspect": f"{env.base_url}{r.inspect_link}"},
        }
        for r in page.data
    ]
    next_cursor = page.next_cursor or ""
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "data": hits,
        "has_more": next_cursor != "",
    }
    if next_cursor:
        payload["next_cursor"] = next_cursor
    return tool_result(env.ctx, payload)


def _require_db(ctx: Context, db: str, what: str) -> None:
    """Soft-fail a tool whose backing DB is not wired into this process shape."""
    if not ctx.is_db_allowed(db):
        raise ToolError(
            CODE_INTERNAL_ERROR,
            f"{what} is not available on this deployment (the {db} surface is not "
            "co-located with the control plane)",
        )


# ── inspect_operation ─────────────────────────────────────────────────────────

_INSPECT_PARAMS = [_OPERATION_ID_SPEC, ParamSpec("revision", "string")]


async def handle_inspect_operation(
    env: CallEnv, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """GET /inspect in-process (Go: ``handleInspectOperation``)."""
    args = normalize_tool_args(arguments, _INSPECT_PARAMS)
    target = args.get("operation_id", "")
    if not target:
        raise invalid_params(
            'inspect_operation requires "operation_id" (aliases: "id", "uuid"): '
            'a METHOD:url pair like "GET:https://api.example.com/v1/things" '
            "(a search_apis hit's target)"
        )
    require_permissions(env.identity, ["apis:read"])
    payload = await _inspect_document(env, target, args.get("revision", ""))
    payload["schema_version"] = SCHEMA_VERSION
    return tool_result(env.ctx, payload)


async def _inspect_document(env: CallEnv, target: str, revision: str) -> dict[str, Any]:
    """Resolve one inspect target to its full JSON document, in-process.

    The 404 → RESOLVE_FAILED mapping (with the search_apis pointer) matches
    the Go tool and the execute resolve path.
    """
    _require_db(env.ctx, "registry", "inspect")
    not_found = ToolError(
        CODE_RESOLVE_FAILED,
        f"operation {target!r} not found",
        actionable="Call search_apis with a natural-language description of what you "
        "want to do, then inspect the target from one of its hits.",
        next_tool="search_apis",
    )
    rev_id: uuid_mod.UUID | None = None
    if revision:
        try:
            rev_id = uuid_mod.UUID(revision)
        except ValueError:
            raise invalid_params(f"invalid revision id {revision!r}") from None
    try:
        async with env.ctx.registry_db.session() as session:
            svc = InspectService(session, base_url=env.base_url)
            if (pair := _parse_method_url(target)) is not None:
                method, url = pair
                lookup = await URLLookupService(session).resolve(
                    method=method, url=url, revision_id=rev_id
                )
                if lookup is None:
                    raise not_found
                result = await svc.inspect(
                    operation_id=lookup.operation_id,
                    method=method,
                    url=url,
                    load_options=SUMMARY_LOAD_OPTIONS,
                )
            else:
                result = await svc.inspect_by_id(
                    operation_id=target, load_options=SUMMARY_LOAD_OPTIONS
                )
    except OperationNotFoundError:
        raise not_found from None
    doc: dict[str, Any] = result.model_dump(mode="json", by_alias=True)
    return doc


# ── search_catalog ────────────────────────────────────────────────────────────

_SEARCH_CATALOG_PARAMS = [
    ParamSpec("query", "string", ("q",)),
    ParamSpec("limit", "int"),
    ParamSpec("cursor", "string", ("next_cursor",)),
]


async def handle_search_catalog(
    env: CallEnv, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """GET /catalog in-process (Go: ``handleSearchCatalog``) — same envelope."""
    args = normalize_tool_args(arguments, _SEARCH_CATALOG_PARAMS)
    limit = args.get("limit", 0)
    if limit and not 1 <= limit <= 200:
        raise invalid_params(f"limit must be between 1 and 200, got {limit}")
    try:
        require_permissions(env.identity, ["capabilities:read"])
    except ToolError as exc:
        # The Go special case: a 403 on THIS route is the missing
        # capabilities:read permission — an access gap the operator closes with a
        # dashboard grant, not a revoked identity. The wire
        # error rides as the message tail, like Go's ``: %v`` (mcp_catalog.go).
        raise ToolError(
            CODE_BROKER_DENIED,
            f"reading the catalog requires the capabilities:read permission: {exc}",
            actionable="Ask your human operator to grant this agent the "
            "capabilities:read permission in the dashboard, then retry "
            "search_catalog once they confirm.",
        ) from None
    _require_db(env.ctx, "registry", "the catalog")

    try:
        page = await CatalogService(env.ctx).list_all(
            q=args.get("query") or None,
            cursor=args.get("cursor") or None,
            limit=limit or 50,
        )
    except InvalidCursorError:
        raise invalid_params("invalid pagination cursor") from None

    entries = []
    for view in page.data:
        self_link = f"{env.base_url}/catalog/{view.api_id}"
        entries.append(
            {
                "api_id": view.api_id,
                "vendor": view.vendor,
                "path": view.path,
                "spec_url": view.spec_url,
                "registered": view.registered,
                "update_available": view.update_available,
                "_links": {
                    "self": self_link,
                    "operations": f"{self_link}/operations",
                    "import": f"{self_link}:import",
                    "github": view.github_url or "",
                },
            }
        )
    next_cursor = (page.next_cursor or "") if page.has_more else ""
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "data": entries,
        "catalog_total": page.catalog_total,
        "registered_count": page.registered_count,
        "outdated_count": page.outdated_count,
        "manifest_age_seconds": page.manifest_age_seconds,
        "has_more": next_cursor != "",
    }
    if next_cursor:
        payload["next_cursor"] = next_cursor
    return tool_result(env.ctx, payload)


# ── import_api ────────────────────────────────────────────────────────────────

_IMPORT_API_PARAMS = [ParamSpec("api_id", "string", ("id", "api"))]

#: How long import_api tracks the import job in-process before handing the
#: still-running job back to the model (Go: ``defaultImportWaitBudget``). The
#: mount applies no per-call deadline of its own, so this constant also bounds
#: how long the blocking handler holds the ASGI request open — sized inside
#: typical MCP client tool timeouts. A plain module constant beside
#: ``_EXECUTE_TIMEOUT_SECONDS``'s pattern (tests inject via monkeypatch, like
#: the Go side's ``importWaitBudget``); no config knob until someone needs one.
_IMPORT_WAIT_BUDGET_SECONDS = 15.0

#: Grace on top of the wait budget for the hard per-leg ceiling
#: (``asyncio.timeout`` around the filing call + the whole track-and-promote
#: tail). The budget alone only gates BETWEEN polls — a single hung poll /
#: result fetch / promote would hold the ASGI request open indefinitely — and
#: the filing leg's catalog read can lazily refresh the upstream manifest
#: (bounded only by ``ingest.fetch_timeout_s``, ~30s default). The ceiling
#: turns either into a soft error (unknown state, never "still running").
_IMPORT_WAIT_GRACE_SECONDS = 5.0

#: In-process poll cadence for the job tracker: the first poll is immediate,
#: then back off from the step to the max (Go: ``App.PollCadence``).
_IMPORT_POLL_STEP_SECONDS = 0.25
_IMPORT_POLL_MAX_SECONDS = 2.0

#: The stable leading fragment of ``DuplicateRevisionError``'s message
#: (``registry/ingest/errors.py`` — also what ``import_service.py`` mints for an
#: identical-content IntegrityError at commit). A lost race for the one-active
#: revision slot reads differently (``ACTIVE_REVISION_CONFLICT_ERROR``): the
#: competing revision can hold other content, so it never matches here.
#: ``job.error`` is truncated to 128 chars after a ~42-char wrapper prefix, so
#: only the message head survives: match this fragment, never the full message.
_DUPLICATE_CONTENT_FRAGMENT = "identical content already exists"

#: The exact ``job.error`` head of a SINGLE-source import whose one source
#: failed (``ImportHandler``'s wrapper templates rendered for one source):
#: ``"all 1 import source(s) failed: source[0]: "``. The duplicate-content
#: remap keys on it because only a job whose ENTIRE failure is the duplicate
#: may report ``already_imported``: ``POST /apis`` enqueues multi-source
#: ``JobKind.IMPORT`` jobs, and on those a duplicate source[0] must never mask
#: a genuine source[1] failure. ``CatalogService.import_entry`` (import_api's
#: filing leg) always files exactly one source, so the import_api tracker can
#: never see a multi-source job — the gate rides there too as defense in depth.
_SINGLE_SOURCE_IMPORT_FAILURE_PREFIX = ALL_SOURCES_FAILED_PREFIX_TEMPLATE.format(
    count=1
) + SOURCE_FAILURE_PREFIX_TEMPLATE.format(index=0)

#: Job statuses that end the tracking loop (Go: ``catJobCompleted`` /
#: ``catJobFailed`` / ``catJobCancelled`` / ``catJobDeadLetter`` — dead_letter
#: IS terminal: the worker's retry-backoff ladder parks poison jobs there).
_JOB_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "dead_letter"})

#: the one non-failure terminal status: its result document rides the poll
#: payload, and it is exempt from the duplicate-content remap (the worker
#: clears ``job.error`` on completion; the exemption keeps that independent of
#: the worker).
_JOB_COMPLETED = "completed"

#: the job kind the catalog import loop rides (``JobKind.IMPORT``).
_JOB_KIND_IMPORT = "import"

#: execution jobs that end ``failed`` may carry their problem body as the
#: result (a denied or expired held execution).
_JOB_KIND_EXECUTION = "execution"
_JOB_FAILED = "failed"


def validate_api_id(api_id: str) -> None:
    """Syntactic guard on the catalog entry id (Go: ``validateAPIID``).

    The Go client splices the api_id into the ``{api_id:path}`` route verbatim,
    so it rejects traversal shapes before the wire. In-process there is no
    route to rewrite, but the error contract must not differ: the same shapes
    are refused as the same correctable protocol error.
    """
    if api_id.startswith("/"):
        raise invalid_params(
            f'invalid api_id {api_id!r}: a leading "/" is not allowed — pass the api_id '
            "from a search_catalog hit verbatim"
        )
    for segment in api_id.split("/"):
        if segment in ("", ".", ".."):
            raise invalid_params(
                f'invalid api_id {api_id!r}: empty, ".", or ".." path segments are not '
                "allowed — pass the api_id from a search_catalog hit verbatim"
            )


def _already_imported_payload(job_id: str) -> dict[str, Any]:
    """The duplicate-content short-circuit envelope (import_api + the job poll).

    The wording says "already present", never "you imported this before": the
    same ``job.error`` fragment also covers a lost concurrent race to store the
    identical content (the ``uq_api_revisions_api_id_spec_digest`` collision),
    where "already imported" means "another import just won" — the recovery
    (search_apis) is identical either way. This mapping is what keeps the pinned description's
    "re-importing converges (idempotent)" true on this backend.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "status": "already_imported",
        "note": "a revision with identical content is already present in the registry "
        "(imported earlier, or a concurrent import just won the race) — nothing new was "
        "imported and nothing needs promoting; find the API's operations with search_apis",
        "next_tool": "search_apis",
    }


def _is_single_source_duplicate_failure(error: str | None) -> bool:
    """True when a job's ENTIRE failure is the duplicate-content collision.

    Keys on the single-source wrapper head (``all 1 import source(s) failed:
    source[0]: ``) AND the duplicate fragment: a multi-source job (``POST
    /apis`` enqueues those) whose source[0] hit the duplicate can still carry a
    genuine source[1] failure inside the truncated ``job.error`` — remapping it
    to ``already_imported`` would mask that failure, so only the single-source
    rendering qualifies. Both halves survive the worker's 128-char truncation
    (the wrapper is ~43 chars, the fragment heads the duplicate message —
    pinned end-to-end by the fragment test).
    """
    return bool(
        error
        and error.startswith(_SINGLE_SOURCE_IMPORT_FAILURE_PREFIX)
        and _DUPLICATE_CONTENT_FRAGMENT in error
    )


async def _track_import_job(
    ctx: Context, job_id: str, *, identity: Identity
) -> tuple[JobView, bool]:
    """Poll the import job briefly; returns ``(job, duplicate_content)``.

    Mirrors Go's ``trackImportJob`` three-outcome contract: a terminal job
    returns with its terminal status, a budget lapse returns the last
    non-terminal status (the caller renders both from ``job.status``), and a
    failing poll PROPAGATES — the job's state is then unknown, and the caller
    must surface that as a failure, never as a clean "still running" result.

    The duplicate-content check runs on EVERY poll, including non-terminal
    requeued states. The worker fails an import whose only failure is the
    duplicate on its first attempt (``NonRetryableJobError``), but a job can
    also sit requeued with the duplicate in ``job.error`` (e.g. its overlay
    recovery raised and is retried), and waiting for a terminal status would
    spend the wait budget on it. A COMPLETED job is exempt: its result is the
    outcome, whatever an earlier attempt wrote to ``job.error`` (the worker
    clears it on completion).
    """
    svc = JobService(ctx)
    deadline = time.monotonic() + _IMPORT_WAIT_BUDGET_SECONDS
    delay = _IMPORT_POLL_STEP_SECONDS  # the first poll is immediate; back off from the step
    while True:
        job = await svc.get_by_id(job_id, identity=identity)
        if job.status != _JOB_COMPLETED and _is_single_source_duplicate_failure(job.error):
            return job, True
        if job.status in _JOB_TERMINAL_STATUSES or time.monotonic() >= deadline:
            return job, False
        await asyncio.sleep(delay)
        delay = min(delay + _IMPORT_POLL_STEP_SECONDS, _IMPORT_POLL_MAX_SECONDS)


async def _promote_revisions(env: CallEnv, revisions: list[Any]) -> dict[str, str]:
    """Promote each imported draft revision live, softly (Go: ``promoteRevisions``).

    Catalog imports normally land ``IMPORTED`` (already live/searchable), so
    this loop is usually a runtime no-op — non-draft revisions map to their
    state verbatim, exactly like Go. For a genuine draft:
    ``RevisionService.promote`` enforces NO permissions in-process (the
    ``apis:write`` gate lives only on the REST route), so the handler
    soft-checks the permission itself — an unguarded call would let a default agent
    actually promote, a capability escalation over REST. The check is
    ``has_effective_permission`` (implication-map expansion), never a literal
    membership test: grants arrive unexpanded and ``org:admin`` implies
    ``apis:write`` only via the implication map. Every failure becomes a
    per-revision ``"promote failed: …"`` entry — never a hard error; a
    malformed row (non-dict, or no ``revision_id``) gets an explicit
    index-keyed entry rather than vanishing silently.
    """
    promoted: dict[str, str] = {}
    can_write = has_effective_permission(env.identity.permissions, "apis:write")
    for idx, rev in enumerate(revisions):
        if not isinstance(rev, dict):
            promoted[f"revision[{idx}]"] = "promote failed: malformed revision entry"
            continue
        revision_id = str(rev.get("revision_id") or "")
        if not revision_id:
            promoted[f"revision[{idx}]"] = "promote failed: malformed revision entry"
            continue
        state = str(rev.get("state") or "")
        if rev.get("held_for_review"):
            # Server-host change guard: the revision changes where the API's bound
            # credentials are sent, so it stays a draft until an organisation admin
            # promotes it. The wording matches HostChangeRequiresOperatorError, since
            # the agent relays this to its operator verbatim.
            promoted[revision_id] = (
                "held for organisation-admin review: the new revision changes the API's "
                "server hosts (making it current requires 'org:admin')"
            )
            continue
        if state != "draft":
            promoted[revision_id] = state
            continue
        if not can_write:
            promoted[revision_id] = "promote failed: missing apis:write permission"
            continue
        api = rev.get("api") or {}
        try:
            await RevisionService(env.ctx).promote(
                str(api.get("vendor") or ""),
                str(api.get("name") or ""),
                str(api.get("version") or ""),
                revision_id,
                identity=env.identity,
            )
        except Exception as exc:  # per-revision softness — mirror Go's posture
            promoted[revision_id] = f"promote failed: {exc}"
            continue
        promoted[revision_id] = "live"
    return promoted


async def handle_import_api(env: CallEnv, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    """POST /catalog/{api_id}:import + the track-and-promote loop, in-process
    (Go: ``handleImportAPI`` + ``trackImportJob`` + ``promoteRevisions``)."""
    args = normalize_tool_args(arguments, _IMPORT_API_PARAMS)
    api_id = args.get("api_id", "")
    if not api_id:
        raise invalid_params(
            'import_api requires "api_id" (aliases: "id", "api"): a catalog entry id '
            'from a search_catalog hit, e.g. "googleapis.com/sheets"'
        )
    validate_api_id(api_id)
    try:
        require_permissions(env.identity, ["catalog:import"])
    except ToolError as exc:
        # The Go special case (importAPIError's 403 arm): a 403 on THIS route
        # is the missing catalog:import permission — an access gap the operator
        # closes with a dashboard grant, not a revoked identity.
        raise ToolError(
            CODE_BROKER_DENIED,
            f"importing a cataloged API requires the catalog:import permission: {exc}",
            actionable="Ask your human operator to grant this agent the "
            "catalog:import permission in the dashboard, then retry import_api "
            "once they confirm.",
        ) from None
    _require_db(env.ctx, "registry", "the catalog")
    _require_db(env.ctx, "admin", "import job tracking")

    job_id: str | None = None
    try:
        # The hard per-leg ceiling, from the filing call onward. Two legs need
        # it: the filing leg's catalog read can lazily refresh the upstream
        # manifest (bounded only by ``ingest.fetch_timeout_s``, ~30s default),
        # and the wait budget in the tail only gates BETWEEN polls — a hung
        # poll / result fetch / promote would hold the ASGI request open
        # indefinitely. One ceiling over both bounds the whole request inside
        # typical MCP client tool timeouts.
        async with asyncio.timeout(_IMPORT_WAIT_BUDGET_SECONDS + _IMPORT_WAIT_GRACE_SECONDS):
            job_id = await _file_import(env, api_id)

            try:
                require_permissions(env.identity, ["jobs:read"])
            except ToolError:
                # In-process tracking rides the same jobs:read gate the Go
                # client's poll leg does (GET /jobs/{id}). Absent the permission,
                # degrade to the filed-{job_id, status} envelope — NOT an
                # error: the filing succeeded, only the courtesy tracking is
                # off the table (REST parity: the poll would have been
                # refused, the import would not). Both permissions ride
                # DEFAULT_AGENT_PERMISSIONS, so defaults are unaffected.
                return tool_result(
                    env.ctx,
                    {"schema_version": SCHEMA_VERSION, "job_id": job_id, "status": "queued"},
                )

            return await _finish_import(env, api_id, job_id)
    except TimeoutError as exc:
        if job_id is None:
            # The ceiling lapsed inside the FILING leg: whether the enqueue
            # committed is unknowable from here (the lapse may have hit the
            # lazy manifest refresh before it, or the enqueue itself). A
            # re-import converges either way — the backend de-duplicates
            # identical content — so this is a retryable transport-class
            # lapse (Go's transportSoftError posture), never "stop, bug".
            raise ToolError(
                CODE_TRANSPORT_ERROR,
                f"filing the import of {api_id} timed out — the import may or may not "
                "have been filed",
                actionable="Re-calling import_api with the same api_id is safe "
                "(re-importing converges), or re-check the entry with search_catalog "
                "first.",
                next_tool="search_catalog",
                extra={"retryable": True},
            ) from exc
        # The ceiling lapsed in the tracking tail: maps to the poll-failure
        # arm — the job's state is UNKNOWN (a leg hung mid-flight), never a
        # clean "still running".
        raise ToolError(
            CODE_INTERNAL_ERROR,
            f"import of {api_id} was filed as job {job_id}, but tracking it timed out mid-poll",
            actionable="The job's state is unknown — prefer polling this job with "
            "get_execution_result (using the job_id in this result) over re-importing "
            "(a re-import of the same api_id converges, but files a fresh job).",
            next_tool="get_execution_result",
            extra={"job_id": job_id},
        ) from exc


async def _file_import(env: CallEnv, api_id: str) -> str:
    """The filing leg: POST /catalog/{api_id}:import in-process, error-mapped.

    Runs under the caller's hard ceiling — ``import_entry``'s catalog read can
    lazily refresh the upstream manifest (``_refresh_if_stale``), a fetch
    bounded only by ``ingest.fetch_timeout_s``.
    """
    try:
        return await CatalogService(env.ctx).import_entry(api_id, env.identity)
    except CatalogEntryNotFoundError:
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"catalog entry {api_id!r} not found",
            actionable="Call search_catalog with a keyword for the API you need and use "
            "the api_id from one of its hits.",
            next_tool="search_catalog",
        ) from None
    except OverlaySupersedeForbiddenError as exc:
        # An arm Go never sees distinctly: re-importing would supersede an
        # operator's confirmed overlay, which requires overlays:confirm.
        # Mapped honestly, never folded into a generic "import failed".
        raise ToolError(
            CODE_BROKER_DENIED,
            str(exc),
            actionable="Relay this to your human operator: superseding a confirmed "
            "overlay is an operator decision (overlays:confirm), not a permission an agent "
            "should request for itself.",
        ) from None
    except CatalogUnavailableError as exc:
        raise ToolError(CODE_INTERNAL_ERROR, f"catalog not available: {exc}") from None


async def _finish_import(env: CallEnv, api_id: str, job_id: str) -> mcp_types.CallToolResult:
    """The track-and-promote tail of import_api (runs under the hard ceiling)."""
    try:
        job, duplicate_content = await _track_import_job(env.ctx, job_id, identity=env.identity)
    except Exception as exc:
        # Go's poll-failure arm: a failing job poll is UNKNOWN state — never a
        # clean "still running" result (which would send the model into a
        # re-import loop against a backend that de-duplicates nothing).
        # Surface the failure with the job_id so the model keeps watching
        # THIS job instead of filing another.
        raise ToolError(
            CODE_INTERNAL_ERROR,
            f"import of {api_id} was filed as job {job_id}, but polling the job failed: {exc}",
            actionable="The job's state is unknown — prefer polling this job with "
            "get_execution_result (using the job_id in this result) over re-importing "
            "(a re-import of the same api_id converges, but files a fresh job).",
            next_tool="get_execution_result",
            extra={"job_id": job_id},
        ) from exc
    if duplicate_content:
        # Identical content already present (or a concurrent import of the
        # same content just won): short-circuit honestly with already_imported
        # instead of reporting the job's failed status.
        return tool_result(env.ctx, _already_imported_payload(job_id))
    if job.status not in _JOB_TERMINAL_STATUSES:
        # Budget lapsed with the job still running: a normal result — the
        # model converges by re-calling import_api (idempotent) or watches
        # the job with get_execution_result. Never block out the call.
        return tool_result(
            env.ctx,
            {"schema_version": SCHEMA_VERSION, "job_id": job_id, "status": job.status},
        )
    if job.status != _JOB_COMPLETED:
        raise ToolError(
            CODE_INTERNAL_ERROR,
            f"import of {api_id} {job.status}: {job.error or 'no detail'}",
            actionable="Re-check the api_id against a search_catalog hit and retry "
            "import_api; if the import keeps failing, relay this error to your operator.",
            next_tool="search_catalog",
            extra={"job_id": job_id, "job_status": job.status},
        )

    try:
        view = await JobResultService(env.ctx).get(job_id, identity=env.identity)
    except Exception as exc:
        raise ToolError(
            CODE_INTERNAL_ERROR,
            f"import job {job_id} completed but its result could not be fetched: {exc}",
            actionable="Poll this job with get_execution_result using the job_id in this result.",
            next_tool="get_execution_result",
            extra={"job_id": job_id},
        ) from exc
    revisions = view.body.get("revisions", []) if isinstance(view.body, dict) else []
    promoted = await _promote_revisions(env, revisions)
    return tool_result(
        env.ctx,
        {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "status": job.status,
            "revisions": revisions,
            "promoted": promoted,
        },
    )


# ── execute / execute_read ────────────────────────────────────────────────────

_EXECUTE_PARAMS = [
    _OPERATION_ID_SPEC,
    ParamSpec("inputs", "object", ("params", "parameters")),
    ParamSpec("headers", "object"),
    ParamSpec("body", "json", ("data",)),
    ParamSpec("revision", "string"),
    ParamSpec("idempotency_key", "string"),
]


async def handle_execute(env: CallEnv, arguments: dict[str, Any]) -> ToolCallResult:
    return await _execute_tool(env, arguments, read_only_variant=False)


async def handle_execute_read(env: CallEnv, arguments: dict[str, Any]) -> ToolCallResult:
    return await _execute_tool(env, arguments, read_only_variant=True)


async def _execute_tool(
    env: CallEnv, arguments: dict[str, Any], *, read_only_variant: bool
) -> ToolCallResult:
    """The shared execute/execute_read handler (Go: ``executeTool``).

    Resolve → build → send → classify, with the broker leg proxied
    control-plane→broker server-side: the caller's own bearer
    rides the hop, so the broker enforces identity/bindings/rules exactly as
    if the agent had dialed it directly. The held (202) envelope passes
    through as a normal result — the model polls with get_execution_result
    and never re-sends.
    """
    tool_name = "execute_read" if read_only_variant else "execute"
    if env.request_state is not None:
        # A multi-round-trip retry of a held call: never re-send it — read the
        # job the sealed state names.
        return await _resume_held(env, env.request_state)
    args = normalize_tool_args(arguments, _EXECUTE_PARAMS)
    target = args.get("operation_id", "")
    if not target:
        raise invalid_params(
            f'{tool_name} requires "operation_id" (aliases: "id", "uuid"): a METHOD:url '
            'pair like "GET:https://api.example.com/v1/things" (a search_apis hit\'s target)'
        )
    body_value = args.get("body")
    if read_only_variant and body_value is not None:
        raise invalid_params(
            "execute_read never sends a request body; use the execute tool for body-carrying calls"
        )

    scheme, host = ex.resolve_broker_target(env.ctx.config.server.mcp.broker_url)

    # Resolve the operation: METHOD:/path is broker-relative (no lookup);
    # METHOD:url and opaque ids resolve through the in-process inspect seam.
    method, path = ex.parse_method_path(target)
    upstream_target = path
    broker_relative = bool(method)
    if not method:
        doc = await _inspect_document(env, target, args.get("revision", ""))
        method = str(doc.get("method", "")).upper()
        upstream_target = str(doc.get("url", ""))
        if not method or not upstream_target:
            raise ToolError(CODE_INTERNAL_ERROR, "inspect response missing method or url")
        if not upstream_target.startswith(("http://", "https://")):
            # A host-relative url (spec declares no absolute server — the case
            # where a search hit's target is the registry operation_id): the
            # contract is inspectable, but there is no upstream host to proxy
            # to (Go: ``agentops.ensureAbsoluteUpstream``).
            raise ToolError(
                CODE_RESOLVE_FAILED,
                f"operation {target!r} has no upstream host (url {upstream_target!r}): "
                "its spec declares no absolute server, so it cannot be executed",
                actionable="Call search_apis for an operation whose target is a METHOD:url pair.",
                next_tool="search_apis",
            )
    if method == "TRACE":
        # The broker's proxy route never serves TRACE: it echoes the request
        # back, which would reflect the credentials the broker injects. Refuse
        # locally (Go: ``agentops.ensureExecutableMethod``) instead of a 405.
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"operation {target!r} is a TRACE operation, which cannot be executed",
            actionable="Call search_apis for a different operation; TRACE is never "
            "proxied by the broker.",
            next_tool="search_apis",
        )
    if read_only_variant and method not in ("GET", "HEAD"):
        raise invalid_params(
            f"operation {target!r} resolves to {method} — execute_read only performs "
            "GET/HEAD; call the execute tool instead"
        )

    path_params, query_params = ex.split_inputs(args.get("inputs"), upstream_target)
    headers = _header_kvs(args.get("headers"))
    upstream = ex.build_upstream_url(upstream_target, path_params, query_params)
    broker_url = ex.broker_request_url(scheme, host, upstream, broker_relative=broker_relative)

    body_bytes: bytes | None = None
    if body_value is not None:
        body_bytes = json.dumps(body_value, ensure_ascii=False).encode("utf-8")

    idempotency_key = args.get("idempotency_key", "")
    # Both failure paths judge retry safety on the key that actually rides the
    # wire, so a key passed via ``headers`` counts the same as idempotency_key.
    retry_safe = ex.retry_safe(method, ex.effective_idempotency_key(headers, idempotency_key))
    try:
        status, response_headers, raw, execution_id = await ex.send_to_broker(
            method=method,
            broker_url=broker_url,
            credential=env.credential,
            headers=headers,
            body=body_bytes,
            session_id=env.session_id,
            idempotency_key=idempotency_key or None,
        )
    except Exception as exc:
        # ``from None``: the raw transport message stays out of any rendered
        # traceback (``transport_error`` logs a redacted copy).
        raise ex.transport_error(exc, retry_safe=retry_safe) from None

    if (redirect := ex.broker_redirect_error(status, response_headers)) is not None:
        raise redirect
    if (denial := ex.classify_denial(status, response_headers, raw)) is not None:
        raise denial
    if (resolve_failure := ex.classify_broker_error(status, response_headers, raw)) is not None:
        raise resolve_failure
    if (
        broker_failure := ex.classify_broker_transport_error(
            status, response_headers, raw, retry_safe=retry_safe
        )
    ) is not None:
        raise broker_failure
    payload = ex.execute_result_payload(status, response_headers, raw, execution_id)
    held = ex.held_envelope(status, payload.get("body"))
    if held is not None:
        return await _answer_held(env, held, payload, method, urlsplit(upstream).path or "/")
    return tool_result(env.ctx, payload)


async def _answer_held(
    env: CallEnv, envelope: dict[str, Any], payload: dict[str, Any], method: str, path: str
) -> ToolCallResult:
    """Shape a held (202) call for this client: URL elicitation, else the held result at once."""
    door = approvals.front_door(env.client_capabilities, env.protocol_version)
    _log_front_door(env, door, envelope)
    if door == approvals.FRONT_DOOR_URL_ELICITATION:
        state = approvals.seal_request_state(
            env.ctx.encryption, envelope=envelope, agent_id=env.identity.sub
        )
        return approvals.url_elicitation_result(
            review_url=envelope["review_url"],
            message=approvals.review_message(method, path),
            request_state=state,
        )
    return tool_result(env.ctx, payload)


async def _resume_held(env: CallEnv, token: str) -> ToolCallResult:
    """A URL-elicitation retry: the call's result if finished, else short wait then held result.

    The elicitation response the client sends back is consent to open the
    review page only — it never decides the approval.
    """
    state = approvals.open_request_state(env.ctx.encryption, token, agent_id=env.identity.sub)
    if state is None:
        raise invalid_params("Invalid or expired requestState")
    _log_front_door(env, "url_elicitation_retry", {"held_execution_id": state["held_execution_id"]})
    terminal = await approvals.short_wait(_held_poller(env), state["held_execution_id"])
    if terminal is not None:
        return tool_result(env.ctx, terminal)
    envelope = approvals.envelope_from_state(state, HELD_AGENT_DIRECTIVE)
    return tool_result(env.ctx, approvals.held_result_payload(envelope))


def _poller(env: CallEnv) -> Callable[[str], Awaitable[dict[str, Any]]]:
    async def poll(job_id: str) -> dict[str, Any]:
        return await job_poll_payload(env, job_id)

    return poll


def _log_front_door(env: CallEnv, door: str, envelope: dict[str, Any]) -> None:
    logger.info(
        "mcp_held_execution_front_door",
        front_door=door,
        held_execution_id=envelope.get("held_execution_id"),
        client_name=env.client_name,
        client_version=env.client_version,
        actor_id=env.identity.sub,
    )


def _header_kvs(obj: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The ``headers`` object as sorted KV pairs (Go: ``headerKVs``)."""
    if not obj:
        return []
    out = []
    for key in sorted(obj):
        value = obj[key]
        if not isinstance(value, str):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise invalid_params(f"header {key!r}: expected a string value")
            value = json.dumps(value)
        out.append((key, value))
    return out


# ── get_execution_result ──────────────────────────────────────────────────────

_GET_EXECUTION_RESULT_PARAMS = [
    ParamSpec("held_execution_id", "string", ("held_execution",)),
    ParamSpec("job_id", "string", ("id", "job")),
    ParamSpec("timeout", "int"),
]

#: get_execution_result's ``timeout`` in seconds when the caller sets none, and
#: its cap (Go: ``defaultPollTimeoutSeconds`` / ``maxPollTimeoutSeconds``).
DEFAULT_POLL_TIMEOUT_SECONDS = 30
MAX_POLL_TIMEOUT_SECONDS = 30


async def handle_get_execution_result(
    env: CallEnv, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """Poll a held execution, or an async execution's job (Go: ``handleGetExecutionResult``).

    ``held_execution_id`` (from a held 202) reads through the held execution
    itself; ``job_id`` (from an async 202) reads the job. It re-polls until the
    call has finished or ``timeout`` seconds lapse — 30 when absent, and at most
    30; ``0`` polls once and answers at once.
    """
    args = normalize_tool_args(arguments, _GET_EXECUTION_RESULT_PARAMS)
    held_execution_id = args.get("held_execution_id", "")
    job_id = args.get("job_id", "")
    if bool(held_execution_id) == bool(job_id):
        raise invalid_params(
            'get_execution_result requires exactly one of "held_execution_id" (from a held '
            '202 execute response) or "job_id" (aliases: "id", "job"; from an async one)'
        )
    timeout = min(
        max(int(args.get("timeout", DEFAULT_POLL_TIMEOUT_SECONDS)), 0), MAX_POLL_TIMEOUT_SECONDS
    )
    if held_execution_id:
        poll = _held_poller(env)
        target = held_execution_id
    else:
        poll = _poller(env)
        target = job_id
    return tool_result(env.ctx, await _poll_until_finished(poll, target, float(timeout)))


async def _poll_until_finished(
    poll: Callable[[str], Awaitable[dict[str, Any]]], target: str, wait: float
) -> dict[str, Any]:
    """Poll ``target``, re-polling until finished or ``wait`` seconds lapse; zero polls once."""
    deadline = time.monotonic() + wait
    while True:
        payload = await poll(target)
        if payload.get("status") in approvals.TERMINAL_JOB_STATUSES:
            return payload
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return payload
        await asyncio.sleep(min(approvals.SHORT_WAIT_POLL_SECONDS, remaining))


async def held_execution_poll_payload(env: CallEnv, held_execution_id: str) -> dict[str, Any]:
    """The poll payload for one held execution.

    ``{held_execution_id, state, status, review_url, expires_at,
    execution_id?, result?, agent_directive?}`` — ``state`` is the decision,
    ``status`` where the call stands, and the result rides once the call has
    finished: the upstream response, or the problem explaining why it never
    ran. Every poll names the held execution and its review page; until the
    call has finished it also carries the directive to show that page again.
    Needs no ``jobs:*`` permission: visibility is the held execution's own (the
    filing agent, its owner, or ``org:admin``).
    """
    _require_db(env.ctx, "admin", "held execution polling")
    svc = HeldExecutionService(env.ctx)
    try:
        view = await svc.get(held_execution_id, identity=env.identity)
    except HeldExecutionNotFoundError:
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"held execution {held_execution_id!r} not found",
            actionable="Re-check the id — it is the held_execution_id of the held (202) "
            "execute response — and call get_execution_result again with the exact value.",
            next_tool="get_execution_result",
        ) from None
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "held_execution_id": view.id,
        "state": view.state,
        "status": view.execution_status,
        "review_url": env.base_url.rstrip("/") + review_page_path(view.id),
        "expires_at": _rfc3339(view.expires_at),
    }
    if view.execution_id:
        payload["execution_id"] = view.execution_id
    if view.execution_status not in approvals.TERMINAL_JOB_STATUSES:
        payload["agent_directive"] = STILL_HELD_AGENT_DIRECTIVE
        return payload
    if view.execution_status in (_JOB_COMPLETED, _JOB_FAILED):
        try:
            result = await svc.get_result(held_execution_id, identity=env.identity)
        except HeldExecutionResultNotReadyError:
            return payload
        except Exception as exc:
            payload["result_error"] = (
                f"the call finished but its result could not be fetched: {exc}"
            )
            return payload
        _attach_result_view(result, payload)
    return payload


def _rfc3339(when: datetime) -> str:
    """UTC RFC 3339 with ``Z`` and no trailing fraction zeros (Go: ``time.RFC3339Nano``)."""
    text = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".")
    return f"{text}Z"


def _held_poller(env: CallEnv) -> Callable[[str], Awaitable[dict[str, Any]]]:
    async def poll(held_execution_id: str) -> dict[str, Any]:
        return await held_execution_poll_payload(env, held_execution_id)

    return poll


async def job_poll_payload(env: CallEnv, job_id: str) -> dict[str, Any]:
    """The poll payload for one job (Go: ``handleGetExecutionResult`` body).

    ``{job_id, kind, status, error?, execution_id?, result?}`` — the result
    rides once the job is ``completed``.
    """
    require_permissions(env.identity, ["jobs:read"])
    _require_db(env.ctx, "admin", "job polling")

    try:
        job = await JobService(env.ctx).get_by_id(job_id, identity=env.identity)
    except JobNotFoundError:
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"job {job_id!r} not found",
            actionable="Re-check the job id — it is carried by the async (202) execute "
            "response — and call get_execution_result again with the exact value.",
            next_tool="get_execution_result",
        ) from None

    if (
        job.kind == _JOB_KIND_IMPORT
        and job.status != _JOB_COMPLETED
        and _is_single_source_duplicate_failure(job.error)
    ):
        # The same duplicate-content short-circuit import_api makes: a
        # duplicate import job polled here reports already_imported (the
        # content is present — possibly a lost concurrent race), never a
        # bare failed status.
        # Gated on the single-source wrapper: a multi-source job (POST /apis)
        # whose failure is only PARTLY the duplicate keeps its honest
        # failure payload — see _is_single_source_duplicate_failure.
        # COMPLETED jobs are exempt: the completed result is the outcome,
        # whatever an earlier attempt wrote to job.error.
        return _already_imported_payload(job.id)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job.id,
        "kind": job.kind,
        "status": job.status,
    }
    if job.error:
        payload["error"] = job.error
    if job.execution_id:
        payload["execution_id"] = job.execution_id
    if job.status == _JOB_COMPLETED:
        await _attach_job_result(env, job_id, payload)
    return payload


async def _attach_job_result(env: CallEnv, job_id: str, payload: dict[str, Any]) -> None:
    """Attach the completed job's result, size-capped (Go: ``attachJobResult``).

    A result fetch failure degrades to a ``result_error`` note rather than
    failing the poll — the status the model asked for is already in hand.
    """
    try:
        view = await JobResultService(env.ctx).get(job_id, identity=env.identity)
    except JobNotCompletedError:
        # A failed job without a recorded problem body has no result to attach.
        return
    except Exception as exc:
        payload["result_error"] = f"the job completed but its result could not be fetched: {exc}"
        return
    _attach_result_view(view, payload)


def _attach_result_view(view: JobResultView, payload: dict[str, Any]) -> None:
    """Attach a result document, size-capped: the upstream body, or JSON for other kinds."""
    raw: bytes
    if view.kind == "execution" and view.content_type and view.raw_body is not None:
        raw = view.raw_body
    else:
        raw = json.dumps(view.body, ensure_ascii=False).encode("utf-8")
    if len(raw) > ex.MAX_RESULT_BYTES:
        payload["result"] = raw[: ex.MAX_RESULT_BYTES].decode("utf-8", errors="ignore")
        payload["truncated"] = True
        payload["total_bytes"] = len(raw)
        return
    try:
        payload["result"] = json.loads(raw)
    except ValueError:
        if raw:
            payload["result"] = raw.decode("utf-8", errors="replace")


# ── dispatch ──────────────────────────────────────────────────────────────────

#: name → handler for every tool this mount serves (must cover
#: :data:`jentic_one.mcp.spec.SERVED_TOOLS` exactly — pinned by the drift test).
HANDLERS: dict[str, Handler] = {
    "whoami": handle_whoami,
    "search_apis": handle_search_apis,
    "inspect_operation": handle_inspect_operation,
    "search_catalog": handle_search_catalog,
    "import_api": handle_import_api,
    "execute": handle_execute,
    "execute_read": handle_execute_read,
    "get_execution_result": handle_get_execution_result,
    "request_connection": handle_request_connection,
    "show_access": handle_show_access,
    "inspect_api": handle_inspect_api,
    "request_access_change": handle_request_access_change,
    "amend_request": handle_amend_request,
}


async def dispatch_tool_call(
    env: CallEnv, name: str, arguments: dict[str, Any] | None
) -> mcp_types.CallToolResult:
    """Route one tools/call whose caller declared no input-required support.

    Only a held execute for a client declaring ``elicitation.url`` answers
    with an input-required result, and ``env`` carries the capabilities, so a
    caller passing none always gets a plain tool result.
    """
    result = await dispatch_mcp_tool_call(env, name, arguments)
    if not isinstance(result, mcp_types.CallToolResult):
        raise MCPError(mcp_types.INTERNAL_ERROR, "tool answered with input required")
    return result


async def dispatch_mcp_tool_call(
    env: CallEnv, name: str, arguments: dict[str, Any] | None
) -> ToolCallResult:
    """Route one authenticated tools/call to its handler.

    ``ToolError`` renders as the coded ``isError`` result (diagnosable
    states are data the model acts on); so do malformed arguments
    (``MISSING_ARGUMENT``), since a client may show the model a protocol
    error only as a generic failure, hiding what to fix. Unexpected failures
    degrade to INTERNAL_ERROR instead of a protocol error, matching the Go
    posture.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        raise MCPError(_INVALID_PARAMS, f"unknown tool: {name}")
    try:
        require_password_current(env.identity, name)
        return await handler(env, arguments or {})
    except ToolError as err:
        return soft_error_result(env.ctx, err)
    except MCPError as err:
        if err.error.code != _INVALID_PARAMS:
            raise
        return soft_error_result(
            env.ctx,
            ToolError(
                CODE_MISSING_ARGUMENT,
                err.error.message,
                actionable="Fix the argument the error names and call the tool again.",
            ),
        )
    except Exception as exc:
        # The exception text can carry request material (headers, credential
        # values), so it never reaches the agent — only the class name does.
        # The server-side log keeps a redacted copy for diagnosis.
        logger.error(
            "mcp_tool_unexpected_failure",
            tool=name,
            error_type=type(exc).__name__,
            error=redact_value(str(exc)),
        )
        return soft_error_result(
            env.ctx,
            ToolError(CODE_INTERNAL_ERROR, f"unexpected failure ({type(exc).__name__})"),
        )
