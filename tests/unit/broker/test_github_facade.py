"""Unit tests for the GitHub CLI facade: host matching, URL mapping, header rewriting, errors.

The routing tests build a small app holding the facade's route class (with a
config-only context on ``app.state``) ahead of a catch-all, and check that the
facade routes match on the facade host only, that every other host falls
through to the catch-all, and that errors on the facade host reach the client
in GitHub's error shape.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, Unauthorized
from starlette.requests import Request
from starlette.responses import Response

from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    AgentDirective,
    OperationNotFoundError,
)
from jentic_one.broker.core.facades import (
    GITHUB_META_BODY,
    github_error_body,
    github_upstream_url,
    is_facade_host,
    is_github_response_header,
    request_host,
    rewrite_github_link_header,
    rewrite_github_url,
)
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.facades import (
    GitHubFacadeRoute,
    _github_response,
    github_error_response,
    on_github_facade_host,
)
from jentic_one.shared.config import CliFacadesConfig
from jentic_one.shared.web.app_factory import spa_aware_problem_detail_handler
from jentic_one.shared.web.errors import unhandled_exception_handler

_HOST = "github.jentic.test"
_BASE = f"https://{_HOST}"


def _scope(raw_path: bytes, query: bytes = b"") -> dict[str, Any]:
    return {"type": "http", "raw_path": raw_path, "path": raw_path.decode(), "query_string": query}


# ── host matching ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("github.jentic.test", "github.jentic.test"),
        ("GitHub.Jentic.Test", "github.jentic.test"),
        ("github.jentic.test:8443", "github.jentic.test"),
        ("github.jentic.test.", "github.jentic.test"),
        (" github.jentic.test ", "github.jentic.test"),
        ("[::1]:8443", "[::1]"),
        ("github.jentic.test:", "github.jentic.test:"),
        ("", None),
        (None, None),
    ],
)
def test_request_host_is_lowercased_without_port(header: str | None, expected: str | None) -> None:
    assert request_host(header) == expected


@pytest.mark.parametrize(
    ("header", "matches"),
    [
        ("github.jentic.test", True),
        ("GITHUB.JENTIC.TEST:443", True),
        ("broker.jentic.test", False),
        ("evil-github.jentic.test", False),
        ("github.jentic.test.evil.example", False),
        ("jentic.test", False),
        (None, False),
    ],
)
def test_facade_host_is_an_exact_match(header: str | None, matches: bool) -> None:
    assert is_facade_host(header, _HOST) is matches


def test_no_configured_host_never_matches() -> None:
    assert is_facade_host("github.jentic.test", None) is False


# ── upstream URL mapping ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw_path", "query", "expected"),
    [
        (
            b"/api/v3/repos/o/r/pulls",
            b"per_page=5",
            "https://api.github.com/repos/o/r/pulls?per_page=5",
        ),
        (
            b"/api/v3/repos/o/r/contents/a%2Fb",
            b"",
            "https://api.github.com/repos/o/r/contents/a%2Fb",
        ),
        (b"/api/v3/", b"", "https://api.github.com/"),
        (b"/api/graphql", b"", "https://api.github.com/graphql"),
        (
            b"/api/uploads/repos/o/r/releases/1/assets",
            b"name=a.zip",
            "https://uploads.github.com/repos/o/r/releases/1/assets?name=a.zip",
        ),
    ],
)
def test_github_paths_map_to_github_com(raw_path: bytes, query: bytes, expected: str) -> None:
    assert github_upstream_url(_scope(raw_path, query)) == expected


@pytest.mark.parametrize(
    "raw_path",
    [
        b"/api/v3",
        b"/api/v%33/repos",
        b"/api/graphql/x",
        b"/api/graphq%6c",
        b"/api/uploads",
        b"/api/v3/../user",
        b"/api/v3/repos/%2e%2e/x",
        b"/api/uploads/./x",
        b"/repos/o/r",
        b"/o/r.git/info/refs",
        b"/_facade/cloudflare/zones",
    ],
)
def test_other_paths_have_no_upstream(raw_path: bytes) -> None:
    assert github_upstream_url(_scope(raw_path)) is None


def test_meta_body_carries_the_version_gh_reads() -> None:
    assert isinstance(GITHUB_META_BODY["installed_version"], str)
    major, minor, _patch = (int(part) for part in GITHUB_META_BODY["installed_version"].split("."))
    # Above every Enterprise Server release gh gates a fallback on (3.17, 3.20 in gh 2.89).
    assert (major, minor) >= (3, 20)


# ── response headers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://api.github.com/repositories/1/pulls?page=2&per_page=30",
            f"{_BASE}/api/v3/repositories/1/pulls?page=2&per_page=30",
        ),
        ("https://API.GitHub.com/user", f"{_BASE}/api/v3/user"),
        ("https://api.github.com:443/user", f"{_BASE}/api/v3/user"),
        ("https://api.github.com", f"{_BASE}/api/v3"),
        ("https://api.github.com/x#frag", f"{_BASE}/api/v3/x#frag"),
        (
            "https://uploads.github.com/repos/o/r/releases/1/assets?name=a",
            f"{_BASE}/api/uploads/repos/o/r/releases/1/assets?name=a",
        ),
    ],
)
def test_github_api_urls_point_at_the_facade(url: str, expected: str) -> None:
    assert rewrite_github_url(url, _BASE) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://productionresultssa1.blob.core.windows.net/actions-results/x?sig=abc",
        "https://github.com/o/r/pull/1",
        "https://objects.githubusercontent.com/x",
        "https://api.github.com.evil.example/user",
        "http://api.github.com/user",
        "https://user:pw@api.github.com/user",
        "https://api.github.com:8443/user",
        "https://api.github.com:notaport/user",
        "/relative/path",
        "not a url",
        "",
    ],
)
def test_other_urls_are_untouched(url: str) -> None:
    assert rewrite_github_url(url, _BASE) == url


def test_link_header_rewrites_every_github_target_and_keeps_params() -> None:
    value = (
        '<https://api.github.com/repositories/1/issues?page=2>; rel="next", '
        '<https://api.github.com/repositories/1/issues?page=5>; rel="last", '
        '<https://example.com/elsewhere>; rel="help"'
    )
    assert rewrite_github_link_header(value, _BASE + "/") == (
        f'<{_BASE}/api/v3/repositories/1/issues?page=2>; rel="next", '
        f'<{_BASE}/api/v3/repositories/1/issues?page=5>; rel="last", '
        '<https://example.com/elsewhere>; rel="help"'
    )


def test_link_naming_the_called_repository_by_id_keeps_the_calls_repo_form() -> None:
    value = (
        '<https://api.github.com/repositories/42/pulls?state=open&page=2>; rel="next", '
        '<https://api.github.com/repositories/42/pulls?state=open&page=9>; rel="last"'
    )
    upstream = "https://api.github.com/repos/o/r/pulls?state=open"
    assert rewrite_github_link_header(value, _BASE, upstream) == (
        f'<{_BASE}/api/v3/repos/o/r/pulls?state=open&page=2>; rel="next", '
        f'<{_BASE}/api/v3/repos/o/r/pulls?state=open&page=9>; rel="last"'
    )


@pytest.mark.parametrize(
    ("link", "upstream"),
    [
        # Another endpoint than the one called.
        (
            "https://api.github.com/repositories/42/issues?page=2",
            "https://api.github.com/repos/o/r/pulls",
        ),
        # The call did not name a repository.
        (
            "https://api.github.com/repositories/42/pulls?page=2",
            "https://api.github.com/user/repos",
        ),
        # Not a github.com API call.
        (
            "https://api.github.com/repositories/42/pulls?page=2",
            "https://uploads.github.com/repos/o/r/pulls",
        ),
        (
            "https://api.github.com/repositories/abc/pulls?page=2",
            "https://api.github.com/repos/o/r/pulls",
        ),
    ],
)
def test_other_id_form_links_keep_the_id_form(link: str, upstream: str) -> None:
    expected = link.replace("https://api.github.com", f"{_BASE}/api/v3")
    assert rewrite_github_link_header(f"<{link}>", _BASE, upstream) == f"<{expected}>"


@pytest.mark.parametrize(
    "value",
    ["", "garbage", 'https://api.github.com/x; rel="next"', "<https://api.github.com/x"],
)
def test_malformed_link_header_is_untouched(value: str) -> None:
    assert rewrite_github_link_header(value, _BASE) == value


@pytest.mark.parametrize(
    ("name", "kept"),
    [
        ("Link", True),
        ("Location", True),
        ("ETag", True),
        ("Last-Modified", True),
        ("Content-Type", True),
        ("Retry-After", True),
        ("X-GitHub-Request-Id", True),
        ("X-GitHub-Media-Type", True),
        ("X-GitHub-SSO", True),
        ("X-RateLimit-Limit", True),
        ("X-RateLimit-Remaining", True),
        ("X-RateLimit-Reset", True),
        ("X-RateLimit-Used", True),
        ("X-RateLimit-Resource", True),
        ("X-Accepted-OAuth-Scopes", True),
        ("X-Accepted-GitHub-Permissions", True),
        ("Jentic-Execution-Id", True),
        ("X-OAuth-Scopes", False),
        ("Set-Cookie", False),
        ("WWW-Authenticate", False),
        ("Strict-Transport-Security", False),
        ("Content-Security-Policy", False),
    ],
)
def test_github_response_header_allowlist(name: str, kept: bool) -> None:
    assert is_github_response_header(name) is kept


def test_github_response_rewrites_link_and_location_and_drops_unlisted_headers() -> None:
    response = Response(
        b"{}",
        status_code=301,
        headers={
            "Link": '<https://api.github.com/x?page=2>; rel="next"',
            "Location": "https://api.github.com/repositories/9",
            "X-OAuth-Scopes": "repo, admin:org",
            "Set-Cookie": "_gh_sess=1",
            "X-GitHub-Request-Id": "AB:CD",
        },
    )

    _github_response(response, _BASE)

    assert response.headers["link"] == f'<{_BASE}/api/v3/x?page=2>; rel="next"'
    assert response.headers["location"] == f"{_BASE}/api/v3/repositories/9"
    assert response.headers["x-github-request-id"] == "AB:CD"
    assert "x-oauth-scopes" not in response.headers
    assert "set-cookie" not in response.headers


def test_github_response_keeps_a_blob_storage_redirect() -> None:
    blob = "https://productionresultssa1.blob.core.windows.net/logs/1.zip?sig=abc"
    response = Response(b"", status_code=302, headers={"Location": blob})

    _github_response(response, _BASE)

    assert response.headers["location"] == blob


# ── error envelope ───────────────────────────────────────────────────────────


def test_github_error_body_is_githubs_shape() -> None:
    assert github_error_body(404, {"detail": "Operation not found."}) == {
        "message": "Jentic: Operation not found.",
        "status": "404",
    }


def test_held_error_body_links_the_review() -> None:
    problem = {
        "detail": "Held as h1.",
        "review_url": "https://jentic.example.com/app/approvals/h1",
        "agent_directive": {"human_readable_instruction": "Show the user the review link."},
    }
    assert github_error_body(409, problem) == {
        "message": "Jentic: Held as h1. Show the user the review link.",
        "documentation_url": "https://jentic.example.com/app/approvals/h1",
        "status": "409",
    }


def test_github_error_response_keeps_status_and_allowlisted_headers() -> None:
    problem = JSONResponse(
        {"type": "rate_limit_exceeded", "status": 429, "detail": "Slow down."},
        status_code=429,
        media_type="application/problem+json",
        headers={"Retry-After": "7", "Jentic-Error-Origin": "broker", "Set-Cookie": "x=1"},
    )
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    response = github_error_response(request, problem)

    assert response.status_code == 429
    assert response.headers["content-type"] == "application/json"
    assert response.headers["retry-after"] == "7"
    assert response.headers["jentic-error-origin"] == "broker"
    assert "set-cookie" not in response.headers
    assert json.loads(bytes(response.body)) == github_error_body(429, {"detail": "Slow down."})


# ── routing on the facade host ───────────────────────────────────────────────


def _facades(*, enabled: bool = True, host: str | None = _HOST) -> CliFacadesConfig:
    return CliFacadesConfig.model_validate(
        {"enabled": True, "vendors": {"github": {"enabled": enabled, "host": host}}}
    )


def _app(facades: CliFacadesConfig) -> FastAPI:
    app = FastAPI()
    app.state.ctx = SimpleNamespace(config=SimpleNamespace(cli_facades=facades))
    app.add_exception_handler(ProblemDetailException, spa_aware_problem_detail_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    install_broker_error_handlers(app)

    facade = APIRouter(route_class=GitHubFacadeRoute)
    catch_all = APIRouter()

    @facade.get("/api/v3/{kind}")
    async def _facade_route(kind: str) -> dict[str, str]:
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
        if kind == "crash":
            raise RuntimeError("boom")
        return {"served_by": "facade"}

    @catch_all.get("/{rest:path}")
    async def _catch_all(rest: str) -> dict[str, str]:
        return {"served_by": "catch-all"}

    app.include_router(facade)
    app.include_router(catch_all)
    return app


def test_facade_routes_match_on_the_facade_host() -> None:
    client = TestClient(_app(_facades()), base_url=f"http://{_HOST}:8443")
    assert client.get("/api/v3/ok").json() == {"served_by": "facade"}


@pytest.mark.parametrize(
    "host", ["broker.jentic.test", "127.0.0.1:8100", "evil.github.jentic.test"]
)
def test_other_hosts_fall_through_to_the_routes_after(host: str) -> None:
    client = TestClient(_app(_facades()), base_url=f"http://{host}")
    assert client.get("/api/v3/ok").json() == {"served_by": "catch-all"}


def test_forwarded_host_headers_are_not_trusted() -> None:
    client = TestClient(_app(_facades()), base_url="http://broker.jentic.test")
    response = client.get(
        "/api/v3/ok", headers={"X-Forwarded-Host": _HOST, "Forwarded": f"host={_HOST}"}
    )
    assert response.json() == {"served_by": "catch-all"}


@pytest.mark.parametrize(
    "facades",
    [
        _facades(enabled=False),
        _facades(enabled=False, host=None),
        CliFacadesConfig.model_validate(
            {"enabled": False, "vendors": {"github": {"enabled": True, "host": _HOST}}}
        ),
    ],
    ids=["vendor-off", "no-host", "master-off"],
)
def test_switched_off_facade_never_engages(facades: CliFacadesConfig) -> None:
    client = TestClient(_app(facades), base_url=f"http://{_HOST}")
    assert client.get("/api/v3/ok").json() == {"served_by": "catch-all"}


def test_non_http_scopes_never_match() -> None:
    assert on_github_facade_host({"type": "websocket", "headers": []}) is False
    assert on_github_facade_host({"type": "http", "headers": [(b"host", _HOST.encode())]}) is False


@pytest.mark.parametrize(
    ("kind", "status", "message"),
    [
        ("denied", 403, "Jentic: Operation denied by a permission rule. Ask the owner."),
        ("missing", 404, "Jentic: Operation not found."),
        ("unauthorized", 401, "Jentic: Invalid or expired CLI invocation token"),
        ("crash", 500, "Jentic: An unexpected error occurred."),
    ],
)
def test_facade_errors_reach_the_client_in_githubs_shape(
    kind: str, status: int, message: str
) -> None:
    client = TestClient(_app(_facades()), base_url=f"http://{_HOST}", raise_server_exceptions=False)

    response = client.get(f"/api/v3/{kind}")

    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"message": message, "status": str(status)}
