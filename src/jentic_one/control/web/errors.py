"""Service-error to Problem Details mapping for the control web layer."""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse

from jentic_one.control.services.access_requests.errors import (
    AccessRequestNotFoundError,
    CredentialNotFoundForBindError,
    CredentialReferenceAmbiguousError,
    CredentialReferenceUnresolvedError,
    DuplicatePendingError,
    ItemNotOnRequestError,
    ItemNotPendingError,
    NotAReviewerError,
    RequestNotPendingError,
    RequiredFieldMissingError,
    RuleSetNotFoundForBindError,
    RulesNotSupportedForBindError,
    RulesRequiredForBindError,
    UnsupportedAccessRequestItemError,
    UnsupportedScopeGrantError,
)
from jentic_one.control.services.credentials.errors import (
    AgentBindingNotFoundError,
    CredentialNotFoundError,
    ImmutableFieldError,
    InvalidCredentialInputError,
    RuleSetAccessDeniedError,
    RuleSetInUseError,
    RuleSetNameConflictError,
    RuleSetNotFoundError,
    UnsupportedProviderForTypeError,
)
from jentic_one.control.services.toolkits.errors import (
    BindingNotFoundError,
    ConflictingApiBindingError,
    DuplicateBindingError,
    KeyAlreadyRevokedError,
    ToolkitAccessDeniedError,
    ToolkitKeyNotFoundError,
    ToolkitKeysRetiredError,
    ToolkitLevelPermissionsUnsupportedError,
    ToolkitNotFoundError,
)
from jentic_one.shared.db.errors import (
    DatabaseDataError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)
from jentic_one.shared.web.errors import make_service_error_handler

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
}

credential_service_error_handler = make_service_error_handler(_ERROR_MAP)

_TOOLKIT_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    ToolkitNotFoundError: (404, "toolkit_not_found"),
    ToolkitAccessDeniedError: (403, "toolkit_access_denied"),
    ToolkitKeyNotFoundError: (404, "toolkit_key_not_found"),
    BindingNotFoundError: (404, "binding_not_found"),
    DuplicateBindingError: (409, "duplicate_binding"),
    ConflictingApiBindingError: (409, "conflicting_api_binding"),
    KeyAlreadyRevokedError: (409, "key_already_revoked"),
    ToolkitKeysRetiredError: (410, "toolkit_keys_retired"),
    ToolkitLevelPermissionsUnsupportedError: (422, "toolkit_level_permissions_unsupported"),
}

toolkit_service_error_handler = make_service_error_handler(_TOOLKIT_ERROR_MAP)

_ACCESS_REQUEST_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    AccessRequestNotFoundError: (404, "access_request_not_found"),
    DuplicatePendingError: (409, "access_request_duplicate_pending"),
    RequestNotPendingError: (409, "access_request_not_pending"),
    ItemNotPendingError: (409, "access_request_item_not_pending"),
    ItemNotOnRequestError: (422, "access_request_item_not_on_request"),
    NotAReviewerError: (403, "access_request_not_reviewer"),
    CredentialReferenceUnresolvedError: (422, "access_request_credential_unresolved"),
    CredentialReferenceAmbiguousError: (409, "access_request_credential_ambiguous"),
    CredentialNotFoundForBindError: (422, "access_request_credential_not_found"),
    UnsupportedScopeGrantError: (422, "access_request_unsupported_scope"),
    RulesNotSupportedForBindError: (422, "access_request_rules_not_supported_for_bind"),
    RulesRequiredForBindError: (422, "access_request_rules_required_for_bind"),
    RuleSetNotFoundForBindError: (422, "access_request_rule_set_not_found"),
    UnsupportedAccessRequestItemError: (422, "access_request_unsupported_item"),
    RequiredFieldMissingError: (422, "access_request_required_field_missing"),
}


def _access_request_response_hook(
    request: Request, exc: Exception, status_code: int, response: JSONResponse
) -> JSONResponse:
    if isinstance(exc, DuplicatePendingError):
        content: dict[str, object] = json.loads(bytes(response.body))
        content["approve_url"] = exc.approve_url
        content["existing_request_id"] = exc.existing_request_id
        return JSONResponse(
            status_code=status_code,
            content=content,
            media_type="application/problem+json",
        )
    return response


access_request_service_error_handler = make_service_error_handler(
    _ACCESS_REQUEST_ERROR_MAP, response_hook=_access_request_response_hook
)


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
    DatabaseUnavailableError: "The database is temporarily unavailable; please retry.",
}

database_error_handler = make_service_error_handler(_DB_ERROR_MAP, safe_details=_DB_SAFE_DETAILS)
