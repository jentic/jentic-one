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
Contributors receive a frozen :class:`CapabilityView` — never the live
``Context`` — and run once at app build, never on the unauthenticated request
path. Route override is not an option by design — ``AppContainer.extra_routers``
mount after built-ins and never shadow.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Iterable, Mapping
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.instance_identity import sanitized_url_parts

CAPABILITIES_PATH = "/capabilities"

#: Bump only when the document's shape changes incompatibly (a field is
#: removed, renamed, or retyped). Additive growth (new keys) never bumps it —
#: clients branch on key presence and ignore unknown keys, and hard-fail only
#: on a major version they do not understand.
CAPABILITIES_VERSION = 1

_AUTHORIZATION_SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server"
_AUTHORIZATION_SERVER_METADATA_MCP_PATH = "/.well-known/oauth-authorization-server/mcp"
_PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
_AUTHORIZE_PATH = "/authorize"
_TOKEN_PATH = "/oauth/token"
_AGENT_REGISTER_PATH = "/register"
_OAUTH_CLIENTS_PATH = "/oauth-clients"

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_log = structlog.get_logger(__name__)


# --- contributor registry -----------------------------------------------------


class CapabilityView(BaseModel):
    """Read-only projection of deployment state handed to contributors.

    Contributors run behind an unauthenticated route, so they are never handed
    the live application ``Context`` — a mutable god-object whose ``config``
    holds security controls (egress allowlists, DNS pinning, auth gates). This
    frozen value object carries exactly the facts a feature flag can depend on
    and nothing writable.
    """

    model_config = ConfigDict(frozen=True)

    backend: Literal["local", "remote"]
    canonical_base_url: str
    surfaces: tuple[str, ...]
    mcp_enabled: bool


@runtime_checkable
class CapabilityContributor(Protocol):
    """Contributes extra ``features.*`` keys to the capability document."""

    def __call__(self, view: CapabilityView) -> Mapping[str, bool]:
        """Return extra ``features`` entries for this deployment.

        Receives a frozen :class:`CapabilityView` (never the live ``Context``)
        and must stay cheap and DB-free. Keys must be ``str``, values ``bool``
        — anything else is logged and dropped, never published. Contributions
        **never override** an existing key: built-in keys and earlier-registered
        contributors win, case-insensitively, and a collision is logged and
        dropped — so a downstream package cannot silently rewrite OSS
        semantics. Prefix keys with your package name (e.g. ``"acme_sso"``) to
        stay collision-free. A raised exception is logged and the contribution
        skipped; it never fails the public document.
        """
        ...


_capability_contributors: list[CapabilityContributor] = []


