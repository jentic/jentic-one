"""OAuth discovery and JWKS endpoints (RFC 8414 + RFC 9728).

Two discovery surfaces live here:

- The **root** RFC 8414 document (``/.well-known/oauth-authorization-server``)
  — the platform AS metadata whose ``registration_endpoint`` is the *agent*
  DCR door ``/register``. Its body is pinned byte-identical by a golden
  regression test (phase-3a acceptance): do not change it when touching the
  MCP-scoped documents below.
- The **/mcp-scoped** documents (phase-3a §4.7, D10): a path-scoped RFC 8414
  doc for the logical issuer ``{base}/mcp`` plus the RFC 9728
  protected-resource doc (path-scoped and a root alias), and the ``/mcp``
  401 challenge that starts the discovery chain. All are gated by
  ``server.mcp.oauth.enabled`` — off (the default) they answer the
  framework's plain route-not-found 404, so the *gate state* is unobservable.
  (The *build* still shows routing-level tells — 405+``Allow`` on
  non-registered methods, the slash redirect, the doc paths in the live
  OpenAPI schema — identical in both gate arms; pinned by tests.)
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from jentic_one.shared.auth import CachedJWKSPublisher
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.context import Context
from jentic_one.shared.scopes import MCP_TOOL_SCOPES
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.links import deployment_base_url

router = APIRouter()

_jwks_publishers: dict[tuple[tuple[str, str], ...], CachedJWKSPublisher] = {}


def _publisher_key(config: AuthConfig) -> tuple[tuple[str, str], ...]:
    """Identity of the active signing material, so a key rotation rebuilds the JWKS."""
    return tuple((k.kid, k.private_key_pem.get_secret_value()) for k in config.id_signing)


def _get_publisher(config: AuthConfig) -> CachedJWKSPublisher:
    key = _publisher_key(config)
    publisher = _jwks_publishers.get(key)
    if publisher is None:
        publisher = CachedJWKSPublisher(config)
        _jwks_publishers[key] = publisher
    return publisher


@router.get(
    "/.well-known/oauth-authorization-server",
    summary="OAuth authorization server metadata",
)
async def oauth_authorization_server(
    request: Request, ctx: Context = Depends(get_ctx)
) -> dict[str, Any]:
    """Return RFC 8414 authorization-server metadata (endpoints, grant types, algorithms)."""
    issuer = deployment_base_url(ctx.config.auth, request)
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "introspection_endpoint": f"{issuer}/oauth/introspect",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "grant_types_supported": [
            "authorization_code",
            "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "refresh_token",
            "client_credentials",
        ],
        "token_endpoint_auth_methods_supported": [
            "private_key_jwt",
            "client_secret_basic",
            "client_secret_post",
            "none",
        ],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "id_token_signing_alg_values_supported": ["ES256"],
        "token_endpoint_auth_signing_alg_values_supported": ["EdDSA"],
    }


@router.get("/.well-known/jwks.json", summary="JSON Web Key Set")
async def jwks(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """Return the JWKS document with the active public signing keys (ES256)."""
    return _get_publisher(ctx.config.auth).get_jwks()


@router.get(
    "/auth/idp",
    operation_id="authIdpDescriptor",
    summary="External IdP login descriptor",
)
async def auth_idp_descriptor(ctx: Context = Depends(get_ctx)) -> dict[str, Any]:
    """Public capability hint: whether IdP login is enabled, and which provider.

    The SPA reads this before login to decide whether to show a "Continue with
    <provider>" button. Unauthenticated and secret-free — it advertises only the
    enabled flag and the provider name (never client secrets or endpoints).
    """
    idp = ctx.config.auth.idp
    return {
        "enabled": idp.enabled,
        "provider": idp.provider if idp.enabled else None,
    }


class _McpDiscoveryRoute(APIRoute):
    """Route class owning the ``server.mcp.oauth.enabled`` gate (§4.7).

    Runs *before* the FastAPI dependency machinery so the **gate state is
    unobservable**: on a full route match a disabled surface answers the
    framework's own route-not-found 404, and no handler, dependency, or
    ``WWW-Authenticate`` header runs (same posture as the DCR front door's
    ``_Rfc7591Route``, §4.2). What remains observable is *build* state, not
    gate state: Starlette answers partial matches (405 + ``Allow`` on
    non-registered methods on the doc paths), issues the ``redirect_slashes``
    307, and lists the doc paths in the live OpenAPI schema before this gate
    can run — all identically in both gate arms, so nothing distinguishes
    enabled-from-disabled. Pinned by tests so any change is deliberate.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            ctx: Context = request.app.state.ctx
            if not ctx.config.server.mcp.oauth.enabled:
                # Mirror the framework's route-not-found body exactly.
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            return await original(request)

        return handler


