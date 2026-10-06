"""The credential check (#630): one safe read call that names why a credential fails.

The probe tests run the real broker injection and HTTP runner against local
fake upstreams (``httpx.MockTransport``, or a closed loopback port for the
unreachable case): no third-party call is ever made. Each failure class has a
test, and every test that sends a secret asserts it never comes back out in the
verdict or the logs.
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import structlog
from fastapi import FastAPI
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql.functions import Function

import jentic_one.registry.core.schema  # noqa: F401  (register all registry tables)
from jentic_one.broker.adapters.http_client import build_client
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.headers import REGION_MISMATCH_HINT
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.credential_check import (
    InProcessCredentialChecker,
    ProbeTarget,
    _Verdict,
    classify,
    pick_probe_operation,
)
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import EgressConfig, UpstreamClientConfig
from jentic_one.shared.credential_check import CredentialCheckResult, CredentialCheckStatus
from jentic_one.shared.db.base import RegistryBase
from jentic_one.shared.models.credentials import (
    CredentialLocation,
    CredentialType,
    StoredCredentialType,
)
from jentic_one.shared.schemas import APIReference
from jentic_one.wiring import install_control_credential_checker

Status = CredentialCheckStatus
SECRET = "phx_live_s3cr3t_value"  # pragma: allowlist secret
_IDENTITY = Identity(sub="usr_alice", permissions=["credentials:write"])
_API = APIReference(vendor="posthog-com", name="posthog", version="1")


def _ctx(egress: EgressConfig | None = None) -> MagicMock:
    """A context whose secrets decrypt to themselves and whose audit write is a no-op."""
    ctx = MagicMock()
    ctx.encryption.decrypt.side_effect = lambda blob: blob.removeprefix("enc:")
    ctx.config.broker.egress = egress or EgressConfig(allowed_private_subnets=["127.0.0.0/8"])

    @asynccontextmanager
    async def _transaction() -> AsyncGenerator[None]:
        yield None

    ctx.admin_db.transaction = _transaction
    return ctx


def _api_key(**overrides: Any) -> ResolvedCredential:
    fields: dict[str, Any] = {
        "credential_id": "cred_1",
        "name": "posthog",
        "wire_type": CredentialType.API_KEY,
        "stored_type": StoredCredentialType.API_KEY,
        "provider": "static",
        "encrypted_secret": f"enc:{SECRET}",
        "location": CredentialLocation.HEADER,
        "field_name": "Authorization",
    }
    return ResolvedCredential(**{**fields, **overrides})


def _upstream(handler: Callable[[httpx.Request], httpx.Response]) -> HttpRunner:
    """The broker's real HTTP runner over a local fake upstream.

    The runner streams raw bytes, so the fake's answer is re-wrapped as a stream.
    """

    def streamed(request: httpx.Request) -> httpx.Response:
        answer = handler(request)
        return httpx.Response(
            answer.status_code, headers=answer.headers, stream=httpx.ByteStream(answer.content)
        )

    return HttpRunner(httpx.AsyncClient(transport=httpx.MockTransport(streamed)))


async def _probe(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    resolved: ResolvedCredential | None = None,
    target: ProbeTarget | None = None,
    ctx: MagicMock | None = None,
) -> CredentialCheckResult:
    checker = InProcessCredentialChecker(ctx or _ctx(), runner=_upstream(handler))
    with patch("jentic_one.credential_check.emit_credential_access", new=AsyncMock()):
        return await checker.probe(
            resolved or _api_key(),
            target or ProbeTarget(api=_API, url="http://127.0.0.1/api/users/@me/"),
            identity=_IDENTITY,
        )


def _assert_no_secret(result: CredentialCheckResult) -> None:
    assert SECRET not in repr(result)


# --- picking the call -------------------------------------------------------


def test_pick_prefers_an_authenticated_whoami_call() -> None:
    auth: dict[str, Any] = {"security": [{"key": []}]}
    rows = [
        ("op_root", "/", {}),
        ("op_status", "/status", {}),
        ("op_events", "/api/events", auth),
        ("op_me", "/api/users/@me", auth),
    ]
    picked = pick_probe_operation(rows)
    assert picked is not None and picked[0] == "op_me"


def test_pick_never_invents_input() -> None:
    rows = [
        ("op_param", "/users/{id}", {"security": [{"key": []}]}),
        ("op_query", "/search", {"parameters": [{"name": "q", "in": "query", "required": True}]}),
        ("op_body", "/report", {"requestBody": {"required": True, "content": {}}}),
        ("op_ok", "/projects", {"parameters": [{"name": "page", "in": "query"}]}),
    ]
    picked = pick_probe_operation(rows)
    assert picked is not None and picked[0] == "op_ok"
    assert pick_probe_operation(rows[:3]) is None


def test_pick_avoids_the_api_root() -> None:
    picked = pick_probe_operation([("op_root", "/", {}), ("op_pets", "/pets", {})])
    assert picked is not None and picked[0] == "op_pets"


# --- naming the failure -----------------------------------------------------

_URL = "https://api.example.test/v1/me"


@pytest.mark.parametrize(
    ("status_code", "headers", "body", "want"),
    [
        (200, {}, b"{}", Status.OK),
        (401, {}, b'{"detail":"Personal API key is invalid."}', Status.BAD_KEY),
        (401, {}, b'{"message":"Invalid or expired token"}', Status.BAD_KEY),
        (
            401,
            {"WWW-Authenticate": 'Bearer error="invalid_token", error_description="token expired"'},
            b"",
            Status.EXPIRED,
        ),
        (403, {}, b"<Code>ExpiredToken</Code>", Status.EXPIRED),
        (403, {}, b"<Code>InvalidClientTokenId</Code>", Status.BAD_KEY),
        (
            400,
            {},
            b'{"error":{"message":"API key not valid. Please pass a valid API key."}}',
            Status.BAD_KEY,
        ),
        (
            403,
            {"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="repo"'},
            b"",
            Status.MISSING_SCOPE,
        ),
        (
            403,
            {"X-Accepted-OAuth-Scopes": "repo"},
            b'{"message":"Not allowed"}',
            Status.MISSING_SCOPE,
        ),
        (404, {}, b"Not Found", Status.WRONG_BASE_URL),
        (301, {"Location": "https://eu.example.test/v1/me"}, b"", Status.WRONG_BASE_URL),
        (503, {}, b"", Status.UNREACHABLE),
        (429, {}, b"", Status.UNTESTED),
    ],
)
def test_classify_names_each_failure(
    status_code: int, headers: dict[str, str], body: bytes, want: Status
) -> None:
    status, reason = classify(status_code, headers, body, url=_URL)
    assert status is want
    assert "api.example.test" in reason


def test_missing_scope_names_the_scope() -> None:
    _, from_header = classify(403, {"X-Accepted-OAuth-Scopes": "repo"}, b"", url=_URL)
    _, from_spec = classify(403, {}, b"", url=_URL, scopes=["read:user"])
    assert "(needs: repo)" in from_header
    assert "(needs: read:user)" in from_spec


def test_redirect_names_the_host_but_not_its_query() -> None:
    _, reason = classify(
        302, {"Location": f"https://eu.example.test/x?key={SECRET}"}, b"", url=_URL
    )
    assert "eu.example.test" in reason and SECRET not in reason


# --- the probe, against local fake upstreams -------------------------------


async def test_ok_sends_the_injected_credential() -> None:
    seen: dict[str, str] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["method"] = request.method
        return httpx.Response(200, json={"email": "a@example.test"})

    result = await _probe(upstream)
    assert result.status is Status.OK
    assert result.upstream_status == 200
    assert result.probe == "GET http://127.0.0.1/api/users/@me/"
    assert seen == {"auth": SECRET, "method": "GET"}


async def test_bad_key_never_echoes_the_secret() -> None:
    # A query-parameter key, and an upstream that echoes it back: the verdict
    # and the logs must still never carry it.
    resolved = _api_key(location=CredentialLocation.QUERY, field_name="api_key")

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.params["api_key"] == SECRET
        return httpx.Response(401, json={"detail": f"key {SECRET} is invalid"})

    with structlog.testing.capture_logs() as logs:
        result = await _probe(upstream, resolved=resolved)
    assert result.status is Status.BAD_KEY
    assert result.probe is not None and "api_key=" in result.probe
    _assert_no_secret(result)
    assert SECRET not in repr(logs)


async def test_expired_token() -> None:
    result = await _probe(
        lambda _: httpx.Response(
            401,
            headers={
                "WWW-Authenticate": 'Bearer error="invalid_token", error_description="expired"'
            },
        )
    )
    assert result.status is Status.EXPIRED


async def test_missing_scope() -> None:
    target = ProbeTarget(api=_API, url="http://127.0.0.1/user/repos", scopes=("repo",))
    result = await _probe(
        lambda _: httpx.Response(403, json={"message": "Forbidden"}), target=target
    )
    assert result.status is Status.MISSING_SCOPE
    assert "(needs: repo)" in result.reason


async def test_wrong_base_url() -> None:
    result = await _probe(lambda _: httpx.Response(404, text="Not Found"))
    assert result.status is Status.WRONG_BASE_URL
    assert "/api/users/@me/" in result.reason


async def test_wrong_region_is_named_when_the_host_is_a_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #630's PostHog case: an EU key, no region set, so the spec default (us) is
    # used and the upstream says "invalid key". The verdict points at the region.
    monkeypatch.setattr("jentic_one.credential_check.validate_upstream_url", lambda url, _: url)
    target = ProbeTarget(
        api=_API,
        url="https://{region}.posthog.example/api/users/@me/",
        defaults={"region": "us"},
    )

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.host == "eu.posthog.example":
            return httpx.Response(200, json={})
        return httpx.Response(401, json={"detail": "Personal API key is invalid."})

    defaulted = await _probe(upstream, target=target)
    assert defaulted.status is Status.BAD_KEY
    assert REGION_MISMATCH_HINT in defaulted.reason
    assert "This check used region=us (the spec default)." in defaulted.reason

    pinned = await _probe(
        upstream, target=target, resolved=_api_key(server_variables={"region": "eu"})
    )
    assert pinned.status is Status.OK
    assert pinned.probe == "GET https://eu.posthog.example/api/users/@me/"


async def test_unreachable_when_nothing_listens() -> None:
    with socket.socket() as sock:  # grab a free loopback port, then close it
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    egress = EgressConfig(allowed_private_subnets=["127.0.0.0/8"])
    client = build_client(UpstreamClientConfig(), egress)
    checker = InProcessCredentialChecker(_ctx(egress), runner=HttpRunner(client))
    try:
        with patch("jentic_one.credential_check.emit_credential_access", new=AsyncMock()):
            result = await checker.probe(
                _api_key(),
                ProbeTarget(api=_API, url=f"http://127.0.0.1:{port}/v1/me"),
                identity=_IDENTITY,
            )
    finally:
        await client.aclose()
    assert result.status is Status.UNREACHABLE
    _assert_no_secret(result)


async def test_unreachable_times_out_fast() -> None:
    async def hang(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200)

    runner = HttpRunner(httpx.AsyncClient(transport=httpx.MockTransport(hang)))
    checker = InProcessCredentialChecker(_ctx(), runner=runner, timeout_s=0.2)
    started = time.monotonic()
    with patch("jentic_one.credential_check.emit_credential_access", new=AsyncMock()):
        result = await checker.probe(
            _api_key(), ProbeTarget(api=_API, url="http://127.0.0.1/v1/me"), identity=_IDENTITY
        )
    assert result.status is Status.UNREACHABLE
    assert "within 0.2s" in result.reason
    assert time.monotonic() - started < 2


async def test_egress_refusal_sends_and_decrypts_nothing() -> None:
    ctx = _ctx(EgressConfig())  # strict default: private ranges refused

    def upstream(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may leave")

    result = await _probe(
        upstream, ctx=ctx, target=ProbeTarget(api=_API, url="http://10.1.2.3/v1/me")
    )
    assert result.status is Status.UNTESTED
    ctx.encryption.decrypt.assert_not_called()


def _oauth(*, expires_in: timedelta, refresh: bool) -> ResolvedCredential:
    return ResolvedCredential(
        credential_id="cred_1",
        name="slack",
        wire_type=CredentialType.OAUTH2,
        stored_type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
        provider="direct_oauth2",
        encrypted_access_token=f"enc:{SECRET}",
        encrypted_refresh_token="enc:refresh" if refresh else None,
        token_expires_at=datetime.now(UTC) + expires_in,
    )


async def test_oauth_token_expired_without_refresh_is_expired() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        raise AssertionError("an expired token is not sent")

    result = await _probe(upstream, resolved=_oauth(expires_in=timedelta(hours=-1), refresh=False))
    assert result.status is Status.EXPIRED
    assert "reconnect" in result.reason


async def test_oauth_refreshable_token_is_left_to_the_broker() -> None:
    result = await _probe(
        lambda _: httpx.Response(200),
        resolved=_oauth(expires_in=timedelta(hours=-1), refresh=True),
    )
    assert result.status is Status.UNTESTED
    assert "never refreshes" in result.reason


async def test_oauth_live_token_is_sent() -> None:
    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        return httpx.Response(200)

    result = await _probe(upstream, resolved=_oauth(expires_in=timedelta(hours=1), refresh=False))
    assert result.status is Status.OK


# --- the whole check: registry pick, resolve, probe, log --------------------


def _create_registry_tables(sync_conn: Connection) -> None:
    """Create every registry table on SQLite, dropping Postgres-only function defaults."""
    tables = RegistryBase.metadata.sorted_tables
    saved = {
        col: col.server_default
        for table in tables
        for col in table.columns
        if col.server_default is not None
        and isinstance(getattr(col.server_default, "arg", None), Function)
    }
    for col in saved:
        col.server_default = None
    try:
        for table in tables:
            sync_conn.execute(CreateTable(table, if_not_exists=True))
    finally:
        for col, default in saved.items():
            col.server_default = default


@pytest.fixture()
async def registry_session() -> AsyncGenerator[AsyncSession]:
    """A real in-memory SQLite registry holding one PostHog-like API."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(_create_registry_tables)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        api = Api(vendor="posthog-com", name="posthog", version="1")
        session.add(api)
        await session.flush()
        revision = ApiRevision(api_id=api.id, state="live")
        session.add(revision)
        await session.flush()
        api.current_revision_id = revision.id
        auth: dict[str, Any] = {"security": [{"PersonalAPIKey": []}]}
        session.add_all(
            [
                Operation(
                    id="op_me",
                    revision_id=revision.id,
                    path="/api/users/@me/",
                    method="GET",
                    raw_operation=auth,
                ),
                Operation(
                    id="op_new",
                    revision_id=revision.id,
                    path="/api/projects/",
                    method="POST",
                    raw_operation=auth,
                ),
                Operation(
                    id="op_old",
                    revision_id=revision.id,
                    path="/me",
                    method="GET",
                    deprecated=True,
                    raw_operation=auth,
                ),
            ]
        )
        server = Server(revision_id=revision.id, url="https://{region}.posthog.example")
        server.variables = [ServerVariable(name="region", default_value="us", enum=["us", "eu"])]
        session.add(server)
        await session.commit()
        yield session
    await engine.dispose()


