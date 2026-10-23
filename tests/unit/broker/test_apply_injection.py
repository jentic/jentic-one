"""Unit tests for the broker's outbound-auth step, sync and async (#1957).

The sync router (``_apply_injection``) and the async worker
(``ExecutionHandler``) both apply injected auth through
``shared.http.injection.apply_injection``. The parity tests pin that a call
sent inline and the same call sent with ``Prefer: respond-async`` reach the
upstream with identical URL and headers, for header, query and cookie
credentials, with and without the caller's own cookies.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from jentic_one.broker.core.proxy_headers import replay_headers
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.routers.execute import (
    _apply_injection,
    _async_job_payload,
    _captured_cookie,
)
from jentic_one.shared.config import EncryptionConfig
from jentic_one.shared.crypto.encryption import EncryptionService
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.hold import INBOUND_COOKIE_PAYLOAD_KEY
from jentic_one.shared.jobs.protocols import (
    ActorAuthority,
    InjectedAuth,
    QueuedExecutionRequest,
    QueuedExecutionVerdict,
    UpstreamExecRequest,
    UpstreamExecResult,
)
from tests.unit.fake_tx import SessionScope

_URL = "https://api.example.com/v1/things?page=2"
_ENCRYPTION = EncryptionService(
    EncryptionConfig.model_validate(
        {
            "active_id": "v1",
            "entries": [{"id": "v1", "material": base64.b64encode(b"k" * 32).decode()}],
        }
    )
)


def _request(*, cookies: list[str] | None = None) -> Request:
    raw: list[tuple[bytes, bytes]] = [
        (b"host", b"broker.local:8080"),
        (b"content-type", b"application/json"),
        (b"accept", b"application/json"),
        (b"notion-version", b"2022-06-28"),
        (b"prefer", b"respond-async"),
        (b"jentic-credential-id", b"cred_1"),
    ]
    raw.extend((b"cookie", value.encode()) for value in cookies or [])
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": "http",
        "server": ("broker.local", 8080),
        "path": "/api.example.com/v1/things",
        "raw_path": b"/api.example.com/v1/things",
        "root_path": "",
        "query_string": b"page=2",
        "headers": raw,
    }
    return Request(scope)


def _keyed_ctx(*, keyset: bool = True) -> Any:
    """The two ``Context`` members ``_captured_cookie`` reads."""
    return SimpleNamespace(has_encryption_keyset=keyset, encryption=_ENCRYPTION if keyset else None)


# --- sync router ------------------------------------------------------------


def test_sync_header_creds_overlay_the_forwarded_headers() -> None:
    auth = InjectedAuth(headers={"X-Api-Key": "k"}, query_params={}, cookies={})
    url, headers = _apply_injection(_URL, auth, _request())
    assert url == _URL
    assert headers["X-Api-Key"] == "k"
    assert headers["content-type"] == "application/json"
    # Hop-by-hop and broker-consumed headers are filtered before injection.
    assert "host" not in headers
    assert "jentic-credential-id" not in headers


def test_sync_query_creds_merge_into_url() -> None:
    auth = InjectedAuth(headers={}, query_params={"api_key": "k"}, cookies={})
    url, _ = _apply_injection(_URL, auth, _request())
    assert url == f"{_URL}&api_key=k"


def test_sync_cookie_creds_append_to_the_forwarded_cookie_in_one_header() -> None:
    auth = InjectedAuth(headers={}, query_params={}, cookies={"session": "s"})
    _, headers = _apply_injection(_URL, auth, _request(cookies=["forwarded=1"]))
    cookie_headers = {k: v for k, v in headers.items() if k.lower() == "cookie"}
    assert cookie_headers == {"Cookie": "forwarded=1; session=s"}


def test_sync_split_cookie_fields_are_all_kept() -> None:
    auth = InjectedAuth(headers={}, query_params={}, cookies={"session": "s"})
    _, headers = _apply_injection(_URL, auth, _request(cookies=["a=1", "b=2"]))
    assert headers["Cookie"] == "a=1; b=2; session=s"


def test_sync_cookie_creds_set_when_no_inbound_cookie() -> None:
    auth = InjectedAuth(headers={}, query_params={}, cookies={"session": "s"})
    _, headers = _apply_injection(_URL, auth, _request())
    assert headers["Cookie"] == "session=s"


# --- async capture ----------------------------------------------------------


def test_async_payload_keeps_the_cookie_only_as_ciphertext() -> None:
    request = _request(cookies=["sid=agent-session"])
    payload = _async_job_payload(
        ExecuteRequestContext(upstream_url=_URL, method="POST", trace_id="ab" * 16),
        execution_id="exec_1",
        origin="api",
        headers=replay_headers(request.headers),
        inbound_cookie_enc=_captured_cookie(request, _keyed_ctx()),
    )
    assert "agent-session" not in json.dumps(payload)
    assert "cookie" not in payload["headers"]
    assert _ENCRYPTION.decrypt(payload[INBOUND_COOKIE_PAYLOAD_KEY]) == "sid=agent-session"


def test_async_payload_without_a_cookie_has_no_cookie_key() -> None:
    request = _request()
    assert _captured_cookie(request, _keyed_ctx()) is None
    payload = _async_job_payload(
        ExecuteRequestContext(upstream_url=_URL, method="POST", trace_id="ab" * 16),
        execution_id="exec_1",
        origin="api",
        inbound_cookie_enc=None,
    )
    assert INBOUND_COOKIE_PAYLOAD_KEY not in payload


def test_async_capture_without_a_keyset_keeps_nothing() -> None:
    assert _captured_cookie(_request(cookies=["sid=1"]), _keyed_ctx(keyset=False)) is None


# --- sync / async parity ----------------------------------------------------


class _RecordingExecutor:
    def __init__(self) -> None:
        self.last_request: UpstreamExecRequest | None = None

    async def execute(self, request: UpstreamExecRequest, *, db: Any) -> UpstreamExecResult:
        self.last_request = request
        return UpstreamExecResult(status_code=200, body=b"", content_type=None, duration_ms=1)


class _FixedInjector:
    def __init__(self, injection: InjectedAuth) -> None:
        self._injection = injection

    async def inject(self, **_: Any) -> InjectedAuth:
        return self._injection


class _AllowAll:
    async def authorize(self, request: QueuedExecutionRequest) -> QueuedExecutionVerdict:
        return QueuedExecutionVerdict(allowed=True)


class _ActiveActor:
    async def authority(self, *, actor_id: str, actor_type: str, permission: str) -> ActorAuthority:
        return ActorAuthority(active=True, holds_permission=True)


class _FakeSession:
    """The handler touches the session only through best-effort event writes."""


async def _async_outbound(request: Request, injection: InjectedAuth) -> tuple[str, dict[str, str]]:
    """Enqueue ``request`` as the async route does, then run it as the worker does."""
    payload = _async_job_payload(
        ExecuteRequestContext(
            upstream_url=_URL,
            method="POST",
            trace_id="ab" * 16,
            api_vendor="example.com",
            api_name="example",
            api_version="1",
        ),
        execution_id="exec_parity",
        origin="api",
        headers=replay_headers(request.headers),
        inbound_cookie_enc=_captured_cookie(request, _keyed_ctx()),
    )
    executor = _RecordingExecutor()
    handler = ExecutionHandler(
        executor=executor,
        credential_injector=_FixedInjector(injection),
        execution_authorizer=_AllowAll(),
        actor_status=_ActiveActor(),
        encryption=_ENCRYPTION,
    )
    await handler.execute(
        "job_parity",
        SessionScope(_FakeSession()),
        payload=payload,
        created_by="agnt_parity",
        actor_type="agent",
    )
    sent = executor.last_request
    assert sent is not None
    return sent.url, sent.headers


_QUERY_CRED = {"api_key": "qval"}  # pragma: allowlist secret


def _lowered(headers: dict[str, str]) -> dict[str, str]:
    lowered = {name.lower(): value for name, value in headers.items()}
    assert len(lowered) == len(headers), f"a header is sent twice: {headers}"
    return lowered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "injection",
    [
        InjectedAuth(headers={"X-Api-Key": "hval"}, query_params={}, cookies={}),
        InjectedAuth(headers={}, query_params=_QUERY_CRED, cookies={}),
        InjectedAuth(headers={}, query_params={}, cookies={"session": "cval"}),
        InjectedAuth(
            headers={"Authorization": "Bearer tok"},
            query_params=_QUERY_CRED,
            cookies={"session": "cval"},
        ),
    ],
    ids=["header", "query", "cookie", "all"],
)
@pytest.mark.parametrize(
    "inbound_cookies",
    [[], ["sid=agent-session; theme=dark"], ["a=1", "b=2"]],
    ids=["no-inbound-cookie", "inbound-cookie", "split-inbound-cookie"],
)
async def test_sync_and_async_send_identical_upstream_requests(
    injection: InjectedAuth, inbound_cookies: list[str]
) -> None:
    sync_url, sync_headers = _apply_injection(_URL, injection, _request(cookies=inbound_cookies))
    async_url, async_headers = await _async_outbound(_request(cookies=inbound_cookies), injection)

    assert async_url == sync_url
    assert _lowered(async_headers) == _lowered(sync_headers)
    if inbound_cookies:
        assert _lowered(async_headers)["cookie"].startswith("; ".join(inbound_cookies))