mcp_router = APIRouter(route_class=_McpDiscoveryRoute)

#: The RFC 9728 protected-resource metadata path for the ``/mcp`` resource —
#: RFC 9728 §3 well-known path insertion for a resource with path ``/mcp``.
#: Single source for the document routes and the 401 challenge pointer.
_MCP_PRM_PATH = "/.well-known/oauth-protected-resource/mcp"

_GATED_404_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Interactive OAuth for MCP is disabled "
        "(`server.mcp.oauth.enabled=false`): the route answers the framework's "
        "plain route-not-found 404, so the gate state is unobservable."
    }
}


def _mcp_authorization_server_document(base: str) -> dict[str, Any]:
    """The /mcp-scoped RFC 8414 document, per D10 (one deliberate deviation).

    A *logical* second AS for the path-scoped issuer ``{base}/mcp``: the
    endpoints are shared with the root AS — only the metadata doc is scoped.
    ``registration_endpoint`` is the anonymous OAuth-client DCR door
    ``/oauth-clients`` (D1), never the agent ``/register``. Only the public
    secret-less client profile is advertised (``none`` + PKCE S256, D5); CIMD
    (``client_id_metadata_document_supported``) is deliberately **not**
    advertised yet — flipping it on is the §6 follow-on and non-breaking.
    Deviation from D10: no ``revocation_endpoint`` (see the field comment).
    """
    return {
        "issuer": f"{base}/mcp",
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth-clients",
        # No revocation_endpoint (deliberate deviation from D10, which lists
        # {base}/oauth/revoke): the platform's /oauth/revoke requires an
        # authenticated platform Identity and a JSON body, so an RFC 7009
        # public-client revoke (client_id only, form-encoded) can never
        # succeed against it. RFC 8414 makes the field optional — omit it;
        # public clients revoke by grant lifecycle (grant :revoke, client
        # deactivation) until phase 3 decides otherwise.
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": sorted(MCP_TOOL_SCOPES),
    }


def _mcp_protected_resource_document(base: str) -> dict[str, Any]:
    """The RFC 9728 protected-resource document for the ``/mcp`` resource (D10)."""
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [f"{base}/mcp"],
        "scopes_supported": sorted(MCP_TOOL_SCOPES),
        "bearer_methods_supported": ["header"],
    }


@mcp_router.get(
    "/.well-known/oauth-authorization-server/mcp",
    summary="OAuth authorization server metadata for the MCP resource",
    responses=_GATED_404_RESPONSE,
)
async def mcp_oauth_authorization_server(
    request: Request, ctx: Context = Depends(get_ctx)
) -> dict[str, Any]:
    """RFC 8414 metadata for the path-scoped issuer `{base}/mcp` (phase-3a §4.7).

    RFC 8414 §3.1 path insertion: this is the metadata URL clients derive for
    the issuer `{base}/mcp` named by the protected-resource document. It
    advertises the anonymous OAuth-client registration door (`/oauth-clients`)
    and the public-client profile (`none` + PKCE S256) — the root document is a
    separate, unchanged surface whose `registration_endpoint` remains the agent
    `/register`.
    """
    base = deployment_base_url(ctx.config.auth, request)
    return _mcp_authorization_server_document(base)


