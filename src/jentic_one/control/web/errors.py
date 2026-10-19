"""Service-error to Problem Details mapping for the control web layer."""

from __future__ import annotations

import json
import re

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from jentic_one.control.services.credentials.errors import (
    AgentBindingNotFoundError,
    CredentialNotFoundError,
    ImmutableFieldError,
    InvalidCredentialInputError,
    RuleSetAccessDeniedError,
    RuleSetAttachDeniedError,
    RuleSetInUseError,
    RuleSetNameConflictError,
    RuleSetNotFoundError,
    UnsupportedProviderForTypeError,
)
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectSessionService,
)
from jentic_one.control.services.integrations.device_authorization import (
    DeviceAuthorizationError,
)
from jentic_one.control.services.integrations.errors import (
    AgentInactiveError,
    AgentNotFoundError,
    AmendFieldsMismatchError,
    AuthTypeNotDeclaredError,
    AuthTypeRequiredError,
    BaselineChangedError,
    ConfirmationForbiddenError,
    ConfirmKindMismatchError,
    ConnectOpenError,
    ConnectSessionServiceError,
    EditBindingNotFoundError,
    EditOpenError,
    ExistingCredentialNotFoundError,
    InsufficientGrantedScopesError,
    InvalidOAuthAppRegistrationError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    ManualFlowsDisabledError,
    NoDeclaredSchemeError,
    NoOpForFlowError,
    NothingToChangeError,
    OAuthAppChangedError,
    OwnClientInvalidError,
    PointsAtAccessError,
    RecentlyRejectedError,
    ReservedAuthFieldError,
    ReviewStaleError,
    RevisionChangedError,
    RevisionRequiredError,
    RuleSetConfirmForbiddenError,
    RulesRequiredError,
    SchemeChangedError,
    ScopesNotSupportedError,
    ScopeValidationError,
    ScopeWideningForbiddenError,
    SecuritySchemesLookupUnavailableError,
    ServersChangedError,
    SessionClosedError,
    SessionNotFoundError,
    TooManyOpenSessionsError,
    UnknownApiError,
    UnknownRuleError,
    UnpinnedServerHostError,
)
from jentic_one.control.services.oauth_app_registrations.errors import (
    InvalidOAuthAppRegistrationInputError,
    OAuthAppRegistrationInUseError,
    OAuthAppRegistrationNotFoundError,
    SecretRotationNotSupportedError,
)
from jentic_one.control.services.vendors.service import (
    AmbiguousVendorError,
    UnknownVendorError,
    UnsupportedFlowError,
    VendorNotConfiguredError,
)
from jentic_one.shared.db.errors import (
    DatabaseDataError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web.errors import (
    DB_UNAVAILABLE_SAFE_DETAILS,
    make_service_error_handler,
    register_validation_enricher,
)

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    CredentialNotFoundError: (404, "credential_not_found"),
    ImmutableFieldError: (409, "immutable_field"),
    UnsupportedProviderForTypeError: (422, "unsupported_provider_for_type"),
    InvalidCredentialInputError: (400, "invalid_credential_input"),
    AgentBindingNotFoundError: (404, "agent_binding_not_found"),
    RuleSetNotFoundError: (404, "rule_set_not_found"),
    RuleSetNameConflictError: (409, "rule_set_name_conflict"),
    RuleSetInUseError: (409, "rule_set_in_use"),
    RuleSetAccessDeniedError: (403, "rule_set_access_denied"),
    RuleSetAttachDeniedError: (403, "rule_set_attach_denied"),
}

_CREDENTIAL_SAFE_DETAILS: dict[type[Exception], str] = {
    # ``RuleSetInUseError`` guards deletion against the full org-wide binding
    # count (#1858), so its raw ``str(exc)`` stays in server logs while the
    # client detail does not disclose how many invisible bindings reference the
    # rule set.
    RuleSetInUseError: (
        "Permission rule set is still referenced by one or more bindings; detach them first"
    ),
}

credential_service_error_handler = make_service_error_handler(
    _ERROR_MAP, safe_details=_CREDENTIAL_SAFE_DETAILS
)


