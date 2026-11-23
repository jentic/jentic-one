"""Authenticated call context, argument normalization, and permission gates for MCP tools."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import mcp.types as mcp_types
from mcp.shared.exceptions import MCPError

from jentic_one.mcp.envelopes import CODE_NOT_AUTHENTICATED, ToolError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import compute_effective
from jentic_one.shared.catalog.protocols import (
    CatalogAutoImportProtocol,
    SecuritySchemesLookupProtocol,
)
from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter

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
    #: this request's declared ``_meta`` client capabilities (per request —
    #: there is no session to remember them).
    client_capabilities: dict[str, Any] = field(default_factory=dict)
    #: this request's ``_meta`` clientInfo, for logging only.
    client_name: str | None = None
    client_version: str | None = None
    #: the ``requestState`` a multi-round-trip retry echoes back, if any.
    request_state: str | None = None
    #: the protocol version this request was served under (``None`` when unknown).
    protocol_version: str | None = None
    #: the process-level catalog auto-importer the HTTP connect route threads
    #: into ``ConnectSessionService`` (``app.state.catalog_auto_importer``);
    #: ``None`` when this process does not serve the registry.
    catalog_auto_importer: CatalogAutoImportProtocol | None = None
    #: the process-level registry lookup the HTTP connect route threads into
    #: ``ConnectSessionService`` (``app.state.security_schemes_lookup``) for
    #: sessions that target a registry API; ``None`` when this process cannot
    #: read the registry.
    security_schemes_lookup: SecuritySchemesLookupProtocol | None = None
    #: the control surface's per-actor connect limiter for ``request_connection``
    #: (``app.state.mcp_connect_limiter``, on the configured shared-state
    #: backend); ``None`` when the process does not wire one.
    connect_limiter: RateLimiter | None = None


ToolCallResult = mcp_types.CallToolResult | mcp_types.InputRequiredResult

Handler = Callable[[CallEnv, dict[str, Any]], Awaitable[ToolCallResult]]


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


# ── permission enforcement (same permissions as the REST routes fronted) ────


def require_permissions(identity: Identity, required: list[str]) -> None:
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
        # Lane-aware prose (#1327 deferred work): the stdio taxonomy points
        # this code at get_started, which this mount does not serve. whoami
        # DOES resolve here (the credential authenticated; it lacks permissions),
        # so it is the honest next step on this lane.
        actionable="call whoami to see this connection's identity and granted "
        "permissions, and relay the missing ones to your operator — permissions are "
        "granted by a human in the Jentic One dashboard",
    )


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
