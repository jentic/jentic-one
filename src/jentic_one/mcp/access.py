"""Connect-session and credential-access tool handlers for the mounted MCP app."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import mcp.types as mcp_types
import structlog
from pydantic import ValidationError

from jentic_one.control.services.credentials.errors import (
    AgentBindingNotFoundError,
    CredentialNotFoundError,
)
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.integrations.connect_session_service import (
    ApiTarget,
    ConnectSessionService,
    SessionAmend,
)
from jentic_one.control.services.integrations.edits import EditRequest, RuleAdd
from jentic_one.control.services.integrations.errors import (
    AmendFieldsMismatchError,
    AuthTypeNotDeclaredError,
    AuthTypeRequiredError,
    BindingSuspendedError,
    ConnectOpenError,
    EditBindingNotFoundError,
    EditOpenError,
    InvalidOAuthAppRegistrationError,
    InvalidPollTokenError,
    ManualFlowsDisabledError,
    NoDeclaredSchemeError,
    NoOpForFlowError,
    PointsAtAccessError,
    RecentlyRejectedError,
    ReservedAuthFieldError,
    RevisionChangedError,
    ScopesNotSupportedError,
    ScopeValidationError,
    SecuritySchemesLookupUnavailableError,
    SessionClosedError,
    TooManyOpenSessionsError,
    TooManyRevisionsError,
    UnknownApiError,
    UnpinnedServerHostError,
)
from jentic_one.control.services.vendors.service import (
    AmbiguousVendorError,
    UnknownVendorError,
    UnsupportedFlowError,
    VendorNotConfiguredError,
    VendorRegistryService,
)
from jentic_one.control.web.errors import check_access_directive
from jentic_one.control.web.routers.credentials import binding_access_response
from jentic_one.control.web.schemas.integrations import (
    SCOPE_COMMENT_MAX,
    ConnectOptionsResponse,
)
from jentic_one.control.web.schemas.permission_rules import PermissionRuleSchema
from jentic_one.mcp.call_context import (
    CallEnv,
    ParamSpec,
    invalid_params,
    normalize_tool_args,
    require_permissions,
)
from jentic_one.mcp.envelopes import (
    CODE_BROKER_DENIED,
    CODE_RESOLVE_FAILED,
    CODE_TRANSPORT_ERROR,
    SCHEMA_VERSION,
    ToolError,
    tool_result,
)
from jentic_one.shared.models import ActorType

logger = structlog.get_logger()

# ── request_connection ────────────────────────────────────────────────────────

_REQUEST_CONNECTION_PARAMS = [
    ParamSpec("vendor", "string"),
    ParamSpec("api", "json"),
    ParamSpec("auth_type", "string"),
    ParamSpec("requested_scopes", "string_list", ("scopes",)),
    ParamSpec("reason", "string"),
    ParamSpec("oauth_app_registration_id", "string", ("registration_id",)),
    ParamSpec("requested_permission_rules", "json", ("permission_rules",)),
]

#: Guidance when the agent already has an open request for the target (Go:
#: ``requestConnectionOpenInstruction``).
_REQUEST_CONNECTION_OPEN_INSTRUCTION = (
    "You already asked for this connection and the request is still open: relay its "
    "approval_url to your human operator instead of asking again. Once they confirm, call "
    "whoami to see the new credential binding, then retry the call that was blocked."
)

#: The operator-relay guidance stamped on every successful result (Go:
#: ``requestConnectionInstruction`` in ``mcp_request_connection.go``).
#: Lane-invariant by construction: it names only whoami and the human
#: approval step, never a stdio-only tool.
_REQUEST_CONNECTION_INSTRUCTION = (
    "Relay the approval_url to your human operator — they open it in their browser and "
    "approve (or reject) the connection; you cannot open it or approve it yourself. Once "
    "they confirm, call whoami to see the new credential binding, then retry the call "
    "that was blocked."
)

#: Replaces :data:`_REQUEST_CONNECTION_INSTRUCTION` for a session a human
#: finishes by entering a credential (or picking an OAuth app) — Go:
#: ``requestConnectionHumanEntryInstruction``, same text. It can take hours and
#: this surface has no status tool, so the agent ends its turn and retries
#: ``execute`` later.
_REQUEST_CONNECTION_HUMAN_ENTRY_INSTRUCTION = (
    "Relay the approval_url to your human user and end your turn: a human enters the "
    "credential (or picks the OAuth app) in the browser, which can take hours, so do not "
    "wait or poll. Later, check your bindings with whoami and retry the call that was "
    "blocked. Asking again for the same API returns this same request."
)

#: ``resolved_flow`` of an OAuth API with no OAuth app to connect through yet.
_FLOW_AWAITING_APP = "awaiting_app"

#: the route's ``auth_type`` bound (``max_length=255``) — Go: ``connectAuthTypeMax``.
_REQUEST_CONNECTION_AUTH_TYPE_MAX = 255

#: the route's ``reason`` bound (``IntegrationsConnectRequest.reason`` —
#: ``max_length=1024``), enforced here because the in-process call skips the
#: route's pydantic validation.
_REQUEST_CONNECTION_REASON_MAX = 1024

#: Upper bound on ``requested_scopes`` — the Go mount's ``connectScopesMax``
#: twin, so neither mount forwards an unbounded list to the vendor authorize URL.
_REQUEST_CONNECTION_SCOPES_MAX = 100
#: Characters in a rule id, as the route's schema bounds ``before``.
_EDIT_RULE_ID_MAX = 30
#: Characters in one scope's reason: the route's own bound, read from the
#: schema that applies it (``EditScopesRequest``), so the mount cannot drift
#: into refusing a body the route accepts.
_SCOPE_COMMENT_MAX = SCOPE_COMMENT_MAX

#: The route's ``oauth_app_registration_id`` bound (``max_length=30``) — the
#: Go mount's ``connectRegistrationMax`` twin.
_REQUEST_CONNECTION_REGISTRATION_MAX = 30

#: The route's ``requested_permission_rules`` bound (``max_length=100``) — the
#: Go mount's ``connectRulesMax`` twin.
_REQUEST_CONNECTION_RULES_MAX = 100

#: The MCP lanes' retry form in the ambiguous-vendor advice (Go:
#: ``requestConnectionRegistrationRetry``).
_REQUEST_CONNECTION_REGISTRATION_RETRY = (
    "call request_connection again with oauth_app_registration_id set to the "
    "registration_id they pick"
)


def _ambiguous_vendor_actionable(vendor: str, *, listed: bool) -> str:
    """The ask-your-user advice (Go: ``ambiguousVendorActionable``, same text).

    Choosing which shared OAuth app mints the credential is the user's policy
    decision, so the agent asks and retries with the id — it never picks.
    """
    which = "ask your human user which app to use and for its registration_id"
    if listed:
        which = (
            "show your human user the apps in details.candidates (name and "
            "registration_id) and ask which one to use"
        )
    return (
        f'Several shared OAuth apps serve vendor "{vendor}", and choosing one is your '
        f"user's decision, not yours: {which}, then {_REQUEST_CONNECTION_REGISTRATION_RETRY}."
    )


async def _ambiguous_vendor_candidates(
    env: CallEnv, registration_ids: list[str]
) -> list[dict[str, str]]:
    """The shared apps an ambiguous connect matched, as ``GET /vendors`` rows.

    Same projection and gate as the Go mount's ``GET /vendors`` read
    (``capabilities:read``), narrowed to the registrations the resolver
    counted. Best effort: a failure yields ``[]`` and the advice still routes
    the choice to the user.
    """
    try:
        require_permissions(env.identity, ["capabilities:read"])
    except ToolError:
        return []
    wanted = set(registration_ids)
    try:
        entries = await VendorRegistryService(env.ctx).list_entries()
    except Exception:
        logger.warning("mcp_request_connection_candidates_failed", exc_info=True)
        return []
    return [
        {
            "registration_id": e.registration_id,
            "name": e.name,
            "display_name": e.display_name,
        }
        for e in entries
        if e.registration_id is not None and e.registration_id in wanted
    ]


def _requested_permission_rules(raw: Any) -> list[dict[str, object]] | None:
    """Validate the rule ask with the route's schema (``PermissionRuleSchema``).

    The in-process call skips the route's pydantic validation, so the same
    model runs here; a malformed ask is an invalid-params error. Returns the
    route's wire shape (``model_dump(exclude_none=True)``), or ``None`` when
    no rules were passed.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise invalid_params(
            "requested_permission_rules must be a list of rule objects "
            "({effect, methods, path, match_mode, operations})"
        )
    if len(raw) > _REQUEST_CONNECTION_RULES_MAX:
        raise invalid_params(
            f"requested_permission_rules must list at most {_REQUEST_CONNECTION_RULES_MAX} "
            f"rules, got {len(raw)}"
        )
    rules: list[dict[str, object]] = []
    for idx, item in enumerate(raw):
        try:
            rule = PermissionRuleSchema.model_validate(item)
        except ValidationError as exc:
            reasons = "; ".join(str(e.get("msg", "")) for e in exc.errors())
            raise invalid_params(f"requested_permission_rules[{idx}]: {reasons}") from None
        rules.append(rule.model_dump(exclude_none=True))
    return rules or None


