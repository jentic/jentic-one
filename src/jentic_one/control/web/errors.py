"""Service-error to Problem Details mapping for the control web layer."""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse

from jentic_one.control.services.access_requests.errors import (
    AccessRequestNotFoundError,
    CredentialNotFoundForBindError,
    DuplicatePendingError,
    ItemNotOnRequestError,
    ItemNotPendingError,
    NotAReviewerError,
    PrerequisiteNotMetError,
    RequestNotPendingError,
    RequiredFieldMissingError,
    RulesNotSupportedForBindError,
    ToolkitNotVisibleError,
    ToolkitReferenceAmbiguousError,
    ToolkitReferenceUnresolvedError,
    UnsupportedScopeGrantError,
)
from jentic_one.control.services.credentials.errors import (
    CredentialNotFoundError,
    ImmutableFieldError,
    InvalidCredentialInputError,
    UnsupportedProviderForTypeError,
)
from jentic_one.control.services.toolkits.errors import (
    BindingNotFoundError,
    ConflictingApiBindingError,
    DuplicateBindingError,
    KeyAlreadyRevokedError,
    ToolkitKeyNotFoundError,
    ToolkitNotFoundError,
)
from jentic_one.shared.web.errors import make_service_error_handler

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    CredentialNotFoundError: (404, "credential_not_found"),
    ImmutableFieldError: (409, "immutable_field"),
    UnsupportedProviderForTypeError: (422, "unsupported_provider_for_type"),
    InvalidCredentialInputError: (400, "invalid_credential_input"),
}

credential_service_error_handler = make_service_error_handler(_ERROR_MAP)

_TOOLKIT_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    ToolkitNotFoundError: (404, "toolkit_not_found"),
    ToolkitKeyNotFoundError: (404, "toolkit_key_not_found"),
    BindingNotFoundError: (404, "binding_not_found"),
    DuplicateBindingError: (409, "duplicate_binding"),
    ConflictingApiBindingError: (409, "conflicting_api_binding"),
    KeyAlreadyRevokedError: (409, "key_already_revoked"),
}

toolkit_service_error_handler = make_service_error_handler(_TOOLKIT_ERROR_MAP)

_ACCESS_REQUEST_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    AccessRequestNotFoundError: (404, "access_request_not_found"),
    PrerequisiteNotMetError: (403, "access_request_prerequisite_not_met"),
    DuplicatePendingError: (409, "access_request_duplicate_pending"),
    RequestNotPendingError: (409, "access_request_not_pending"),
    ItemNotPendingError: (409, "access_request_item_not_pending"),
    ItemNotOnRequestError: (422, "access_request_item_not_on_request"),
    NotAReviewerError: (403, "access_request_not_reviewer"),
    ToolkitReferenceUnresolvedError: (422, "access_request_toolkit_unresolved"),
    ToolkitReferenceAmbiguousError: (409, "access_request_toolkit_ambiguous"),
    ToolkitNotVisibleError: (403, "access_request_toolkit_not_visible"),
    CredentialNotFoundForBindError: (422, "access_request_credential_not_found"),
    UnsupportedScopeGrantError: (422, "access_request_unsupported_scope"),
    RulesNotSupportedForBindError: (422, "access_request_rules_not_supported_for_bind"),
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
