"""Unit tests for the broker upstream scheme guard on HTTPS-only operations."""

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

import jentic_one.broker.services.execution.service as exec_service_mod
import jentic_one.broker.web.routers.execute as execute_mod
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.headers import JenticHeader
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


def _build_route_harness(
    monkeypatch: pytest.MonkeyPatch,
    sample_config_dict: dict[str, Any],
    *,
    plaintext_http_allowed: bool,
    verdict: RuleVerdict = RuleVerdict.ALLOW,
) -> tuple[TestClient, dict[str, int], list[str]]:
    counts = {
        "egress": 0,
        "discover": 0,
        "authorize": 0,
        "hold": 0,
        "enqueue": 0,
        "runner": 0,
    }
    forwarded_urls: list[str] = []

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
                    id="op-charges",
                    path="/v1/charges",
                    method=method,
                    relative_path="/v1/charges",
                ),
                api=APIReference(vendor="vendor", name="payments", version="v1"),
                path_params={},
                plaintext_http_allowed=plaintext_http_allowed,
            )

    selected = ResolvedCredential(
        credential_id="cred-1",
        name="vendor-cred",
        wire_type=CredentialType.BEARER_TOKEN,
        stored_type=StoredCredentialType.STATIC_BEARER_TOKEN,
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
        forwarded_urls.append(str(req.url))
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

    @app.api_route("/{upstream_url:path}", methods=["GET", "POST"])
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

    return TestClient(app, raise_server_exceptions=False), counts, forwarded_urls


@pytest.mark.parametrize(
    ("extra_headers", "verdict"),
    [
        ({}, RuleVerdict.ALLOW),
        ({"Prefer": "respond-async"}, RuleVerdict.ALLOW),
        ({}, RuleVerdict.REQUIRE_APPROVAL),
    ],
)
def test_http_request_rejected_before_authorization_when_https_only(
    monkeypatch: pytest.MonkeyPatch,
    sample_config_dict: dict[str, Any],
    extra_headers: dict[str, str],
    verdict: RuleVerdict,
) -> None:
    client, counts, forwarded_urls = _build_route_harness(
        monkeypatch,
        sample_config_dict,
        plaintext_http_allowed=False,
        verdict=verdict,
    )

    resp = client.post("/http://api.vendor.example/v1/charges", headers=extra_headers)

    assert resp.status_code == 400, resp.text
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers[JenticHeader.ERROR_ORIGIN.value] == "broker"
    body = resp.json()
    assert body["type"] == "insecure_upstream_scheme"
    assert body["status"] == 400
    assert body["detail"] == "This API is only served over HTTPS; call it with https://."
    assert body["error_origin"] == "broker"
    assert body["instance"] == "/http://api.vendor.example/v1/charges"
    assert body["agent_directive"]["strategy"] == "retry"
    assert "https://" in body["agent_directive"]["human_readable_instruction"]
    assert counts == {
        "egress": 1,
        "discover": 1,
        "authorize": 0,
        "hold": 0,
        "enqueue": 0,
        "runner": 0,
    }
    assert forwarded_urls == []


def test_http_request_allowed_when_operation_permits_plaintext_http(
    monkeypatch: pytest.MonkeyPatch,
    sample_config_dict: dict[str, Any],
) -> None:
    client, counts, forwarded_urls = _build_route_harness(
        monkeypatch,
        sample_config_dict,
        plaintext_http_allowed=True,
    )

    resp = client.get("/http://api.vendor.example/v1/charges")

    assert resp.status_code == 200, resp.text
    assert counts["discover"] == 1
    assert counts["authorize"] == 1
    assert counts["runner"] == 1
    assert forwarded_urls == ["http://api.vendor.example/v1/charges"]


def test_https_request_allowed_when_operation_is_https_only(
    monkeypatch: pytest.MonkeyPatch,
    sample_config_dict: dict[str, Any],
) -> None:
    client, counts, forwarded_urls = _build_route_harness(
        monkeypatch,
        sample_config_dict,
        plaintext_http_allowed=False,
    )

    resp = client.get("/https://api.vendor.example/v1/charges")

    assert resp.status_code == 200, resp.text
    assert counts["discover"] == 1
    assert counts["authorize"] == 1
    assert counts["runner"] == 1
    assert forwarded_urls == ["https://api.vendor.example/v1/charges"]
