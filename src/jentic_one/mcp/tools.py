"""Tool handlers for the mounted MCP app — the pinned tool surface, in-process.

Each handler is the Python twin of the Go stdio server's handler for the same
tool (``cli/internal/cli/api/mcp_tools.go`` / ``mcp_discovery.go`` /
``mcp_access.go`` / ``mcp_execute.go``): the same argument normalization
(aliases + coercions), the same envelope keys, and the same coded soft-error
mapping — the golden contract tests replay identical tool calls against both
implementations. Where the Go server calls REST routes, these handlers call
the owning services **in-process** (registry search/inspect/catalog, admin
jobs, auth identity); the execute family proxies to the broker server-side
(the broker stays MCP-free).

Scope enforcement mirrors the REST routes fronted: the same
``required_permissions`` the routers declare, checked against the resolved
identity through the same ``compute_effective`` expansion + ``org:admin``
bypass ``get_current_identity`` applies. A scope failure maps exactly like the
Go client's wire 403 (``mcpCoded``): NOT_AUTHENTICATED with the get_started
pointer — except ``search_catalog`` and ``import_api``, whose 403s are
missing-scope facts the agent can fix itself (BROKER_DENIED + request_access,
the Go special case).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid as uuid_mod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import mcp.types as mcp_types
from jentic.problem_details import Forbidden, Unauthorized
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from jentic_one.admin.services.errors import JobNotFoundError
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.schemas.jobs import JobView
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.service_account_service import ServiceAccountService
from jentic_one.auth.web.routers.identity import (
    _resolve_agent,
    _resolve_service_account,
    _resolve_user,
)
from jentic_one.control.services.access_requests.errors import (
    AccessRequestNotFoundError,
    DuplicatePendingError,
    PrerequisiteNotMetError,
    RequiredFieldMissingError,
    RulesNotSupportedForBindError,
    UnsupportedScopeGrantError,
)
from jentic_one.control.services.access_requests.schemas.access_requests import AccessRequestView
from jentic_one.control.services.access_requests.service import AccessRequestService
from jentic_one.control.web.routers.access_requests import _to_response
from jentic_one.control.web.schemas.access_requests import AccessRequestFileRequest
from jentic_one.mcp import execute as ex
from jentic_one.mcp.access_compose import (
    AccessRequestOptions,
    AccessTargetRequiredError,
    ComposeError,
    rules_json_values,
)
from jentic_one.mcp.envelopes import (
    CODE_BROKER_DENIED,
    CODE_INTERNAL_ERROR,
    CODE_NOT_AUTHENTICATED,
    CODE_PARTIAL_APPROVAL,
    CODE_RESOLVE_FAILED,
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
    OperationNotFoundError,
    OverlaySupersedeForbiddenError,
    SearchUnavailableError,
)
from jentic_one.registry.services.inspect.models import SUMMARY_LOAD_OPTIONS
from jentic_one.registry.services.inspect.service import InspectService
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.registry.services.search_service import SearchService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import compute_effective
from jentic_one.shared.auth.permissions import has_effective_permission
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import InvalidCursorError, InvalidSearchCursorError

_INVALID_PARAMS = mcp_types.INVALID_PARAMS


@dataclass(frozen=True)
class CallEnv:
    """Everything one authenticated tool call needs from the HTTP layer."""

    ctx: Context
    identity: Identity
    #: the raw credential the caller presented — relayed as the bearer on the
    #: execute family's broker leg (the broker authenticates the AGENT).
    credential: str
    #: deployment base URL for in-process ``_links`` building.
    base_url: str
    #: sanitized ``X-Jentic-Session-Id`` when the inbound request carried one.
    session_id: str | None


Handler = Callable[[CallEnv, dict[str, Any]], Awaitable[mcp_types.CallToolResult]]


def invalid_params(message: str) -> MCPError:
    """A malformed-arguments protocol error (Go: ``invalidParams``)."""
    return MCPError(_INVALID_PARAMS, message)


# ── argument normalization (port of mcp_params.go's subset these tools use) ──


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str  # "string" | "int" | "object" | "json" | "string_list"
    aliases: tuple[str, ...] = ()


def normalize_tool_args(arguments: dict[str, Any] | None, specs: list[ParamSpec]) -> dict[str, Any]:
    """Fold aliases onto canonical names and coerce tolerated shapes.

    Mirrors the Go normalizer's posture: aliases resolve handler-side (the
    schemas stay permissive), a canonical spelling wins over its aliases,
    scalars coerce to the declared kind where unambiguous, and an
    uninterpretable value is an invalid-params protocol error.
    """
    args = dict(arguments or {})
    out: dict[str, Any] = {}
    for spec in specs:
        value, found = None, False
        for key in (spec.name, *spec.aliases):
            if key in args and args[key] is not None:
                value, found = args[key], True
                break
        if not found:
            continue
        out[spec.name] = _coerce(spec, value)
    return out


def _coerce(spec: ParamSpec, value: Any) -> Any:
    if spec.kind == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, bool) or value is None:
            raise invalid_params(f'parameter "{spec.name}": expected a string')
        if isinstance(value, (int, float)):
            return json.dumps(value)
        raise invalid_params(f'parameter "{spec.name}": expected a string')
    if spec.kind == "int":
        if isinstance(value, bool):
            raise invalid_params(f'parameter "{spec.name}": expected an integer')
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                raise invalid_params(
                    f'parameter "{spec.name}": expected an integer, got {value!r}'
                ) from None
        raise invalid_params(f'parameter "{spec.name}": expected an integer')
    if spec.kind == "object":
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                raise invalid_params(f'parameter "{spec.name}": expected an object') from None
            if isinstance(decoded, dict):
                return decoded
        raise invalid_params(f'parameter "{spec.name}": expected an object')
    if spec.kind == "string_list":
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return value
        raise invalid_params(f'parameter "{spec.name}": expected a list of strings')
    # "json": keep the raw JSON value; a string that parses as JSON is
    # deliberately treated as a stringified body (the Go body contract).
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# ── scope enforcement (same scopes as the REST routes fronted) ──────────────


def require_scopes(identity: Identity, required: list[str]) -> None:
    """The ``get_current_identity(required_permissions=…)`` check, mount-side.

    Same expansion (``compute_effective``) and the same ``org:admin`` bypass;
    a failure raises the coded error the Go client maps a wire 403 to
    (``mcpCoded`` — NOT_AUTHENTICATED, get_started pointer).
    """
    caller = compute_effective(set(identity.permissions))
    if "org:admin" in caller or caller.intersection(required):
        return
    raise ToolError(
        CODE_NOT_AUTHENTICATED,
        "the control plane rejected this agent's credentials "
        f"(http 403: This action requires one of: {', '.join(required)}) — "
        "the identity may have been revoked or disabled",
        actionable="call get_started to diagnose this machine's setup and relay its "
        "instruction to your operator",
    )


_OPERATION_ID_SPEC = ParamSpec("operation_id", "string", ("id", "uuid"))

#: Tools reachable with an expired password — the REST parity map: ``whoami``
#: fronts ``GET /me``, the one route ``get_current_identity`` grants
#: ``allow_expired_password=True`` (so a locked-out user can still see WHY).
_EXPIRED_PASSWORD_ALLOWED = frozenset({"whoami"})


def require_password_current(identity: Identity, tool: str) -> None:
    """The ``must_change_password`` gate (``shared/web/deps.py``), mount-side.

    Every REST route except ``/me`` refuses a password-expired identity with
    403 ``password_rotation_required``; the mount mirrors that per tool so a
    web-session JWT for a password-expired user cannot drive tools over
    ``/mcp`` that the REST routes fronted would refuse. Only login-JWT
    identities carry the flag — agents and API keys are unaffected.
    """
    if tool in _EXPIRED_PASSWORD_ALLOWED or not identity.must_change_password:
        return
    raise ToolError(
        CODE_NOT_AUTHENTICATED,
        "the control plane rejected this credential (http 403: Password rotation "
        "required before accessing this resource)",
        actionable="This user must change their password before this credential can "
        "drive tools again; relay this to your human operator — the rotation happens "
        "in the dashboard, never through an agent.",
        next_tool="whoami",
    )


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
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
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
        elif sub.startswith("sva_"):
            me = await _resolve_service_account(
                request, env.identity, ServiceAccountService(env.ctx)
            )
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
            actionable="call get_started to diagnose this machine's setup and relay "
            "its instruction to your operator",
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
    require_scopes(env.identity, ["apis:read"])
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
            "a registry operation id from a search_apis hit, or a METHOD:url pair "
            'like "GET:https://api.example.com/v1/things"'
        )
    require_scopes(env.identity, ["apis:read"])
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
        "want to do, then inspect the operation_id (or the METHOD:url) from one of "
        "its hits.",
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
        require_scopes(env.identity, ["capabilities:read"])
    except ToolError as exc:
        # The Go special case: a 403 on THIS route is the missing
        # capabilities:read scope, which the agent can fix itself. The wire
        # error rides as the message tail, like Go's ``: %v`` (mcp_access.go).
        raise ToolError(
            CODE_BROKER_DENIED,
            f"reading the catalog requires the capabilities:read scope: {exc}",
            actionable='Request the scope with request_access, e.g. {"scopes": '
            '["capabilities:read"], "reason": "search the catalog for the API needed '
            "for this task\"}, wait for your operator's approval, then retry "
            "search_catalog.",
            next_tool="request_access",
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
#: (``asyncio.timeout`` around the whole track-and-promote tail). The budget
#: alone only gates BETWEEN polls — a single hung poll / result fetch /
#: promote would hold the ASGI request open indefinitely; the ceiling turns
#: that into the poll-failure arm (unknown state, never "still running").
_IMPORT_WAIT_GRACE_SECONDS = 5.0

#: In-process poll cadence for the job tracker: the first poll is immediate,
#: then back off from the step to the max (Go: ``App.PollCadence``).
_IMPORT_POLL_STEP_SECONDS = 0.25
_IMPORT_POLL_MAX_SECONDS = 2.0

#: The stable leading fragment of ``DuplicateRevisionError``'s message
#: (``registry/ingest/exc.py`` — also what the worker's IntegrityError
#: translation mints for a lost one-active race, ``import_service.py``).
#: ``job.error`` is truncated to 128 chars after a ~42-char wrapper prefix, so
#: only the message head survives: match this fragment, never the full message.
_DUPLICATE_CONTENT_FRAGMENT = "identical content already exists"

#: Job statuses that end the tracking loop (Go: ``catJobCompleted`` /
#: ``catJobFailed`` / ``catJobCancelled`` / ``catJobDeadLetter`` — dead_letter
#: IS terminal: the worker's retry-backoff ladder parks poison jobs there).
_JOB_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "dead_letter"})

#: the job kind the catalog import loop rides (``JobKind.IMPORT``).
_JOB_KIND_IMPORT = "import"


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
    same ``job.error`` fragment also covers a lost concurrent-import race
    (the ``ix_api_revisions_one_active`` collision), where "already imported"
    means "another import just won" — the recovery (search_apis) is identical
    either way. This mapping is what keeps the pinned description's
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


async def _track_import_job(ctx: Context, job_id: str) -> tuple[JobView, bool]:
    """Poll the import job briefly; returns ``(job, duplicate_content)``.

    Mirrors Go's ``trackImportJob`` three-outcome contract: a terminal job
    returns with its terminal status, a budget lapse returns the last
    non-terminal status (the caller renders both from ``job.status``), and a
    failing poll PROPAGATES — the job's state is then unknown, and the caller
    must surface that as a failure, never as a clean "still running" result.

    The duplicate-content check runs on EVERY poll, including non-terminal
    requeued states: the worker treats a duplicate ingest as retryable
    (exponential backoff to a terminal DEAD_LETTER, ~30s+ total), so waiting
    for a terminal status would burn the whole wait budget on a job that can
    never succeed. ``job.error`` is populated while requeued, which is what
    makes the early exit possible. A COMPLETED job is exempt: the worker never
    clears ``job.error`` (requeue writes it; neither claim nor completion
    resets it), so a job that failed once with the duplicate message and
    succeeded on a later attempt carries the stale fragment forever — its
    completed result is always more honest than its residual error.
    """
    svc = JobService(ctx)
    deadline = time.monotonic() + _IMPORT_WAIT_BUDGET_SECONDS
    delay = _IMPORT_POLL_STEP_SECONDS  # the first poll is immediate; back off from the step
    while True:
        job = await svc.get_by_id(job_id)
        if job.status != _JOB_COMPLETED and job.error and _DUPLICATE_CONTENT_FRAGMENT in job.error:
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
    ``RevisionService.promote`` enforces NO scopes in-process (the
    ``apis:write`` gate lives only on the REST route), so the handler
    soft-checks the scope itself — an unguarded call would let a default agent
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
        if state != "draft":
            promoted[revision_id] = state
            continue
        if not can_write:
            promoted[revision_id] = "promote failed: missing apis:write scope"
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
        require_scopes(env.identity, ["catalog:import"])
    except ToolError as exc:
        # The Go special case (importAPIError's 403 arm): a 403 on THIS route
        # is the missing catalog:import scope — an access gap the agent can
        # close itself via request_access, not a revoked identity.
        raise ToolError(
            CODE_BROKER_DENIED,
            f"importing a cataloged API requires the catalog:import scope: {exc}",
            actionable='Request the scope with request_access, e.g. {"scopes": '
            '["catalog:import"], "reason": "import the API needed for this task"}, wait '
            "for your operator's approval, then retry import_api.",
            next_tool="request_access",
        ) from None
    _require_db(env.ctx, "registry", "the catalog")
    _require_db(env.ctx, "admin", "import job tracking")

    try:
        job_id = await CatalogService(env.ctx).import_entry(api_id, env.identity)
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
            "overlay is an operator decision (overlays:confirm), not a scope an agent "
            "should request for itself.",
        ) from None
    except CatalogUnavailableError as exc:
        raise ToolError(CODE_INTERNAL_ERROR, f"catalog not available: {exc}") from None

    try:
        require_scopes(env.identity, ["jobs:read"])
    except ToolError:
        # In-process tracking rides the same jobs:read gate the Go client's
        # poll leg does (GET /jobs/{id}). Absent the scope, degrade to the
        # filed-{job_id, status} envelope — NOT an error: the filing
        # succeeded, only the courtesy tracking is off the table (REST
        # parity: the poll would have been refused, the import would not).
        # Both scopes ride DEFAULT_AGENT_SCOPES, so defaults are unaffected.
        return tool_result(
            env.ctx,
            {"schema_version": SCHEMA_VERSION, "job_id": job_id, "status": "queued"},
        )

    try:
        # The hard per-leg ceiling: the wait budget only gates BETWEEN polls —
        # a hung poll / result fetch / promote inside the tail would hold the
        # ASGI request open indefinitely without it.
        async with asyncio.timeout(_IMPORT_WAIT_BUDGET_SECONDS + _IMPORT_WAIT_GRACE_SECONDS):
            return await _finish_import(env, api_id, job_id)
    except TimeoutError as exc:
        # The ceiling lapse maps to the poll-failure arm: the job's state is
        # UNKNOWN (a leg hung mid-flight) — never a clean "still running".
        raise ToolError(
            CODE_INTERNAL_ERROR,
            f"import of {api_id} was filed as job {job_id}, but tracking it timed out mid-poll",
            actionable="The job's state is unknown — do not re-import; poll this job "
            "with get_execution_result using the job_id in this result.",
            next_tool="get_execution_result",
            extra={"job_id": job_id},
        ) from exc


async def _finish_import(env: CallEnv, api_id: str, job_id: str) -> mcp_types.CallToolResult:
    """The track-and-promote tail of import_api (runs under the hard ceiling)."""
    try:
        job, duplicate_content = await _track_import_job(env.ctx, job_id)
    except Exception as exc:
        # Go's poll-failure arm: a failing job poll is UNKNOWN state — never a
        # clean "still running" result (which would send the model into a
        # re-import loop against a backend that de-duplicates nothing).
        # Surface the failure with the job_id so the model keeps watching
        # THIS job instead of filing another.
        raise ToolError(
            CODE_INTERNAL_ERROR,
            f"import of {api_id} was filed as job {job_id}, but polling the job failed: {exc}",
            actionable="The job's state is unknown — do not re-import; poll this job "
            "with get_execution_result using the job_id in this result.",
            next_tool="get_execution_result",
            extra={"job_id": job_id},
        ) from exc
    if duplicate_content:
        # Identical content already present (or a concurrent import just won
        # the one-active race): short-circuit honestly instead of letting the
        # job burn its retry backoff to DEAD_LETTER inside the wait budget.
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
        view = await JobResultService(env.ctx).get(job_id)
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


async def handle_execute(env: CallEnv, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    return await _execute_tool(env, arguments, read_only_variant=False)


async def handle_execute_read(env: CallEnv, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    return await _execute_tool(env, arguments, read_only_variant=True)


async def _execute_tool(
    env: CallEnv, arguments: dict[str, Any], *, read_only_variant: bool
) -> mcp_types.CallToolResult:
    """The shared execute/execute_read handler (Go: ``executeTool``).

    Resolve → build → send → classify, with the broker leg proxied
    control-plane→broker server-side: the caller's own bearer
    rides the hop, so the broker enforces identity/bindings/rules exactly as
    if the agent had dialed it directly. The held (202) envelope passes
    through as a normal result — the model polls with get_execution_result
    and never re-sends.
    """
    tool_name = "execute_read" if read_only_variant else "execute"
    args = normalize_tool_args(arguments, _EXECUTE_PARAMS)
    target = args.get("operation_id", "")
    if not target:
        raise invalid_params(
            f'{tool_name} requires "operation_id" (aliases: "id", "uuid"): a registry '
            "operation id from a search_apis hit, or a METHOD:url pair like "
            '"GET:https://api.example.com/v1/things"'
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
        retry_safe = bool(idempotency_key) or method in ("GET", "HEAD")
        raise ex.transport_error(exc, retry_safe=retry_safe) from exc

    if (redirect := ex.broker_redirect_error(status, response_headers)) is not None:
        raise redirect
    if (denial := ex.classify_denial(status, response_headers, raw)) is not None:
        raise denial
    return tool_result(
        env.ctx, ex.execute_result_payload(status, response_headers, raw, execution_id)
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

_GET_EXECUTION_RESULT_PARAMS = [ParamSpec("job_id", "string", ("id", "job"))]

#: the terminal state whose result document rides the poll payload.
_JOB_COMPLETED = "completed"


async def handle_get_execution_result(
    env: CallEnv, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """GET /jobs/{id} (+ /result) in-process (Go: ``handleGetExecutionResult``)."""
    args = normalize_tool_args(arguments, _GET_EXECUTION_RESULT_PARAMS)
    job_id = args.get("job_id", "")
    if not job_id:
        raise invalid_params(
            'get_execution_result requires "job_id" (aliases: "id", "job"): the job id '
            "from a held (202) execute response"
        )
    require_scopes(env.identity, ["jobs:read"])
    _require_db(env.ctx, "admin", "job polling")

    try:
        job = await JobService(env.ctx).get_by_id(job_id)
    except JobNotFoundError:
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"job {job_id!r} not found",
            actionable="Re-check the job id — it is carried by the held (202) execute "
            "response — and call get_execution_result again with the exact value.",
            next_tool="get_execution_result",
        ) from None

    if (
        job.kind == _JOB_KIND_IMPORT
        and job.status != _JOB_COMPLETED
        and job.error
        and _DUPLICATE_CONTENT_FRAGMENT in job.error
    ):
        # The same duplicate-content short-circuit import_api makes: a
        # duplicate import job polled here reports already_imported (the
        # content is present — possibly a lost concurrent race), never a
        # scary dead_letter after the worker burns its retry backoff.
        # COMPLETED jobs are exempt: the worker never clears job.error, so a
        # job that failed once with the duplicate message and succeeded on a
        # later attempt carries the stale fragment — its completed result is
        # always more honest than its residual error.
        return tool_result(env.ctx, _already_imported_payload(job.id))

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
    return tool_result(env.ctx, payload)


async def _attach_job_result(env: CallEnv, job_id: str, payload: dict[str, Any]) -> None:
    """Attach the completed job's result, size-capped (Go: ``attachJobResult``).

    A result fetch failure degrades to a ``result_error`` note rather than
    failing the poll — the status the model asked for is already in hand.
    """
    try:
        view = await JobResultService(env.ctx).get(job_id)
    except Exception as exc:
        payload["result_error"] = f"the job completed but its result could not be fetched: {exc}"
        return
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


# ── request_access ────────────────────────────────────────────────────────────

_REQUEST_ACCESS_PARAMS = [
    ParamSpec("request_id", "string", ("id",)),
    ParamSpec("provision", "string_list", ("provisions",)),
    ParamSpec("toolkits", "string_list", ("toolkit",)),
    ParamSpec("toolkit_ids", "string_list", ("toolkit_id",)),
    ParamSpec("scopes", "string_list", ("scope",)),
    ParamSpec("auth", "string_list", ("auths",)),
    # rules_json is "json", not "string_list": a JSON rules array carries
    # commas, and the string-list coercion would comma-split a bare string.
    ParamSpec("rules_json", "json", ("rules",)),
    ParamSpec("reason", "string"),
]

#: The human-in-the-loop wording every pending request_access result carries
#: (Go: ``pendingAccessInstruction``, verbatim): the tool files and polls, a
#: HUMAN approves.
_PENDING_ACCESS_INSTRUCTION = (
    "Relay approve_url to your human operator — granting is always a human "
    "action in the dashboard; this tool never approves. Poll the decision by "
    "calling request_access "
    'with {"request_id": "<id>"}; never re-file the same request while one is pending.'
)


def _request_access_options(args: dict[str, Any]) -> AccessRequestOptions:
    """Fold the normalized arguments onto the compose() options (Go:
    ``requestAccessOptions``) — a malformed ``rules_json`` is an
    invalid-params protocol error on BOTH arms."""
    try:
        rules_jsons = rules_json_values(args.get("rules_json"))
    except ComposeError as exc:
        raise invalid_params(str(exc)) from None
    return AccessRequestOptions(
        provisions=args.get("provision") or [],
        toolkits=args.get("toolkits") or [],
        toolkit_ids=args.get("toolkit_ids") or [],
        scopes=args.get("scopes") or [],
        auths=args.get("auth") or [],
        rules_jsons=rules_jsons,
        reason=args.get("reason", ""),
    )


def absolutize_approve_url(base_url: str, approve_url: str) -> str:
    """Absolutize a service-stored approve_url onto the deployment base URL.

    The service stores ``{control.access_requests.canonical_base_url}/…``,
    which is RELATIVE (a rooted path) when that knob is unset (default ``""``).
    Mirrors Go's ``absolutizeApproveURL`` refusal posture: an already-absolute
    URL passes through (the stored canonical base wins over ``env.base_url``
    when the two knobs disagree), a scheme-relative ``//host/…`` value would
    resolve onto a FOREIGN host and is cleared, and only rooted paths are
    absolutizable — anything else is cleared rather than relayed as a
    dead/hijackable link.
    """
    if not approve_url:
        return ""
    if approve_url.startswith("//"):
        return ""
    if urlparse(approve_url).scheme:
        return approve_url
    if not approve_url.startswith("/"):
        return ""
    return base_url.rstrip("/") + approve_url


def _granted_scopes(view: AccessRequestView) -> list[str]:
    """The scope names this request's APPROVED scope:grant items granted."""
    return [
        item.resource_id
        for item in view.items
        if item.resource_type == "scope"
        and item.action == "grant"
        and item.status == "approved"
        and item.resource_id
    ]