async def test_find_target_never_chooses_between_specs(registry_session: AsyncSession) -> None:
    """A second spec under the vendor (apis:write) must not get to aim the check at its host."""
    other = Api(vendor="posthog-com", name="aaa-lookalike", version="1")
    registry_session.add(other)
    await registry_session.flush()
    revision = ApiRevision(api_id=other.id, state="live")
    registry_session.add(revision)
    await registry_session.flush()
    other.current_revision_id = revision.id
    registry_session.add_all(
        [
            Operation(
                id="op_lookalike_me",
                revision_id=revision.id,
                path="/me",
                method="GET",
                raw_operation={"security": [{"Key": []}]},
            ),
            Server(revision_id=revision.id, url="https://collector.attacker.example"),
        ]
    )
    await registry_session.commit()
    ctx = _ctx()

    @asynccontextmanager
    async def _session() -> AsyncGenerator[AsyncSession]:
        yield registry_session

    ctx.registry_db.session = _session
    checker = InProcessCredentialChecker(ctx)
    with pytest.raises(_Verdict) as verdict:  # vendor-wide credential: two specs, no pick
        await checker._find_target("posthog-com", None, None)
    assert verdict.value.result.status is CredentialCheckStatus.UNTESTED
    assert "nothing was sent" in verdict.value.result.reason
    target = await checker._find_target("posthog-com", "posthog", None)  # scoped: still works
    assert target.url == "https://{region}.posthog.example/api/users/@me/"