def register_capability_contributor(contributor: CapabilityContributor) -> None:
    """Register a ``features`` contributor for the public capability document.

    Same process-global, import-time registry posture as
    ``set_claim_token_minter``: call from your package's ``__init__`` before app
    construction — the document is resolved once per app build, so a
    registration after the app exists contributes nothing to it. Registration
    order is preserved; on key collisions the earlier writer wins (see
    :class:`CapabilityContributor`).

    Raises ``TypeError`` for a non-callable or a callable that cannot accept
    the single positional :class:`CapabilityView` argument, and ``ValueError``
    for a duplicate registration — matching the up-front validation posture of
    the other extension registries (config extensions, telemetry events).
    """
    if not callable(contributor):
        raise TypeError(
            f"capability contributor must be callable, got {type(contributor).__name__}"
        )
    try:
        inspect.signature(contributor).bind(CapabilityView.model_construct())
    except TypeError as exc:
        raise TypeError(
            "capability contributor must accept exactly one positional argument "
            f"(CapabilityView): {contributor!r}"
        ) from exc
    if contributor in _capability_contributors:
        raise ValueError(f"capability contributor already registered: {contributor!r}")
    _capability_contributors.append(contributor)
    _log.info(
        "capability_contributor_registered",
        module=getattr(contributor, "__module__", None),
        contributor=getattr(contributor, "__qualname__", repr(contributor)),
    )


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
    """Where to reach the deployment's public endpoints.

    Endpoint fields are absolute URLs whenever a base URL is known
    (``auth.canonical_base_url``, else the origin the request arrived on);
    metadata paths degrade to root-relative paths only when neither is
    resolvable. ``null`` means the corresponding surface or gate is not
    available on the process answering this request.
    """

    broker: str | None = Field(
        description=(
            "Advertised broker base URL for data-plane traffic — the value a client "
            "needs to route agent traffic through this deployment's broker. "
            "Published only when the operator sets server.advertised_broker_url "
            "(http/https; userinfo, query, and fragment are stripped); the "
            "deployment's internal control-plane→broker hop URL "
            "(server.mcp.broker_url) is topology-private and never published. "
            "Null when unset or invalid."
        )
    )
    authorization_server_metadata: str | None = Field(
        description=(
            "RFC 8414 authorization-server metadata document for the platform "
            "issuer (its registration_endpoint is the agent DCR door /register); "
            "null when the auth surface is not mounted on this process."
        )
    )
    authorization_server_metadata_mcp: str | None = Field(
        description=(
            "RFC 8414 metadata document for the /mcp logical issuer — the one whose "
            "registration_endpoint is the OAuth-client DCR door /oauth-clients "
            "(see auth.methods.oauth_client_dcr). Null when the auth surface is not "
            "mounted on this process or server.mcp.oauth is disabled."
        )
    )
    protected_resource_metadata: str | None = Field(
        description=(
            "RFC 9728 protected-resource metadata document for the MCP surface; "
            "null when the auth surface is not mounted on this process or "
            "server.mcp.oauth is disabled."
        )
    )
    authorize: str | None = Field(
        description="OAuth authorization endpoint (interactive sign-in entry point "
        "for idp and local_login); null when the auth surface is not mounted on "
        "this process."
    )
    token: str | None = Field(
        description="OAuth token endpoint (authorization_code, refresh_token, and "
        "the service-account jwt-bearer grant); null when the auth surface is not "
        "mounted on this process."
    )
    agent_registration: str | None = Field(
        description="Agent dynamic-registration endpoint (RFC 7591, see "
        "auth.methods.agent_dcr); null when the auth surface is not mounted on "
        "this process."
    )
    oauth_client_registration: str | None = Field(
        description="OAuth-client dynamic-registration endpoint (see "
        "auth.methods.oauth_client_dcr); null when the auth surface is not mounted "
        "on this process."
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
    """The login-picker contract: the sign-in options on the process answering.

    Scope caveat for split deployments: mount-derived flags (``agent_dcr``,
    ``service_accounts``) describe **this process only** — ``false`` means "not
    served here", not "does not exist on the deployment"; a sibling tier may
    serve it (see ``surfaces``).
    """

    idp: IdpMethodResponse
    local_login: EnabledMethodResponse = Field(
        description=(
            "Local-account login form on the /authorize flow (auth.local_login). "
            "The *effective* offer: false whenever an external IdP is enabled, "
            "because the IdP always wins and the form is never reachable (no "
            "mixed mode). Entry point: urls.authorize."
        )
    )
    oauth_client_dcr: OauthClientDcrMethodResponse
    agent_dcr: EnabledMethodResponse = Field(
        description="Anonymous agent self-registration (RFC 7591, POST at "
        "urls.agent_registration); true iff the auth surface is mounted on this "
        "process."
    )
    service_accounts: EnabledMethodResponse = Field(
        description="Operator-managed service accounts (jwt-bearer grant at "
        "urls.token); true iff the auth surface is mounted on this process."
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
        description="The control-plane surfaces served by the process answering "
        "this request (sorted), e.g. ['admin', 'auth', 'control', 'registry']. "
        "On a split deployment each tier reports only its own surfaces — a "
        "capability absent here may be served by a sibling tier."
    )
    urls: CapabilitiesUrlsResponse
    auth: CapabilitiesAuthResponse
    features: dict[str, bool] = Field(
        description="Deployment feature flags. OSS ships 'mcp'; downstream packages "
        "may contribute additional boolean flags (additive — never overriding "
        "built-ins)."
    )
    capabilities_version: int = Field(
        description="Shape version of this document, bumped only when a field is "
        "removed, renamed, or retyped. New keys appear without a bump — ignore "
        "unknown keys; hard-fail only on a version you do not understand."
    )


# --- resolution ---------------------------------------------------------------


def _advertised_broker_url(ctx: Context) -> str | None:
    """The broker URL to publish: ``server.advertised_broker_url``, sanitized.

    Only the operator's explicit answer is ever published. The deployment's own
    control-plane→broker hop URL (``server.mcp.broker_url``) is topology-private
    (a compose service name, an internal listener) and never appears on this
    unauthenticated document — even on a local backend, because
    ``server.backend`` is a self-declared hint, not a topology fact.

    The value must be http(s) with a host; userinfo, query, and fragment are
    dropped so a credential embedded in the config can never be published.
    An unusable value is logged and published as ``null`` rather than verbatim.
    """
    raw = ctx.config.server.advertised_broker_url
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        _log.warning(
            "capabilities_advertised_broker_url_invalid",
            reason="must be an absolute http(s) URL with a host",
        )
        return None
    host = parts.hostname
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _resolve_urls(ctx: Context, auth_mounted: bool, base_url: str) -> CapabilitiesUrlsResponse:
    """Build the ``urls`` block; endpoints are absolute when ``base_url`` is known."""

    def _absolute(path: str) -> str:
        return f"{base_url}{path}" if base_url else path

    def _auth_url(path: str) -> str | None:
        return _absolute(path) if auth_mounted else None

    mcp_oauth_discoverable = auth_mounted and ctx.config.server.mcp.oauth.enabled
    return CapabilitiesUrlsResponse(
        broker=_advertised_broker_url(ctx),
        authorization_server_metadata=_auth_url(_AUTHORIZATION_SERVER_METADATA_PATH),
        authorization_server_metadata_mcp=(
            _absolute(_AUTHORIZATION_SERVER_METADATA_MCP_PATH) if mcp_oauth_discoverable else None
        ),
        protected_resource_metadata=(
            _absolute(_PROTECTED_RESOURCE_METADATA_PATH) if mcp_oauth_discoverable else None
        ),
        authorize=_auth_url(_AUTHORIZE_PATH),
        token=_auth_url(_TOKEN_PATH),
        agent_registration=_auth_url(_AGENT_REGISTER_PATH),
        oauth_client_registration=_auth_url(_OAUTH_CLIENTS_PATH),
    )


def _merge_contributed_features(view: CapabilityView, features: dict[str, bool]) -> None:
    """Merge contributor flags into ``features``, isolating every failure mode.

    A contributor is downstream code running behind a public route: a raised
    exception, a non-mapping return, a non-str key, a non-bool value, or a
    (case-insensitive) collision with an existing key is logged and dropped —
    the document itself never fails or degrades because of one contribution.
    """
    for contributor in get_capability_contributors():
        name = getattr(contributor, "__qualname__", repr(contributor))
        try:
            contributed = dict(contributor(view))
        except Exception:
            _log.warning("capability_contributor_failed", contributor=name, exc_info=True)
            continue
        for key, value in contributed.items():
            if not isinstance(key, str) or not isinstance(value, bool):
                _log.warning(
                    "capability_feature_dropped",
                    key=repr(key),
                    contributor=name,
                    reason="keys must be str and values bool",
                )
                continue
            if any(key.lower() == existing.lower() for existing in features):
                # First writer wins, case-insensitively: a contribution never
                # rewrites (or case-shadows) a built-in or earlier-contributed
                # flag's meaning. Logged so the colliding package is
                # discoverable, then dropped.
                _log.warning("capability_feature_collision_ignored", key=key, contributor=name)
                continue
            features[key] = value


def resolve_capabilities(
    ctx: Context, enabled_apps: Iterable[str], *, base_url: str | None = None
) -> CapabilitiesResponse:
    """Build the capability document from the live ``Context`` (config-only, no DB).

    ``base_url`` roots the ``urls`` endpoints; when ``None`` it defaults to the
    sanitized ``auth.canonical_base_url`` (metadata paths stay root-relative if
    that is unset — the route handler substitutes the request origin instead).
    """
    surfaces = sorted(set(enabled_apps))
    auth_mounted = "auth" in surfaces
    idp = ctx.config.auth.idp
    mcp_oauth = ctx.config.server.mcp.oauth

    canonical_base_url = ctx.config.auth.canonical_base_url or ""
    if canonical_base_url:
        canonical_base_url, _host = sanitized_url_parts(canonical_base_url)
    if base_url is None:
        base_url = canonical_base_url
    base_url = base_url.rstrip("/")

    features: dict[str, bool] = {"mcp": ctx.config.server.mcp.enabled}
    view = CapabilityView(
        backend=ctx.config.server.backend,
        canonical_base_url=canonical_base_url,
        surfaces=tuple(surfaces),
        mcp_enabled=ctx.config.server.mcp.enabled,
    )
    _merge_contributed_features(view, features)

    return CapabilitiesResponse(
        instance=CapabilitiesInstanceResponse(
            backend=ctx.config.server.backend,
            canonical_base_url=canonical_base_url,
        ),
        surfaces=surfaces,
        urls=_resolve_urls(ctx, auth_mounted, base_url),
        auth=CapabilitiesAuthResponse(
            methods=AuthMethodsResponse(
                idp=IdpMethodResponse(
                    enabled=idp.enabled,
                    provider=idp.provider if idp.enabled else None,
                ),
                # Effective offer, not raw config: the /authorize flow only
                # falls through to the login form when no IdP is configured
                # ("IdP always wins" — see authorize.py), so a picker must see
                # false while an IdP is enabled even if the flag is on.
                local_login=EnabledMethodResponse(
                    enabled=ctx.config.auth.local_login.enabled and not idp.enabled
                ),
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


def _warn_if_loopback_broker(snapshot: CapabilitiesResponse) -> None:
    """One-shot boot warning: a loopback broker beside a public canonical URL.

    An operator who advertises ``http://127.0.0.1:…`` while clients reach the
    deployment at a public canonical URL is almost certainly publishing a
    misconfiguration — clients cannot route to it, and it confirms an internal
    listener to unauthenticated callers.
    """
    broker = snapshot.urls.broker
    canonical = snapshot.instance.canonical_base_url
    if not broker or not canonical:
        return
    broker_host = urlsplit(broker).hostname or ""
    canonical_host = urlsplit(canonical).hostname or ""
    if broker_host in _LOOPBACK_HOSTS and canonical_host not in _LOOPBACK_HOSTS:
        _log.warning(
            "capabilities_broker_url_is_loopback",
            broker_url=broker,
            canonical_base_url=canonical,
        )


def get_capabilities_router(ctx: Context, enabled_apps: Iterable[str]) -> APIRouter:
    """Router exposing the public capability document (``GET /capabilities``).

    ``enabled_apps`` is captured at app-build time (it is deployment topology,
    not per-request state). Mounted next to the instance router in both app
    factories; the broker opts out exactly as it does for ``/instance``.

    The document is resolved **once, here** — contributors run at app build,
    never on the request path, so a slow, crashing, or colliding contribution
    surfaces as a boot-time log line instead of an unauthenticated per-request
    cost. The one-shot ``capabilities_resolved`` line gives operators the
    resolved ``features`` map so an unexplained key is traceable to its
    ``capability_contributor_registered`` line.
    """
    surfaces = sorted(set(enabled_apps))
    auth_mounted = "auth" in surfaces
    snapshot = resolve_capabilities(ctx, surfaces)
    _log.info(
        "capabilities_resolved",
        surfaces=surfaces,
        broker_url=snapshot.urls.broker,
        features=sorted(snapshot.features),
    )
    _warn_if_loopback_broker(snapshot)
    router = APIRouter()

    @router.get(
        CAPABILITIES_PATH,
        operation_id="getCapabilities",
        summary="Deployment capabilities",
        response_model=CapabilitiesResponse,
    )
    async def capabilities(request: Request, ctx: Context = Depends(get_ctx)) -> Response:
        """Return this deployment's public self-description.

        Unauthenticated and DB-free so any client can discover — from one URL —
        which sign-in methods this deployment supports, where its broker is,
        which surfaces are mounted, and which optional features are enabled,
        instead of probe-and-guess across ``/instance``, ``/auth/idp``, and the
        RFC 8414 document. The body is deterministic for a given config, so it
        carries an ``ETag`` and honours ``If-None-Match`` (304) — every client
        fetches this before sign-in, and a fleet restart should revalidate, not
        re-download.
        """
        base_url = snapshot.instance.canonical_base_url or str(request.base_url).rstrip("/")
        doc = snapshot.model_copy(update={"urls": _resolve_urls(ctx, auth_mounted, base_url)})
        body = doc.model_dump_json().encode()
        etag = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
        headers = {"ETag": etag, "Cache-Control": "public, max-age=60"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=body, media_type="application/json", headers=headers)

    return router