# Connect-session flow errors. The MRO walk maps subclasses first, so the
# ``ConnectSessionServiceError`` base is the safety net for unmapped
# subclasses (500 with a static detail — never ``str(exc)``, which could
# carry internals).
#
# ``InvalidPollTokenError`` is deliberately the only answer for "session
# missing", "token mismatch" and "not the agent's owner / org:admin" on the
# session endpoints (review / confirm / status / cancel): session ids travel
# in approval URLs, so a 404-vs-403 split would be a session-id enumeration
# oracle. Its client detail is static for the same reason.
_CONNECT_SESSION_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    SessionNotFoundError: (404, "connect_session_not_found"),
    InvalidPollTokenError: (403, "invalid_poll_token"),
    InvalidStateTransitionError: (409, "connect_session_invalid_state"),
    ConfirmationForbiddenError: (403, "connect_session_confirmation_forbidden"),
    AgentNotFoundError: (400, "connect_session_agent_not_found"),
    AgentInactiveError: (409, "connect_session_agent_inactive"),
    ScopeValidationError: (400, "connect_session_unknown_scopes"),
    NoOpForFlowError: (400, "connect_session_unsupported_flow"),
    InvalidOAuthAppRegistrationError: (400, "invalid_oauth_app_registration"),
    OAuthAppChangedError: (409, "connect_session_oauth_app_changed"),
    ManualFlowsDisabledError: (404, "manual_flows_disabled"),
    UnknownApiError: (404, "unknown_api"),
    SecuritySchemesLookupUnavailableError: (503, "security_schemes_lookup_unavailable"),
    AuthTypeRequiredError: (400, "auth_type_required"),
    AuthTypeNotDeclaredError: (422, "auth_type_not_declared"),
    NoDeclaredSchemeError: (409, "no_declared_scheme"),
    ReservedAuthFieldError: (422, "reserved_auth_field"),
    UnpinnedServerHostError: (409, "host_variable_not_pinned"),
    SchemeChangedError: (409, "scheme_changed"),
    ServersChangedError: (409, "servers_changed"),
    ReviewStaleError: (409, "review_stale"),
    RulesRequiredError: (422, "rules_required"),
    ConfirmKindMismatchError: (400, "confirm_kind_mismatch"),
    TooManyOpenSessionsError: (429, "too_many_open_sessions"),
    RecentlyRejectedError: (429, "recently_rejected"),
    ExistingCredentialNotFoundError: (404, "existing_credential_not_found"),
    InsufficientGrantedScopesError: (409, "insufficient_granted_scopes"),
    OwnClientInvalidError: (400, "own_oauth_client_invalid"),
    EditBindingNotFoundError: (404, "agent_binding_not_found"),
    NothingToChangeError: (422, "nothing_to_change"),
    UnknownRuleError: (422, "unknown_rule"),
    EditOpenError: (409, "edit_open"),
    RevisionChangedError: (409, "revision_changed"),
    BaselineChangedError: (409, "baseline_changed"),
    RuleSetConfirmForbiddenError: (403, "rule_set_confirm_forbidden"),
    RevisionRequiredError: (422, "revision_required"),
    SessionClosedError: (409, "session_closed"),
    ConnectOpenError: (409, "connect_open"),
    AmendFieldsMismatchError: (422, "amend_fields_mismatch"),
    ScopesNotSupportedError: (422, "scopes_not_supported"),
    ScopeWideningForbiddenError: (403, "scope_widening_forbidden"),
    ConnectSessionServiceError: (500, "connect_session_error"),
}

_CONNECT_SESSION_SAFE_DETAILS: dict[type[Exception], str] = {
    InvalidPollTokenError: "Connect session not found or not accessible to the caller",
    ConnectSessionServiceError: "Internal error handling the connect session.",
}


#: What an agent should do when an edit or amend refusal means its picture of
#: its access is wrong (the broker's ``agent_directive`` shape).
_CHECK_ACCESS_INSTRUCTION = (
    "Your request didn't match your current access. Check it with show_access "
    "(`jentic access show <credential_id>`), then retry."
)


def check_access_directive(
    *, credential_id: str, agent_id: str | None, base_url: str
) -> dict[str, object]:
    """The ``check_access`` directive pointing an agent at a binding's current access.

    Shared with the hosted MCP mount, which renders the same refusals.
    """
    parameters: dict[str, object] = {
        "credential_id": credential_id,
        "suggested_command": f"jentic access show {credential_id}",
    }
    if agent_id is not None:
        parameters["access_url"] = (
            f"{base_url.rstrip('/')}/credentials/{credential_id}/agents/{agent_id}/permissions"
        )
    return {
        "strategy": "check_access",
        "parameters": parameters,
        "human_readable_instruction": _CHECK_ACCESS_INSTRUCTION,
    }


