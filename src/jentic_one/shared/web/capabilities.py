"""Public ``GET /capabilities`` — deployment self-description for clients (#1279).

A client connecting to an arbitrary jentic-one deployment (native desktop app,
CLI against a remote server, SPA, MCP client) needs to know what that deployment
supports before it can sign in or route traffic: which login methods exist,
where the broker is, which surfaces are mounted, which optional features are on.
That information exists today but is scattered (``/instance``, ``/auth/idp``,
RFC 8414 metadata) and incomplete (nothing publishes the broker URL — #1249).

This module generalises the ``GET /auth/idp`` "public capability hint" into one
consolidated, unauthenticated document — the same pattern MCP authorization
(RFC 9728), Matrix ``/capabilities``, and GitLab ``/metadata`` converged on.
Clients treat it as additive: absent (an older server), they fall back to
today's probes.

Deliberately **minimal** (ASVS fingerprinting posture): no exact version string
— that stays behind the authenticated ``GET /system/version``.

Downstream packages extend ``features`` via :func:`register_capability_contributor`
(same process-global, import-time registry posture as ``set_claim_token_minter``).
Route override is not an option by design — ``AppContainer.extra_routers`` mount
after built-ins and never shadow.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal, Protocol, runtime_checkable

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.instance_identity import sanitized_url_parts

CAPABILITIES_PATH = "/capabilities"

#: Bump when the document's shape changes incompatibly. Clients hard-fail on a
#: major version they do not understand instead of misreading the body.
CAPABILITIES_VERSION = 1

_AUTHORIZATION_SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server"

_log = structlog.get_logger(__name__)


# --- contributor registry -----------------------------------------------------


@runtime_checkable
class CapabilityContributor(Protocol):
    """Contributes extra ``features.*`` keys to the capability document."""

    def __call__(self, ctx: Context) -> Mapping[str, object]:
        """Return extra ``features`` entries for this deployment.

        Called per request with the live application ``Context`` (read config /
        in-memory state only — the endpoint is public and must stay cheap and
        DB-free). Values must be JSON-serialisable. Contributions **never
        override** an existing key: built-in keys and earlier-registered
        contributors win, and a collision is logged and dropped — so a
        downstream package cannot silently rewrite OSS semantics. Prefix keys
        with your package name (e.g. ``"acme_sso"``) to stay collision-free.
        """
        ...


_capability_contributors: list[CapabilityContributor] = []


def register_capability_contributor(contributor: CapabilityContributor) -> None:
    """Register a ``features`` contributor for the public capability document.

    Same process-global, import-time registry posture as
    ``set_claim_token_minter``: call from your package's ``__init__`` before app
    construction. Registration order is preserved; on key collisions the
    earlier writer wins (see :class:`CapabilityContributor`).
    """
    _capability_contributors.append(contributor)


def get_capability_contributors() -> tuple[CapabilityContributor, ...]:
    """Return the registered contributors (empty by default)."""
    return tuple(_capability_contributors)


# --- response shape -----------------------------------------------------------


class CapabilitiesInstanceResponse(BaseModel):
    """Identity slice of the document (the full probe stays ``GET /instance``)."""

    backend: Literal["local", "remote"] = Field(
        description="Operator-declared backend locality (server.backend); a hint, "
        "not an authorization signal."
    )
    canonical_base_url: str = Field(
        description="The instance's own canonical base URL (auth.canonical_base_url), "
        "with any userinfo stripped; '' if unset."
    )


class CapabilitiesUrlsResponse(BaseModel):
    """Where the deployment's sibling surfaces live."""

    broker: str | None = Field(
        description=(
            "Advertised broker base URL for data-plane traffic — the value a client "
            "needs to route agent traffic through this deployment's broker "
            "(server.advertised_broker_url, falling back to server.mcp.broker_url, "
            "the URL the deployment already uses for its own control-plane→broker "
            "hop). Userinfo is stripped; null when neither is configured. Split "
            "deployments whose internal broker URL is not client-reachable should "
            "set server.advertised_broker_url explicitly."
        )
    )
    authorization_server_metadata: str | None = Field(
        description=(
            "Path of the RFC 8414 authorization-server metadata document; null when "
            "the auth surface is not mounted on this deployment."
        )
    )


class IdpMethodResponse(BaseModel):
    """External-IdP login (the ``GET /auth/idp`` hint, restated)."""

    enabled: bool
    provider: str | None = Field(
        description="Provider name when enabled (e.g. 'google'); null when disabled."
    )


class EnabledMethodResponse(BaseModel):
    """A simple on/off login capability."""

    enabled: bool


class OauthClientDcrMethodResponse(BaseModel):
    """Anonymous OAuth-client dynamic registration (``POST /oauth-clients``)."""

    enabled: bool
    approval: Literal["auto", "manual"] = Field(
        description=(
            "Registration admission posture (server.mcp.oauth.auto_approve_clients): "
            "'auto' activates registrations immediately; 'manual' parks them pending "
            "operator approval. Only meaningful when enabled."
        )
    )


class AuthMethodsResponse(BaseModel):
    """The login-picker contract: exactly the sign-in options this deployment supports."""

    idp: IdpMethodResponse
    local_login: EnabledMethodResponse = Field(
        description=(
            "Local-account login form on the /authorize flow (no external IdP). "
            "Currently always false; wired to auth.local_login when #1276 ships."
        )
    )
    oauth_client_dcr: OauthClientDcrMethodResponse
    agent_dcr: EnabledMethodResponse = Field(
        description="Anonymous agent self-registration (POST /register, RFC 7591); "
        "available whenever the auth surface is mounted."
    )
    service_accounts: EnabledMethodResponse = Field(
        description="Operator-managed service accounts (JWT-bearer grant); available "
        "whenever the auth surface is mounted."
    )


