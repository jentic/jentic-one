"""Every mutating connect route spends the shared per-actor bucket.

The bucket exists because these routes call a vendor: spamming them can get
the platform's IP throttled, which fails fast for every legitimate user on
the install. A route that skips it also lets an actor double its rate by
splitting requests across the surfaces, so the set is pinned here rather
than left to each handler's author.
"""

from __future__ import annotations

import inspect

import pytest

from jentic_one.control.web.routers import integrations

#: The mutating connect routes. A new one belongs here with its limiter call.
_MUTATING_ROUTES = (
    "integrations_connect",
    "integrations_edit_connection",
    "confirm_connect_session",
    "amend_connect_session",
)


@pytest.mark.parametrize("handler_name", _MUTATING_ROUTES)
def test_a_mutating_connect_route_enforces_the_connect_rate_limit(handler_name: str) -> None:
    handler = getattr(integrations, handler_name, None)
    assert handler is not None, f"{handler_name} is no longer in the integrations router"
    source = inspect.getsource(handler)
    assert "_enforce_connect_rate_limit" in source, (
        f"{handler_name} does not spend the connect rate-limit bucket; an actor can "
        "drive it at whatever rate it can issue requests, and can double its rate by "
        "splitting calls across HTTP and MCP"
    )
