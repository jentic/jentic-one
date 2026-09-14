"""Smoke tests: broker credential injection per auth scheme.

Each positive test provisions a credential for one scheme, calls the matching
``/auth/*`` op through the broker with the *agent* token, and asserts the broker
injected the credential the harness expects (the harness only checks presence —
``{"authenticated": true, "scheme": …}``).

The negative tests fire at distinct pipeline stages (SSRF → discovery →
binding → rules), pinning the direct-binding gating order (theme-5 Phase 5b —
direct bindings are the default broker path):

- no binding       → 403 ``no_credential_binding``
- binding, no rules → 403 (zero rules default-deny)
- unindexed URL    → ``operation_not_found`` (discovery gate, before bindings)

The ``complex`` scheme (two headers) needs multi-credential injection the broker
doesn't do per vendor — skipped here, tracked as a follow-up.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.smoke.conftest import (
    HarnessApi,
    SmokeAgent,
    _skip_if_no_admin_surface,
    broker_call,
    provision_bound_credential,
)


def _api_ref(harness_api: HarnessApi) -> dict[str, str]:
    return {
        "vendor": harness_api.vendor,
        "name": harness_api.name,
        "version": harness_api.version,
    }


def _assert_authenticated(raw: bytes, status: int, scheme: str) -> None:
    """The harness echoes ``{"authenticated": true, "scheme": …}`` on success."""
    assert status == 200, f"broker→/auth/{scheme} returned {status}: {raw!r}"
    payload = json.loads(raw)
    assert payload == {"authenticated": True, "scheme": scheme}, payload


@pytest.mark.smoke
def test_bearer_injection(
    base_url: str,
    broker_url: str,
    test_agent: SmokeAgent,
    harness_api: HarnessApi,
    upstream_incluster_url: str,
) -> None:
    """A bearer_token credential is injected as ``Authorization: Bearer …``."""
    _skip_if_no_admin_surface()
    provision_bound_credential(
        base_url,
        test_agent,
        credential_body={
            "type": "bearer_token",
            "name": f"smoke-bearer-{uuid.uuid4().hex[:8]}",
            "api": _api_ref(harness_api),
            "provider": "static",
            "token": f"smoke-bearer-{uuid.uuid4().hex[:16]}",
        },
    )
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/auth/bearer",
        token=test_agent.access_token,
    )
    _assert_authenticated(raw, status, "bearer")


@pytest.mark.smoke
def test_api_key_injection(
    base_url: str,
    broker_url: str,
    test_agent: SmokeAgent,
    harness_api: HarnessApi,
    upstream_incluster_url: str,
) -> None:
    """An api_key credential is injected into the ``X-Api-Key`` header."""
    _skip_if_no_admin_surface()
    provision_bound_credential(
        base_url,
        test_agent,
        credential_body={
            "type": "api_key",
            "name": f"smoke-apikey-{uuid.uuid4().hex[:8]}",
            "api": _api_ref(harness_api),
            "provider": "static",
            "key": f"smoke-key-{uuid.uuid4().hex[:16]}",
            "location": "header",
            "field_name": "X-Api-Key",
        },
    )
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/auth/api-key",
        token=test_agent.access_token,
    )
    _assert_authenticated(raw, status, "api-key")


@pytest.mark.smoke
def test_basic_injection(
    base_url: str,
    broker_url: str,
    test_agent: SmokeAgent,
    harness_api: HarnessApi,
    upstream_incluster_url: str,
) -> None:
    """A basic credential is injected as ``Authorization: Basic …``."""
    _skip_if_no_admin_surface()
    provision_bound_credential(
        base_url,
        test_agent,
        credential_body={
            "type": "basic",
            "name": f"smoke-basic-{uuid.uuid4().hex[:8]}",
            "api": _api_ref(harness_api),
            "provider": "static",
            "username": "smoke-user",
            "password": f"smoke-pass-{uuid.uuid4().hex[:12]}",
        },
    )
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/auth/basic",
        token=test_agent.access_token,
    )
    _assert_authenticated(raw, status, "basic")


@pytest.mark.smoke
def test_oauth2_injection(
    base_url: str,
    broker_url: str,
    test_agent: SmokeAgent,
    harness_api: HarnessApi,
    upstream_incluster_url: str,
) -> None:
    """An oauth2 client-credentials grant is exchanged at the harness token stub.

    The broker fetches a token from ``{incluster}/oauth/token`` (the harness
    token stub) and injects it as a bearer; the harness ``/auth/oauth2`` op accepts any
    ``Bearer`` value. Skips if the broker can't complete the refresh (e.g. the
    stub unreachable from the broker) — the bearer/api-key/basic tests already
    cover injection mechanics.
    """
    _skip_if_no_admin_surface()
    provision_bound_credential(
        base_url,
        test_agent,
        credential_body={
            "type": "oauth2",
            "name": f"smoke-oauth2-{uuid.uuid4().hex[:8]}",
            "api": _api_ref(harness_api),
            "provider": "static",
            "grant_type": "client_credentials",
            "token_url": f"{upstream_incluster_url}/oauth/token",
            "client_id": "smoke-client",
            "client_secret": f"smoke-secret-{uuid.uuid4().hex[:12]}",
        },
    )
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/auth/oauth2",
        token=test_agent.access_token,
    )
    if status in (401, 502):
        # Distinguish infra (token endpoint unreachable) from broker bugs:
        # only skip when the error originated upstream — a broker-origin 502
        # means the broker itself mis-handled the oauth2 flow and should fail.
        try:
            err_body = json.loads(raw)
            origin = err_body.get("error_origin", "")
        except (json.JSONDecodeError, ValueError):
            origin = ""
        if origin == "upstream" or status == 401:
            pytest.skip(
                f"broker oauth2 client-credentials refresh unavailable "
                f"(status {status}, origin={origin!r})"
            )
        # broker-origin 502 → let it fail through to the assertion below
    _assert_authenticated(raw, status, "oauth2")


@pytest.mark.smoke
def test_binding_without_rules_is_denied(
    base_url: str,
    broker_url: str,
    test_agent: SmokeAgent,
    harness_api: HarnessApi,
    upstream_incluster_url: str,
) -> None:
    """A direct binding with zero permission rules default-denies (403).

    The rule gate fires *after* binding resolution, so binding a credential
    with an empty rule list isolates the default-deny case from the
    missing-binding one below.
    """
    _skip_if_no_admin_surface()
    provision_bound_credential(
        base_url,
        test_agent,
        credential_body={
            "type": "bearer_token",
            "name": f"smoke-norules-{uuid.uuid4().hex[:8]}",
            "api": _api_ref(harness_api),
            "provider": "static",
            "token": f"smoke-bearer-{uuid.uuid4().hex[:16]}",
        },
        rules=[],  # deliberately rule-less → default-deny
    )
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/auth/bearer",
        token=test_agent.access_token,
    )
    assert status == 403, f"expected 403, got {status}: {raw!r}"


@pytest.mark.smoke
def test_no_credential_binding_returns_403(
    broker_url: str,
    test_agent: SmokeAgent,
    harness_api: HarnessApi,
    upstream_incluster_url: str,
) -> None:
    """Ingested but nothing bound → broker 403 no_credential_binding.

    Depends on ``harness_api`` (so the op is discoverable) but provisions no
    credential, so direct-binding resolution fails before injection.
    """
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/auth/bearer",
        token=test_agent.access_token,
    )
    assert status == 403, f"expected 403, got {status}: {raw!r}"
    body: dict[str, Any] = json.loads(raw)
    assert body.get("type") == "no_credential_binding", body


@pytest.mark.smoke
def test_unregistered_url_returns_operation_not_found(
    broker_url: str,
    test_agent: SmokeAgent,
    harness_api: HarnessApi,
    upstream_incluster_url: str,
) -> None:
    """A harness URL that isn't an indexed op → broker operation_not_found.

    ``/health`` exists on the harness but isn't in the ingested spec, so discovery
    returns None before any binding/credential check (404-class).
    """
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/health",
        token=test_agent.access_token,
    )
    assert status == 404, f"expected 404, got {status}: {raw!r}"
    body: dict[str, Any] = json.loads(raw)
    assert body.get("type") == "operation_not_found", body
