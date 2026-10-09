"""Fixtures for the live-stack connect-flow security regressions.

These tests drive a full app + broker round trip against the isolated stack
that ``scripts/e2e_1556_security_stack.sh up`` starts (Postgres, the combined
app, a broker, ``tests.harness.smoke_upstream`` and
``tests.harness.request_recorder``, all on 127.0.0.1). They carry the ``e2e``
marker and skip when the stack is not reachable, so CI never runs them::

    scripts/e2e_1556_security_stack.sh up
    uv run pytest tests/e2e -m e2e --no-cov
    scripts/e2e_1556_security_stack.sh down
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from tests.smoke.conftest import (
    agent_token_exchange,
    approve_agent,
    authed_request,
    generate_ed25519_jwks,
    grant_agent_permission,
    register_agent,
)

#: Vendor for every API these tests import: the live upstream host may serve only
#: one vendor, so tests vary the API name instead.
E2E_VENDOR = "e2esec"

_ADMIN_EMAIL = "e2e-sec-admin@local.test"
_ADMIN_PASSWORD = "E2eSecAdmin12345!"  # pragma: allowlist secret


@dataclass(frozen=True)
class Stack:
    app_url: str
    broker_url: str
    upstream_url: str
    recorder_url: str
    recorder_log: Path
    state_dir: Path


@dataclass(frozen=True)
class StackAgent:
    agent_id: str
    token: str


def _reachable(url: str) -> bool:
    try:
        with urlopen(url, timeout=3) as resp:
            return bool(200 <= resp.status < 500)
    except (URLError, OSError):
        return False


@pytest.fixture(scope="session")
def stack() -> Stack:
    state_dir = Path(os.environ.get("E2E_SEC_DIR", "/tmp/e2e-sec"))
    stack = Stack(
        app_url=f"http://127.0.0.1:{os.environ.get('E2E_SEC_APP_PORT', '55800')}",
        broker_url=f"http://127.0.0.1:{os.environ.get('E2E_SEC_BROKER_PORT', '55801')}",
        upstream_url=f"http://127.0.0.1:{os.environ.get('E2E_SEC_UPSTREAM_PORT', '55802')}",
        recorder_url=f"http://127.0.0.1:{os.environ.get('E2E_SEC_RECORDER_PORT', '55803')}",
        recorder_log=state_dir / "recorder.jsonl",
        state_dir=state_dir,
    )
    for url in (
        f"{stack.app_url}/health",
        f"{stack.broker_url}/health",
        f"{stack.recorder_url}/__recorder/health",
    ):
        if not _reachable(url):
            pytest.skip(f"security stack not running ({url}); see tests/e2e/conftest.py")
    return stack


@pytest.fixture(scope="session")
def admin_token(stack: Stack) -> str:
    body, status = authed_request(
        f"{stack.app_url}/auth/login",
        method="POST",
        body={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
    )
    if status == 200 and isinstance(body, dict):
        return str(body["access_token"])
    body, status = authed_request(
        f"{stack.app_url}/users:create-admin",
        method="POST",
        body={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
    )
    assert status == 200 and isinstance(body, dict), (status, body)
    return str(body["access_token"])


def make_agent(stack: Stack, admin_token: str, *, extra_permissions: list[str]) -> StackAgent:
    """Register, approve and token-exchange an agent holding ``extra_permissions``."""
    private_key, jwks = generate_ed25519_jwks()
    agent_id, _ = register_agent(stack.app_url, f"e2e-sec-{uuid.uuid4().hex[:10]}", jwks)
    approve_agent(stack.app_url, agent_id, admin_token)
    for permission in extra_permissions:
        grant_agent_permission(stack.app_url, agent_id, admin_token, permission)
    return StackAgent(
        agent_id=agent_id, token=agent_token_exchange(stack.app_url, agent_id, private_key)
    )


def spec(
    *,
    title: str,
    version: str,
    server: str,
    path: str,
    schemes: dict[str, Any] | None = None,
) -> str:
    """A minimal OpenAPI document with one ``GET`` operation."""
    document = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version, "description": uuid.uuid4().hex},
        "servers": [{"url": server}],
        "components": {
            "securitySchemes": schemes
            or {"key": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}}
        },
        "security": [{next(iter(schemes or {"key": None})): []}],
        "paths": {
            path: {"get": {"operationId": "op", "responses": {"200": {"description": "OK"}}}}
        },
    }
    return json.dumps(document)


def import_inline(
    stack: Stack, token: str, *, vendor: str, name: str, version: str, content: str
) -> dict[str, Any]:
    """``POST /apis`` an inline spec and wait for the job; returns the revision summary."""
    body, status = authed_request(
        f"{stack.app_url}/apis",
        method="POST",
        token=token,
        body={
            "sources": [
                {
                    "type": "inline",
                    "content": content,
                    "filename": "openapi.json",
                    "vendor": vendor,
                    "api_name": name,
                    "version": version,
                }
            ]
        },
    )
    assert status == 202 and isinstance(body, dict), (status, body)
    job_id = body["job_id"]
    deadline = time.monotonic() + 60
    job: Any = None
    while time.monotonic() < deadline:
        job, job_status = authed_request(f"{stack.app_url}/jobs/{job_id}", token=token)
        assert job_status == 200, job
        if isinstance(job, dict) and job["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert isinstance(job, dict) and job["status"] == "completed", job
    result, status = authed_request(f"{stack.app_url}/jobs/{job_id}/result", token=token)
    assert status == 200 and isinstance(result, dict), result
    revision: dict[str, Any] = result["revisions"][0]
    return revision


def recorded_requests(stack: Stack) -> list[dict[str, Any]]:
    if not stack.recorder_log.exists():
        return []
    return [
        json.loads(line)
        for line in stack.recorder_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture()
def recorder(stack: Stack) -> Iterator[Stack]:
    """Start each test with an empty recorder log."""
    stack.recorder_log.write_text("", encoding="utf-8")
    yield stack
