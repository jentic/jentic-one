"""Response-header and redirect-body reflection regressions."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Response
from starlette.requests import Request

import jentic_one.shared.http.injection as injection_mod
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.idempotency import fingerprint
from jentic_one.broker.core.proxy_headers import passthrough_response_header_items
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.execution.authorization import ExecutionAuthorization
from jentic_one.broker.services.idempotency import IdempotencyState, SharedStateIdempotencyStore
from jentic_one.broker.web.routers import execute as execute_mod
from jentic_one.broker.web.routers.execute import _assemble_response, _handle_hold
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.execution import ExecutionContext, ExecutionOutcome, RunnerResult
from jentic_one.shared.broker.protocols import ResolveResult
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.http.injection import (
    apply_injection,
    clear_upstream_cookie_scopes,
    filter_inbound_cookie,
    record_upstream_set_cookie,
)
from jentic_one.shared.jobs.hold import HoldOutcome
from jentic_one.shared.jobs.protocols import InjectedAuth
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from jentic_one.shared.schemas import APIReference, OperationInfo
from jentic_one.shared.state.backend import MemoryStateBackend

_BASE_URL = "https://api.example.com/v1/items"
_QUERY_SECRET = "SECRET-123"  # pragma: allowlist secret
_QUERY_PLUS_SECRET = "SECRET+1"  # pragma: allowlist secret
_COOKIE_SECRET = "cookie-secret"  # pragma: allowlist secret


def _auth(
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> InjectedAuth:
    return InjectedAuth(headers=headers or {}, query_params=query or {}, cookies=cookies or {})


@pytest.fixture(autouse=True)
def _reset_injection_context() -> Iterator[None]:
    clear_upstream_cookie_scopes()
    apply_injection({}, _BASE_URL, _auth())
    yield
    apply_injection({}, _BASE_URL, _auth())
    clear_upstream_cookie_scopes()


def _ctx_req(**overrides: object) -> ExecuteRequestContext:
    values: dict[str, object] = {
        "upstream_url": _BASE_URL,
        "method": "GET",
        "trace_id": "ab" * 16,
        "operation": OperationInfo(id="op-items"),
        "api_vendor": "example",
        "api_name": "items",
        "api_version": "v1",
    }
    values.update(overrides)
    return ExecuteRequestContext(**values)  # type: ignore[arg-type]


def _outcome(
    *,
    status_code: int,
    body: bytes,
    headers: dict[str, str],
    content_type: str | None = "text/html",
) -> ExecutionOutcome:
    return ExecutionOutcome(
        result=RunnerResult(
            status_code=status_code,
            body=body,
            headers=headers,
            content_type=content_type,
            duration_ms=1.0,
        ),
        context=ExecutionContext(
            execution_id="exec-reflection",
            toolkit_id=None,
            operation=OperationInfo(id="op-items"),
            api=None,
            trace_id="ab" * 16,
        ),
    )


def _header(items: list[tuple[str, str]], name: str) -> str:
    values = [value for key, value in items if key.lower() == name.lower()]
    assert len(values) == 1
    return values[0]


def test_redirect_location_drops_injected_query_param() -> None:
    apply_injection({}, f"{_BASE_URL}?page=2", _auth(query={"api_key": _QUERY_SECRET}))

    items = passthrough_response_header_items(
        {"Location": (f"/v1/items/?page=2&api_key={_QUERY_SECRET}&%61pi_key=caller-value")}
    )

    assert _header(items, "Location") == "/v1/items/?page=2"


def test_link_content_location_and_refresh_targets_scrubbed() -> None:
    apply_injection({}, _BASE_URL, _auth(query={"api_key": _QUERY_SECRET}))

    items = passthrough_response_header_items(
        [
            (
                "Link",
                f'</v1/items/?page=2&api_key={_QUERY_SECRET}>; rel="next", '
                f'</v1/other?api%5Fkey={_QUERY_SECRET}&x=1>; rel="last"',
            ),
            ("Content-Location", f"/v1/items?page=2&%61pi_key={_QUERY_SECRET}"),
            ("Refresh", f"0; url=/v1/items?page=2&api_key={_QUERY_SECRET}"),
        ]
    )

    assert _header(items, "Link") == (
        '</v1/items/?page=2>; rel="next", </v1/other?x=1>; rel="last"'
    )
    assert _header(items, "Content-Location") == "/v1/items?page=2"
    assert _header(items, "Refresh") == "0; url=/v1/items?page=2"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('0; url="/next?api_key=SECRET%2B1"', '0; url="/next"'),
        ('0; url="/next?api_key=SECRET%2B1&page=2"', '0; url="/next?page=2"'),
    ],
)
def test_quoted_refresh_url_preserves_closing_quote(value: str, expected: str) -> None:
    apply_injection({}, _BASE_URL, _auth(query={"api_key": _QUERY_PLUS_SECRET}))

    items = passthrough_response_header_items({"Refresh": value})

    assert _header(items, "Refresh") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("key%3DSECRET%2B1", "key%3D[REDACTED]"),
        ("key%3DSECRET%2b1", "key%3D[REDACTED]"),
        ("key=SECRET+1", "key=[REDACTED]"),
        ("key=secret+1", "key=secret+1"),
    ],
)
def test_percent_and_plus_encoded_secret_variants_redacted(value: str, expected: str) -> None:
    apply_injection({}, _BASE_URL, _auth(query={"api_key": _QUERY_PLUS_SECRET}))

    items = passthrough_response_header_items({"X-Debug": value})

    assert _header(items, "X-Debug") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Bearer tok+ABC/DEF", "[REDACTED]"),
        ("Bearer tok%2BABC%2FDEF", "Bearer [REDACTED]"),
        ("Bearer tok%2bABC%2fDEF", "Bearer [REDACTED]"),
        ("Bearer tok%2bABC%2FDEF", "Bearer [REDACTED]"),
        ("Bearer TOK+ABC/DEF", "Bearer TOK+ABC/DEF"),
    ],
)
def test_reflected_bearer_token_redacted_in_custom_header(value: str, expected: str) -> None:
    apply_injection({}, _BASE_URL, _auth(headers={"Authorization": "Bearer tok+ABC/DEF"}))

    items = passthrough_response_header_items({"X-Echo-Auth": value})

    assert _header(items, "X-Echo-Auth") == expected


def test_short_values_not_scrubbed() -> None:
    apply_injection({}, _BASE_URL, _auth(query={"api_key": "tiny"}))  # pragma: allowlist secret

    items = passthrough_response_header_items({"ETag": '"tiny"'})

    assert _header(items, "ETag") == '"tiny"'


def test_upstream_jentic_headers_stripped() -> None:
    apply_injection({}, _BASE_URL, _auth())
    response = _assemble_response(
        _outcome(
            status_code=200,
            body=b"{}",
            content_type="application/json",
            headers={
                "content-type": "application/json",
                "Jentic-Error-Origin": "broker",
                "Jentic-Hint": "upstream instruction",
                "Jentic-Credential-Id": "cred-upstream",
                "Jentic-Upstream-Status": "599",
                "X-Jentic-Internal": "upstream",
                "Idempotent-Replayed": "true",
                "tracestate": "upstream=value",
            },
        ),
        _ctx_req(credential_id=None, credential_name=None),
    )

    assert response.headers[JenticHeader.UPSTREAM_STATUS.value] == "200"
    assert response.headers[JenticHeader.EXECUTION_ID.value] == "exec-reflection"
    assert response.headers["tracestate"].startswith("jentic=")
    assert JenticHeader.ERROR_ORIGIN.value not in response.headers
    assert JenticHeader.HINT.value not in response.headers
    assert JenticHeader.CREDENTIAL_ID.value not in response.headers
    assert JenticHeader.IDEMPOTENT_REPLAYED.value not in response.headers
    assert "X-Jentic-Internal" not in response.headers


def test_guard_reset_between_calls() -> None:
    apply_injection(
        {},
        _BASE_URL,
        _auth(query={"api_key": _QUERY_SECRET}, cookies={"session": _COOKIE_SECRET}),
    )
    guard = injection_mod.get_current_reflection_guard()
    assert guard is not None
    assert guard.secrets
    assert guard.credential_cookies == (("session", _COOKIE_SECRET),)

    apply_injection({}, _BASE_URL, _auth())
    reset = injection_mod.get_current_reflection_guard()

    assert reset is not None
    assert reset.secrets == ()
    assert reset.credential_cookies == ()
    assert reset.query_param_names == frozenset()


def test_guard_repr_elides_secrets_and_is_immutable_and_slotted() -> None:
    guard = injection_mod.ReflectionGuard(
        query_param_names=frozenset({"api_key"}),
        secrets=(_QUERY_SECRET,),
        credential_cookies=(("session", _COOKIE_SECRET),),
    )

    rendered = repr(guard)

    assert _QUERY_SECRET not in rendered
    assert _COOKIE_SECRET not in rendered
    assert not hasattr(guard, "__dict__")
    with pytest.raises(FrozenInstanceError):
        guard.secrets = ()  # type: ignore[misc]


def test_tracked_restrictive_path_does_not_drop_current_credential() -> None:
    record_upstream_set_cookie(
        "https://api.example.com/auth/login", f"session={_COOKIE_SECRET}; Path=/auth"
    )
    record_upstream_set_cookie(
        "https://api.example.com/auth/login", "caller=tracked-value; Path=/auth"
    )
    target_url, outbound = apply_injection(
        {}, "https://api.example.com/items", _auth(cookies={"session": _COOKIE_SECRET})
    )

    filtered = filter_inbound_cookie(
        f"{outbound['Cookie']}; caller=tracked-value",
        target_url,
        allow_untracked=True,
    )

    assert filtered == f"session={_COOKIE_SECRET}"


@pytest.mark.parametrize(
    "case",
    ["empty_guard", "other_target", "different_value", "first_stage"],
)
def test_tracked_scope_bypass_requires_exact_current_credential(case: str) -> None:
    record_upstream_set_cookie(
        "https://api.example.com/auth/login", f"session={_COOKIE_SECRET}; Path=/auth"
    )
    target_url = "https://api.example.com/items"
    if case == "empty_guard":
        apply_injection({}, target_url, _auth())
    elif case == "other_target":
        apply_injection(
            {},
            "https://api.example.com/other",
            _auth(cookies={"session": _COOKIE_SECRET}),
        )
    elif case == "different_value":
        apply_injection({}, target_url, _auth(cookies={"session": "different-cookie-value"}))
    else:
        apply_injection({}, target_url, _auth(cookies={"session": _COOKIE_SECRET}))

    filtered = filter_inbound_cookie(
        f"session={_COOKIE_SECRET}",
        target_url,
        allow_untracked=case != "first_stage",
    )

    assert filtered is None


def test_buffered_3xx_body_dropped_when_query_credential() -> None:
    target_url, _ = apply_injection({}, _BASE_URL, _auth(query={"api_key": _QUERY_SECRET}))
    response = _assemble_response(
        _outcome(
            status_code=307,
            body=f'<a href="{target_url}">{_QUERY_SECRET}</a>'.encode(),
            headers={
                "location": target_url,
                "content-type": "text/html",
                "content-encoding": "gzip",
            },
        ),
        _ctx_req(upstream_url=target_url),
    )

    assert response.status_code == 307
    assert response.body == b""
    assert response.headers["location"] == _BASE_URL
    assert "content-type" not in response.headers
    assert "content-encoding" not in response.headers
    assert response.headers[JenticHeader.UPSTREAM_STATUS.value] == "307"


def test_2xx_body_untouched() -> None:
    body = f'{{"credential_echo":"{_QUERY_SECRET}"}}'.encode()
    apply_injection({}, _BASE_URL, _auth(query={"api_key": _QUERY_SECRET}))

    response = _assemble_response(
        _outcome(
            status_code=200,
            body=body,
            content_type="application/json",
            headers={"content-type": "application/json", "etag": '"ok"'},
        ),
        _ctx_req(),
    )

    assert response.body == body
    assert response.headers["content-type"] == "application/json"
    assert response.headers["etag"] == '"ok"'


@pytest.mark.asyncio
async def test_idempotent_replay_record_has_no_secret(
    monkeypatch: pytest.MonkeyPatch, sample_config_dict: dict[str, Any]
) -> None:
    """Idempotent completion persists and replays the sanitized redirect response."""

    class _Resolver:
        async def resolve_operation(
            self, *, method: str, url: str, revision_id: object = None
        ) -> ResolveResult:
            return ResolveResult(
                operation=OperationInfo(
                    id="op-items", path="/v1/items", method=method, relative_path="/v1/items"
                ),
                api=APIReference(vendor="example", name="items", version="v1"),
                path_params={},
            )

    async def _validate(raw_url: str, egress: Any = None) -> str:
        return raw_url

    selected = ResolvedCredential(
        credential_id="cred-1",
        name="query-credential",
        wire_type=CredentialType.API_KEY,
        stored_type=StoredCredentialType.API_KEY,
        provider="example",
    )

    async def _authorize(*_args: Any, **_kwargs: Any) -> ExecutionAuthorization:
        return ExecutionAuthorization(
            allowed_credential_ids=[selected.credential_id], selected_credential=selected
        )

    async def _resolve(*_args: Any, **_kwargs: Any) -> InjectedAuth:
        return InjectedAuth(
            headers={},
            query_params={"api_key": _QUERY_PLUS_SECRET},
            cookies={},
            credential_id=selected.credential_id,
            credential_name=selected.name,
        )

    executions = 0

    async def _run_execution(
        request_ctx: ExecuteRequestContext,
        *,
        on_dispatched: Callable[[], None] | None = None,
        on_outcome: Callable[[ExecutionOutcome], Awaitable[None]] | None = None,
        **_kwargs: Any,
    ) -> ExecutionOutcome:
        nonlocal executions
        executions += 1
        if on_dispatched is not None:
            on_dispatched()
        outcome = _outcome(
            status_code=307,
            body=f"redirect:{_QUERY_PLUS_SECRET}".encode(),
            headers={
                "location": request_ctx.upstream_url,
                "content-type": "text/plain",
                "x-debug": "key%3DSECRET%2b1",
            },
            content_type="text/plain",
        )
        if on_outcome is not None:
            await on_outcome(outcome)
        return outcome

    monkeypatch.setattr(execute_mod, "validate_upstream_url_async", _validate)
    monkeypatch.setattr(execute_mod, "authorize_execution", _authorize)
    monkeypatch.setattr(execute_mod, "_resolve_credentials", _resolve)
    monkeypatch.setattr(execute_mod, "run_execution", _run_execution)

    app = FastAPI()
    app.state.broker_registry_resolver = _Resolver()
    raw_path = f"/{_BASE_URL}".encode("latin-1")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("broker.example", 443),
            "path": raw_path.decode("latin-1"),
            "raw_path": raw_path,
            "query_string": b"",
            "headers": [(b"idempotency-key", b"key-1")],
            "client": ("127.0.0.1", 1234),
            "app": app,
        }
    )
    identity = Identity(
        sub="agent-1",
        actor_type=ActorType.AGENT,
        permissions=["execute"],
        expires_at=None,
        active=True,
    )
    ctx = Context(AppConfig.model_validate(sample_config_dict))
    store = SharedStateIdempotencyStore(
        MemoryStateBackend(), pending_ttl_s=30, done_ttl_s=60, max_response_bytes=4096
    )

    async def _handle_request() -> Response:
        return await execute_mod._handle(
            request,
            "GET",
            ctx,
            identity,
            SimpleNamespace(),
            SimpleNamespace(),
            SimpleNamespace(),
            store,
        )

    response = await _handle_request()

    assert response.status_code == 307
    assert response.body == b""
    assert response.headers["location"] == _BASE_URL

    request_fingerprint = fingerprint(
        "GET", _BASE_URL, selected.credential_id, None, headers=request.headers
    )
    replay = await store.begin(identity.sub, "key-1", request_fingerprint)
    assert replay.state is IdempotencyState.REPLAY
    assert replay.stored is not None
    stored_location = [
        value for name, value in replay.stored.header_items if name.lower() == "location"
    ]
    assert stored_location == [_BASE_URL]
    assert all(_QUERY_PLUS_SECRET not in value for _, value in replay.stored.header_items)
    stored_debug = [
        value for name, value in replay.stored.header_items if name.lower() == "x-debug"
    ]
    assert stored_debug == ["key%3D[REDACTED]"]
    assert replay.stored.body == b""

    replayed = await _handle_request()

    assert executions == 1
    assert response.headers["x-debug"] == "key%3D[REDACTED]"
    assert replayed.status_code == 307
    assert replayed.headers["location"] == _BASE_URL
    assert replayed.headers["x-debug"] == "key%3D[REDACTED]"
    assert replayed.headers[JenticHeader.IDEMPOTENT_REPLAYED.value] == "true"
    assert replayed.body == b""


@pytest.mark.asyncio
async def test_broker_held_response_omits_upstream_status_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def transaction() -> AsyncIterator[object]:
        yield object()

    async def fake_file_hold(*_args: Any, **_kwargs: Any) -> HoldOutcome:
        return HoldOutcome(
            job_id="job-held",
            approval_id="held-1",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
            execution_id="exec-held",
            joined=True,
        )

    monkeypatch.setattr(execute_mod, "file_hold", fake_file_hold)
    ctx = SimpleNamespace(
        has_encryption_keyset=True,
        encryption=SimpleNamespace(encrypt=lambda _value: "ciphertext"),
        admin_db=SimpleNamespace(transaction=transaction),
        config=SimpleNamespace(
            held_executions=SimpleNamespace(ttl_seconds=60, max_pending_per_agent=10),
            broker=SimpleNamespace(jobs_api_base_url="https://broker.example"),
            server=SimpleNamespace(public_base_url=None),
        ),
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("broker.example", 443),
            "path": "/https://api.example.com/v1/items",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )
    identity = Identity(
        sub="agent-held",
        actor_type=ActorType.AGENT,
        permissions=["execute"],
        expires_at=None,
        active=True,
    )
    authorization = SimpleNamespace(
        selected_credential=SimpleNamespace(credential_id="cred-held"),
        allowed_credential_ids=["cred-held"],
        matched_rule_id="rule-held",
    )

    response = await _handle_hold(
        request,
        _ctx_req(),
        ctx,  # type: ignore[arg-type]
        identity,
        authorization,  # type: ignore[arg-type]
    )

    assert response.status_code == 202
    assert JenticHeader.UPSTREAM_STATUS.value not in response.headers
