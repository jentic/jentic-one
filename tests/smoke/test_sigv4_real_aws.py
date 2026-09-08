"""Real-AWS SigV4 verification — signs requests and sends them to AWS's live verifier.

The unit suite pins the signer against the *published* AWS test vectors; these
tests close the remaining gap by asking AWS's **real** verifier (#776, #888).
They are opt-in and skip cleanly unless explicitly enabled:

    SIGV4_REAL_AWS=1 \
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... [AWS_SESSION_TOKEN=...] \
    [AWS_REGION=us-east-1] \
    [SIGV4_REAL_AOSS_ENDPOINT=https://<collection-id>.<region>.aoss.amazonaws.com] \
    uv run pytest tests/smoke/test_sigv4_real_aws.py -m smoke --no-cov -rs

Three layers, cheapest first:

1. **STS GetCallerIdentity** — needs nothing but a valid IAM principal (any
   account, no infrastructure, free). Proves the signature verifies against a
   real AWS endpoint, for both a GET (signed query canonicalisation) and a POST
   (signed payload hash).
2. **Negative control** — the same request with a corrupted secret must be
   *rejected*, proving the 200 above actually exercised signature verification.
3. **OpenSearch Serverless** (the #776 scenario) — signs with service ``aoss``
   against a real collection endpoint. Requires ``SIGV4_REAL_AOSS_ENDPOINT``
   and a data-access policy granting the caller ``aoss:DescribeIndex`` (e.g. on
   ``index/<collection>/*``). This is the exact call class that dead-ended in
   the original dogfooding attempt.

The gating env var is deliberately separate from the standard AWS_* variables:
CI runners often carry ambient AWS credentials, and these tests must never fire
implicitly.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import pytest

from jentic_one.shared.aws.sigv4 import SigV4Material, sign_request
from tests.smoke.conftest import (
    SmokeAgent,
    _app_is_reachable,
    authed_request,
    broker_call,
    provision_toolkit_and_credential,
    unique_vendor,
)

pytestmark = pytest.mark.smoke

_STS_QUERY = "Action=GetCallerIdentity&Version=2011-06-15"


def _real_aws_enabled() -> bool:
    return os.environ.get("SIGV4_REAL_AWS") == "1"


def _material(service: str) -> SigV4Material:
    """Build signing material from the standard AWS env vars, skipping if absent."""
    if not _real_aws_enabled():
        pytest.skip("real-AWS SigV4 tests disabled (set SIGV4_REAL_AWS=1 to enable)")
    access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not access_key_id or not secret:
        pytest.skip("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY not set")
    return SigV4Material(
        access_key_id=access_key_id,
        secret_access_key=secret,
        region=os.environ.get("AWS_REGION", "us-east-1"),
        service=service,
        session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
    )


def _sts_url(material: SigV4Material, *, query: str = "") -> str:
    base = f"https://sts.{material.region}.amazonaws.com/"
    return f"{base}?{query}" if query else base


def _send_signed(
    *, method: str, url: str, body: bytes | None, material: SigV4Material
) -> httpx.Response:
    """Sign exactly what will go on the wire and send it.

    Mirrors the broker's contract: the signer sees the final (method, URL, body)
    and its headers are merged onto the outbound request (SigV4SigningRunner).
    """
    headers = sign_request(method=method, url=url, body=body, material=material)
    if method == "POST":
        headers["content-type"] = "application/x-www-form-urlencoded"
    return httpx.request(method, url, content=body, headers=headers, timeout=30)


def _assert_signature_accepted(resp: httpx.Response) -> None:
    """Fail loudly if AWS rejected the *signature* (vs. a later authz denial)."""
    text = resp.text
    for marker in ("SignatureDoesNotMatch", "InvalidClientTokenId", "InvalidSignatureException"):
        assert marker not in text, f"AWS rejected the signature ({resp.status_code}): {text[:500]}"


def test_sts_get_caller_identity_get_signed_query() -> None:
    """A GET with a signed query string verifies against the real STS endpoint."""
    material = _material("sts")
    resp = _send_signed(
        method="GET", url=_sts_url(material, query=_STS_QUERY), body=None, material=material
    )
    _assert_signature_accepted(resp)
    assert resp.status_code == 200, f"STS returned {resp.status_code}: {resp.text[:500]}"
    assert "GetCallerIdentityResult" in resp.text


def test_sts_get_caller_identity_post_signed_payload() -> None:
    """A POST with a signed payload hash verifies against the real STS endpoint."""
    material = _material("sts")
    resp = _send_signed(
        method="POST", url=_sts_url(material), body=_STS_QUERY.encode(), material=material
    )
    _assert_signature_accepted(resp)
    assert resp.status_code == 200, f"STS returned {resp.status_code}: {resp.text[:500]}"
    assert "GetCallerIdentityResult" in resp.text


def test_sts_rejects_corrupted_secret_negative_control() -> None:
    """The same call with a corrupted secret is rejected.

    Guards the positive tests against false confidence: if STS accepted this
    too, the 200s above would prove nothing about signature verification.
    """
    good = _material("sts")
    bad = SigV4Material(
        access_key_id=good.access_key_id,
        secret_access_key=good.secret_access_key[:-4] + "XXXX",
        region=good.region,
        service=good.service,
        session_token=good.session_token,
    )
    resp = _send_signed(method="GET", url=_sts_url(bad, query=_STS_QUERY), body=None, material=bad)
    assert resp.status_code == 403, (
        f"corrupted secret should be rejected, got {resp.status_code}: {resp.text[:500]}"
    )


def test_opensearch_serverless_cat_indices() -> None:
    """The #776 scenario: a signed ``GET /_cat/indices`` against a real collection.

    OpenSearch Serverless accepts *only* SigV4 (service ``aoss``) and requires
    the ``x-amz-content-sha256`` header on every request — precisely what the
    signer emits and signs. A 200 here is the end-to-end proof the issue asked
    for. A 403 naming the signature fails immediately; a 403 without a
    signature complaint means signing verified but the collection's data-access
    policy does not grant this principal ``aoss:DescribeIndex`` — the failure
    message says so, because that is an environment fix, not a code fix.
    """
    endpoint = os.environ.get("SIGV4_REAL_AOSS_ENDPOINT")
    if not endpoint:
        pytest.skip("SIGV4_REAL_AOSS_ENDPOINT not set (needs a real OpenSearch collection)")
    material = _material("aoss")
    url = f"{endpoint.rstrip('/')}/_cat/indices"
    resp = _send_signed(method="GET", url=url, body=None, material=material)
    _assert_signature_accepted(resp)
    assert resp.status_code == 200, (
        f"OpenSearch Serverless returned {resp.status_code}: {resp.text[:500]}\n"
        "The signature was NOT rejected — if this is a 403, grant the calling "
        "principal aoss:DescribeIndex (and aoss:APIAccessAll in IAM) in the "
        "collection's data-access policy, then re-run."
    )


# ---------------------------------------------------------------------------
# Full-pipeline E2E: agent → broker → signed upstream → real OpenSearch.
# ---------------------------------------------------------------------------


def _minimal_aoss_spec(endpoint: str) -> str:
    """A minimal OpenAPI doc whose server is the real collection endpoint.

    One operation is enough: the broker resolves credentials by API tuple, not
    per-operation, and ``GET /_cat/indices`` is the same supported probe the
    signer-level test uses.
    """
    return json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "OpenSearch Serverless (sigv4 smoke)", "version": "1.0.0"},
            "servers": [{"url": endpoint.rstrip("/")}],
            "paths": {
                "/_cat/indices": {
                    "get": {
                        "operationId": "catIndices",
                        "responses": {"200": {"description": "index listing"}},
                    }
                }
            },
        }
    )


def test_broker_e2e_sigv4_to_real_opensearch(
    request: pytest.FixtureRequest, base_url: str, broker_url: str
) -> None:
    """The complete #776 flow, exactly as a user would drive it.

    Import the collection as an API → store a ``sigv4`` credential through the
    control plane (secret encrypted at rest) → bind agent + toolkit → execute
    ``GET /_cat/indices`` through the broker proxy. The broker resolves the
    credential, decrypts the material, and the SigV4SigningRunner signs the
    final wire request — a 200 from the real collection proves the whole
    pipeline, not just the signer.

    Needs: SIGV4_REAL_AWS=1, AWS creds, SIGV4_REAL_AOSS_ENDPOINT, and a
    reachable deployed stack (base_url/broker_url).
    """
    endpoint = os.environ.get("SIGV4_REAL_AOSS_ENDPOINT")
    if not endpoint:
        pytest.skip("SIGV4_REAL_AOSS_ENDPOINT not set (needs a real OpenSearch collection)")
    material = _material("aoss")
    if not _app_is_reachable(base_url):
        pytest.skip(f"App not reachable at {base_url}")
    # Resolved lazily so the admin/agent fixtures only run once every gate above
    # has passed — they would otherwise fail (not skip) on a machine where the
    # env vars are absent but some unrelated app answers on base_url.
    test_agent: SmokeAgent = request.getfixturevalue("test_agent")

    # 1. Import the collection endpoint as an API (inline spec, unique vendor).
    vendor = unique_vendor("sigv4-real")
    import_body, status = authed_request(
        f"{base_url}/apis",
        method="POST",
        token=test_agent.access_token,
        body={
            "sources": [
                {
                    "type": "inline",
                    "content": _minimal_aoss_spec(endpoint),
                    "filename": "aoss-sigv4-smoke.json",
                    "vendor": vendor,
                }
            ]
        },
    )
    assert status == 202, f"inline import failed: {status} {import_body}"
    assert isinstance(import_body, dict)
    job_id = import_body["job_id"]

    deadline = time.monotonic() + 60
    job: dict[str, Any] | list[Any] | None = None
    while time.monotonic() < deadline:
        job, job_status = authed_request(f"{base_url}/jobs/{job_id}", token=test_agent.access_token)
        assert job_status == 200 and isinstance(job, dict)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(2)
    assert isinstance(job, dict) and job["status"] == "completed", f"import job: {job}"

    result, status = authed_request(
        f"{base_url}/jobs/{job_id}/result", token=test_agent.access_token
    )
    assert status == 200 and isinstance(result, dict), f"job result: {status} {result}"
    api_ref = result["revisions"][0]["api"]

    # 2. Toolkit + sigv4 credential through the real control plane.
    toolkit_id, credential_id = provision_toolkit_and_credential(
        base_url,
        test_agent,
        credential_body={
            "type": "sigv4",
            "name": f"sigv4-smoke-{vendor}",
            "api": {
                "vendor": api_ref["vendor"],
                "name": api_ref["name"],
                "version": api_ref["version"],
            },
            "provider": "static",
            "access_key_id": material.access_key_id,
            "secret_access_key": material.secret_access_key,
            "session_token": material.session_token,
            "aws_region": material.region,
            "aws_service": "aoss",
        },
    )

    # The broker default-denies bindings with zero permission rules, so allow
    # GET across this (single-endpoint, throwaway) API before executing.
    _, status = authed_request(
        f"{base_url}/toolkits/{toolkit_id}/credentials/{credential_id}/permissions",
        method="PUT",
        token=test_agent.owner_token,
        body=[{"effect": "allow", "methods": ["GET"], "path": "/", "match_mode": "prefix"}],
    )
    assert status == 200, f"binding permission rules failed: {status}"

    # 3. Execute through the broker proxy — the signing runner does the rest.
    raw, status, _headers = broker_call(
        broker_url,
        f"{endpoint.rstrip('/')}/_cat/indices",
        token=test_agent.access_token,
    )
    text = raw.decode(errors="replace")
    for marker in ("SignatureDoesNotMatch", "InvalidSignatureException"):
        assert marker not in text, f"AWS rejected the broker-signed request: {text[:500]}"
    assert status == 200, (
        f"broker → OpenSearch returned {status}: {text[:500]}\n"
        "Signature was not rejected — a 403 here usually means the collection's "
        "data-access policy lacks aoss:DescribeIndex for this principal; a "
        "broker envelope (403/424 JSON) means toolkit/credential wiring failed."
    )