#: Request fields carrying permission rules on the edit and amend routes.
_RULE_FIELDS = frozenset({"rules", "requested_permission_rules"})
_AMEND_PATH = re.compile(r"/connect-sessions/(?P<session_id>[^/]+):amend$")


async def _edit_rules_validation_enricher(
    request: Request, exc: RequestValidationError
) -> dict[str, object] | None:
    """A malformed rule on an edit or amend points the agent at its current access.

    Only the rule fields count: a rule the binding's schema refuses usually
    means the caller is working from a wrong picture of its access. The
    credential comes from the edit request's body, or from the session an
    amend names.
    """
    if not any(_RULE_FIELDS.intersection(map(str, err.get("loc", ()))) for err in exc.errors()):
        return None
    path = request.url.path
    credential_id: str | None = None
    agent_id: str | None = None
    if path.endswith("/integrations:edit-connection"):
        body = exc.body if isinstance(exc.body, dict) else {}
        raw = body.get("credential_id")
        credential_id = raw if isinstance(raw, str) and raw else None
        raw_agent = body.get("agent_id")
        agent_id = raw_agent if isinstance(raw_agent, str) and raw_agent else None
    elif (match := _AMEND_PATH.search(path)) is not None:
        ctx = getattr(request.app.state, "ctx", None)
        if ctx is None:
            return None
        target = await ConnectSessionService(ctx).edit_binding_of(match["session_id"])
        if target is None:
            return None
        credential_id, agent_id = target
    if credential_id is None:
        return None
    return {
        "agent_directive": check_access_directive(
            credential_id=credential_id, agent_id=agent_id, base_url=str(request.base_url)
        )
    }


register_validation_enricher(_edit_rules_validation_enricher)


def _connect_session_response_hook(
    request: Request, exc: Exception, status_code: int, response: JSONResponse
) -> JSONResponse:
    # Structured extensions — all server data (scheme names, scope names,
    # limits), never echoed request input.
    extra: dict[str, object] = {}
    headers: dict[str, str] = {}
    if isinstance(exc, ScopeValidationError):
        extra["unknown_scopes"] = exc.unknown
    elif isinstance(exc, AuthTypeRequiredError | AuthTypeNotDeclaredError):
        extra["options"] = exc.options
    elif isinstance(exc, InsufficientGrantedScopesError):
        extra["missing_scopes"] = exc.missing
    elif isinstance(exc, UnpinnedServerHostError):
        extra["variables"] = exc.variables
    elif isinstance(exc, TooManyOpenSessionsError):
        extra.update(scope=exc.scope, limit=exc.limit)
    elif isinstance(exc, ConfirmKindMismatchError):
        extra["allowed_kinds"] = exc.allowed
    elif isinstance(exc, RecentlyRejectedError):
        headers["Retry-After"] = str(exc.retry_after_seconds)
    elif isinstance(exc, EditOpenError | SessionClosedError):
        extra["session_id"] = exc.session_id
    elif isinstance(exc, ConnectOpenError):
        extra.update(session_id=exc.session_id, approval_url=exc.approval_url)
    elif isinstance(exc, AmendFieldsMismatchError):
        extra["fields"] = exc.fields
    if isinstance(exc, PointsAtAccessError) and exc.credential_id is not None:
        extra["agent_directive"] = check_access_directive(
            credential_id=exc.credential_id, agent_id=exc.agent_id, base_url=str(request.base_url)
        )
    elif isinstance(exc, RevisionChangedError | BaselineChangedError):
        extra["current_revision_id"] = exc.current_revision_id
    if not extra and not headers:
        return response
    content: dict[str, object] = json.loads(bytes(response.body))
    content.update(extra)
    return JSONResponse(
        status_code=status_code,
        content=content,
        media_type="application/problem+json",
        headers=headers or None,
    )


connect_session_error_handler = make_service_error_handler(
    _CONNECT_SESSION_ERROR_MAP,
    response_hook=_connect_session_response_hook,
    safe_details=_CONNECT_SESSION_SAFE_DETAILS,
)

