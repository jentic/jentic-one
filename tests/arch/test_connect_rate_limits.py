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
from jentic_one.mcp import tools

pytestmark = pytest.mark.arch

#: The mutating connect routes. A new one belongs here with its limiter call.
_MUTATING_ROUTES = (
    "integrations_connect",
    "integrations_edit_connection",
    "confirm_connect_session",
    "amend_connect_session",
    "rebase_connect_session",
)

#: The MCP mount's twins of those routes, which share the same bucket.
_MUTATING_TOOLS = (
    "handle_request_connection",
    "handle_request_access_change",
    "handle_amend_request",
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


@pytest.mark.parametrize("handler_name", _MUTATING_ROUTES)
def test_a_mutating_connect_route_declares_its_429(handler_name: str) -> None:
    """Spending the bucket is half of it: the limit has to be on the wire.

    A 429 the spec does not declare reaches no generated client, so nothing
    honours the ``Retry-After`` these routes return and a caller retries into
    the same refusal. Pinned beside the spend, so one cannot be added without
    the other.
    """
    route = next(
        (r for r in integrations.router.routes if getattr(r, "name", None) == handler_name),
        None,
    )
    assert route is not None, f"{handler_name} is no longer a route on the integrations router"
    assert 429 in getattr(route, "responses", {}), (
        f"{handler_name} spends the connect bucket but declares no 429, so no generated "
        "client can honour the Retry-After it returns"
    )


@pytest.mark.parametrize("handler_name", _MUTATING_TOOLS)
def test_a_mutating_connect_tool_enforces_the_connect_rate_limit(handler_name: str) -> None:
    handler = getattr(tools, handler_name, None)
    assert handler is not None, f"{handler_name} is no longer in the MCP tool mount"
    source = inspect.getsource(handler)
    assert "_spend_connect_bucket" in source, (
        f"{handler_name} does not spend the connect rate-limit bucket; the tool mount "
        "reaches the same vendors its route does, and an actor that finds one "
        "unlimited surface has no reason to use the limited one"
    )
