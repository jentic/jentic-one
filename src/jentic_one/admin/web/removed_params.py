"""Runtime signal for query parameters the admin API accepts but ignores.

FastAPI ignores unknown query parameters, so a stale caller that still sends a
retired *filter* gets a 200 with a wider result than it asked for. Logging the
ignored name lets operators find those callers; the value is never logged.
"""

from __future__ import annotations

import structlog
from fastapi import Request

from jentic_one.shared.auth.identity import Identity

logger = structlog.get_logger()

# Filters a caller may still send; each is ignored and logged.
# ``toolkit_id`` (#1745): executions keep it only as response attribution.
IGNORED_QUERY_PARAMS: frozenset[str] = frozenset({"toolkit_id"})


def log_ignored_query_params(request: Request, identity: Identity) -> None:
    """Emit one ``removed_query_param_ignored`` INFO per ignored param present.

    INFO, not WARNING: a stale dashboard or script sends the param on every
    call; that is client-side configuration for the operator to fix, not a
    server fault.

    ``route`` is the matched route template (``/executions/{execution_id}``),
    not the resolved path, so resource ids never land in the field.
    """
    ignored = sorted(IGNORED_QUERY_PARAMS.intersection(request.query_params.keys()))
    if not ignored:
        return
    route = _route_template(request)
    for param in ignored:
        logger.info(
            "removed_query_param_ignored",
            param=param,
            route=route,
            actor_id=identity.sub,
        )


def _route_template(request: Request) -> str:
    """Return the matched route's path template, or the URL path if none matched.

    Starlette sets ``scope["route"]`` once routing matches, which is always the
    case inside a handler; the fallback covers a caller outside routing.
    """
    path_format = getattr(request.scope.get("route"), "path_format", None)
    if isinstance(path_format, str):
        return path_format
    return request.url.path
