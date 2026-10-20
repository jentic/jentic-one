"""Vendor CLI facade routes: a vendor's own CLI, pointed at the broker.

Each facade is a real ``APIRoute`` registered before the forward-proxy
catch-all, under the reserved ``/_facade`` prefix (see
``broker/core/facades.py`` for why the prefix cannot collide with a proxied
URL). A call is authenticated with a ``cit_`` invocation token minted for the
facade's vendor, mapped to the vendor's real upstream URL and run through the
execute pipeline (:func:`~jentic_one.broker.web.routers.execute.handle_facade_call`)
under the token's server-chosen trace id and credential choice.

The vendor CLI cannot read RFC 9457 Problem Details, so a facade route's
class sets an error-response hook
(:func:`~jentic_one.shared.web.errors.apply_route_error_response_hook`): every
central exception handler still maps and logs the error as usual, and the
hook re-renders the finished problem response in the vendor's error envelope
with the same status. Successful responses keep only the facade response
header allowlist.

The proxy route (``/_facade/proxy/<full upstream URL>``) is not a vendor
facade: it is the broker end of an egress proxy that relays a vendor CLI's
calls with the command's invocation token, so it takes the full upstream URL
as the forward-proxy catch-all does and requires the URL to resolve to an API
of the token's vendor. The CLI behind the egress proxy believes it is talking
to the vendor, so the route's errors take the shape of the vendor the URL's
host belongs to (:class:`ProxyFacadeRoute`): GitHub's for ``*.github.com``,
Cloudflare's for ``*.cloudflare.com``, standard Problem Details for any other
host.

Paths under ``/_facade`` that no facade serves answer 404 and never reach the
catch-all.

The GitHub facade is host-routed: its routes match only when the request's
``Host`` names the configured ``cli_facades.vendors.github.host`` and the
vendor is enabled (:class:`GitHubFacadeRoute`), so on the broker's own host
they never match and every request reaches the routes after them as before.
On the facade host every other path answers GitHub's 404 and never reaches
the catch-all, apart from the broker routes included ahead of the facades
(probes, ``/metrics``, agent discovery, the path-mode facades). Successful
responses keep the GitHub header allowlist, with ``Link`` and ``Location``
pointed back at the facade.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.routing import Match
from starlette.types import Scope

from jentic_one.broker.adapters.runners.base import UpstreamRunner
from jentic_one.broker.core.exceptions import OperationNotFoundError
from jentic_one.broker.core.facades import (
    CLOUDFLARE_FACADE,
    FACADE_PATH_PREFIX,
    GITHUB_GRAPHQL_PATH,
    GITHUB_META_BODY,
    GITHUB_META_PATH,
    GITHUB_REST_FACADE,
    GITHUB_UPLOADS_FACADE,
    PROXY_FACADE_PREFIX,
    cloudflare_error_body,
    facade_upstream_url,
    github_error_body,
    github_upstream_url,
    is_facade_host,
    is_facade_response_header,
    is_github_response_header,
    proxy_error_vendor,
    proxy_upstream_url,
    rewrite_github_link_header,
    rewrite_github_url,
)
from jentic_one.broker.web.deps import (
    AgentRuleEvaluatorDep,
    CredentialDeriver,
    IdempotencyStoreDep,
    get_cli_facade_runner,
    get_cli_proxy_runner,
    require_cli_facade_invocation,
    require_cli_facade_served,
    require_cli_invocation,
    require_cli_proxy_invocation,
)
from jentic_one.broker.web.routers.execute import FacadeCall, handle_facade_call
from jentic_one.shared.auth.cli_invocation import CliInvocationIdentity
from jentic_one.shared.config import CliFacadesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.tracing import server_chosen_trace
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

#: Every method the forward proxy serves.
_FACADE_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _restrict_response_headers(
    response: Response, allowed: Callable[[str], bool] = is_facade_response_header
) -> Response:
    """Drop every response header ``allowed`` rejects (in place); default: the facade allowlist."""
    for name in {key for key in response.headers if not allowed(key)}:
        del response.headers[name]
    return response


def _problem_of(response: Response) -> dict[str, Any]:
    """The problem body a central handler rendered, or ``{}`` if it is not JSON."""
    try:
        problem = json.loads(bytes(response.body))
    except (AttributeError, UnicodeDecodeError, ValueError):
        return {}
    return problem if isinstance(problem, dict) else {}


def _error_headers(response: Response, allowed: Callable[[str], bool]) -> dict[str, str]:
    """The headers of a central handler's error response a facade keeps on its own error."""
    return {
        key: value
        for key, value in response.headers.items()
        if allowed(key) and key.lower() not in ("content-length", "content-type")
    }


