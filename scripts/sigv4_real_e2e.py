#!/usr/bin/env python3
"""Real end-to-end check for the ``sigv4`` credential type (#776 / PR #888).

Drives the exact user story from issue #776 against a **running** jentic-one
instance and a **real** AWS SigV4 endpoint (OpenSearch Serverless by default):

  1. Direct probe — sign ``GET <endpoint><path>`` with the in-repo signer and
     call AWS directly (no jentic stack involved). Isolates signer correctness.
  2. Admin login → register + approve a throwaway agent → token exchange.
  3. Inline-import a minimal OpenAPI spec whose server URL is the endpoint.
  4. Create a ``sigv4`` credential via the control plane (secret encrypted at
     rest), create a toolkit, bind agent↔toolkit and credential↔toolkit.
  5. Execute the operation **through the broker** as the agent — the broker
     resolves + decrypts the credential and the SigV4SigningRunner signs the
     final wire request.
  6. Clean up the agent, toolkit, and credential (keep with ``--keep``).

Usage (AWS credentials come from the standard env vars, never argv):

    export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... [AWS_SESSION_TOKEN=...]
    uv run python scripts/sigv4_real_e2e.py \
        --base-url http://127.0.0.1:8000 \
        --broker-url http://127.0.0.1:8100 \
        --admin-email admin@example.com --admin-password '...' \
        --endpoint https://<collection-id>.<region>.aoss.amazonaws.com \
        --region <region>

Works for any SigV4 API, not just OpenSearch: e.g. ``--service execute-api
--endpoint https://<api-id>.execute-api.<region>.amazonaws.com --path /prod/x``.
A quick no-infrastructure sanity run: ``--service sts --endpoint
https://sts.us-east-1.amazonaws.com --path '/?Action=GetCallerIdentity&Version=2011-06-15'``.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import uuid
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Allow running as `python scripts/sigv4_real_e2e.py` from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from jentic_one.shared.aws.sigv4 import SigV4Material, sign_request

KID = "sigv4-e2e-key-1"
_SIG_REJECTIONS = ("SignatureDoesNotMatch", "InvalidSignatureException", "InvalidClientTokenId")


def _fail(step: str, detail: str) -> None:
    print(f"\nFAILED at '{step}': {detail}", file=sys.stderr)
    sys.exit(1)


def _expect(step: str, resp: httpx.Response, status: int) -> dict[str, Any]:
    if resp.status_code != status:
        _fail(step, f"HTTP {resp.status_code}: {resp.text[:800]}")
    return resp.json() if resp.content else {}


def _material(args: argparse.Namespace) -> SigV4Material:
    access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if not access_key_id or not secret:
        _fail("credentials", "set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in the environment")
    assert access_key_id and secret
    return SigV4Material(
        access_key_id=access_key_id,
        secret_access_key=secret,
        region=args.region,
        service=args.service,
        session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
    )


def _check_aws_response(step: str, status: int, text: str) -> None:
    for marker in _SIG_REJECTIONS:
        if marker in text:
            _fail(step, f"AWS rejected the signature (HTTP {status}): {text[:500]}")
    if status != 200:
        _fail(
            step,
            f"HTTP {status}: {text[:500]}\n"
            "The signature was NOT rejected — a 403 here usually means the "
            "collection's data-access policy lacks aoss:DescribeIndex (or the "
            "IAM principal lacks aoss:APIAccessAll).",
        )


def step_direct_probe(args: argparse.Namespace, material: SigV4Material, url: str) -> None:
    print(f"[1/6] direct signed probe: GET {url}")
    headers = sign_request(method="GET", url=url, body=None, material=material)
    resp = httpx.get(url, headers=headers, timeout=30)
    _check_aws_response("direct probe", resp.status_code, resp.text)
    print(f"      ok (HTTP 200, {len(resp.content)} bytes) — signer verified by AWS directly")


def step_agent(client: httpx.Client, admin_token: str) -> tuple[str, str]:
    print("[3/6] register + approve a throwaway agent")
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    jwks = {
        "keys": [
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": base64.urlsafe_b64encode(public_raw).rstrip(b"=").decode("ascii"),
                "kid": KID,
                "use": "sig",
                "alg": "EdDSA",
            }
        ]
    }
    reg = _expect(
        "register",
        client.post(
            "/register",
            json={"client_name": f"sigv4-e2e-{uuid.uuid4().hex[:8]}", "jwks": jwks},
        ),
        201,
    )
    agent_id = reg["client_id"]
    _expect(
        "approve",
        client.post(
            f"/agents/{agent_id}:approve", headers={"Authorization": f"Bearer {admin_token}"}
        ),
        200,
    )
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": agent_id,
            "sub": agent_id,
            "aud": f"{str(client.base_url).rstrip('/')}/oauth/token",
            "iat": now,
            "exp": now + 120,
            "jti": str(uuid.uuid4()),
        },
        private_key,
        algorithm="EdDSA",
        headers={"kid": KID},
    )
    tok = _expect(
        "token exchange",
        client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        ),
        200,
    )
    print(f"      agent {agent_id} active")
    return agent_id, tok["access_token"]


def step_import(client: httpx.Client, admin_token: str, endpoint: str, path: str) -> dict[str, str]:
    print("[4/6] inline-import a minimal spec for the endpoint")
    spec_path = path.split("?", 1)[0] or "/"
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "SigV4 e2e target", "version": "1.0.0"},
        "servers": [{"url": endpoint.rstrip("/")}],
        "paths": {
            spec_path: {
                "get": {
                    "operationId": "sigv4Probe",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    auth = {"Authorization": f"Bearer {admin_token}"}
    body = _expect(
        "import",
        client.post(
            "/apis",
            headers=auth,
            json={
                "sources": [
                    {
                        "type": "inline",
                        "content": json.dumps(spec),
                        "filename": "sigv4-e2e.json",
                        "vendor": f"sigv4-e2e-{uuid.uuid4().hex[:8]}",
                    }
                ]
            },
        ),
        202,
    )
    job_id = body["job_id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = _expect("job poll", client.get(f"/jobs/{job_id}", headers=auth), 200)
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(2)
    else:
        _fail("import", f"job {job_id} did not finish within 60s")
    if job["status"] != "completed":
        _fail("import", f"job failed: {json.dumps(job)[:800]}")
    result = _expect("job result", client.get(f"/jobs/{job_id}/result", headers=auth), 200)
    api = result["revisions"][0]["api"]
    print(f"      imported as {api['vendor']}/{api['name']}/{api['version']}")
    return {"vendor": api["vendor"], "name": api["name"], "version": api["version"]}


def step_provision(
    client: httpx.Client,
    admin_token: str,
    agent_id: str,
    api: dict[str, str],
    material: SigV4Material,
) -> tuple[str, str]:
    print("[5/6] create sigv4 credential + toolkit, bind agent")
    auth = {"Authorization": f"Bearer {admin_token}"}
    tk = _expect(
        "toolkit create",
        client.post("/toolkits", headers=auth, json={"name": f"sigv4-e2e-{uuid.uuid4().hex[:8]}"}),
        201,
    )
    toolkit_id = tk["toolkit"]["toolkit_id"]
    _expect(
        "agent↔toolkit bind",
        client.post(f"/agents/{agent_id}/toolkits", headers=auth, json={"toolkit_id": toolkit_id}),
        201,
    )
    cred = _expect(
        "credential create",
        client.post(
            "/credentials",
            headers=auth,
            json={
                "type": "sigv4",
                "name": f"sigv4-e2e-{uuid.uuid4().hex[:8]}",
                "api": api,
                "provider": "static",
                "access_key_id": material.access_key_id,
                "secret_access_key": material.secret_access_key,
                "session_token": material.session_token,
                "aws_region": material.region,
                "aws_service": material.service,
            },
        ),
        201,
    )
    credential_id = cred["credential"]["credential_id"]
    _expect(
        "credential↔toolkit bind",
        client.post(
            f"/toolkits/{toolkit_id}/credentials",
            headers=auth,
            json={"credential_id": credential_id},
        ),
        201,
    )
    # The broker default-denies a binding with zero permission rules, so grant
    # GET on all paths of this (single-endpoint, throwaway) API.
    _expect(
        "binding permission rules",
        client.put(
            f"/toolkits/{toolkit_id}/credentials/{credential_id}/permissions",
            headers=auth,
            json=[{"effect": "allow", "methods": ["GET"], "path": "/", "match_mode": "prefix"}],
        ),
        200,
    )
    print(f"      toolkit {toolkit_id}, credential {credential_id}")
    return toolkit_id, credential_id


def step_broker_execute(broker_url: str, agent_token: str, url: str) -> None:
    print(f"[6/6] execute through the broker: GET {broker_url.rstrip('/')}/{url}")
    resp = httpx.get(
        f"{broker_url.rstrip('/')}/{url}",
        headers={"Authorization": f"Bearer {agent_token}"},
        timeout=60,
    )
    _check_aws_response("broker execute", resp.status_code, resp.text)
    preview = resp.text[:200].replace("\n", " | ")
    print(f"      ok (HTTP 200 via broker) — upstream said: {preview or '<empty body>'}")


def cleanup(
    client: httpx.Client,
    admin_token: str,
    *,
    agent_id: str | None,
    toolkit_id: str | None,
    credential_id: str | None,
) -> None:
    auth = {"Authorization": f"Bearer {admin_token}"}
    if toolkit_id and agent_id:
        client.delete(f"/agents/{agent_id}/toolkits/{toolkit_id}", headers=auth)
    if credential_id:
        client.delete(f"/credentials/{credential_id}", headers=auth)
    if toolkit_id:
        client.delete(f"/toolkits/{toolkit_id}", headers=auth)
    if agent_id:
        client.delete(f"/agents/{agent_id}", headers=auth)
    print("      cleaned up agent / toolkit / credential (imported API left in place)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--broker-url", default="http://127.0.0.1:8100")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", required=True)
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("SIGV4_REAL_AOSS_ENDPOINT"),
        help="Full https endpoint of the SigV4 API (e.g. the OpenSearch collection URL)",
    )
    parser.add_argument("--path", default="/_cat/indices", help="Path to probe (GET)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--service", default="aoss", help="SigV4 service name (aoss, sts, s3, …)")
    parser.add_argument("--keep", action="store_true", help="Skip cleanup of created resources")
    args = parser.parse_args()
    if not args.endpoint:
        parser.error("--endpoint (or SIGV4_REAL_AOSS_ENDPOINT) is required")

    material = _material(args)
    target = f"{args.endpoint.rstrip('/')}{args.path}"

    step_direct_probe(args, material, target)

    agent_id = toolkit_id = credential_id = None
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        print("[2/6] admin login")
        login = _expect(
            "admin login",
            client.post(
                "/auth/login", json={"email": args.admin_email, "password": args.admin_password}
            ),
            200,
        )
        admin_token = login["access_token"]
        try:
            agent_id, agent_token = step_agent(client, admin_token)
            api = step_import(client, admin_token, args.endpoint, args.path)
            toolkit_id, credential_id = step_provision(client, admin_token, agent_id, api, material)
            step_broker_execute(args.broker_url, agent_token, target)
        finally:
            if args.keep:
                print("      --keep set; leaving resources in place")
            else:
                cleanup(
                    client,
                    admin_token,
                    agent_id=agent_id,
                    toolkit_id=toolkit_id,
                    credential_id=credential_id,
                )

    print("\nPASS — SigV4 signing verified end-to-end through the broker.")


if __name__ == "__main__":
    main()