async def test_find_target_reads_the_live_spec(registry_session: AsyncSession) -> None:
    ctx = _ctx()

    @asynccontextmanager
    async def _session() -> AsyncGenerator[AsyncSession]:
        yield registry_session

    ctx.registry_db.session = _session
    target = await InProcessCredentialChecker(ctx)._find_target("posthog-com", "posthog", None)
    assert target.api == _API
    assert target.url == "https://{region}.posthog.example/api/users/@me/"
    assert target.defaults == {"region": "us"}
    assert target.authenticated


async def test_check_logs_the_verdict_without_the_secret() -> None:
    credential = MagicMock(
        active=True, api_vendor="posthog-com", api_name="posthog", api_version=None
    )
    ctx = _ctx()

    @asynccontextmanager
    async def _session() -> AsyncGenerator[None]:
        yield None

    ctx.control_db.session = _session
    checker = InProcessCredentialChecker(ctx, runner=_upstream(lambda _: httpx.Response(401)))
    target = ProbeTarget(api=_API, url="http://127.0.0.1/api/users/@me/")
    with (
        patch(
            "jentic_one.credential_check.CredentialRepository.get_by_id",
            new=AsyncMock(return_value=credential),
        ),
        patch.object(
            InProcessCredentialChecker, "_find_target", new=AsyncMock(return_value=target)
        ),
        patch(
            "jentic_one.credential_check.CredentialResolver.resolve",
            new=AsyncMock(return_value=_api_key()),
        ),
        patch("jentic_one.credential_check.emit_credential_access", new=AsyncMock()) as emitted,
        structlog.testing.capture_logs() as logs,
    ):
        result = await checker.check(credential_id="cred_1", identity=_IDENTITY)

    assert result.status is Status.BAD_KEY
    assert emitted.await_args is not None and emitted.await_args.kwargs["credential_id"] == "cred_1"
    completed = [e for e in logs if e["event"] == "credential_check.completed"]
    assert completed and completed[0]["status"] == "bad_key"
    assert SECRET not in repr(logs)