def _is_human_entry_flow(flow: str) -> bool:
    """A session a human finishes by entering a credential (Go: ``isHumanEntryFlow``)."""
    return flow == _FLOW_AWAITING_APP or flow.startswith("manual_")


def _connect_api_target(raw: Any) -> ApiTarget:
    """The ``api`` argument: a ``{vendor, name, version}`` object or a slug string."""
    if isinstance(raw, str):
        parts = raw.strip().split("/")
        if len(parts) == 3 and all(parts):
            return ApiTarget(vendor=parts[0], name=parts[1], version=parts[2])
    elif isinstance(raw, dict):
        fields = [raw.get(k) for k in ("vendor", "name", "version")]
        if all(isinstance(v, str) and v for v in fields):
            vendor, name, version = (str(v) for v in fields)
            return ApiTarget(vendor=vendor, name=name, version=version)
    raise invalid_params(
        'api must be {"vendor", "name", "version"} or a "vendor/name/version" string'
    )


def _api_target_tool_error(exc: Exception, target: str) -> ToolError | None:
    """The API-target and session-cap service errors as coded tool errors.

    Same codes, prose and ``next_tool`` as the Go mount's ``apiTargetCoded``
    (MCP vocabulary); ``None`` for any other error.
    """
    if isinstance(exc, ManualFlowsDisabledError):
        return ToolError(
            CODE_BROKER_DENIED,
            f"this deployment does not take connect requests for registry APIs ({target}): {exc}",
            actionable="Report the gap to your human user once: ask them to connect a "
            f"credential for {target} in the dashboard and bind this agent — name the auth "
            "type the API's spec declares, the permission rules you need, and why. Then end "
            "your turn and retry later.",
            extra={"retryable": False},
        )
    if isinstance(exc, UnknownApiError):
        return ToolError(
            CODE_RESOLVE_FAILED,
            f"no live API {target} in this deployment's registry: {exc}",
            actionable="Check the identity against the api.vendor/name/version that "
            "inspect_operation reports; if the API is not imported yet, find it with "
            "search_catalog and import it with import_api, then call request_connection again.",
            next_tool="search_catalog",
            extra={"retryable": False},
        )
    if isinstance(exc, AuthTypeRequiredError | AuthTypeNotDeclaredError):
        lead = (
            "The API's spec declares several auth schemes"
            if isinstance(exc, AuthTypeRequiredError)
            else "That auth type is not one the API's spec declares"
        )
        retry = "call request_connection again with auth_type set to one of them"
        if exc.options:
            actionable = (
                f"{lead} ({', '.join(exc.options)}): pick the one the task needs and {retry}."
            )
        else:
            actionable = f"{lead}: read them from the API's spec and {retry}."
        return ToolError(
            CODE_RESOLVE_FAILED,
            f"cannot start a connect session for {target}: {exc}",
            actionable=actionable,
            details={"options": list(exc.options)} if exc.options else None,
            next_tool="request_connection",
            extra={"retryable": False},
        )
    if isinstance(exc, NoDeclaredSchemeError):
        return ToolError(
            CODE_BROKER_DENIED,
            f"API {target} declares no auth scheme a connect session can collect: {exc}",
            actionable="You cannot start this connection. Tell your human user: an operator "
            f"can create the credential for {target} directly in the dashboard (or the API's "
            "spec must declare its auth). Do not retry the connect.",
            extra={"retryable": False},
        )
    if isinstance(exc, UnpinnedServerHostError):
        return ToolError(
            CODE_BROKER_DENIED,
            f"API {target} takes its server host from an unrestricted variable: {exc}",
            actionable="A credential for it cannot be pinned to known hosts, so you cannot "
            "start this connection. Tell your human user: an operator can create the "
            "credential directly in the dashboard (or the API's spec must limit its server "
            "variables to an enum). Do not retry the connect.",
            details={"variables": list(exc.variables)} if exc.variables else None,
            extra={"retryable": False},
        )
    if isinstance(exc, ReservedAuthFieldError):
        return ToolError(
            CODE_BROKER_DENIED,
            f"API {target} injects its key into a header the platform reserves: {exc}",
            actionable="You cannot start this connection. Tell your human user: an operator "
            "can create the credential directly in the dashboard. Do not retry the connect.",
            extra={"retryable": False},
        )
    if isinstance(exc, SecuritySchemesLookupUnavailableError):
        return ToolError(
            CODE_BROKER_DENIED,
            f"this instance cannot read the registry to connect {target}: {exc}",
            actionable="Report it to your human user: the connect service cannot reach the "
            "registry on this deployment. They can connect the credential in the dashboard "
            "instead.",
            extra={"retryable": False},
        )
    if isinstance(exc, TooManyOpenSessionsError):
        return ToolError(
            CODE_BROKER_DENIED,
            f"too many open connect requests are already waiting for a human: {exc}",
            actionable="Do not open more. Relay the approval URLs you already have to your "
            "human user, end your turn, and retry the blocked calls once they act.",
            extra={"retryable": False},
        )
    if isinstance(exc, RecentlyRejectedError):
        return ToolError(
            CODE_BROKER_DENIED,
            f"a human recently rejected this agent's request to connect {target}: {exc}",
            actionable="Do not ask again. Tell your human user the request was rejected and "
            "what you needed it for; continue without this API unless they say otherwise.",
            extra={"retryable": False},
        )
    if isinstance(exc, BindingSuspendedError):
        return ToolError(
            CODE_BROKER_DENIED,
            f"this agent's access to {target} is suspended: {exc}",
            actionable="Your operator suspended your access to this API. Do not ask again; "
            "ask your operator to resume it.",
            extra={"retryable": False},
        )
    return None


