"""Problem bodies for a queued execution denied at run time.

Shared by the broker's ``QueuedExecutionAuthorizer`` and the worker's
``ExecutionHandler`` so both denial points hand a polling caller the same
RFC 9457 body the sync execute route would have returned.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Mirrors the sync edge's 401 for a caller whose actor is no longer active
# (``require_broker_identity`` → ``Unauthorized(type="unauthorized")``).
INACTIVE_ACTOR_DETAIL = "The actor that queued this execution is no longer active."


def queued_execution_instance(upstream_url: str) -> str:
    """The broker request path a sync call to ``upstream_url`` would have had.

    Used as the problem ``instance`` so a queued denial names the same
    occurrence the sync route's ``request.url.path`` would.
    """
    parsed = urlparse(upstream_url)
    return f"/{parsed.netloc}{parsed.path}"


def inactive_actor_problem(instance: str) -> dict[str, object]:
    """The 401 body for a job whose enqueuing actor is no longer active."""
    return {
        "type": "unauthorized",
        "title": "Unauthorized",
        "status": 401,
        "detail": INACTIVE_ACTOR_DETAIL,
        "instance": instance,
    }