async def test_check_of_a_disabled_credential_sends_nothing() -> None:
    ctx = _ctx()

    @asynccontextmanager
    async def _session() -> AsyncGenerator[None]:
        yield None

    ctx.control_db.session = _session
    disabled = MagicMock(active=False)
    with patch(
        "jentic_one.credential_check.CredentialRepository.get_by_id",
        new=AsyncMock(return_value=disabled),
    ):
        result = await InProcessCredentialChecker(ctx).check(
            credential_id="cred_1", identity=_IDENTITY
        )
    assert result.status is Status.UNTESTED
    assert "disabled" in result.reason


async def test_a_crashed_check_is_untested_not_an_error() -> None:
    # The save-time check runs after the credential is stored: an internal
    # failure must come back as a verdict, never as a 500 on the save.
    ctx = _ctx()

    @asynccontextmanager
    async def _session() -> AsyncGenerator[None]:
        raise RuntimeError("control DB is down")
        yield None

    ctx.control_db.session = _session
    result = await InProcessCredentialChecker(ctx).check(credential_id="cred_1", identity=_IDENTITY)
    assert result.status is Status.UNTESTED
    assert "could not run" in result.reason


def test_install_puts_the_checker_on_control_app_state() -> None:
    app = FastAPI()
    install_control_credential_checker(app, MagicMock())
    assert isinstance(app.state.credential_checker, InProcessCredentialChecker)