def _unknown_vendor_tool_error(exc: UnknownVendorError, *, vendor: str) -> ToolError:
    return ToolError(
        CODE_RESOLVE_FAILED,
        f"cannot start a connect session for vendor {vendor!r}: {exc}",
        actionable='Pass a vendor registry key this deployment supports (e.g. "github"). '
        "For an API outside the registry, call request_connection with api set to its "
        "vendor/name/version instead (find it with search_catalog first); if this "
        "deployment does not take API connect requests, ask your human operator to "
        "connect a credential for it in the dashboard.",
        next_tool="search_catalog",
    )


async def _ambiguous_vendor_tool_error(
    env: CallEnv, exc: AmbiguousVendorError, *, vendor: str, target: str
) -> ToolError:
    """List matching shared OAuth apps so the caller asks its user which one to pin."""
    candidates = await _ambiguous_vendor_candidates(env, exc.registration_ids)
    return ToolError(
        CODE_RESOLVE_FAILED,
        f"cannot start a connect session for {target!r}: {exc}",
        actionable=_ambiguous_vendor_actionable(vendor or exc.vendor, listed=bool(candidates)),
        details={"candidates": candidates} if candidates else None,
        next_tool="request_connection",
    )


def _invalid_registration_tool_error(
    exc: InvalidOAuthAppRegistrationError, *, vendor: str, target: str
) -> ToolError:
    return ToolError(
        CODE_RESOLVE_FAILED,
        f"cannot start a connect session for {target!r}: {exc}",
        actionable="The registration_id does not name an active shared OAuth app for "
        f'"{vendor or target}". Check the id with your human user, or call request_connection '
        "again without oauth_app_registration_id to see the shared apps that serve it.",
        next_tool="request_connection",
    )