@mcp_router.get(
    _MCP_PRM_PATH,
    summary="OAuth protected resource metadata for the MCP resource",
    responses=_GATED_404_RESPONSE,
)
async def mcp_oauth_protected_resource(
    request: Request, ctx: Context = Depends(get_ctx)
) -> dict[str, Any]:
    """RFC 9728 protected-resource metadata for `{base}/mcp` (phase-3a §4.7).

    Names the /mcp-scoped authorization server and the MCP tool scopes. The
    same body is also served at the root well-known path for clients that
    ignore the 401's `resource_metadata` pointer.
    """
    base = deployment_base_url(ctx.config.auth, request)
    return _mcp_protected_resource_document(base)


@mcp_router.get(
    "/.well-known/oauth-protected-resource",
    # MCP-specific operation_id: the body is the /mcp resource's document, so
    # don't squat the generic root-doc name (a future root PRM for a non-MCP
    # resource should get `oauthProtectedResource`, not fight this alias for it).
    operation_id="mcpOauthProtectedResourceRootAlias",
    summary="OAuth protected resource metadata (root alias for the MCP resource)",
    responses=_GATED_404_RESPONSE,
)
async def oauth_protected_resource(
    request: Request, ctx: Context = Depends(get_ctx)
) -> dict[str, Any]:
    """Root-path alias of the MCP protected-resource document (phase-3a §4.7).

    Compatibility fallback for clients that probe the root
    `/.well-known/oauth-protected-resource` instead of following the 401's
    `resource_metadata` pointer (the documented Claude Code behaviour). Safe
    because this deployment has exactly one OAuth-protected resource, so the
    root and path-scoped documents are the same body.

    Two acknowledged trades (§4.7, review F6):

    - RFC 9728 §3 says this well-known path corresponds to resource identifier
      ``{base}`` (no path), and §3.3 has clients validate ``resource`` against
      the resource they queried. This body claims ``resource={base}/mcp`` — a
      strict path-deriving validator would reject it, but Claude's fallback
      validates against the MCP server URL (``{base}/mcp``), which is exactly
      what the alias exists to satisfy. That is the intended trade.
    - The alias squats the deployment's only root PRM slot: a future non-MCP
      protected resource at ``{base}`` cannot get its own root document
      without breaking this fallback. Phase 3's mount must re-confirm the
      "exactly one OAuth-protected resource" premise before adding one.
    """
    base = deployment_base_url(ctx.config.auth, request)
    return _mcp_protected_resource_document(base)


@mcp_router.api_route(
    "/mcp",
    # The full common method set, not just the streamable-HTTP verbs
    # (GET/POST/DELETE): auth precedes method semantics on a protected
    # resource, the phase-3 mounted ASGI app will cover all methods anyway,
    # and registering them now keeps the disabled arm's answer a uniform 404
    # (no 405 + Allow method tell on the resource path itself; review F3/F4).
    methods=["GET", "POST", "DELETE", "HEAD", "OPTIONS", "PUT", "PATCH"],
    include_in_schema=False,
)
async def mcp_resource_challenge(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
    """The RFC 9728 discovery-chain entry point at the MCP resource path (§4.7).

    Phase 3 mounts the actual MCP app here; until then this placeholder owns
    the ``/mcp`` path so an unauthenticated (or any) probe answers ``401`` with
    ``WWW-Authenticate: Bearer resource_metadata="…"`` — exactly what a
    spec-following MCP client needs to start discovery. When the mounted app
    lands it replaces this route and keeps the same challenge contract on
    missing/invalid bearers. Schema-hidden: it is a protocol seam, not a
    control-plane API operation. Gated with the documents by
    ``server.mcp.oauth.enabled`` — off, the path 404s exactly as today.
    """
    base = deployment_base_url(ctx.config.auth, request)
    headers = {"WWW-Authenticate": f'Bearer resource_metadata="{base}{_MCP_PRM_PATH}"'}
    if request.method == "HEAD":
        # HEAD carries the same status and headers as GET but no body
        # (RFC 9110 §9.3.2).
        return Response(status_code=401, headers=headers)
    return JSONResponse(status_code=401, content={"detail": "Unauthorized"}, headers=headers)
