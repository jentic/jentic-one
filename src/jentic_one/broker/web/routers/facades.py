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

Paths under ``/_facade`` that no facade serves answer 404 and never reach the
catch-all.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from jentic_one.broker.adapters.runners.base import UpstreamRunner
from jentic_one.broker.core.exceptions import OperationNotFoundError
from jentic_one.broker.core.facades import (
    CLOUDFLARE_FACADE,
    FACADE_PATH_PREFIX,
    cloudflare_error_body,
    facade_upstream_url,
    is_facade_response_header,
)
from jentic_one.broker.web.deps import (
    AgentRuleEvaluatorDep,
    CredentialDeriver,
    IdempotencyStoreDep,
    get_cli_facade_runner,
    require_cli_facade_invocation,
    require_cli_facade_served,
    require_cli_invocation,
)
from jentic_one.broker.web.routers.execute import FacadeCall, handle_facade_call
from jentic_one.shared.auth.cli_invocation import CliInvocationIdentity
from jentic_one.shared.context import Context
from jentic_one.shared.tracing import server_chosen_trace
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

#: Every method the forward proxy serves.
_FACADE_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def _restrict_response_headers(response: Response) -> Response:
    """Drop every response header outside the facade allowlist (in place)."""
    for name in {key for key in response.headers if not is_facade_response_header(key)}:
        del response.headers[name]
    return response


def _problem_of(response: Response) -> dict[str, Any]:
    """The problem body a central handler rendered, or ``{}`` if it is not JSON."""
    try:
        problem = json.loads(bytes(response.body))
    except (AttributeError, UnicodeDecodeError, ValueError):
        return {}
    return problem if isinstance(problem, dict) else {}


def cloudflare_error_response(request: Request, response: Response) -> Response:
    """Re-render a central handler's error response as a Cloudflare API v4 error.

    Same status; the body becomes ``{"success": false, "errors": [{"code",
    "message"}], "messages": [], "result": null}`` with the problem's detail
    and agent directive in the message; only allowlisted headers (e.g.
    ``Retry-After``, ``Jentic-*``) are kept.
    """
    body = cloudflare_error_body(response.status_code, _problem_of(response))
    headers = {
        key: value
        for key, value in response.headers.items()
        if is_facade_response_header(key) and key.lower() not in ("content-length", "content-type")
    }
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
    "cloudflare_error_response",
    "cloudflare_router",
    "reserved_router",
]