def _unsupported_flow_tool_error(
    exc: UnsupportedFlowError | NoOpForFlowError, *, target: str
) -> ToolError:
    """Render an unusable vendor connect flow as a caller-shaped resolve error."""
    return ToolError(
        CODE_RESOLVE_FAILED,
        f"cannot start a connect session for {target!r}: {exc}",
        actionable="This vendor's connect flow is not usable on this deployment, so "
        "this tool cannot connect it: find the API with search_catalog and ask your "
        "human operator to connect a credential for it in the dashboard instead.",
        next_tool="search_catalog",
    )


def _vendor_not_configured_tool_error(exc: VendorNotConfiguredError, *, vendor: str) -> ToolError:
    return ToolError(
        CODE_BROKER_DENIED,
        f"vendor {vendor!r} is registered but not configured on this deployment: {exc}",
        actionable=f"Ask your human operator to configure the {vendor!r} vendor's "
        "OAuth client on this deployment (or connect the credential in the "
        "dashboard), then retry.",
    )


async def handle_request_connection(
    env: CallEnv, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """POST /integrations:connect in-process (Go: ``handleRequestConnection``).

    Create-only (theme-7 Phase 1b): starts a connect session for a registry
    vendor (``vendor``) or a registry API (``api``, with an optional
    ``auth_type``) and returns ``{session_id, approval_url, resolved_flow}``
    plus the operator-relay instruction — for a session a human finishes by
    entering a credential, the end-your-turn instruction and ``next_tool:
    execute``. ``poll_token`` is not surfaced — this surface
    serves no poll leg (the recovery loop is relay approval_url → operator
    approves → confirm via whoami → retry), and the approval_url carries only
    the session id: the agent's owner or ``org:admin`` approves without the
    token. ``agent_id``
    is never taken from arguments: the caller *is* the agent (the route
    refuses a supplied agent_id with 403 for the same reason). Like the route,
    it forwards ``oauth_app_registration_id``, the validated
    ``requested_permission_rules`` and the process's catalog auto-importer and
    security-schemes lookup.
    """
    args = normalize_tool_args(arguments, _REQUEST_CONNECTION_PARAMS)
    vendor = args.get("vendor", "")
    api_target = _connect_api_target(args["api"]) if "api" in args else None
    if bool(vendor) == (api_target is not None):
        raise invalid_params(
            'request_connection requires exactly one of "vendor" (the vendor registry key, '
            'e.g. {"vendor": "github"}) or "api" (e.g. {"api": {"vendor": "stripe-com", '
            '"name": "stripe-com-api", "version": "2024-06-20"}})'
        )
    auth_type = args.get("auth_type") or None
    if auth_type is not None and api_target is None:
        raise invalid_params('auth_type applies to an "api" target only')
    if auth_type is not None and len(auth_type) > _REQUEST_CONNECTION_AUTH_TYPE_MAX:
        raise invalid_params(
            f"auth_type must be at most {_REQUEST_CONNECTION_AUTH_TYPE_MAX} characters, "
            f"got {len(auth_type)}"
        )
    target = (
        f"{api_target.vendor}/{api_target.name}/{api_target.version}"
        if api_target is not None
        else vendor
    )
    reason = args.get("reason", "")
    if len(reason) > _REQUEST_CONNECTION_REASON_MAX:
        raise invalid_params(
            f"reason must be at most {_REQUEST_CONNECTION_REASON_MAX} characters, got {len(reason)}"
        )
    requested_scopes = args.get("requested_scopes") or None
    if requested_scopes is not None and len(requested_scopes) > _REQUEST_CONNECTION_SCOPES_MAX:
        raise invalid_params(
            f"requested_scopes must list at most {_REQUEST_CONNECTION_SCOPES_MAX} scopes, "
            f"got {len(requested_scopes)}"
        )
    registration_id = args.get("oauth_app_registration_id") or None
    if registration_id is not None and len(registration_id) > _REQUEST_CONNECTION_REGISTRATION_MAX:
        raise invalid_params(
            "oauth_app_registration_id must be at most "
            f"{_REQUEST_CONNECTION_REGISTRATION_MAX} characters, got {len(registration_id)}"
        )
    requested_rules = _requested_permission_rules(args.get("requested_permission_rules"))
    try:
        # The route's any-of gate (credentials:connect | credentials:write);
        # agents hold credentials:connect by default.
        require_permissions(env.identity, ["credentials:connect", "credentials:write"])
    except ToolError as exc:
        # The Go special case (requestConnectionError's 403 arm): a 403 on
        # THIS route is the missing credentials:connect permission — an access gap
        # the operator closes with a dashboard grant, not a revoked identity.
        raise ToolError(
            CODE_BROKER_DENIED,
            f"starting a connect session requires the credentials:connect permission: {exc}",
            actionable="Ask your human operator to grant this agent the "
            "credentials:connect permission in the dashboard, then retry "
            "request_connection once they confirm.",
        ) from None

    await _spend_connect_bucket(env, "request_connection")

    # Mirror the route's identity injection: an agent caller connects for
    # itself; a user caller over this mount connects an
    # unbound credential (the tool surface carries no agent_id).
    agent_id = env.identity.sub if env.identity.actor_type == ActorType.AGENT else None
    try:
        created = await ConnectSessionService(
            env.ctx,
            catalog_auto_importer=env.catalog_auto_importer,
            security_schemes_lookup=env.security_schemes_lookup,
        ).create_session(
            vendor_key=vendor,
            api_target=api_target,
            auth_type=auth_type,
            agent_id=agent_id,
            identity=env.identity,
            requested_scopes=requested_scopes,
            requested_permission_rules=requested_rules,
            preferred_flow=None,
            reason=reason or None,
            oauth_app_registration_id=registration_id,
        )
    except (
        ManualFlowsDisabledError,
        UnknownApiError,
        AuthTypeRequiredError,
        AuthTypeNotDeclaredError,
        NoDeclaredSchemeError,
        UnpinnedServerHostError,
        ReservedAuthFieldError,
        SecuritySchemesLookupUnavailableError,
        TooManyOpenSessionsError,
        RecentlyRejectedError,
        BindingSuspendedError,
    ) as exc:
        mapped = _api_target_tool_error(exc, target)
        assert mapped is not None
        raise mapped from None
    except UnknownVendorError as exc:
        raise _unknown_vendor_tool_error(exc, vendor=vendor) from None
    except AmbiguousVendorError as exc:
        raise await _ambiguous_vendor_tool_error(env, exc, vendor=vendor, target=target) from None
    except InvalidOAuthAppRegistrationError as exc:
        raise _invalid_registration_tool_error(exc, vendor=vendor, target=target) from None
    except (UnsupportedFlowError, NoOpForFlowError) as exc:
        raise _unsupported_flow_tool_error(exc, target=target) from None
    except ConnectOpenError as exc:
        # The agent already asked: point it at that open session, which its
        # human reviews once (the Go mount maps the route's 409 connect_open
        # the same way).
        logger.info("mcp_request_connection_open", target=target, session_id=exc.session_id)
        return tool_result(
            env.ctx,
            {
                "schema_version": SCHEMA_VERSION,
                "session_id": exc.session_id,
                "approval_url": exc.approval_url,
                "already_open": True,
                "instruction": _REQUEST_CONNECTION_OPEN_INSTRUCTION,
            },
        )
    except VendorNotConfiguredError as exc:
        raise _vendor_not_configured_tool_error(exc, vendor=vendor) from None

    logger.info(
        "mcp_request_connection",
        target=target,
        session_id=created.session_id,
        flow=created.resolved_flow,
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": created.session_id,
        "approval_url": created.approval_url,
        "resolved_flow": created.resolved_flow,
        "instruction": _REQUEST_CONNECTION_INSTRUCTION,
    }
    if _is_human_entry_flow(created.resolved_flow):
        payload["instruction"] = _REQUEST_CONNECTION_HUMAN_ENTRY_INSTRUCTION
        payload["next_tool"] = "execute"
    return tool_result(env.ctx, payload)


# ── show_access ───────────────────────────────────────────────────────────────

_SHOW_ACCESS_PARAMS = [ParamSpec("credential_id", "string")]


async def handle_show_access(env: CallEnv, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    """GET /credentials/{cid}/agents/{me}/permissions in-process (Go: ``handleShowAccess``).

    The calling agent's own binding to ``credential_id``, in the route's wire
    shape. A credential it is not bound to, or whose binding is suspended
    (which hides the credential from the agent), is RESOLVE_FAILED pointing
    at whoami.
    """
    args = normalize_tool_args(arguments, _SHOW_ACCESS_PARAMS)
    credential_id = args.get("credential_id", "")
    if not credential_id:
        raise invalid_params(
            'show_access requires "credential_id", e.g. {"credential_id": "cred_…"}'
        )
    require_permissions(env.identity, ["credentials:read", "owner:credentials:read"])
    try:
        access = await CredentialService(env.ctx).get_binding_access(
            credential_id, env.identity.sub, identity=env.identity
        )
    except (CredentialNotFoundError, AgentBindingNotFoundError):
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"no active credential binding for {credential_id!r}",
            actionable="check whoami (`jentic whoami`) for your credential bindings and their "
            "credential ids; a suspended binding is hidden until your operator resumes it",
            next_tool="whoami",
        ) from None
    addable = await ConnectSessionService(env.ctx).scope_options(credential_id)
    payload = binding_access_response(access, addable).model_dump(mode="json", by_alias=True)
    payload["schema_version"] = SCHEMA_VERSION
    return tool_result(env.ctx, payload)


# ── inspect_api ───────────────────────────────────────────────────────────────

_INSPECT_API_PARAMS = [
    ParamSpec("api", "json"),
    ParamSpec("oauth_app_registration_id", "string", ("registration_id",)),
]

_CONNECT_OPTIONS_NEXT_STEP = (
    "To connect it, call request_connection (`jentic connect --api <vendor/name/version>`) "
    "with requested_scopes from the scopes marked requestable, and auth_type when it declares "
    "several schemes. If whoami already shows a binding serving this API, change its scopes "
    "with request_access_change instead."
)


async def handle_inspect_api(env: CallEnv, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    """GET …/connect-options for one API, in-process (Go: ``handleInspectAPI``).

    How ``:connect`` would connect the API, scheme by scheme, and which scopes a
    request may name. An API the registry doesn't hold is RESOLVE_FAILED
    pointing at import_api.
    """
    args = normalize_tool_args(arguments, _INSPECT_API_PARAMS)
    if "api" not in args:
        raise invalid_params('inspect_api requires "api", e.g. {"api": "vendor/name/version"}')
    target = _connect_api_target(args["api"])
    registration_id = args.get("oauth_app_registration_id") or None
    if registration_id is not None and len(registration_id) > _REQUEST_CONNECTION_REGISTRATION_MAX:
        raise invalid_params(
            "oauth_app_registration_id must be at most "
            f"{_REQUEST_CONNECTION_REGISTRATION_MAX} characters"
        )
    label = f"{target.vendor}/{target.name}/{target.version}"
    require_permissions(env.identity, ["credentials:connect", "credentials:write"])
    try:
        options = await ConnectSessionService(
            env.ctx, security_schemes_lookup=env.security_schemes_lookup
        ).connect_options(target, registration_id=registration_id)
    except UnknownApiError:
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"the registry holds no API {label}",
            actionable="find it with search_catalog (`jentic catalog search`) and import it "
            "(import_api / `jentic catalog import`), then ask again",
            next_tool="import_api",
        ) from None
    except InvalidOAuthAppRegistrationError as exc:
        # Same refusal request_connection codes: a pin naming no active app.
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"cannot read the connect options for {label}: {exc}",
            actionable="The registration_id does not name an active shared OAuth app for this "
            "API. Check the id with your human user, or call inspect_api again without "
            "oauth_app_registration_id to see the shared apps that serve it.",
            next_tool="inspect_api",
        ) from None
    except SecuritySchemesLookupUnavailableError as exc:
        raise ToolError(
            CODE_BROKER_DENIED,
            f"this instance cannot read the registry to inspect {label}: {exc}",
            actionable="Report it to your human user: the connect service cannot reach the "
            "registry on this deployment.",
            extra={"retryable": False},
        ) from None
    payload = ConnectOptionsResponse.model_validate(asdict(options)).model_dump(mode="json")
    payload["next_step"] = _CONNECT_OPTIONS_NEXT_STEP
    payload["schema_version"] = SCHEMA_VERSION
    return tool_result(env.ctx, payload)