class CapabilitiesAuthResponse(BaseModel):
    """Authentication capabilities."""

    methods: AuthMethodsResponse


class CapabilitiesResponse(BaseModel):
    """Deployment self-description for one-URL client onboarding.

    Additive contract: clients must ignore unknown keys (``features`` grows via
    downstream contributions) and hard-fail only on an unknown
    ``capabilities_version``.
    """

    instance: CapabilitiesInstanceResponse
    surfaces: list[str] = Field(
        description="The control-plane surfaces this deployment serves (sorted), "
        "e.g. ['admin', 'auth', 'control', 'registry']."
    )
    urls: CapabilitiesUrlsResponse
    auth: CapabilitiesAuthResponse
    features: dict[str, object] = Field(
        description="Deployment feature flags. OSS ships 'mcp'; downstream packages "
        "may contribute additional keys (additive — never overriding built-ins)."
    )
    capabilities_version: int = Field(
        description="Shape version of this document; clients hard-fail on versions "
        "they do not understand."
    )


# --- resolution ---------------------------------------------------------------


def _advertised_broker_url(ctx: Context) -> str | None:
    """The broker URL to publish: the advertised key, else the MCP-hop value.

    ``server.advertised_broker_url`` exists for split deployments whose
    internal broker URL (e.g. a compose service name) is not client-reachable.
    When unset, ``server.mcp.broker_url`` is the deployment's own working
    broker URL and the best available answer (exact for the local install
    topology). Userinfo is stripped before publishing, mirroring ``/instance``.
    """
    raw = ctx.config.server.advertised_broker_url or ctx.config.server.mcp.broker_url
    if not raw:
        return None
    sanitized, _host = sanitized_url_parts(raw)
    return sanitized


def resolve_capabilities(ctx: Context, enabled_apps: Iterable[str]) -> CapabilitiesResponse:
    """Build the capability document from the live ``Context`` (config-only, no DB)."""
    surfaces = sorted(set(enabled_apps))
    auth_mounted = "auth" in surfaces
    idp = ctx.config.auth.idp
    mcp_oauth = ctx.config.server.mcp.oauth

    canonical_base_url = ctx.config.auth.canonical_base_url or ""
    if canonical_base_url:
        canonical_base_url, _host = sanitized_url_parts(canonical_base_url)

    features: dict[str, object] = {"mcp": ctx.config.server.mcp.enabled}
    for contributor in get_capability_contributors():
        for key, value in contributor(ctx).items():
            if key in features:
                # First writer wins: a contribution never rewrites a built-in
                # (or earlier-contributed) flag's meaning. Logged so the
                # colliding package is discoverable, then dropped.
                _log.warning(
                    "capability_feature_collision_ignored",
                    key=key,
                    contributor=getattr(contributor, "__qualname__", repr(contributor)),
                )
                continue
            features[key] = value

    return CapabilitiesResponse(
        instance=CapabilitiesInstanceResponse(
            backend=ctx.config.server.backend,
            canonical_base_url=canonical_base_url,
        ),
        surfaces=surfaces,
        urls=CapabilitiesUrlsResponse(
            broker=_advertised_broker_url(ctx),
            authorization_server_metadata=(
                _AUTHORIZATION_SERVER_METADATA_PATH if auth_mounted else None
            ),
        ),
        auth=CapabilitiesAuthResponse(
            methods=AuthMethodsResponse(
                idp=IdpMethodResponse(
                    enabled=idp.enabled,
                    provider=idp.provider if idp.enabled else None,
                ),
                # Always false until #1276 lands auth.local_login; publishing the
                # key now pins the document shape so clients need no migration.
                local_login=EnabledMethodResponse(enabled=False),
                # Advertising the DCR gate does not weaken the deliberate
                # 404-unobservability of the *route* gate: the door's presence
                # is already observable by POSTing to /oauth-clients.
                oauth_client_dcr=OauthClientDcrMethodResponse(
                    enabled=mcp_oauth.enabled,
                    approval="auto" if mcp_oauth.auto_approve_clients else "manual",
                ),
                agent_dcr=EnabledMethodResponse(enabled=auth_mounted),
                service_accounts=EnabledMethodResponse(enabled=auth_mounted),
            )
        ),
        features=features,
        capabilities_version=CAPABILITIES_VERSION,
    )


def get_capabilities_router(enabled_apps: Iterable[str]) -> APIRouter:
    """Router exposing the public capability document (``GET /capabilities``).

    ``enabled_apps`` is captured at app-build time (it is deployment topology,
    not per-request state). Mounted next to the instance router in both app
    factories; the broker opts out exactly as it does for ``/instance``.
    """
    surfaces = sorted(set(enabled_apps))
    router = APIRouter()

    @router.get(
        CAPABILITIES_PATH,
        operation_id="getCapabilities",
        summary="Deployment capabilities",
        response_model=CapabilitiesResponse,
    )
    async def capabilities(ctx: Context = Depends(get_ctx)) -> CapabilitiesResponse:
        """Return this deployment's public self-description.

        Unauthenticated and DB-free so any client can discover — from one URL —
        which sign-in methods this deployment supports, where its broker is,
        which surfaces are mounted, and which optional features are enabled,
        instead of probe-and-guess across ``/instance``, ``/auth/idp``, and the
        RFC 8414 document.
        """
        return resolve_capabilities(ctx, surfaces)

    return router