def _scope_grant_instruction(env: CallEnv, view: AccessRequestView) -> str | None:
    """The honesty branch that replaces the CLI's token re-mint.

    There is nothing to re-mint on the mount: ``resolve_effective_scopes``
    draws agent scopes live from ``actor_scope_grants`` on every request, so
    an approved grant is active on the very next tool call — UNLESS this
    session's consent/client ceiling excludes it. ``env.identity.permissions``
    is already the full intersection (live scopes ∩ client allowed_scopes ∩
    consent-grant scopes), re-resolved per request, and permissions carry
    literal scope names — so a granted scope's membership decides the wording.
    Never promise "retry and it works" when the scope is absent.
    """
    granted = _granted_scopes(view)
    if not granted:
        return None
    held = set(env.identity.permissions)
    missing = sorted(scope for scope in granted if scope not in held)
    if not missing:
        return (
            f"The granted scope(s) ({', '.join(sorted(granted))}) are active now — "
            "scopes are drawn live on every call, so retry the tool call that was denied."
        )
    return (
        f"Scope(s) {', '.join(missing)} were approved, but this session's consent "
        "does not cover them — re-authorization is required before this session can "
        "use them. Do not assume a retry will succeed; relay this to your human "
        "operator."
    )


def _access_request_result(
    env: CallEnv,
    view: AccessRequestView,
    extra: dict[str, Any] | None,
) -> mcp_types.CallToolResult:
    """Render one access request as the tool result (Go: ``accessRequestResult``).

    The payload is the FULL access-request object — the REST response-schema
    dump of the request row (the ``_to_response`` projection the router
    serves; Go: ``structToMap(AccessRequestResponse)``) — with
    ``schema_version`` joined as a top-level sibling and extras
    (``attached_to_existing``, ``instruction``) merged without clobbering.
    Terminal non-approved states wrap that same payload under the coded
    error's ``request`` extra, exactly as Go does.
    """
    payload: dict[str, Any] = _to_response(view).model_dump(mode="json")
    payload["approve_url"] = absolutize_approve_url(
        env.base_url, str(payload.get("approve_url") or "")
    )
    payload["schema_version"] = SCHEMA_VERSION
    for key, value in (extra or {}).items():
        payload.setdefault(key, value)

    status = view.status
    if status == "denied":
        raise ToolError(
            CODE_BROKER_DENIED,
            f"access request {view.id} was denied",
            actionable="Read the items' decision_reason in this result to learn why "
            "before giving up. A bare toolkit bind for an API nothing serves "
            'auto-denies — file a provisioning plan ({"provision": ["vendor/name"], …}) '
            "instead. Only re-file if something material changed.",
            next_tool="whoami",
            extra={"request": payload},
        )
    if status in ("expired", "withdrawn"):
        raise ToolError(
            CODE_BROKER_DENIED,
            f"request {view.id} is {status}, not approved; nothing was granted",
            actionable="File a fresh request_access naming what you still need, with "
            "a clear reason.",
            next_tool="request_access",
            extra={"request": payload},
        )
    if status == "partially_approved":
        extras: dict[str, Any] = {"request": payload}
        if (instruction := _scope_grant_instruction(env, view)) is not None:
            extras["instruction"] = instruction
        raise ToolError(
            CODE_PARTIAL_APPROVAL,
            "partially approved — not all requested items were granted",
            actionable="Check items[].status in this result: proceed only with what "
            "was approved, and do not assume the rest is available.",
            next_tool="whoami",
            extra=extras,
        )
    if status == "approved":
        if (instruction := _scope_grant_instruction(env, view)) is not None:
            payload.setdefault("instruction", instruction)
        return tool_result(env.ctx, payload)
    # pending
    payload.setdefault("instruction", _PENDING_ACCESS_INSTRUCTION)
    return tool_result(env.ctx, payload)


