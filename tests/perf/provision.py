"""Provision the perf stack: vendors, credentials, bindings, and callers.

Runs on the host against the published app/broker ports and writes
``tests/perf/results/.perf-env.json`` for the load generators. Idempotent: a
re-run reuses imported APIs, replaces each perf vendor's credential (deleting
any stale one first, so a vendor never holds two active credentials), and mints
fresh callers.

What it sets up (see tests/perf/README.md):

* one API per perf vendor, imported **inline** from the harness live spec with
  ``servers[0].url`` rewritten to that vendor's network alias;
* one active credential per vendor (``no_auth``, ``bearer_token``, ``api_key``
  header/query/cookie, ``sigv4``), direct-bound to the perf agent with an
  explicit allow-all rule;
* three callers for the same agent: an agent API key (``jak_``), an opaque
  access token (``at_``, via the jwt-bearer exchange), and an HS256 JWT signed
  with ``broker.jwt_secret``;
* N extra agents (``jak_``) bound to ``perf-noauth`` for the rate-limit scenario.

Usage::

    uv run python -m tests.perf.provision [--pool-agents 10]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import jwt
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm

from tests.perf.common import (
    APP_INTERNAL,
    BROKER_INTERNAL,
    DEFAULT_ENV_FILE,
    ECHO_PATH,
    PERF_DIR,
    VENDORS,
    Caller,
    PerfEnv,
    Vendor,
    upstream_base,
)

ADMIN_EMAIL = "admin@perf.local"
ADMIN_PASSWORD = "PerfAdmin-12345!"  # pragma: allowlist secret
ALLOW_ALL_RULE = [{"effect": "allow", "path": ".*", "match_mode": "regex"}]
API_KEY_FIELD = {"header": "X-Api-Key", "query": "api_key", "cookie": "api_key"}
#: Fake (harness never verifies signatures) but well-formed AWS material.
SIGV4_ACCESS_KEY = "AKIAPERFTEST00000000"  # pragma: allowlist secret
SIGV4_SECRET = "perf/secret/key/0000000000000000000000000"  # pragma: allowlist secret
JWT_TTL_S = 24 * 3600


class ProvisionError(RuntimeError):
    pass


def _check(resp: httpx.Response, *expected: int) -> Any:
    if resp.status_code not in expected:
        raise ProvisionError(
            f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json() if resp.content else None


def _first_list(body: Any) -> list[dict[str, Any]]:
    """List endpoints wrap items under a resource key; take the first list value."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for value in body.values():
            if isinstance(value, list):
                return value
    return []