# ── request_access_change / amend_request ─────────────────────────────────────

_REQUEST_ACCESS_CHANGE_PARAMS = [
    ParamSpec("credential_id", "string"),
    ParamSpec("rules", "json"),
    ParamSpec("scopes", "json"),
    ParamSpec("resume", "json"),
    ParamSpec("reason", "string"),
]
_AMEND_REQUEST_PARAMS = [
    ParamSpec("session_id", "string"),
    ParamSpec("rules", "json"),
    ParamSpec("scopes", "json"),
    ParamSpec("resume", "json"),
    ParamSpec("reason", "string"),
    ParamSpec("requested_permission_rules", "json", ("permission_rules",)),
    ParamSpec("requested_scopes", "string_list"),
]

#: Go: ``requestAccessChangeInstruction`` in ``mcp_access.go``.
_REQUEST_ACCESS_CHANGE_INSTRUCTION = (
    "Relay the approval_url to your human operator — they review the change in the browser; "
    "you cannot approve it yourself. Once they confirm, check it with show_access and retry "
    "the call that was blocked. To change the request before then, use amend_request rather "
    "than asking again."
)


async def _spend_connect_bucket(env: CallEnv, tool: str) -> None:
    """The route's per-actor policy, on the bucket the routes share.

    The mount calls the connect-session service in-process, so it bypasses
    the route's limiter: every tool that opens or changes a session spends
    this, or an actor doubles its rate by splitting calls across HTTP and
    MCP. The bucket exists because these calls reach a vendor, and being
    throttled there fails every caller on the install.
    """
    if env.connect_limiter is None:
        raise ToolError(
            CODE_TRANSPORT_ERROR,
            "connect sessions are unavailable: this process has no connect rate limiter",
            actionable="Tell your human operator the MCP mount is misconfigured.",
            extra={"retryable": False},
        )
    outcome = await env.connect_limiter.acquire(env.identity.sub)
    if not outcome.allowed:
        raise ToolError(
            CODE_TRANSPORT_ERROR,
            "connect sessions are rate limited (http 429: rate limit exceeded)",
            actionable=f"Wait briefly and retry {tool}; do not loop on it.",
            extra={"retryable": True, "retry_after_s": outcome.retry_after_s},
        )