def cloudflare_error_response(request: Request, response: Response) -> Response:
    """Re-render a central handler's error response as a Cloudflare API v4 error.

    Same status; the body becomes ``{"success": false, "errors": [{"code",
    "message"}], "messages": [], "result": null}`` with the problem's detail
    and agent directive in the message; only allowlisted headers (e.g.
    ``Retry-After``, ``Jentic-*``) are kept.
    """
    body = cloudflare_error_body(response.status_code, _problem_of(response))
    headers = _error_headers(response, is_facade_response_header)
    return JSONResponse(body, status_code=response.status_code, headers=headers)


class CloudflareFacadeRoute(APIRoute):
    """A route whose errors are answered in Cloudflare's error envelope."""

    error_response_hook = staticmethod(cloudflare_error_response)


cloudflare_router = APIRouter(route_class=CloudflareFacadeRoute)

_RequireCloudflareServed = require_cli_facade_served(CLOUDFLARE_FACADE.vendor)
_RequireCloudflareInvocation = require_cli_facade_invocation(CLOUDFLARE_FACADE.vendor)
_CloudflareRunner = get_cli_facade_runner(CLOUDFLARE_FACADE.upstream_base)


@cloudflare_router.api_route(
    f"{CLOUDFLARE_FACADE.prefix}/{{path:path}}",
    methods=_FACADE_METHODS,
    include_in_schema=False,
)
async def cloudflare_facade(
    path: str,
    request: Request,
    credential_deriver: CredentialDeriver,
    agent_rule_evaluator: AgentRuleEvaluatorDep,
    idempotency: IdempotencyStoreDep,
    _served: None = _RequireCloudflareServed,
    invocation: CliInvocationIdentity = _RequireCloudflareInvocation,
    runner: UpstreamRunner = _CloudflareRunner,
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Serve ``cf`` (``CLOUDFLARE_API_BASE_URL=<broker>/_facade/cloudflare``).

    ``/_facade/cloudflare/<path>?<query>`` runs as
    ``https://api.cloudflare.com/client/v4/<path>?<query>``, with ``path``
    and ``query`` exactly as ``cf`` encoded them.
    """
    upstream_url = facade_upstream_url(request.scope, CLOUDFLARE_FACADE)
    if upstream_url is None:
        raise OperationNotFoundError(
            "Operation not found: the path is not a Cloudflare API path.",
            type="operation_not_found",
            instance=request.url.path,
        )
    claims = invocation.claims
    call = FacadeCall(
        upstream_url=upstream_url,
        trace_id=claims.trace_id,
        credential_name=claims.credential_name,
    )
    with server_chosen_trace(claims.trace_id, "broker.cli_facade"):
        response = await handle_facade_call(
            request,
            ctx,
            invocation.identity,
            credential_deriver,
            agent_rule_evaluator,
            runner,
            idempotency,
            call,
        )
    return _restrict_response_headers(response)


# ── GitHub (host mode) ───────────────────────────────────────────────────────


def github_error_response(request: Request, response: Response) -> Response:
    """Re-render a central handler's error response as a GitHub REST error.

    Same status; the body becomes ``{"message", "documentation_url", "status"}``
    (:func:`~jentic_one.broker.core.facades.github_error_body`), which ``gh``
    shows for REST and GraphQL calls alike; only allowlisted headers are kept.
    """
    body = github_error_body(response.status_code, _problem_of(response))
    headers = _error_headers(response, is_github_response_header)
    return JSONResponse(body, status_code=response.status_code, headers=headers)


def _facades_config(scope: Scope) -> CliFacadesConfig | None:
    app = scope.get("app")
    ctx: Context | None = getattr(getattr(app, "state", None), "ctx", None)
    return ctx.config.cli_facades if ctx is not None else None


def on_github_facade_host(scope: Scope) -> bool:
    """Whether ``scope`` is an HTTP request to the enabled GitHub facade's host.

    Read from the live config on every request, so the facade host stops
    matching as soon as the vendor is switched off.
    """
    if scope.get("type") != "http":
        return False
    facades_config = _facades_config(scope)
    if facades_config is None or not facades_config.vendor_enabled("github"):
        return False
    host = Headers(scope=scope).get("host")
    return is_facade_host(host, facades_config.vendors.github.host)


class GitHubFacadeRoute(APIRoute):
    """A GitHub facade route: matches only on the facade host; errors in GitHub's shape.

    On any other host :meth:`matches` answers ``Match.NONE``, so the request
    falls through to the routes after it exactly as if this route did not
    exist. A dependency could not do this: by the time it runs the route has
    matched and the catch-all is out of reach.
    """

    error_response_hook = staticmethod(github_error_response)

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        """Match as an ``APIRoute`` would, on the GitHub facade host only."""
        if not on_github_facade_host(scope):
            return Match.NONE, {}
        return super().matches(scope)


def _github_facade_base(ctx: Context) -> str:
    """The base URL ``gh`` reaches the facade at: the public address, else ``https://<host>``."""
    settings = ctx.config.cli_facades.vendors.github
    if settings.public_address:
        return settings.public_address.rstrip("/")
    return f"https://{settings.host}"


def _github_response(
    response: Response, facade_base: str, upstream_url: str | None = None
) -> Response:
    """Keep the GitHub header allowlist and point ``Link``/``Location`` at the facade (in place).

    Applied to every successful facade response, buffered, streamed or
    replayed from the idempotency store alike: headers are still mutable
    until the response is sent. ``upstream_url`` is the call's github.com URL,
    so pagination links naming its repository by id keep the call's
    ``/repos/<owner>/<repo>`` form.
    """
    _restrict_response_headers(response, is_github_response_header)
    link = response.headers.get("link")
    if link is not None:
        response.headers["link"] = rewrite_github_link_header(link, facade_base, upstream_url)
    location = response.headers.get("location")
    if location is not None:
        response.headers["location"] = rewrite_github_url(location, facade_base)
    return response


github_router = APIRouter(route_class=GitHubFacadeRoute)

_RequireGitHubServed = require_cli_facade_served(GITHUB_REST_FACADE.vendor)
_RequireGitHubInvocation = require_cli_facade_invocation(GITHUB_REST_FACADE.vendor)
_GitHubRunner = get_cli_facade_runner(GITHUB_REST_FACADE.upstream_base)


@github_router.get(GITHUB_META_PATH, include_in_schema=False)
async def github_facade_meta(
    _served: None = _RequireGitHubServed,
    _invocation: CliInvocationIdentity = _RequireGitHubInvocation,
) -> Response:
    """Answer ``gh``'s Enterprise Server version check (``GET /api/v3/meta``) locally.

    Nothing is forwarded or recorded: the body is the static
    :data:`~jentic_one.broker.core.facades.GITHUB_META_BODY`.
    """
    return JSONResponse(dict(GITHUB_META_BODY))


@github_router.api_route(GITHUB_GRAPHQL_PATH, methods=_FACADE_METHODS, include_in_schema=False)
@github_router.api_route(
    f"{GITHUB_REST_FACADE.prefix}/{{path:path}}", methods=_FACADE_METHODS, include_in_schema=False
)
@github_router.api_route(
    f"{GITHUB_UPLOADS_FACADE.prefix}/{{path:path}}",
    methods=_FACADE_METHODS,
    include_in_schema=False,
)
async def github_facade(
    request: Request,
    credential_deriver: CredentialDeriver,
    agent_rule_evaluator: AgentRuleEvaluatorDep,
    idempotency: IdempotencyStoreDep,
    _served: None = _RequireGitHubServed,
    invocation: CliInvocationIdentity = _RequireGitHubInvocation,
    runner: UpstreamRunner = _GitHubRunner,
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Serve ``gh`` (``GH_HOST=<facade host>``) in the GitHub Enterprise Server layout.

    ``/api/v3/<path>`` runs as ``https://api.github.com/<path>``,
    ``/api/graphql`` as ``https://api.github.com/graphql`` and
    ``/api/uploads/<path>`` as ``https://uploads.github.com/<path>``, with the
    path and query exactly as ``gh`` encoded them.
    """
    upstream_url = github_upstream_url(request.scope)
    if upstream_url is None:
        raise OperationNotFoundError(
            "Not Found: the path is not a GitHub API path.",
            type="operation_not_found",
            instance=request.url.path,
        )
    claims = invocation.claims
    call = FacadeCall(
        upstream_url=upstream_url,
        trace_id=claims.trace_id,
        credential_name=claims.credential_name,
    )
    with server_chosen_trace(claims.trace_id, "broker.cli_facade"):
        response = await handle_facade_call(
            request,
            ctx,
            invocation.identity,
            credential_deriver,
            agent_rule_evaluator,
            runner,
            idempotency,
            call,
        )
    return _github_response(response, _github_facade_base(ctx), upstream_url)


#: Every other path on the GitHub facade host. A root catch-all, so the app
#: factory includes it with the forward proxy's, ahead of it: on the facade
#: host it answers GitHub's 404 instead of the request being read as a
#: proxied URL; on any other host it never matches.
github_not_found_router = APIRouter(route_class=GitHubFacadeRoute)


@github_not_found_router.api_route("/{rest:path}", methods=_FACADE_METHODS, include_in_schema=False)
async def github_facade_not_found(
    request: Request,
    _invocation: CliInvocationIdentity = _RequireGitHubInvocation,
) -> Response:
    """Answer GitHub's 404 for a path outside the GitHub layout on the facade host."""
    raise OperationNotFoundError(
        "Not Found: the GitHub facade serves only /api/v3, /api/graphql and /api/uploads.",
        type="cli_facade_not_found",
        instance=request.url.path,
    )


# ── Proxy route (full upstream URL) ──────────────────────────────────────────


def proxy_error_response(request: Request, response: Response) -> Response:
    """Re-render a proxy-route error in the shape of the upstream URL's vendor.

    GitHub's for a ``*.github.com`` URL, Cloudflare's for ``*.cloudflare.com``
    (:func:`~jentic_one.broker.core.facades.proxy_error_vendor`); any other
    URL, or none, keeps the central handler's Problem Details.
    """
    vendor = proxy_error_vendor(request.scope)
    if vendor == "github":
        return github_error_response(request, response)
    if vendor == "cloudflare":
        return cloudflare_error_response(request, response)
    return response


class ProxyFacadeRoute(APIRoute):
    """The proxy route: errors in the shape of the upstream URL's vendor."""

    error_response_hook = staticmethod(proxy_error_response)


proxy_router = APIRouter(route_class=ProxyFacadeRoute)


@proxy_router.api_route(
    f"{PROXY_FACADE_PREFIX}/{{upstream_url:path}}",
    methods=_FACADE_METHODS,
    include_in_schema=False,
)
async def cli_proxy(
    request: Request,
    credential_deriver: CredentialDeriver,
    agent_rule_evaluator: AgentRuleEvaluatorDep,
    idempotency: IdempotencyStoreDep,
    invocation: CliInvocationIdentity = Depends(require_cli_proxy_invocation),
    runner: UpstreamRunner = Depends(get_cli_proxy_runner),
    ctx: Context = Depends(get_ctx),
) -> Response:
    """Run a vendor CLI's call relayed by an egress proxy, with its invocation token.

    ``/_facade/proxy/<url>?<query>`` runs as ``<url>?<query>``, read from the
    raw path exactly as the forward proxy reads ``/<url>``. The URL must
    resolve to a registered API of the token's vendor (else 404 for an
    unregistered URL, 403 ``cli_invocation_vendor_mismatch`` for another
    vendor's API); the call runs on the token's trace id and credential
    choice. The response keeps the facade header allowlist, or GitHub's for a
    GitHub URL (``Link`` pagination, ``X-GitHub-*``), unchanged: the egress
    proxy's client addresses the vendor's own host.
    """
    upstream_url = proxy_upstream_url(request.scope)
    if upstream_url is None:
        raise OperationNotFoundError(
            "Operation not found: no upstream URL follows the proxy route's prefix.",
            type="operation_not_found",
            instance=request.url.path,
        )
    claims = invocation.claims
    call = FacadeCall(
        upstream_url=upstream_url,
        trace_id=claims.trace_id,
        credential_name=claims.credential_name,
        vendor=claims.vendor,
    )
    with server_chosen_trace(claims.trace_id, "broker.cli_proxy"):
        response = await handle_facade_call(
            request,
            ctx,
            invocation.identity,
            credential_deriver,
            agent_rule_evaluator,
            runner,
            idempotency,
            call,
        )
    if proxy_error_vendor(request.scope) == "github":
        return _restrict_response_headers(response, is_github_response_header)
    return _restrict_response_headers(response)


#: Paths under the reserved prefix that no facade serves. Registered after
#: every facade router and before the catch-all, so they answer 404 instead
#: of being read as a proxied URL.
reserved_router = APIRouter()


@reserved_router.api_route(FACADE_PATH_PREFIX, methods=_FACADE_METHODS, include_in_schema=False)
@reserved_router.api_route(
    f"{FACADE_PATH_PREFIX}/{{rest:path}}", methods=_FACADE_METHODS, include_in_schema=False
)
async def unknown_facade(
    request: Request,
    _invocation: CliInvocationIdentity = Depends(require_cli_invocation),
) -> Response:
    """Answer 404 for a ``/_facade`` path no facade serves."""
    raise OperationNotFoundError(
        "Not found: no CLI facade is served at this path.",
        type="cli_facade_not_found",
        instance=request.url.path,
    )


__all__ = [
    "CloudflareFacadeRoute",
    "GitHubFacadeRoute",
    "ProxyFacadeRoute",
    "cloudflare_error_response",
    "cloudflare_router",
    "github_error_response",
    "github_not_found_router",
    "github_router",
    "on_github_facade_host",
    "proxy_error_response",
    "proxy_router",
    "reserved_router",
]