async def handle_request_access(
    env: CallEnv, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """POST /access-requests + GET /access-requests/{id} in-process (Go:
    ``handleRequestAccess``), minus the deliberately-dropped legs.

    No scope gate: the REST route uses bare ``get_current_identity()`` with no
    ``required_permissions`` — filing is open to every current identity (an
    empty-list ``require_scopes`` would deny every non-admin). No post-file
    poll: Go's ``awaitAutoDecision`` waits for a file-time auto-decision that
    does not exist on this backend (``file()`` always leaves the request
    PENDING; the unserved-bind "auto-deny" happens at decide time), so the
    PENDING envelope returns immediately. No token re-mint: scopes are drawn
    live per request — the honesty branch in ``_scope_grant_instruction``
    replaces it.
    """
    args = normalize_tool_args(arguments, _REQUEST_ACCESS_PARAMS)
    opts = _request_access_options(args)
    # The service rides both planes: the access-request tables live on the
    # control DB, and reads/advisories resolve owners/events on the admin DB.
    _require_db(env.ctx, "control", "access requests")
    _require_db(env.ctx, "admin", "access requests")

    # The poll arm: a request_id fetches the decision state and nothing else.
    # Filing parameters riding along are a confused call, not noise to drop:
    # a malformed rules_json or a stray reason silently ignored would teach
    # the model its arguments were accepted.
    if request_id := args.get("request_id", ""):
        if opts.has_filing_params():
            raise invalid_params(
                'pass EITHER "request_id" (to poll an existing request) OR filing '
                'parameters ("provision"/"toolkits"/"toolkit_ids"/"scopes" with '
                '"auth"/"rules_json"/"reason") to file a new one, not both'
            )
        try:
            view = await AccessRequestService(env.ctx).get(request_id, identity=env.identity)
        except AccessRequestNotFoundError:
            # The identity resolved — the id is wrong (or row-filtered out of
            # this caller's visibility); the recovery is re-reading the
            # earlier request_access result (self-pointer).
            raise ToolError(
                CODE_RESOLVE_FAILED,
                f"access request {request_id!r} not found",
                actionable="Re-check the request id — it is the `id` in the "
                "request_access result that filed it — and call request_access "
                "again with the exact value.",
                next_tool="request_access",
            ) from None
        return _access_request_result(env, view, None)

    # The filing arm: compose the same item list `jentic access request`
    # builds (provisioning plans first, then binds, then scope grants) and
    # file it in-process.
    try:
        items = opts.compose()
    except AccessTargetRequiredError:
        raise invalid_params(
            'request_access requires a target: "provision" (vendor/name plans), '
            '"toolkits" (vendor/name binds), "toolkit_ids" (tk_… binds), or "scopes" '
            '— or "request_id" to poll an existing request'
        ) from None
    except ComposeError as exc:
        raise invalid_params(str(exc)) from None

    # Validation parity: round-trip the composed items through the REST
    # pydantic schemas — the same (resource_type, action) allow-list,
    # exactly-one-of resource_id/resource_reference, and rule-shape checks the
    # router applies — then hand file() the router's exact dump. Composed
    # items are shaped to pass; a rules_json whose rules are mis-shaped (e.g.
    # a bad effect) fails here as a correctable protocol error, never reaching
    # the DB with less validation than REST applies.
    try:
        body = AccessRequestFileRequest.model_validate(
            {"reason": opts.reason or None, "items": items}
        )
    except ValidationError as exc:
        raise invalid_params(f"invalid access-request items: {exc}") from None

    svc = AccessRequestService(env.ctx)
    try:
        view = await svc.file(
            actor_id=env.identity.sub,
            reason=body.reason,
            items=[item.model_dump(exclude_none=True) for item in body.items],
            identity=env.identity,
        )
    except DuplicatePendingError as exc:
        return await _attach_or_refuse_duplicate(env, svc, opts, exc)
    except (RulesNotSupportedForBindError, UnsupportedScopeGrantError) as exc:
        # File-time validation-shaped refusals (REST: 422): the call is
        # correctable — a rule on an item type that can't enforce it, or a
        # scope outside the self-service allow-list.
        raise invalid_params(str(exc)) from None
    except RequiredFieldMissingError as exc:
        raise invalid_params(str(exc)) from None
    except PrerequisiteNotMetError as exc:
        # The residual 403-filing arm (REST: 403): the control plane refused
        # the FILING itself. Not the generic revoked-identity mapping — and
        # not a request_access pointer either: an agent that may not file
        # requests cannot request the right to file them.
        raise ToolError(
            CODE_BROKER_DENIED,
            f"the control plane refused to accept this access request: {exc}",
            actionable="This agent is not permitted to file this access request; "
            "relay this error to your human operator — they can grant what you "
            "need directly in the dashboard.",
            next_tool="whoami",
        ) from None
    return _access_request_result(env, view, None)


async def _attach_or_refuse_duplicate(
    env: CallEnv,
    svc: AccessRequestService,
    opts: AccessRequestOptions,
    exc: DuplicatePendingError,
) -> mcp_types.CallToolResult:
    """The duplicate-pending arm (Go: the wire-409 handling, typed in-process).

    Filing is all-or-nothing: a duplicate on a composite means NOTHING was
    filed — attaching would silently swap the composite for the older,
    smaller request. A single target attaches to the existing pending
    request, like the CLI.
    """
    existing_id = exc.existing_request_id
    if opts.target_count() > 1:
        raise ToolError(
            CODE_RESOLVE_FAILED,
            "nothing was filed: one of the requested targets already has a pending "
            f"request ({existing_id})",
            actionable=f'Poll the pending request with request_access {{"request_id": '
            f'"{existing_id}"}} to see what it covers, then either drop that target '
            "from this composite and re-file, or ask your operator to decide the "
            "pending request first.",
            details={"existing_request_id": existing_id},
            next_tool="request_access",
        ) from None
    try:
        attached = await svc.get(existing_id, identity=env.identity)
    except Exception as fetch_exc:
        # The duplicate is actor-scoped so the fetch should succeed; if it
        # doesn't, surface the failure with the id — never a silent swap.
        raise ToolError(
            CODE_INTERNAL_ERROR,
            f"a pending request ({existing_id}) already covers this target, but "
            f"fetching it failed: {fetch_exc}",
            details={"existing_request_id": existing_id},
            next_tool="request_access",
        ) from fetch_exc
    return _access_request_result(env, attached, {"attached_to_existing": True})


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
    "request_access": handle_request_access,
}


async def dispatch_tool_call(
    env: CallEnv, name: str, arguments: dict[str, Any] | None
) -> mcp_types.CallToolResult:
    """Route one authenticated tools/call to its handler.

    ``ToolError`` renders as the coded ``isError`` result (diagnosable
    states are data the model acts on); unexpected failures degrade to
    INTERNAL_ERROR instead of a protocol error, matching the Go posture.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        raise MCPError(_INVALID_PARAMS, f"unknown tool: {name}")
    try:
        require_password_current(env.identity, name)
        return await handler(env, arguments or {})
    except ToolError as err:
        return soft_error_result(env.ctx, err)
    except MCPError:
        raise
    except Exception as exc:
        return soft_error_result(
            env.ctx, ToolError(CODE_INTERNAL_ERROR, f"unexpected failure: {exc}")
        )
