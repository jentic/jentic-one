"""Unit tests for the vendor CLI facades: addressing, header policy and error translation.

The error tests build a small app with the broker's and the platform's
central exception handlers (no per-route handler) and one route of the
Cloudflare facade route class, then check that every kind of failure (a
broker domain error, an auth ``ProblemDetailException``, an unmapped
exception) reaches the client as Cloudflare's error envelope with the
original status, while a plain route keeps RFC 9457 Problem Details.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, Unauthorized
from starlette.requests import Request

from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    AgentDirective,
    CliInvocationVendorMismatchError,
    ExecutionHeldError,
    OperationNotFoundError,
    RateLimitExceededError,
)
from jentic_one.broker.core.facades import (
    CLOUDFLARE_FACADE,
    PROXY_API_VENDORS,
    PROXY_FACADE_PREFIX,
    api_vendor_matches,
    cloudflare_error_body,
    facade_error_message,
    facade_request_headers,
    facade_upstream_url,
    is_facade_response_header,
    proxy_upstream_url,
)
from jentic_one.broker.core.problem import STATUS_BY_ERROR
from jentic_one.broker.web.app import _routers as broker_routers
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.facades import (
    CloudflareFacadeRoute,
    GitHubFacadeRoute,
    _restrict_response_headers,
    cloudflare_error_response,
)
from jentic_one.shared.cli_facades import SERVED_FACADE_CLIS, facade_path_prefix
from jentic_one.shared.config import CliFacadeVendorsConfig
from jentic_one.shared.tracing import current_trace_id, server_chosen_trace
from jentic_one.shared.web.app_factory import spa_aware_problem_detail_handler
from jentic_one.shared.web.errors import (
    apply_route_error_response_hook,
    unhandled_exception_handler,
)

_TOKEN = "cit_" + "A1b2C3d4" * 5 + "xyz"


def _scope(raw_path: bytes, query: bytes = b"") -> dict[str, Any]:
    return {"type": "http", "raw_path": raw_path, "path": raw_path.decode(), "query_string": query}


# ── addressing ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("vendor", sorted(SERVED_FACADE_CLIS))
def test_every_advertised_facade_has_a_broker_route(vendor: str) -> None:
    # Control advertises SERVED_FACADE_CLIS to agents' CLI wrappers; each one
    # must be a route the broker actually serves: under the shared prefix in
    # path mode, on the facade host (the GitHub route class) in host mode.
    routes = [
        route
        for router, _prefix, _tags in broker_routers(readiness_saturation_threshold=1.0)
        for route in router.routes
    ]
    settings = CliFacadeVendorsConfig().get(vendor)
    assert settings is not None
    if settings.mode == "host":
        assert any(isinstance(route, GitHubFacadeRoute) for route in routes)
    else:
        prefix = facade_path_prefix(vendor)
        assert any(getattr(route, "path", "").startswith(f"{prefix}/") for route in routes)


def test_upstream_url_keeps_the_raw_path_and_query() -> None:
    url = facade_upstream_url(
        _scope(b"/_facade/cloudflare/accounts/a1/values/my%2Fkey", b"cursor=a%20b&per_page=5"),
        CLOUDFLARE_FACADE,
    )
    assert url == (
        "https://api.cloudflare.com/client/v4/accounts/a1/values/my%2Fkey?cursor=a%20b&per_page=5"
    )


def test_upstream_url_of_the_bare_prefix_is_the_api_root() -> None:
    url = facade_upstream_url(_scope(b"/_facade/cloudflare/"), CLOUDFLARE_FACADE)
    assert url == "https://api.cloudflare.com/client/v4/"


@pytest.mark.parametrize(
    "raw_path",
    [
        # The route matched only after decoding: the literal prefix is absent.
        b"/_facade/cloudfl%61re/zones",
        b"/_facade/cloudflare",
        # Dot segments, literal or encoded, could leave the API base path.
        b"/_facade/cloudflare/../user/tokens",
        b"/_facade/cloudflare/zones/%2e%2e/x",
        b"/_facade/cloudflare/./zones",
    ],
)
def test_upstream_url_refuses_paths_off_the_facade(raw_path: bytes) -> None:
    assert facade_upstream_url(_scope(raw_path), CLOUDFLARE_FACADE) is None


def test_the_reserved_prefix_cannot_be_a_hostname() -> None:
    # The catch-all reads the first segment as the upstream host; an
    # underscore is not valid in a DNS hostname, so nothing is shadowed.
    first_segment = CLOUDFLARE_FACADE.prefix.strip("/").split("/")[0]
    assert first_segment.startswith("_")


# ── proxy route ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw_path", "query", "expected"),
    [
        (
            b"/_facade/proxy/https://api.github.com/repos/o/r/contents/a%2Fb",
            b"ref=main&x=a%20b",
            "https://api.github.com/repos/o/r/contents/a%2Fb?ref=main&x=a%20b",
        ),
        # Starlette collapses the scheme's doubled slash; it is put back.
        (b"/_facade/proxy/https:/api.github.com/user", b"", "https://api.github.com/user"),
        # No scheme means https, as at the forward proxy.
        (b"/_facade/proxy/api.cloudflare.com/client/v4/zones", b"", None),
    ],
)
def test_proxy_upstream_url_reads_the_full_url_from_the_raw_path(
    raw_path: bytes, query: bytes, expected: str | None
) -> None:
    url = proxy_upstream_url(_scope(raw_path, query))
    assert url == (expected or "https://api.cloudflare.com/client/v4/zones")


@pytest.mark.parametrize(
    "raw_path",
    [
        b"/_facade/proxy",
        b"/_facade/proxy/",
        b"/_facade/proxy//",
        # The route matched only after decoding: the literal prefix is absent.
        b"/_facade/pr%6fxy/https://api.github.com/user",
        b"/_facade/proxy/https://api.github.com/repos/../user",
        b"/_facade/proxy/https://api.github.com/repos/%2E%2E/user",
        b"/_facade/proxy/https://api.github.com/./user",
    ],
)
def test_proxy_upstream_url_refuses_paths_without_a_usable_url(raw_path: bytes) -> None:
    assert proxy_upstream_url(_scope(raw_path)) is None


def test_the_proxy_route_is_under_the_reserved_prefix() -> None:
    assert PROXY_FACADE_PREFIX == "/_facade/proxy"
    assert "proxy" not in SERVED_FACADE_CLIS


def test_the_proxy_route_is_registered_before_the_reserved_prefix_404() -> None:
    paths = [
        getattr(route, "path", "")
        for router, _prefix, _tags in broker_routers(readiness_saturation_threshold=1.0)
        for route in router.routes
    ]
    proxy_index = paths.index(f"{PROXY_FACADE_PREFIX}/{{upstream_url:path}}")
    assert proxy_index < paths.index("/_facade/{rest:path}")
    assert proxy_index < paths.index("/{upstream_url:path}")


@pytest.mark.parametrize(
    ("token_vendor", "api_vendor", "matches"),
    [
        # The registry stores the slug; a raw domain compares the same way.
        ("github", "github-com", True),
        ("github", "github.com", True),
        ("github", "GitHub.com", True),
        ("cloudflare", "cloudflare-com", True),
        ("aws", "amazonaws-com", True),
        ("github", "cloudflare-com", False),
        ("cloudflare", "github-com", False),
        # A subdomain, lookalike or tenant of a shared suffix is another vendor.
        ("github", "api-github-com", False),
        ("github", "github-com-evil-example", False),
        ("github", "acme-github-io", False),
        # The facade id itself is not a registry vendor.
        ("github", "github", False),
        ("unknown", "github.com", False),
        ("github", "", False),
    ],
)
def test_api_vendor_matches(token_vendor: str, api_vendor: str, matches: bool) -> None:
    assert api_vendor_matches(token_vendor, api_vendor) is matches


def test_every_proxy_vendor_is_a_facade_vendor() -> None:
    assert set(PROXY_API_VENDORS) <= set(CliFacadeVendorsConfig.model_fields)


def test_vendor_mismatch_maps_to_403() -> None:
    assert STATUS_BY_ERROR[CliInvocationVendorMismatchError] == 403
    assert CliInvocationVendorMismatchError("x").type == "cli_invocation_vendor_mismatch"


# ── request headers ──────────────────────────────────────────────────────────


def test_request_headers_drop_invocation_tokens_and_trace_context() -> None:
    inbound = {
        "Authorization": f"Bearer {_TOKEN}",
        "X-Debug": f"token={_TOKEN}",
        "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        "tracestate": "vendor=x",
        "baggage": "k=v",
        "Content-Type": "application/json",
        "X-Mode": "explicit_content_filter_enabled_for_everyone_here",
    }
    assert facade_request_headers(inbound) == {
        "Content-Type": "application/json",
        "X-Mode": "explicit_content_filter_enabled_for_everyone_here",
    }


# ── response headers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("Set-Cookie", False),
        ("Location", False),
        ("WWW-Authenticate", False),
        ("Access-Control-Allow-Origin", False),
        ("Content-Type", True),
        ("ETag", True),
        ("Retry-After", True),
        ("cf-ray", True),
        ("X-RateLimit-Remaining", True),
        ("Jentic-Execution-Id", True),
        ("tracestate", True),
    ],
)
def test_response_header_allowlist(name: str, kept: bool) -> None:
    assert is_facade_response_header(name) is kept


def test_restrict_response_headers_removes_every_unlisted_header() -> None:
    response = JSONResponse(
        {"ok": True},
        headers={
            "Set-Cookie": "session=1",
            "Location": "https://api.cloudflare.com/elsewhere",
            "cf-ray": "abc",
            "Jentic-Execution-Id": "exec_1",
        },
    )
    response.headers.append("set-cookie", "other=2")

    _restrict_response_headers(response)

    assert "set-cookie" not in response.headers
    assert "location" not in response.headers
    assert response.headers["cf-ray"] == "abc"
    assert response.headers["jentic-execution-id"] == "exec_1"
    assert response.headers["content-type"] == "application/json"


# ── error bodies ─────────────────────────────────────────────────────────────


def test_error_message_carries_the_detail_and_the_directive() -> None:
    problem = {
        "detail": "Operation denied by a permission rule.",
        "agent_directive": {
            "strategy": "prompt_human",
            "parameters": {},
            "human_readable_instruction": "Ask the owner to allow it.",
        },
    }
    assert facade_error_message(problem) == (
        "Jentic: Operation denied by a permission rule. Ask the owner to allow it."
    )


@pytest.mark.parametrize(
    ("problem", "expected"),
    [
        ({"title": "Not Found"}, "Jentic: Not Found"),
        ({"detail": [{"loc": ["body"]}]}, "Jentic: The request failed."),
        ({}, "Jentic: The request failed."),
    ],
)
def test_error_message_falls_back_without_a_string_detail(
    problem: dict[str, Any], expected: str
) -> None:
    assert facade_error_message(problem) == expected


def test_cloudflare_error_body_is_the_v4_envelope() -> None:
    assert cloudflare_error_body(404, {"detail": "Operation not found."}) == {
        "success": False,
        "errors": [{"code": 404, "message": "Jentic: Operation not found."}],
        "messages": [],
        "result": None,
    }


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def test_cloudflare_error_response_keeps_status_and_allowlisted_headers() -> None:
    problem = JSONResponse(
        {"type": "rate_limit_exceeded", "status": 429, "detail": "Slow down."},
        status_code=429,
        media_type="application/problem+json",
        headers={"Retry-After": "7", "Jentic-Error-Origin": "broker", "Set-Cookie": "x=1"},
    )

    response = cloudflare_error_response(_request(), problem)

    assert response.status_code == 429
    assert response.headers["content-type"] == "application/json"
    assert response.headers["retry-after"] == "7"
    assert response.headers["jentic-error-origin"] == "broker"
    assert "set-cookie" not in response.headers
    assert json.loads(bytes(response.body)) == cloudflare_error_body(429, {"detail": "Slow down."})


def test_cloudflare_error_response_survives_a_non_json_body() -> None:
    response = cloudflare_error_response(_request(), JSONResponse(None, status_code=500))
    body = json.loads(bytes(response.body))
    assert response.status_code == 500
    assert body["errors"] == [{"code": 500, "message": "Jentic: The request failed."}]


def test_route_hook_is_a_no_op_without_a_matched_hook_route() -> None:
    response = JSONResponse({"detail": "x"}, status_code=400)
    assert apply_route_error_response_hook(_request(), response) is response


def test_held_error_maps_to_409() -> None:
    assert STATUS_BY_ERROR[ExecutionHeldError] == 409
    assert ExecutionHeldError("held").type == "execution_held"


# ── central handlers + the facade route class ────────────────────────────────


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, spa_aware_problem_detail_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    install_broker_error_handlers(app)

    facade = APIRouter(route_class=CloudflareFacadeRoute)
    plain = APIRouter()

    def _raise(kind: str) -> None:
        if kind == "denied":
            raise ActionDeniedError(
                "Operation denied by a permission rule.",
                type="action_denied",
                directive=AgentDirective(
                    strategy="prompt_human", human_readable_instruction="Ask the owner."
                ),
            )
        if kind == "missing":
            raise OperationNotFoundError("Operation not found.", type="operation_not_found")
        if kind == "unauthorized":
            raise Unauthorized(detail="Invalid or expired CLI invocation token")
        if kind == "limited":
            raise RateLimitExceededError("Slow down.", headers={"Retry-After": "3"})
        raise RuntimeError("boom")

    @facade.get("/_facade/cloudflare/{kind}")
    async def _facade_route(kind: str) -> dict[str, str]:
        _raise(kind)
        return {}

    @plain.get("/plain/{kind}")
    async def _plain_route(kind: str) -> dict[str, str]:
        _raise(kind)
        return {}

    app.include_router(facade)
    app.include_router(plain)
    return app


@pytest.mark.parametrize(
    ("kind", "status", "message"),
    [
        (
            "denied",
            403,
            "Jentic: Operation denied by a permission rule. Ask the owner.",
        ),
        ("missing", 404, "Jentic: Operation not found."),
        ("unauthorized", 401, "Jentic: Invalid or expired CLI invocation token"),
        ("limited", 429, "Jentic: Slow down."),
        ("crash", 500, "Jentic: An unexpected error occurred."),
    ],
)
def test_facade_route_errors_reach_the_client_as_cloudflare_errors(
    kind: str, status: int, message: str
) -> None:
    client = TestClient(_app(), raise_server_exceptions=False)

    response = client.get(f"/_facade/cloudflare/{kind}")

    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "success": False,
        "errors": [{"code": status, "message": message}],
        "messages": [],
        "result": None,
    }
    if kind == "limited":
        assert response.headers["retry-after"] == "3"


@pytest.mark.parametrize("kind", ["denied", "missing", "unauthorized", "crash"])
def test_other_routes_keep_problem_details(kind: str) -> None:
    client = TestClient(_app(), raise_server_exceptions=False)

    response = client.get(f"/plain/{kind}")

    assert response.headers["content-type"] == "application/problem+json"
    assert "success" not in response.json()


# ── server-chosen trace ──────────────────────────────────────────────────────


def test_server_chosen_trace_sets_the_current_trace_id() -> None:
    trace_id = "ab" * 16
    assert current_trace_id() is None
    with server_chosen_trace(trace_id, "test"):
        assert current_trace_id() == trace_id
    assert current_trace_id() is None


@pytest.mark.parametrize("bad", ["0" * 32, "f" * 33, "not-hex"])
def test_server_chosen_trace_rejects_an_invalid_trace_id(bad: str) -> None:
    with pytest.raises(ValueError), server_chosen_trace(bad, "test"):
        pass
