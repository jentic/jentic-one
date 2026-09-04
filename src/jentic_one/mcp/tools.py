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
pointer — except ``search_catalog``, whose 403 is a missing-scope fact the
agent can fix itself (BROKER_DENIED + request_access, the Go special case).
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import mcp.types as mcp_types
from jentic.problem_details import Forbidden, Unauthorized
from mcp.shared.exceptions import MCPError

from jentic_one.admin.services.errors import JobNotFoundError
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.service_account_service import ServiceAccountService
from jentic_one.auth.web.routers.identity import (
    _resolve_agent,
    _resolve_service_account,
    _resolve_user,
)
from jentic_one.mcp import execute as ex
from jentic_one.mcp.envelopes import (
    CODE_BROKER_DENIED,
    CODE_INTERNAL_ERROR,
    CODE_NOT_AUTHENTICATED,
    CODE_RESOLVE_FAILED,
    SCHEMA_VERSION,
    ToolError,
    soft_error_result,
    tool_result,
)
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.registry.services.errors import (
    ArchivedRevisionPinError,
    InvalidApiFilterError,
    OperationNotFoundError,
    SearchUnavailableError,
)
from jentic_one.registry.services.inspect.models import SUMMARY_LOAD_OPTIONS
from jentic_one.registry.services.inspect.service import InspectService
from jentic_one.registry.services.inspect.url_lookup import URLLookupService
from jentic_one.registry.services.search_service import SearchService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import compute_effective
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


# ── dispatch ──────────────────────────────────────────────────────────────────

#: name → handler for every tool this mount serves (must cover
#: :data:`jentic_one.mcp.spec.SERVED_TOOLS` exactly — pinned by the drift test).
HANDLERS: dict[str, Handler] = {
    "whoami": handle_whoami,
    "search_apis": handle_search_apis,
    "inspect_operation": handle_inspect_operation,
    "search_catalog": handle_search_catalog,
    "execute": handle_execute,
    "execute_read": handle_execute_read,
    "get_execution_result": handle_get_execution_result,
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