def admin_token(client: httpx.Client) -> str:
    resp = client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if resp.status_code == 200:
        return str(resp.json()["access_token"])
    resp = client.post(
        "/users:create-admin", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    body = _check(resp, 200)
    return str(body["access_token"])


def wait_job(client: httpx.Client, job_id: str, timeout_s: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = _check(client.get(f"/jobs/{job_id}"), 200)
        if job["status"] in ("completed", "failed", "dead_lettered"):
            if job["status"] != "completed":
                raise ProvisionError(f"job {job_id} {job['status']}: {job.get('error')}")
            return dict(job)
        time.sleep(0.5)
    raise ProvisionError(f"job {job_id} did not finish in {timeout_s}s")


def spec_for(live_spec: dict[str, Any], vendor: Vendor) -> str:
    spec = json.loads(json.dumps(live_spec))
    spec["servers"] = [{"url": upstream_base(vendor.alias)}]
    spec["info"]["x-vendor"] = vendor.slug
    return json.dumps(spec)


def ensure_api(client: httpx.Client, live_spec: dict[str, Any], vendor: Vendor) -> dict[str, str]:
    """Return the vendor's API identity, importing (and promoting) it if absent.

    Inline imports land as a ``draft`` revision; the broker only resolves URLs
    against an API's current (promoted) revision, so promote it.
    """
    existing = _first_list(_check(client.get("/apis", params={"vendor": vendor.slug}), 200))
    api: dict[str, str] | None = None
    for row in existing:
        ref = row.get("api", row)
        if ref.get("vendor") == vendor.slug:
            api = {"vendor": ref["vendor"], "name": ref["name"], "version": ref["version"]}
            break
    if api is None:
        source = {
            "type": "inline",
            "content": spec_for(live_spec, vendor),
            "filename": f"{vendor.slug}.json",
            "vendor": vendor.slug,
        }
        body = _check(client.post("/apis", json={"sources": [source]}), 202)
        wait_job(client, body["job_id"])
        result = _check(client.get(f"/jobs/{body['job_id']}/result"), 200)
        ref = result["revisions"][0]["api"]
        api = {"vendor": ref["vendor"], "name": ref["name"], "version": ref["version"]}
    api["revision_id"] = promote_latest(client, api)
    return api


def promote_latest(client: httpx.Client, api: dict[str, str]) -> str:
    """Promote the newest revision if none is current; return the current revision id."""
    base = f"/apis/{api['vendor']}/{api['name']}/{api['version']}"
    revisions = _first_list(_check(client.get(f"{base}/revisions"), 200))
    if not revisions:
        raise ProvisionError(f"{base} has no revisions")
    for rev in revisions:
        if rev.get("is_current"):
            return str(rev["revision_id"])
    revision_id = str(revisions[0]["revision_id"])
    _check(client.post(f"{base}/revisions/{revision_id}:promote"), 200)
    return revision_id


def credential_body(vendor: Vendor, api: dict[str, str]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": vendor.credential_type,
        "name": f"{vendor.slug}-cred",
        "api": {"vendor": api["vendor"], "name": api["name"], "version": api["version"]},
        "provider": "static",
    }
    if vendor.credential_type == "bearer_token":
        base["token"] = f"perf-bearer-{uuid.uuid4().hex[:12]}"
    elif vendor.credential_type == "api_key":
        assert vendor.location is not None
        base["key"] = f"perf-key-{uuid.uuid4().hex[:12]}"
        base["location"] = vendor.location
        base["field_name"] = API_KEY_FIELD[vendor.location]
    elif vendor.credential_type == "sigv4":
        base["access_key_id"] = SIGV4_ACCESS_KEY
        base["secret_access_key"] = SIGV4_SECRET
        base["aws_region"] = "us-east-1"
        base["aws_service"] = "execute-api"
    return base


def replace_credential(client: httpx.Client, vendor: Vendor, api: dict[str, str]) -> str:
    """Delete any existing credential on the vendor, then create exactly one."""
    for cred in _first_list(
        _check(client.get("/credentials", params={"vendor": vendor.slug}), 200)
    ):
        cred_id = cred.get("credential_id") or cred.get("id")
        if cred_id:
            _check(client.delete(f"/credentials/{cred_id}"), 204, 404)
    body = _check(client.post("/credentials", json=credential_body(vendor, api)), 201)
    return str(body["credential"]["credential_id"])


def bind(client: httpx.Client, agent_id: str, credential_id: str) -> None:
    _check(
        client.post(f"/agents/{agent_id}/credentials", json={"credential_id": credential_id}), 201
    )
    _check(
        client.put(
            f"/credentials/{credential_id}/agents/{agent_id}/permissions", json=ALLOW_ALL_RULE
        ),
        200,
    )


def purge_perf_agents(client: httpx.Client) -> int:
    """Hard-delete agents from earlier runs (``perf-agent-*`` / ``perf-pool-*``)."""
    stale: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        page = _check(client.get("/agents", params=params), 200)
        stale += [
            str(a.get("agent_id") or a.get("id"))
            for a in page.get("data", [])
            if str(a.get("name", "")).startswith(("perf-agent-", "perf-pool-"))
        ]
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            break
    for agent_id in stale:
        _check(client.delete(f"/agents/{agent_id}", params={"purge": "true"}), 204, 404)
    return len(stale)


def create_agent(client: httpx.Client, name: str) -> tuple[str, str]:
    """Create an agent and mint its ``jak_`` API key; returns (agent_id, key)."""
    agent = _check(client.post("/agents", json={"name": name}), 201)
    agent_id = str(agent["id"])
    key = _check(client.post(f"/agents/{agent_id}:generate-api-key"), 200)["key"]
    return agent_id, str(key)


def opaque_token(client: httpx.Client, app_url: str, agent_id: str) -> Caller:
    """Register a JWKS on the agent and exchange a jwt-bearer assertion for ``at_``.

    The assertion audience is the configured ``auth.canonical_base_url`` (which
    wins over the request origin), not the host URL we POST to.
    """
    config = yaml.safe_load((PERF_DIR / "config.perf.yaml").read_text())
    audience = f"{config['auth']['canonical_base_url'].rstrip('/')}/oauth/token"
    private_key = Ed25519PrivateKey.generate()
    algo = OKPAlgorithm()
    pub_jwk: dict[str, Any] = json.loads(algo.to_jwk(private_key.public_key()))
    pub_jwk.update({"kid": uuid.uuid4().hex[:16], "use": "sig", "alg": "EdDSA"})
    _check(client.put(f"/agents/{agent_id}/jwks", json={"jwks": {"keys": [pub_jwk]}}), 200)
    now = int(time.time())
    assertion = jwt.encode(
        {"iss": agent_id, "aud": audience, "exp": now + 120, "jti": uuid.uuid4().hex},
        private_key,
        algorithm="EdDSA",
        headers={"kid": pub_jwk["kid"]},
    )
    body = _check(
        httpx.post(
            f"{app_url}/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
            timeout=30,
        ),
        200,
    )
    return Caller(
        kind="at",
        token=str(body["access_token"]),
        agent_id=agent_id,
        expires_at=time.time() + float(body.get("expires_in", 3600)),
    )


def hs256_caller(agent_id: str) -> Caller:
    config = yaml.safe_load((PERF_DIR / "config.perf.yaml").read_text())
    secret = config["broker"]["jwt_secret"]
    exp = int(time.time()) + JWT_TTL_S
    token = jwt.encode(
        {
            "sub": agent_id,
            "exp": exp,
            "actor_type": "agent",
            "scopes": ["capabilities:execute"],
        },
        secret,
        algorithm="HS256",
    )
    return Caller(kind="jwt", token=token, agent_id=agent_id, expires_at=float(exp))


def verify(broker_url: str, env: PerfEnv) -> None:
    """One echo per vendor through the broker; assert the credential was injected."""
    for vendor in VENDORS:
        info = env.vendors[vendor.slug]
        url = f"{broker_url}/{upstream_base(vendor.alias)}{ECHO_PATH}"
        for kind, caller in env.callers.items():
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {caller.token}"},
                content=b'{"probe": true}',
                timeout=30,
            )
            if resp.status_code != 200:
                raise ProvisionError(
                    f"verify {vendor.slug} via {kind}: {resp.status_code} {resp.text[:300]}"
                )
        echo = resp.json()
        headers = {k.lower(): v for k, v in echo.get("headers", {}).items()}
        if vendor.credential_type == "bearer_token":
            assert headers.get("authorization", "").startswith("Bearer perf-bearer-"), headers
        elif vendor.credential_type == "api_key" and vendor.location == "header":
            assert headers.get("x-api-key", "").startswith("perf-key-"), headers
        elif vendor.credential_type == "api_key" and vendor.location == "query":
            assert echo.get("query_params", {}).get("api_key", "").startswith("perf-key-"), echo
        elif vendor.credential_type == "api_key" and vendor.location == "cookie":
            assert "api_key=perf-key-" in headers.get("cookie", ""), headers
        elif vendor.credential_type == "sigv4":
            assert headers.get("authorization", "").startswith("AWS4-HMAC-SHA256"), headers
        print(f"  ok  {vendor.slug:<20} {vendor.credential_type:<12} ({info['credential_id']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app", default="http://127.0.0.1:18000", help="app URL from the host")
    parser.add_argument("--broker", default="http://127.0.0.1:18100", help="broker URL from host")
    parser.add_argument("--upstream", default="http://127.0.0.1:18084", help="harness URL")
    parser.add_argument("--pool-agents", type=int, default=10, help="extra jak_ agents")
    parser.add_argument("--out", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args(argv)

    live_spec = httpx.get(f"{args.upstream}/specs/live.json", timeout=10).json()
    with httpx.Client(base_url=args.app, timeout=60) as anon:
        token = admin_token(anon)
    with httpx.Client(
        base_url=args.app, timeout=60, headers={"Authorization": f"Bearer {token}"}
    ) as client:
        purged = purge_perf_agents(client)
        if purged:
            print(f"purged {purged} perf agents from earlier runs")
        agent_id, key = create_agent(client, f"perf-agent-{uuid.uuid4().hex[:8]}")
        env = PerfEnv(
            app_url=APP_INTERNAL,
            broker_url=BROKER_INTERNAL,
            upstream_url=upstream_base("smoke-noauth"),
            agent_id=agent_id,
            admin_token=token,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        env.callers["jak"] = Caller(kind="jak", token=key, agent_id=agent_id)
        print(f"agent {agent_id}")

        for vendor in VENDORS:
            api = ensure_api(client, live_spec, vendor)
            credential_id = replace_credential(client, vendor, api)
            bind(client, agent_id, credential_id)
            env.vendors[vendor.slug] = {
                "credential_id": credential_id,
                "api": api,
                "alias": vendor.alias,
                "credential_type": vendor.credential_type,
                "location": vendor.location,
            }

        env.callers["at"] = opaque_token(client, args.app, agent_id)
        env.callers["jwt"] = hs256_caller(agent_id)

        noauth_cred = env.vendors["perf-noauth"]["credential_id"]
        for i in range(args.pool_agents):
            pool_id, pool_key = create_agent(client, f"perf-pool-{i}-{uuid.uuid4().hex[:6]}")
            bind(client, pool_id, noauth_cred)
            env.pool_agents.append(Caller(kind="jak", token=pool_key, agent_id=pool_id))

    print("verifying every vendor x caller through the broker:")
    verify(args.broker, env)
    env.save(args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
