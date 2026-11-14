"""Unit tests for request method/target override detection and route rejection."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

import jentic_one.broker.services.execution.service as exec_service_mod
import jentic_one.broker.web.routers.execute as execute_mod
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.request_overrides import find_request_override
from jentic_one.broker.services.credentials.resolver import ResolvedCredential
from jentic_one.broker.services.execution.authorization import ExecutionAuthorization
from jentic_one.broker.web.errors import install_broker_error_handlers
from jentic_one.broker.web.routers.execute import _handle
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import ResolveResult, RuleVerdict
from jentic_one.shared.config import AppConfig
from jentic_one.shared.crypto.encryption import EncryptionService
from jentic_one.shared.jobs.protocols import InjectedAuth
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.credentials import CredentialType, StoredCredentialType
from jentic_one.shared.schemas import APIReference, OperationInfo
from tests.unit.fake_tx import SessionScope

_TARGET_URL = "https://api.vendor.example/users/123"


@pytest.mark.parametrize(
    ("method", "raw_headers", "url", "expected"),
    [
        (
            "POST",
            [(b"X-HTTP-Method-Override", b"DELETE")],
            _TARGET_URL,
            "x-http-method-override",
        ),
        (
            "POST",
            [(b"x-http-method", b"delete")],
            _TARGET_URL,
            "x-http-method",
        ),
        (
            "POST",
            [(b"X-Method-Override", b"  Patch  ")],
            _TARGET_URL,
            "x-method-override",
        ),
        (
            "POST",
            [
                (b"X-HTTP-Method-Override", b"POST"),
                (b"x-http-method-override", b"DELETE"),
            ],
            _TARGET_URL,
            "x-http-method-override",
        ),
        (
            "POST",
            [(b"X-HTTP-Method-Override", b"POST, DELETE")],
            _TARGET_URL,
            "x-http-method-override",
        ),
        (
            "POST",
            [(b"X-Original-URL", b"/admin/users/123")],
            _TARGET_URL,
            "x-original-url",
        ),
        (
            "POST",
            [(b"x-rewrite-url", b"/users/123")],
            _TARGET_URL,
            "x-rewrite-url",
        ),
        (
            "POST",
            [(b"X-Original-URI", b"")],
            _TARGET_URL,
            "x-original-uri",
        ),
        (
            "POST",
            [],
            f"{_TARGET_URL}?_method=DELETE",
            "_method",
        ),
        (
            "POST",
            [],
            f"{_TARGET_URL}?_method=delete",
            "_method",
        ),
        (
            "POST",
            [],
            f"{_TARGET_URL}?%5Fmethod=DELETE",
            "_method",
        ),
        (
            "POST",
            [],
            f"{_TARGET_URL}?%5fmethod=put",
            "_method",
        ),
        (
            "POST",
            [],
            f"{_TARGET_URL}?_METHOD=DELETE",
            "_method",
        ),
        (
            "POST",
            [],
            f"{_TARGET_URL}?_method=POST&_method=DELETE",
            "_method",
        ),
        (
            "POST",
            [(b"X-HTTP-Method-Override", b"POST")],
            _TARGET_URL,
            None,
        ),
        (
            "POST",
            [(b"x-http-method", b"post")],
            f"{_TARGET_URL}?_method=post&%5Fmethod=POST",
            None,
        ),
        (
            "GET",
            [(b"Accept", b"application/json")],
            f"{_TARGET_URL}?page=1",
            None,
        ),
    ],
)
def test_find_request_override_matrix(
    method: str,
    raw_headers: list[tuple[bytes, bytes]],
    url: str,
    expected: str | None,
) -> None:
    headers = Headers(raw=raw_headers)
    assert find_request_override(method, headers, url) == expected


def _build_route_harness(
    monkeypatch: pytest.MonkeyPatch,
    sample_config_dict: dict[str, Any],
    *,
    verdict: RuleVerdict = RuleVerdict.ALLOW,
) -> tuple[TestClient, dict[str, int], list[dict[str, str]]]:
    counts = {
        "egress": 0,
        "discover": 0,
        "authorize": 0,
        "hold": 0,
        "enqueue": 0,
        "runner": 0,
    }
    forwarded_requests: list[dict[str, str]] = []

    async def _validate(raw_url: str, egress: Any = None) -> str:
        counts["egress"] += 1
        return raw_url

    class _Resolver:
        async def resolve_operation(
            self, *, method: str, url: str, revision_id: object = None
        ) -> ResolveResult:
            counts["discover"] += 1
            return ResolveResult(
                operation=OperationInfo(
                    id="op-users",
                    path="/users/{id}",
                    method=method,
                    relative_path="/users/{id}",
                ),
                api=APIReference(vendor="vendor", name="users", version="v1"),
                path_params={"id": "123"},
            )

    selected = ResolvedCredential(
        credential_id="cred-1",
        name="vendor-cred",
        wire_type=CredentialType.API_KEY,
        stored_type=StoredCredentialType.API_KEY,
        provider="vendor",
    )

    async def _authorize(*_args: Any, **_kwargs: Any) -> ExecutionAuthorization:
        counts["authorize"] += 1
        return ExecutionAuthorization(
            verdict=verdict,
            allowed_credential_ids=[selected.credential_id],
            selected_credential=selected,
            matched_rule_id="rule-1",
        )

    async def _resolve_credentials(*_args: Any, **_kwargs: Any) -> InjectedAuth:
        return InjectedAuth(
            headers={"Authorization": "Bearer upstream-token"},
            query_params={},
            cookies={},
            credential_id=selected.credential_id,
            credential_name=selected.name,
        )

    async def _file_hold(*_args: Any, **_kwargs: Any) -> Any:
        counts["hold"] += 1
        raise AssertionError("file_hold must not be reached")

    async def _enqueue_job(*_args: Any, **_kwargs: Any) -> str:
        counts["enqueue"] += 1
        raise AssertionError("enqueue_job must not be reached")

    def _transport_handler(req: httpx.Request) -> httpx.Response:
        counts["runner"] += 1
        forwarded_requests.append(dict(req.headers))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(execute_mod, "validate_upstream_url_async", _validate)
    monkeypatch.setattr(execute_mod, "authorize_execution", _authorize)
    monkeypatch.setattr(execute_mod, "_resolve_credentials", _resolve_credentials)
    monkeypatch.setattr(execute_mod, "file_hold", _file_hold)
    monkeypatch.setattr(execute_mod, "enqueue_job", _enqueue_job)
    monkeypatch.setattr(exec_service_mod, "record_execution", AsyncMock(return_value="exec_1"))
    monkeypatch.setattr(exec_service_mod, "emit_event", AsyncMock(return_value="evt_1"))

    cfg_dict = {
        **sample_config_dict,
        "credentials": {
            "encryption": {
                "active_id": "v1",
                "entries": [{"id": "v1", "material": base64.b64encode(b"k" * 32).decode()}],
            }
        },
    }
    cfg = AppConfig.model_validate(cfg_dict)
    cfg.broker.resilience.upstream.stream_passthrough_enabled = False
    assert cfg.credentials.encryption is not None
    encryption = EncryptionService(cfg.credentials.encryption)
    ctx = SimpleNamespace(
        config=cfg,
        admin_db=SessionScope(object()),
        has_encryption_keyset=True,
        encryption=encryption,
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_transport_handler))
    runner = HttpRunner(client)

    app = FastAPI()
    install_broker_error_handlers(app)
    app.state.broker_registry_resolver = _Resolver()

    identity = Identity(
        sub="agnt_test",
        actor_type=ActorType.AGENT,
        permissions=["execute"],
        expires_at=datetime(2999, 1, 1, tzinfo=UTC),
        active=True,
    )

    @app.api_route("/{upstream_url:path}", methods=["GET", "POST", "DELETE"])
    async def proxy(upstream_url: str, request: Request) -> Response:
        return await _handle(
            request,
            request.method,
            ctx,  # type: ignore[arg-type]
            identity,
            SimpleNamespace(),
            SimpleNamespace(),
            runner,
            None,
        )

    return TestClient(app, raise_server_exceptions=False), counts, forwarded_requests


@pytest.mark.parametrize(
    ("extra_headers", "query", "expected_field"),
    [
        ({"X-HTTP-Method-Override": "DELETE"}, "", "x-http-method-override"),
        (
            {"X-HTTP-Method-Override": "DELETE", "Prefer": "respond-async"},
            "",
            "x-http-method-override",
        ),
        ({"X-Original-URL": "/admin/users/123"}, "", "x-original-url"),
        (
            {"X-Original-URL": "/admin/users/123", "Prefer": "respond-async"},
            "",
            "x-original-url",
        ),
        ({}, "?_method=DELETE", "_method"),
        ({"Prefer": "respond-async"}, "?%5Fmethod=DELETE", "_method"),
    ],
)
def test_post_with_delete_override_rejected_before_rules(
    monkeypatch: pytest.MonkeyPatch,
    sample_config_dict: dict[str, Any],
    extra_headers: dict[str, str],
    query: str,
    expected_field: str,
) -> None:
    client, counts, _ = _build_route_harness(
        monkeypatch, sample_config_dict, verdict=RuleVerdict.REQUIRE_APPROVAL
    )

    resp = client.post(f"/{_TARGET_URL}{query}", headers=extra_headers)

    assert resp.status_code == 400, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers[JenticHeader.ERROR_ORIGIN.value] == "broker"
    body = resp.json()
    assert body["type"] == "request_override_not_allowed"
    assert body["status"] == 400
    assert body["error_origin"] == "broker"
    assert body["header"] == expected_field
    assert body["instance"] == f"/{_TARGET_URL}"
    assert body["agent_directive"]["strategy"] in {"modify_headers", "fatal"}
    assert counts == {
        "egress": 0,
        "discover": 0,
        "authorize": 0,
        "hold": 0,
        "enqueue": 0,
        "runner": 0,
    }


def test_matching_override_value_passes(
    monkeypatch: pytest.MonkeyPatch,
    sample_config_dict: dict[str, Any],
) -> None:
    client, counts, forwarded_requests = _build_route_harness(
        monkeypatch, sample_config_dict, verdict=RuleVerdict.ALLOW
    )

    resp = client.post(
        f"/{_TARGET_URL}?_method=post",
        headers={"X-HTTP-Method-Override": "POST", "X-Custom": "kept"},
    )

    assert resp.status_code == 200, resp.text
    assert counts["runner"] == 1
    assert counts["authorize"] == 1
    assert len(forwarded_requests) == 1
    assert "x-http-method-override" not in {k.lower() for k in forwarded_requests[0]}
    assert forwarded_requests[0]["x-custom"] == "kept"
