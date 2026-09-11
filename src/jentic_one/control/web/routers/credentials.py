"""Credentials router — CRUD + connect flow for credential management."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.permission_rule_sets import (
    PermissionRuleSet,
    PermissionRuleSetRule,
)
from jentic_one.control.services.credentials.connect_service import (
    ConnectFlowError,
    ConnectService,
)
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.providers.base import (
    NotConnectableError,
    ProviderError,
)
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectRequest,
)
from jentic_one.control.services.credentials.schemas.credentials import (
    CredentialCreate,
    CredentialRedactedView,
    CredentialUpdate,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.web.deps import (
    get_connect_service,
    get_credential_service,
)
from jentic_one.control.web.schemas.credentials import (
    APIReferenceResponse,
    ConnectChallengeResponse,
    ConnectRequestBody,
    CredentialAgentListResponse,
    CredentialAgentResponse,
    CredentialCreateRequest,
    CredentialCreateResponse,
    CredentialListResponse,
    CredentialRedactedResponse,
    CredentialUpdateRequest,
    ProviderDiscoveryEntryResponse,
    ProviderDiscoveryResponse,
    RuleSetAttachRequest,
    RuleSetCreateRequest,
    RuleSetListResponse,
    RuleSetResponse,
    RuleSetSummaryResponse,
    RuleSetUpdateRequest,
)
from jentic_one.control.web.schemas.permission_rules import (
    PermissionRuleListResponse,
    PermissionRuleReadSchema,
    PermissionRuleSchema,
    PermissionsPatchRequest,
    PermissionTestRequest,
    PermissionTestResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses
from jentic_one.shared.web.static import SPA_MOUNT_PATH

router = APIRouter()

_logger = structlog.get_logger(__name__)


def _to_redacted_response(view: CredentialRedactedView) -> CredentialRedactedResponse:
    """Project a service CredentialRedactedView to a web response."""
    api = view.api
    details = view.details
    return CredentialRedactedResponse(
        credential_id=view.credential_id,
        type=view.type,
        name=view.name,
        api=APIReferenceResponse(vendor=api.vendor, name=api.name, version=api.version),
        catalog_api_id=view.catalog_api_id,
        provider=view.provider,
        provider_account_ref=view.provider_account_ref,
        active=view.active,
        created_by=view.created_by,
        created_at=view.created_at,
        updated_at=view.updated_at,
        details=details.model_dump(exclude_none=True) if details else None,
        server_variables=view.server_variables,
    )


# OR-lists ``owner:credentials:read`` for parity with the other credential reads
# (list / get): a delegated agent is minted the owner scope, not the bare one, so
# gating providers on ``credentials:read`` alone would 403 an agent that can
# already read its owner's credentials. Provider discovery returns static config
# metadata (no ``build_access_filters``, nothing owner-scoped), so admitting the
# delegated agent here leaks nothing — it just keeps the credential reads uniform.
@router.get("/credentials/providers", summary="List credential providers")
async def list_providers(
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
) -> ProviderDiscoveryResponse:
    """Return discovery metadata for all configured credential providers."""
    entries = svc.list_providers()
    return ProviderDiscoveryResponse(
        providers=[
            ProviderDiscoveryEntryResponse(
                id=e.id,
                label=e.label,
                managed=e.managed,
                types=e.types,
                configured=e.configured,
                callback_url=e.callback_url,
            )
            for e in entries
        ]
    )


@router.post("/credentials", status_code=201, summary="Create credential")
async def create_credential(
    body: CredentialCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> CredentialCreateResponse:
    """Create a new credential. The secret is returned once and never readable again."""
    payload = CredentialCreate(
        type=CredentialType(body.type),
        name=body.name,
        api=APIReference(
            vendor=body.api.vendor,
            name=body.api.name,
            version=body.api.version,
        ),
        catalog_api_id=body.api.catalog_api_id,
        provider=body.provider,
        server_variables=body.server_variables,
        token=getattr(body, "token", None),
        key=getattr(body, "key", None),
        location=getattr(body, "location", None),
        field_name=getattr(body, "field_name", None),
        username=getattr(body, "username", None),
        password=getattr(body, "password", None),
        grant_type=getattr(body, "grant_type", None),
        token_url=getattr(body, "token_url", None),
        authorize_url=getattr(body, "authorize_url", None),
        client_id=getattr(body, "client_id", None),
        client_secret=getattr(body, "client_secret", None),
        scopes=getattr(body, "scopes", None),
        access_key_id=getattr(body, "access_key_id", None),
        secret_access_key=getattr(body, "secret_access_key", None),
        session_token=getattr(body, "session_token", None),
        aws_region=getattr(body, "aws_region", None),
        aws_service=getattr(body, "aws_service", None),
    )
    result = await svc.create(payload, identity=identity)

    redacted_api = APIReferenceResponse(
        vendor=result.api.vendor, name=result.api.name, version=result.api.version
    )
    redacted = CredentialRedactedResponse(
        credential_id=result.credential_id,
        type=result.type,
        name=result.name,
        api=redacted_api,
        catalog_api_id=result.catalog_api_id,
        provider=result.provider,
        active=result.active,
        created_at=result.created_at,
        server_variables=result.server_variables,
    )
    return CredentialCreateResponse(
        credential=redacted,
        secret=result.secret.model_dump(),
    )


@router.get("/credentials", summary="List credentials")
async def list_credentials(
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    vendor: str | None = Query(default=None),
) -> CredentialListResponse:
    """List credentials with cursor-based pagination."""
    page = await svc.list_all(cursor=cursor, limit=limit, vendor=vendor, identity=identity)
    return CredentialListResponse(
        data=[_to_redacted_response(v) for v in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


# The OAuth popup the SPA opened lands back here after the IdP round-trip.
# Rather than render product UI from the API (the backend has no business
# emitting HTML, and any inline copy would be served as the API's content
# type), we 302-redirect the popup to a tiny public SPA route that owns the
# user-facing "you can close this" experience and runs window.close().
#
# Only a coarse status travels in the query string — never a reason. The
# parent SPA learns the real outcome by polling GET /credentials/{id}; the
# actual cause (missing state, connect failure, provider error) is recorded
# via structured logging for operators. ``status=error`` carries no detail
# so no PII / IDs / provider-internal text can leak through the redirect URL
# (which is visible in browser history, referrer headers, and server logs).
#
# Served same-origin in every deploy mode (the SPA bundle is mounted under
# ``SPA_MOUNT_PATH``, see shared/web/static.py), so a root-absolute path reaches
# the SPA without knowing the host. The ``/app`` prefix is owned by
# ``SPA_MOUNT_PATH`` (the backend's single source, mirroring the UI's router
# basename) — derived here, never hand-written, so the two can't drift. Kept in
# lockstep with the public route registered in ui/src/App.tsx (which, under
# React Router ``basename="/app"``, declares it as ``/oauth/connected`` relative
# to the basename = ``/app/oauth/connected``).
_CONNECT_RETURN_PATH = f"{SPA_MOUNT_PATH}/oauth/connected"


def _oauth_callback_success() -> RedirectResponse:
    # 303 See Other: force the popup to GET the SPA return route regardless of
    # how it arrived, and avoid any body on the API origin.
    return RedirectResponse(f"{_CONNECT_RETURN_PATH}?status=ok", status_code=303)


def _oauth_callback_error() -> RedirectResponse:
    return RedirectResponse(f"{_CONNECT_RETURN_PATH}?status=error", status_code=303)


@router.get("/credentials/oauth/callback", summary="OAuth connect callback")
async def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    svc: ConnectService = Depends(get_connect_service),
) -> Response:
    """Handle the OAuth callback from the IdP.

    This endpoint is intentionally unauthenticated — it receives redirects
    from external IdPs where the user has no session cookie. Security
    binding is provided by the signed, time-limited state JWT which ties
    the callback to a specific credential and caller.

    Redirects the popup the SPA opened to a public SPA route
    (``/app/oauth/connected``) that owns the user-facing "you can close this"
    experience and self-closes. Two variants, distinguished only by a coarse
    ``status`` query param:

      * Success: ``?status=ok``.
      * Failure: ``?status=error`` — no protocol or provider detail is
        exposed in the redirect URL.

    The parent SPA still learns the real outcome by polling
    ``GET /credentials/{id}`` — never from this redirect. The actual cause
    (missing state, connect failure, provider error, etc.) is recorded via
    structured logging for operators.
    """
    if not state:
        # Almost certainly someone hand-typed/probed the URL (the IdP always
        # echoes state). Don't go through the service — there's nothing to
        # complete.
        _logger.warning(
            "oauth_callback.missing_state",
            has_code=bool(code),
            has_error=bool(error),
            error=error,
        )
        return _oauth_callback_error()

    callback = ConnectCallback(code=code, error=error)

    try:
        credential_id = await svc.complete(state, callback)
    except ConnectFlowError as exc:
        _logger.warning(
            "oauth_callback.connect_flow_error",
            error=str(exc),
            callback_error=error,
        )
        return _oauth_callback_error()
    except CredentialNotFoundError:
        _logger.warning("oauth_callback.credential_not_found")
        return _oauth_callback_error()
    except ProviderError as exc:
        _logger.warning("oauth_callback.provider_error", error=str(exc))
        return _oauth_callback_error()

    _logger.info("oauth_callback.connected", credential_id=credential_id)
    return _oauth_callback_success()


@router.get("/credentials/{credential_id}", summary="Get credential", responses=not_found())
async def get_credential(
    credential_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
) -> CredentialRedactedResponse:
    """Get a single credential with redacted secrets."""
    view = await svc.get(credential_id, identity=identity)
    return _to_redacted_response(view)


@router.get(
    "/credentials/{credential_id}/agents",
    summary="List agents bound to credential",
    responses=not_found(),
)
async def list_credential_agents(
    credential_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> CredentialAgentListResponse:
    """List agents directly bound to a credential with cursor-based pagination.

    The reverse lookup for the credential-detail "Agents" view (theme 5
    phase 1) — the direct-binding mirror of ``GET /toolkits/{id}/agents``.
    Suspended bindings are included with their flag set.
    """
    data, has_more, next_cursor = await svc.list_agents(
        credential_id, cursor=cursor, limit=limit, identity=identity
    )
    return CredentialAgentListResponse(
        data=[
            CredentialAgentResponse(
                agent_id=row.agent_id,
                agent_name=row.agent_name,
                status=row.agent_status,
                bound_at=row.bound_at,
                suspended=row.suspended,
                rule_set_id=row.rule_set_id,
            )
            for row in data
        ],
        has_more=has_more,
        next_cursor=next_cursor,
    )


# --- Per-binding permission rules (theme 5 phase 1) ---


def _to_permission_rule(rule: AgentPermissionRule) -> PermissionRuleReadSchema:
    return PermissionRuleReadSchema.model_validate(
        {
            "effect": rule.effect,
            "methods": rule.methods,
            "path": rule.path,
            "match_mode": rule.match_mode,
            "operations": rule.operations,
            "_system": rule.is_system,
            "_comment": rule.comment,
        }
    )


@router.get(
    "/credentials/{credential_id}/agents/{agent_id}/permissions",
    operation_id="listAgentCredentialPermissions",
    summary="List binding permission rules",
    responses=not_found(),
)
async def list_agent_permissions(
    credential_id: str,
    agent_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
) -> PermissionRuleListResponse:
    """List the ordered PBAC rules for a direct `(agent, credential)` binding."""
    rules = await svc.list_agent_permissions(credential_id, agent_id, identity=identity)
    return PermissionRuleListResponse(data=[_to_permission_rule(r) for r in rules])


@router.put(
    "/credentials/{credential_id}/agents/{agent_id}/permissions",
    operation_id="replaceAgentCredentialPermissions",
    summary="Replace binding permission rules",
    responses=not_found(),
)
async def replace_agent_permissions(
    credential_id: str,
    agent_id: str,
    body: Annotated[list[PermissionRuleSchema], Field(max_length=100)],
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> PermissionRuleListResponse:
    """Replace the full set of permission rules for a binding (idempotent PUT)."""
    rules_data = [r.model_dump(exclude_none=True) for r in body]
    rules = await svc.replace_agent_permissions(
        credential_id, agent_id, rules_data, identity=identity
    )
    return PermissionRuleListResponse(data=[_to_permission_rule(r) for r in rules])


@router.patch(
    "/credentials/{credential_id}/agents/{agent_id}/permissions",
    operation_id="patchAgentCredentialPermissions",
    summary="Patch binding permission rules",
    responses=not_found(),
)
async def patch_agent_permissions(
    credential_id: str,
    agent_id: str,
    body: PermissionsPatchRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> PermissionRuleListResponse:
    """Additively add and/or remove permission rules on a binding."""
    add_data = None
    if body.add:
        add_data = [r.model_dump(exclude_none=True) for r in body.add]
    rules = await svc.patch_agent_permissions(
        credential_id, agent_id, identity=identity, add=add_data, remove=body.remove
    )
    return PermissionRuleListResponse(data=[_to_permission_rule(r) for r in rules])


@router.post(
    "/credentials/{credential_id}/agents/{agent_id}/permissions:test",
    operation_id="testAgentCredentialPermissions",
    summary="Dry-run permission evaluation",
    responses=not_found(),
)
async def test_agent_permissions(
    credential_id: str,
    agent_id: str,
    body: PermissionTestRequest,
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
) -> PermissionTestResponse:
    """Answer "what would the broker do for this request?" without calling upstream.

    Unlike the toolkit `:test` there is **no vendor pooling**: the direct
    binding's rules are one ordered first-match-wins list, so the result is
    exactly this binding's policy. Default-deny when nothing matches.
    """
    result = await svc.test_agent_permissions(
        credential_id,
        agent_id,
        method=body.method,
        path=body.path,
        operation_id=body.operation_id,
        identity=identity,
    )
    return PermissionTestResponse(
        allowed=result.allowed,
        matched=result.matched,
        effect=result.effect,
        rule_index=result.rule_index,
        credential_id=result.credential_id,
        is_system=result.is_system,
    )


# --- Shared permission rule sets (theme 5 phase 1, Q-04) ---


@router.put(
    "/credentials/{credential_id}/agents/{agent_id}/rule-set",
    operation_id="attachAgentCredentialRuleSet",
    status_code=204,
    summary="Attach rule set to binding",
    responses=not_found(),
)
async def attach_agent_rule_set(
    credential_id: str,
    agent_id: str,
    body: RuleSetAttachRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> Response:
    """Point the binding at a shared rule set (idempotent PUT).

    While attached, the set's ordered list is the binding's effective policy
    and its inline rules are dormant — `permissions:test` evaluates the set.
    The set must exist (404 `rule_set_not_found`).
    """
    await svc.attach_binding_rule_set(credential_id, agent_id, body.rule_set_id, identity=identity)
    return Response(status_code=204)


@router.delete(
    "/credentials/{credential_id}/agents/{agent_id}/rule-set",
    operation_id="detachAgentCredentialRuleSet",
    status_code=204,
    summary="Detach rule set from binding",
    responses=not_found(),
)
async def detach_agent_rule_set(
    credential_id: str,
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> Response:
    """Detach the binding's shared rule set — its inline rules apply again.

    Idempotent: detaching a binding already on inline rules is a no-op 204.
    """
    await svc.detach_binding_rule_set(credential_id, agent_id, identity=identity)
    return Response(status_code=204)


def _to_rule_set_response(
    rule_set: PermissionRuleSet, rules: list[PermissionRuleSetRule], binding_count: int
) -> RuleSetResponse:
    return RuleSetResponse(
        rule_set_id=rule_set.id,
        name=rule_set.name,
        description=rule_set.description,
        rules=[
            PermissionRuleReadSchema.model_validate(
                {
                    "effect": r.effect,
                    "methods": r.methods,
                    "path": r.path,
                    "match_mode": r.match_mode,
                    "operations": r.operations,
                    "_system": r.is_system,
                    "_comment": r.comment,
                }
            )
            for r in rules
        ],
        binding_count=binding_count,
        created_by=rule_set.created_by,
        created_at=rule_set.created_at,
    )


@router.get(
    "/permission-rule-sets",
    operation_id="listPermissionRuleSets",
    summary="List permission rule sets",
)
async def list_rule_sets(
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> RuleSetListResponse:
    """List shared rule sets with per-set rule counts (cursor-paginated)."""
    data, has_more, next_cursor = await svc.list_rule_sets(
        cursor=cursor, limit=limit, identity=identity
    )
    return RuleSetListResponse(
        data=[
            RuleSetSummaryResponse(
                rule_set_id=rs.id,
                name=rs.name,
                description=rs.description,
                rule_count=count,
                created_by=rs.created_by,
                created_at=rs.created_at,
            )
            for rs, count in data
        ],
        has_more=has_more,
        next_cursor=next_cursor,
    )


@router.post(
    "/permission-rule-sets",
    operation_id="createPermissionRuleSet",
    status_code=201,
    summary="Create permission rule set",
    responses=conflict(),
)
async def create_rule_set(
    body: RuleSetCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> RuleSetResponse:
    """Create a named, shareable ordered rule list (theme 5 rule grouping).

    N agent-credential bindings can point at one set, so `permissions:test`
    and "revoke this operation everywhere" stay single-place edits.
    """
    rule_set, rules = await svc.create_rule_set(
        name=body.name,
        description=body.description,
        rules=[r.model_dump(exclude_none=True) for r in body.rules],
        identity=identity,
    )
    return _to_rule_set_response(rule_set, rules, binding_count=0)


@router.get(
    "/permission-rule-sets/{rule_set_id}",
    operation_id="getPermissionRuleSet",
    summary="Get permission rule set",
    responses=not_found(),
)
async def get_rule_set(
    rule_set_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
) -> RuleSetResponse:
    """Get a rule set with its ordered rules and referencing-binding count."""
    rule_set, rules, binding_count = await svc.get_rule_set(rule_set_id, identity=identity)
    return _to_rule_set_response(rule_set, rules, binding_count)


@router.patch(
    "/permission-rule-sets/{rule_set_id}",
    operation_id="updatePermissionRuleSet",
    summary="Update permission rule set",
    responses=not_found(),
)
async def update_rule_set(
    rule_set_id: str,
    body: RuleSetUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> RuleSetResponse:
    """Rename or re-describe a rule set (creator or org admin)."""
    await svc.update_rule_set(
        rule_set_id, identity=identity, name=body.name, description=body.description
    )
    rule_set, rules, binding_count = await svc.get_rule_set(rule_set_id, identity=identity)
    return _to_rule_set_response(rule_set, rules, binding_count)


@router.put(
    "/permission-rule-sets/{rule_set_id}/rules",
    operation_id="replacePermissionRuleSetRules",
    summary="Replace rule set rules",
    responses=not_found(),
)
async def replace_rule_set_rules(
    rule_set_id: str,
    body: Annotated[list[PermissionRuleSchema], Field(max_length=100)],
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> PermissionRuleListResponse:
    """Replace the set's full ordered rule list (idempotent PUT).

    Every binding pointing at the set picks the new list up at once — the
    single-place edit rule grouping exists for.
    """
    rules_data = [r.model_dump(exclude_none=True) for r in body]
    rules = await svc.replace_rule_set_rules(rule_set_id, rules_data, identity=identity)
    return PermissionRuleListResponse(
        data=[
            PermissionRuleReadSchema.model_validate(
                {
                    "effect": r.effect,
                    "methods": r.methods,
                    "path": r.path,
                    "match_mode": r.match_mode,
                    "operations": r.operations,
                    "_system": r.is_system,
                    "_comment": r.comment,
                }
            )
            for r in rules
        ]
    )


@router.delete(
    "/permission-rule-sets/{rule_set_id}",
    operation_id="deletePermissionRuleSet",
    status_code=204,
    summary="Delete permission rule set",
    responses=with_responses(not_found(), conflict()),
)
async def delete_rule_set(
    rule_set_id: str,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> Response:
    """Delete a rule set nothing references (409 `rule_set_in_use` otherwise)."""
    await svc.delete_rule_set(rule_set_id, identity=identity)
    return Response(status_code=204)


@router.patch(
    "/credentials/{credential_id}",
    summary="Update or rotate credential",
    responses=not_found(),
)
async def update_credential(
    credential_id: str,
    body: CredentialUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> CredentialRedactedResponse:
    """Update or rotate a credential."""
    payload = CredentialUpdate(
        type=CredentialType(body.type),
        name=body.name,
        active=body.active,
        server_variables=body.server_variables,
        token=getattr(body, "token", None),
        key=getattr(body, "key", None),
        location=getattr(body, "location", None),
        field_name=getattr(body, "field_name", None),
        username=getattr(body, "username", None),
        password=getattr(body, "password", None),
        client_secret=getattr(body, "client_secret", None),
        token_url=getattr(body, "token_url", None),
        scopes=getattr(body, "scopes", None),
        access_key_id=getattr(body, "access_key_id", None),
        secret_access_key=getattr(body, "secret_access_key", None),
        session_token=getattr(body, "session_token", None),
        clear_session_token=getattr(body, "clear_session_token", False),
        aws_region=getattr(body, "aws_region", None),
        aws_service=getattr(body, "aws_service", None),
    )
    view = await svc.update(credential_id, payload, identity=identity)
    return _to_redacted_response(view)


@router.delete(
    "/credentials/{credential_id}",
    status_code=204,
    summary="Delete credential",
    responses=not_found(),
)
async def delete_credential(
    credential_id: str,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> Response:
    """Delete a credential."""
    await svc.delete(credential_id, identity=identity)
    return Response(status_code=204)


@router.post(
    "/credentials/{credential_id}/connect",
    summary="Begin OAuth connect flow",
    responses=with_responses(not_found(), conflict("Credential is not connectable")),
)
async def connect_credential(
    credential_id: str,
    body: ConnectRequestBody,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: ConnectService = Depends(get_connect_service),
) -> ConnectChallengeResponse:
    """Initiate the OAuth connect flow for a credential."""
    connect_req = ConnectRequest(scopes=body.scopes, extra=body.extra)
    try:
        challenge = await svc.begin(
            credential_id,
            connect_req,
            actor_id=identity.sub,
            actor_type=identity.actor_type,
        )
    except CredentialNotFoundError:
        return JSONResponse(status_code=404, content={"detail": "Credential not found"})  # type: ignore[return-value]
    except NotConnectableError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})  # type: ignore[return-value]
    except ProviderError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})  # type: ignore[return-value]
    return ConnectChallengeResponse(authorize_url=challenge.authorize_url, state=challenge.state)