def _edit_rule_list(raw: Any, field: str) -> list[dict[str, object]]:
    """Validate a rule list with the route's schema; the route's wire shape back."""
    if not isinstance(raw, list):
        raise invalid_params(f"{field} must be a list of rule objects")
    if len(raw) > _REQUEST_CONNECTION_RULES_MAX:
        raise invalid_params(
            f"{field} must list at most {_REQUEST_CONNECTION_RULES_MAX} rules, got {len(raw)}"
        )
    rules: list[dict[str, object]] = []
    for idx, item in enumerate(raw):
        try:
            rules.append(PermissionRuleSchema.model_validate(item).model_dump(exclude_none=True))
        except ValidationError as exc:
            reasons = "; ".join(str(e.get("msg", "")) for e in exc.errors())
            raise invalid_params(f"{field}[{idx}]: {reasons}") from None
    return rules


def _edit_request(args: dict[str, Any]) -> EditRequest:
    """The edit ask in a tool call's arguments (Go: ``editInputFromArgs``)."""
    rules = args.get("rules") or {}
    if not isinstance(rules, dict):
        raise invalid_params('rules must be an object {"add": […], "remove": […]} or {"set": […]}')
    if rules.get("set") is not None and (rules.get("add") or rules.get("remove")):
        raise invalid_params("pass either add/remove or set, not both")
    add_raw = rules.get("add") or []
    if not isinstance(add_raw, list):
        raise invalid_params('rules.add must be a list of rule objects or {"rule": …} entries')
    if len(add_raw) > _REQUEST_CONNECTION_RULES_MAX:
        raise invalid_params(
            f"rules.add must list at most {_REQUEST_CONNECTION_RULES_MAX} rules, got {len(add_raw)}"
        )
    adds: list[RuleAdd] = []
    for idx, item in enumerate(add_raw):
        wrapped = isinstance(item, dict) and isinstance(item.get("rule"), dict)
        rule = item["rule"] if wrapped else item
        before = item.get("before") if wrapped else None
        # The route takes a rule id here. Anything else reaches resolve_edit's
        # set lookup and raises an unhashable-type TypeError the agent sees as
        # an internal error, with the connect bucket already spent.
        if before is not None and (not isinstance(before, str) or len(before) > _EDIT_RULE_ID_MAX):
            raise invalid_params(
                f"rules.add[{idx}].before must be a rule id of at most "
                f"{_EDIT_RULE_ID_MAX} characters"
            )
        (validated,) = _edit_rule_list([rule], f"rules.add[{idx}]")
        adds.append(RuleAdd(rule=validated, before=before or None))
    remove = rules.get("remove") or []
    if not isinstance(remove, list) or not all(isinstance(r, str) for r in remove):
        raise invalid_params("rules.remove must be a list of rule ids")
    if len(remove) > _REQUEST_CONNECTION_RULES_MAX:
        raise invalid_params(
            f"rules.remove must list at most {_REQUEST_CONNECTION_RULES_MAX} ids, got {len(remove)}"
        )
    resume = args.get("resume", False)
    if not isinstance(resume, bool):
        raise invalid_params("resume must be true or false")
    reason = args.get("reason") or None
    if reason is not None and len(reason) > _REQUEST_CONNECTION_REASON_MAX:
        raise invalid_params(
            f"reason must be at most {_REQUEST_CONNECTION_REASON_MAX} characters, got {len(reason)}"
        )
    return EditRequest(
        add=adds,
        remove=list(remove),
        set_rules=(
            _edit_rule_list(rules["set"], "rules.set") if rules.get("set") is not None else None
        ),
        **_edit_scopes(args.get("scopes")),
        resume=resume,
        reason=reason,
    )


