"""Redaction: entered secrets and poll tokens live only where they must.

Drives a pasted API key and an approver's own OAuth client secret (distinct
canaries) through the real routes, then sweeps every sink: captured logs
(structlog and stdlib), every column of every table in the three databases
(only the typed-secret ciphertext columns may hold them, encrypted), audit
entries, events, metrics, and every HTTP response body. A poll token never
lands anywhere but its own response, and never in an access-log line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from collections.abc import AsyncGenerator, Iterator
from typing import Any

import httpx
import pytest
import structlog
import uvicorn
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy import inspect, text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.logging import configure_logging
from jentic_one.shared.metrics import reset_metrics
from tests.integration.control.connect_sec.support import (
    AGENT,
    AGENT_ID,
    API_BODY,
    KEY_SCHEME,
    NAME,
    OAUTH_SCHEME,
    OWNER,
    RULES,
    VENDOR,
    VERSION,
    build_app,
    client,
    import_spec,
)

pytestmark = pytest.mark.integration

_PASTED_KEY = "canary_pasted_key_" + "K" * 20
_CLIENT_SECRET = "canary_client_secret_" + "Q" * 20
_CANARIES = (_PASTED_KEY, _CLIENT_SECRET)

# The only places an entered secret may live: encrypted, in its typed table.
_CIPHERTEXT_COLUMNS = {
    ("customer_api_keys", "encrypted_key"): _PASTED_KEY,
    ("oauth_client_credentials", "encrypted_client_secret"): _CLIENT_SECRET,
}


@pytest.fixture()
def metric_reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    reset_metrics()
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    yield reader
    reset_metrics()


async def _table_rows(db: DatabaseSession) -> dict[str, list[dict[str, Any]]]:
    async with db.session() as session:
        conn = await session.connection()
        tables: list[str] = await conn.run_sync(lambda c: inspect(c).get_table_names())
        out: dict[str, list[dict[str, Any]]] = {}
        for table in tables:
            result = await session.execute(text(f'SELECT * FROM "{table}"'))
            out[table] = [dict(row._mapping) for row in result]
    return out


def _texts(value: Any) -> str:
    return json.dumps(value, default=str)


async def _sweep_databases(ctx: Context, needles: tuple[str, ...]) -> list[str]:
    """Every (table, column) holding a needle in clear; ciphertext columns checked apart."""
    leaks: list[str] = []
    seen_ciphertext: set[tuple[str, str]] = set()
    for db in (ctx.control_db, ctx.admin_db, ctx.registry_db):
        for table, rows in (await _table_rows(db)).items():
            for row in rows:
                for column, value in row.items():
                    if value is None:
                        continue
                    expected = _CIPHERTEXT_COLUMNS.get((table, column))
                    if expected is not None:
                        assert expected not in str(value)
                        if ctx.encryption.decrypt(str(value)) == expected:
                            seen_ciphertext.add((table, column))
                        continue
                    rendered = _texts(value)
                    leaks.extend(f"{table}.{column}" for n in needles if n in rendered)
    assert seen_ciphertext == set(_CIPHERTEXT_COLUMNS), "ciphertext rows missing"
    return leaks


async def test_entered_secrets_and_poll_tokens_never_leak(
    env: Context,
    metric_reader: InMemoryMetricReader,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await import_spec(env, KEY_SCHEME)
    await import_spec(env, OAUTH_SCHEME, name=f"{NAME}-oauth")
    responses: list[httpx.Response] = []
    caplog.set_level(logging.DEBUG)

    with structlog.testing.capture_logs() as logs:
        async with client(env, AGENT) as agent:
            key_ask = await agent.post("/integrations:connect", json=API_BODY)
            oauth_ask = await agent.post(
                "/integrations:connect",
                json={
                    "api": {"vendor": VENDOR, "name": f"{NAME}-oauth", "version": VERSION},
                    "requested_scopes": ["read"],
                },
            )
            responses += [key_ask, oauth_ask]
        assert key_ask.status_code == oauth_ask.status_code == 201
        tokens = (key_ask.json()["poll_token"], oauth_ask.json()["poll_token"])
        key_id, oauth_id = key_ask.json()["session_id"], oauth_ask.json()["session_id"]

        async with client(env, OWNER) as owner:
            for session_id in (key_id, oauth_id):
                responses.append(await owner.get(f"/connect-sessions/{session_id}"))
            key_review, oauth_review = responses[-2].json(), responses[-1].json()
            base = {"permission_rules": RULES, "expected_agent_id": AGENT_ID}
            # A refused attempt first: validation errors must not echo either.
            responses.append(
                await owner.post(
                    f"/connect-sessions/{key_id}:confirm",
                    json={**base, "kind": "api_key", "key": _PASTED_KEY},
                )
            )
            responses.append(
                await owner.post(
                    f"/connect-sessions/{key_id}:confirm",
                    json={**base, "kind": "api_key", "key": _PASTED_KEY, "digest": "0" * 64},
                )
            )
            responses.append(
                await owner.post(
                    f"/connect-sessions/{key_id}:confirm",
                    json={
                        **base,
                        "kind": "api_key",
                        "key": _PASTED_KEY,
                        "digest": key_review["digest"],
                    },
                )
            )
            responses.append(
                await owner.post(
                    f"/connect-sessions/{oauth_id}:confirm",
                    json={
                        **base,
                        "kind": "own_oauth_client",
                        "client_id": "approver-client",
                        "client_secret": _CLIENT_SECRET,
                        "authorize_url": "https://idp.approver.example/authorize",
                        "token_url": "https://idp.approver.example/token",
                        "confirmed_scopes": ["read"],
                        "digest": oauth_review["digest"],
                    },
                )
            )
            responses.append(await owner.get("/connect-sessions"))
        assert responses[-3].status_code == 200, responses[-3].text
        assert responses[-2].status_code == 200, responses[-2].text
        async with client(env, AGENT) as agent:
            for session_id, token in ((key_id, tokens[0]), (oauth_id, tokens[1])):
                responses.append(
                    await agent.get(
                        f"/connect-sessions/{session_id}/status", params={"poll_token": token}
                    )
                )

    secrets_and_tokens = (*_CANARIES, *tokens)
    # HTTP: no body carries an entered secret; a poll token only in its own :connect reply.
    for response in responses:
        body = response.text
        assert not [c for c in _CANARIES if c in body], (response.request.url, body)
        if response not in (key_ask, oauth_ask):
            assert not [t for t in tokens if t in body], (response.request.url, body)
    # Logs, both pipelines.
    # (The test client's own ``httpx`` request lines are left out: they are the
    # caller's log, and the app masks query values in that logger anyway.)
    log_text = _texts(logs) + "\n".join(
        f"{r.getMessage()} {r.args!r} {r.__dict__!r}" for r in caplog.records if r.name != "httpx"
    )
    assert not [s for s in secrets_and_tokens if s in log_text]
    # Every DB column except the ciphertext ones — audit entries and events included.
    assert await _sweep_databases(env, secrets_and_tokens) == []
    async with env.admin_db.session() as session:
        audit_count = (await session.execute(text("SELECT COUNT(*) FROM audit_entries"))).scalar()
    assert audit_count and audit_count >= 3
    # Metrics: names, attributes, values.
    data = metric_reader.get_metrics_data()
    assert data is not None
    exported = data.to_json()
    assert "connect" in exported
    assert not [s for s in secrets_and_tokens if s in exported]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    structlog_config = structlog.get_config()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.configure(**structlog_config)


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture()
async def served(env: Context, restore_logging: None) -> AsyncGenerator[tuple[str, _Lines], None]:
    """The integrations router behind a real uvicorn, logging as the app configures it.

    The app's logging setup runs first, then uvicorn's own ``dictConfig``
    (as in ``jentic_one.__main__``); access lines are collected after both.
    """
    app_config: AppConfig = env.config.model_copy(
        update={"runtime": env.config.runtime.model_copy(update={"log_level": "INFO"})}
    )
    configure_logging(app_config)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(build_app(env, AGENT), host="127.0.0.1", port=port))
    access = logging.getLogger("uvicorn.access")
    lines = _Lines()
    access.addHandler(lines)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}", lines
    server.should_exit = True
    await task
    access.removeHandler(lines)


async def test_poll_token_never_reaches_an_access_log_line(
    env: Context, served: tuple[str, _Lines]
) -> None:
    base_url, access = served
    await import_spec(env, KEY_SCHEME)
    async with httpx.AsyncClient(base_url=base_url) as http:
        created = await http.post("/integrations:connect", json=API_BODY)
        assert created.status_code == 201, created.text
        token, session_id = created.json()["poll_token"], created.json()["session_id"]
        status = await http.get(
            f"/connect-sessions/{session_id}/status", params={"poll_token": token}
        )
        assert status.status_code == 200
        cancelled = await http.post(
            f"/connect-sessions/{session_id}:cancel", params={"poll_token": token}
        )
        assert cancelled.status_code == 204
    lines = [line for line in access.lines if session_id in line]
    assert any("/status?poll_token=" in line for line in lines), access.lines
    assert not [line for line in access.lines if token in line]