# A vendor-side failure during ``:confirm`` (device-authorization ``begin``
# rejected by the vendor) is not a server fault and not permanent: the
# session is rolled back to ``created``, so the human can retry once the
# vendor recovers. 502 + ``retryable`` beats the raw 500 the human can't
# act on. Static detail — the exception message carries the vendor HTTP
# status, which is fine, but keeping the client detail static is one less
# thing to audit when the message evolves.
_DEVICE_AUTH_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    DeviceAuthorizationError: (502, "vendor_upstream_error"),
}

_DEVICE_AUTH_SAFE_DETAILS: dict[type[Exception], str] = {
    DeviceAuthorizationError: (
        "The vendor rejected the authorization request; the session is still "
        "confirmable — retry once the vendor recovers"
    ),
}


def _device_auth_response_hook(
    request: Request, exc: Exception, status_code: int, response: JSONResponse
) -> JSONResponse:
    content: dict[str, object] = json.loads(bytes(response.body))
    content["retryable"] = True
    return JSONResponse(
        status_code=status_code,
        content=content,
        media_type="application/problem+json",
    )


device_authorization_error_handler = make_service_error_handler(
    _DEVICE_AUTH_ERROR_MAP,
    response_hook=_device_auth_response_hook,
    safe_details=_DEVICE_AUTH_SAFE_DETAILS,
)

_VENDOR_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    UnknownVendorError: (404, "unknown_vendor"),
    AmbiguousVendorError: (400, "ambiguous_vendor"),
    UnsupportedFlowError: (400, "unsupported_flow"),
    VendorNotConfiguredError: (503, "vendor_not_configured"),
}


def _vendor_response_hook(
    request: Request, exc: Exception, status_code: int, response: JSONResponse
) -> JSONResponse:
    # ``ambiguous_vendor`` names the connect key and the shared apps it
    # counted, so a client can list exactly those for its user to pick from.
    if not isinstance(exc, AmbiguousVendorError):
        return response
    content: dict[str, object] = json.loads(bytes(response.body))
    content.update(vendor=exc.vendor, registration_ids=list(exc.registration_ids))
    return JSONResponse(
        status_code=status_code, content=content, media_type="application/problem+json"
    )


vendor_error_handler = make_service_error_handler(
    _VENDOR_ERROR_MAP, response_hook=_vendor_response_hook
)


_OAUTH_APP_REGISTRATION_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    OAuthAppRegistrationNotFoundError: (404, "oauth_app_registration_not_found"),
    OAuthAppRegistrationInUseError: (409, "oauth_app_registration_in_use"),
    InvalidOAuthAppRegistrationInputError: (400, "invalid_oauth_app_registration_input"),
    SecretRotationNotSupportedError: (409, "secret_rotation_not_supported"),
}

oauth_app_registration_error_handler = make_service_error_handler(_OAUTH_APP_REGISTRATION_ERROR_MAP)


# A DB write failure that escapes a service unmapped is not a server fault: a
# constraint collision is a 409, a value too long for its column is a
# client-fixable 400 (#690), and a transient outage is a retryable 503. Map them
# to structured Problem Details instead of leaking a bare 500.
_DB_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    DatabaseIntegrityError: (409, "conflict"),
    DatabaseDataError: (400, "invalid_input"),
    DatabaseUnavailableError: (503, "database_unavailable"),
}

# The wrapped errors carry the raw SQLAlchemy message — full SQL statement,
# bound parameters, and connection URL. Echoing that leaks internals (CWE-209),
# so the client gets a static, generic detail while the raw message is logged
# server-side (handled by the factory).
_DB_SAFE_DETAILS: dict[type[Exception], str] = {
    DatabaseIntegrityError: "The request conflicts with the current state of the resource.",
    DatabaseDataError: "A field value exceeds the maximum length allowed.",
    **DB_UNAVAILABLE_SAFE_DETAILS,
}

database_error_handler = make_service_error_handler(_DB_ERROR_MAP, safe_details=_DB_SAFE_DETAILS)

# A tampered/garbage pagination cursor is a client fault, not a server one:
# map it to a 400 like the admin surface (`admin/web/errors.py`) and the auth
# surface do. Registered as its own handler because `InvalidCursorError` lives
# outside the control service-error hierarchies (it's a shared pagination
# error, ultimately a ValueError).
_CURSOR_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    InvalidCursorError: (400, "invalid_cursor"),
}

cursor_error_handler = make_service_error_handler(_CURSOR_ERROR_MAP)