def _edit_scopes(raw: Any) -> dict[str, Any]:
    """The scope change in a tool call: ``{"add": […], "remove": […]}`` or ``{"set": […]}``."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise invalid_params('scopes must be an object {"add": […], "remove": […]} or {"set": […]}')
    comments = raw.get("comments") or {}
    if not isinstance(comments, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in comments.items()
    ):
        raise invalid_params("scopes.comments must map scope names to reasons")
    if len(comments) > _REQUEST_CONNECTION_SCOPES_MAX:
        raise invalid_params(
            f"scopes.comments must carry at most {_REQUEST_CONNECTION_SCOPES_MAX} reasons, "
            f"got {len(comments)}"
        )
    if any(len(reason) > _SCOPE_COMMENT_MAX for reason in comments.values()):
        raise invalid_params(
            f"each scopes.comments reason must be at most {_SCOPE_COMMENT_MAX} characters"
        )
    for key in ("add", "remove", "set"):
        value = raw.get(key)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(s, str) for s in value)
        ):
            raise invalid_params(f"scopes.{key} must be a list of scope names")
        if value is not None and len(value) > _REQUEST_CONNECTION_SCOPES_MAX:
            raise invalid_params(
                f"scopes.{key} must list at most {_REQUEST_CONNECTION_SCOPES_MAX} scopes, "
                f"got {len(value)}"
            )
    if raw.get("set") is not None and (raw.get("add") or raw.get("remove")):
        raise invalid_params("pass either add/remove or set, not both")
    return {
        "add_scopes": list(raw.get("add") or []),
        "remove_scopes": list(raw.get("remove") or []),
        "set_scopes": list(raw["set"]) if raw.get("set") is not None else None,
        "scope_comments": dict(comments),
    }


def _edit_refusal(env: CallEnv, exc: Exception, what: str) -> ToolError:
    """A refused edit or amend as a coded error (Go: ``editErrorResult``)."""
    extra: dict[str, Any] | None = None
    next_tool = ""
    if isinstance(exc, PointsAtAccessError) and exc.credential_id is not None:
        extra = {
            "agent_directive": check_access_directive(
                credential_id=exc.credential_id, agent_id=exc.agent_id, base_url=env.base_url
            )
        }
        next_tool = "show_access"
    if isinstance(exc, ScopeValidationError):
        return ToolError(
            CODE_RESOLVE_FAILED,
            f"{what} was refused: {exc}",
            actionable="Ask only for scopes listed in scope_catalog (show_access), then retry.",
            next_tool="show_access",
            extra={"unknown_scopes": exc.unknown},
        )
    # The three refusals the generic arm's "fix the request, then retry" is
    # wrong for: the request as sent is fine, and what has to happen next is
    # not another call of this tool.
    if isinstance(exc, RevisionChangedError):
        return ToolError(
            CODE_RESOLVE_FAILED,
            f"{what} was refused: {exc}",
            actionable=(
                "Someone changed this request at the same moment. Send amend_request again "
                "with the same fields: it resolves against the request as it is now."
            ),
            next_tool="amend_request",
            extra={"retryable": True, "current_revision_id": exc.current_revision_id},
        )
    if isinstance(exc, SessionClosedError):
        return ToolError(
            CODE_RESOLVE_FAILED,
            f"{what} was refused: {exc}",
            actionable=(
                "This request is closed — a human approved, rejected or cancelled it, so no "
                "change to it can land. Read your access with show_access; ask again only if "
                "it still lacks what you need."
            ),
            next_tool="show_access",
            # Merged, not replaced: the arm above built the ``check_access``
            # directive naming this binding, and a pointer at show_access with
            # nothing to go with it is half an answer.
            extra={**(extra or {}), "retryable": False, "session_id": exc.session_id},
        )
    if isinstance(exc, TooManyRevisionsError):
        return ToolError(
            CODE_RESOLVE_FAILED,
            f"{what} was refused: {exc}",
            actionable=(
                "This request holds as many revisions as one request may, so it cannot be "
                "changed again. Only your operator can move it now, by approving or rejecting "
                "it — say what you still need and wait for their decision."
            ),
            extra={"retryable": False, "limit": exc.limit},
        )
    return ToolError(
        CODE_RESOLVE_FAILED,
        f"{what} was refused: {exc}",
        actionable=(
            "Your request didn't match your current access. Check it with show_access, then retry."
            if next_tool
            else "Fix the request the error names, then retry."
        ),
        next_tool=next_tool,
        extra=extra,
    )


_EDIT_REFUSALS = (
    PointsAtAccessError,
    RecentlyRejectedError,
    ConnectOpenError,
    EditBindingNotFoundError,
    ScopesNotSupportedError,
    ScopeValidationError,
    AmendFieldsMismatchError,
    InvalidPollTokenError,
    RevisionChangedError,
    SessionClosedError,
    TooManyOpenSessionsError,
    TooManyRevisionsError,
)


async def handle_request_access_change(
    env: CallEnv, arguments: dict[str, Any]
) -> mcp_types.CallToolResult:
    """POST /integrations:edit-connection in-process (Go: ``handleRequestAccessChange``).

    The caller is the agent whose binding changes (``agent_id`` is never an
    argument). An edit already open for the binding comes back as that
    session with ``next_tool: amend_request``; the poll token is never
    surfaced.
    """
    args = normalize_tool_args(arguments, _REQUEST_ACCESS_CHANGE_PARAMS)
    credential_id = args.get("credential_id", "")
    if not credential_id:
        raise invalid_params('request_access_change requires "credential_id"')
    request = _edit_request(args)
    require_permissions(env.identity, ["credentials:connect", "credentials:write"])
    await _spend_connect_bucket(env, "request_access_change")
    try:
        created = await ConnectSessionService(env.ctx).create_edit_session(
            credential_id=credential_id,
            agent_id=env.identity.sub,
            identity=env.identity,
            request=request,
        )
    except EditOpenError as exc:
        return tool_result(
            env.ctx,
            {
                "schema_version": SCHEMA_VERSION,
                "session_id": exc.session_id,
                "already_open": True,
                "instruction": "You already asked for a change to this binding and it is still "
                "open: change it with amend_request instead of asking again.",
                "next_tool": "amend_request",
            },
        )
    except _EDIT_REFUSALS as exc:
        raise _edit_refusal(env, exc, "the access change") from None
    return tool_result(
        env.ctx,
        {
            "schema_version": SCHEMA_VERSION,
            "session_id": created.session_id,
            "approval_url": created.approval_url,
            "revision_id": created.revision_id,
            "resolved_flow": created.resolved_flow,
            "instruction": _REQUEST_ACCESS_CHANGE_INSTRUCTION,
        },
    )


async def handle_amend_request(env: CallEnv, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
    """POST /connect-sessions/{id}:amend in-process (Go: ``handleAmendRequest``)."""
    args = normalize_tool_args(arguments, _AMEND_REQUEST_PARAMS)
    session_id = args.get("session_id", "")
    if not session_id:
        raise invalid_params('amend_request requires "session_id"')
    fields = {k for k in args if k != "session_id"}
    if not fields:
        raise invalid_params("an amend must change something")
    request = _edit_request(args)
    requested_scopes = args.get("requested_scopes")
    if requested_scopes is not None and len(requested_scopes) > _REQUEST_CONNECTION_SCOPES_MAX:
        raise invalid_params(
            f"requested_scopes must list at most {_REQUEST_CONNECTION_SCOPES_MAX} scopes, "
            f"got {len(requested_scopes)}"
        )
    # Every bound applied before the bucket is spent, as the edit tool's own
    # coercion is: a call refused for its shape must not cost the actor the
    # quota it would have needed to send the right one.
    requested_rules = (
        _edit_rule_list(args["requested_permission_rules"], "requested_permission_rules")
        if "requested_permission_rules" in fields
        else None
    )
    require_permissions(env.identity, ["credentials:connect", "credentials:write"])
    await _spend_connect_bucket(env, "amend_request")
    amend = SessionAmend(
        fields=frozenset(fields),
        rules=request if "rules" in fields else None,
        resume=request.resume,
        reason=request.reason,
        requested_permission_rules=requested_rules,
        requested_scopes=list(args["requested_scopes"]) if "requested_scopes" in fields else None,
        scopes=request if "scopes" in fields else None,
    )
    try:
        amended = await ConnectSessionService(env.ctx).amend_session(
            session_id, identity=env.identity, amend=amend
        )
    except _EDIT_REFUSALS as exc:
        raise _edit_refusal(env, exc, "the amend") from None
    return tool_result(
        env.ctx,
        {
            "schema_version": SCHEMA_VERSION,
            "session_id": amended.session_id,
            "revision_id": amended.revision_id,
            "revision_number": amended.revision_number,
            "instruction": "Amended. The approval_url is unchanged; tell your human user the "
            "request changed so they review the new version.",
        },
    )
