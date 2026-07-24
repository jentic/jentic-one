"""Fixtures for smoke/environment tests."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--base-url",
        default=None,
        help="Base URL for smoke tests (overrides BASE_URL env var)",
    )


@pytest.fixture()
def base_url(request: pytest.FixtureRequest) -> str:
    cli_val = request.config.getoption("--base-url")
    if cli_val:
        return str(cli_val)
    return os.environ.get("BASE_URL", "http://localhost:8000")


@pytest.fixture()
def mode() -> str:
    return os.environ.get("MODE", "combined")


@pytest.fixture()
def broker_url() -> str:
    """Broker URL.

    Across all Helm modes (combined, parts, broker) the broker is exposed at
    nodePort 30081 → host port 8080. BROKER_URL env var overrides; otherwise
    legacy/local-process runs (no MODE set) fall back to BASE_URL.
    """
    explicit = os.environ.get("BROKER_URL")
    if explicit:
        return explicit
    mode = os.environ.get("MODE", "")
    if mode in ("combined", "parts", "broker"):
        return "http://localhost:8080"
    return os.environ.get("BASE_URL", "http://localhost:8000")


@pytest.fixture()
def registry_url() -> str:
    """Registry URL (parts mode only — combined mode hosts under /registry on base_url)."""
    return os.environ.get("REGISTRY_URL", "http://localhost:8003")


@pytest.fixture()
def admin_url() -> str:
    """Admin URL (parts mode only — combined mode hosts under /admin on base_url)."""
    return os.environ.get("ADMIN_URL", "http://localhost:8001")


@pytest.fixture()
def control_url() -> str:
    return os.environ.get("CONTROL_URL", "http://localhost:8002")


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def authed_request(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | list[Any] | None, int]:
    """Make an HTTP request with optional Bearer auth.

    Returns (parsed_json_or_None, status_code).
    """
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode()

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status: int = resp.status
            raw = resp.read()
            if raw:
                return json.loads(raw), status
            return None, status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if raw:
            try:
                return json.loads(raw), exc.code
            except (json.JSONDecodeError, ValueError):
                pass
        return None, exc.code


# ---------------------------------------------------------------------------
# Admin auth fixture
# ---------------------------------------------------------------------------


_SMOKE_ROTATED_PASSWORD = "SmokeAdmin12345!"


def _skip_if_no_admin_surface() -> None:
    """Skip tests that need the admin/auth API when it isn't deployed.

    Broker mode deploys only the broker surface, which exposes /broker/* and
    /health but none of the admin endpoints (/auth/login, /users, ...). Tests
    that authenticate as an admin or provision users therefore can't run there.
    """
    if os.environ.get("MODE", "") == "broker":
        pytest.skip("admin/auth API not deployed in broker mode")


def _admin_login(base_url: str, email: str, password: str) -> tuple[dict[str, Any] | None, int]:
    """POST /auth/login, returning (body, status). Body is None on parse failure."""
    body, status = authed_request(
        f"{base_url}/auth/login",
        method="POST",
        body={"email": email, "password": password},
    )
    if isinstance(body, dict) or body is None:
        return body, status
    return None, status


@pytest.fixture()
def admin_token(base_url: str) -> str:
    """Authenticate as the org:admin user and return a usable access token.

    The platform boots with no credentials (no seeded admin@local). The first
    admin is created at runtime via ``POST /users:create-admin``, which self-closes
    once any user exists. This fixture is idempotent across re-runs against the
    same cluster:

    - Steady state (admin already created by a prior run): login with the smoke
      password succeeds and we return that token.
    - First run (empty cluster): login fails, so we bootstrap the admin via
      create-admin, which returns a ready-to-use token directly.

    Broker mode is skipped (admin API not deployed there).
    """
    _skip_if_no_admin_surface()
    email = os.environ.get("ADMIN_EMAIL", "admin@local")

    # Steady state: the admin already exists (created by an earlier run).
    body, status = _admin_login(base_url, email, _SMOKE_ROTATED_PASSWORD)
    if status == 200:
        assert body is not None
        return str(body["access_token"])

    # First run: no admin yet. Bootstrap one — create-admin returns a live token
    # (auto-login), so no separate sign-in or password rotation is needed.
    create_body, create_status = authed_request(
        f"{base_url}/users:create-admin",
        method="POST",
        body={"email": email, "password": _SMOKE_ROTATED_PASSWORD},
    )
    assert create_status == 200, (
        f"Admin login failed ({status}) and create-admin failed ({create_status}): {create_body}"
    )
    assert isinstance(create_body, dict)
    return str(create_body["access_token"])


# ---------------------------------------------------------------------------
# Test user lifecycle fixture
# ---------------------------------------------------------------------------


@dataclass
class SmokeUser:
    user_id: str
    email: str
    token: str
    password: str


@pytest.fixture()
def test_user(base_url: str, admin_token: str) -> Generator[SmokeUser]:
    """Create a test user, grant permissions, redeem invite, yield credentials, then clean up."""
    unique_email = f"smoke-{uuid.uuid4().hex[:12]}@test.local"
    password = "SmokeTestPass123!"

    create_body, create_status = authed_request(
        f"{base_url}/users",
        method="POST",
        token=admin_token,
        body={
            "email": unique_email,
            "first_name": "Smoke",
            "last_name": "Test",
        },
    )
    assert create_status == 201, f"User creation failed: {create_status} {create_body}"
    assert isinstance(create_body, dict)
    user_id: str = create_body["user"]["id"]
    invite_token: str = create_body["invite_token"]

    permissions = [
        "capabilities:execute",
        "toolkits:write",
        "users:read",
        "jobs:read",
        "events:read",
        "credentials:read",
        "apis:read",
        "executions:read",
    ]
    perm_body, perm_status = authed_request(
        f"{base_url}/users/{user_id}/permissions",
        method="PUT",
        token=admin_token,
        body={"permissions": permissions},
    )
    assert perm_status == 200, f"Permission grant failed: {perm_status} {perm_body}"

    redeem_body, redeem_status = authed_request(
        f"{base_url}/users:redeem-invite",
        method="POST",
        body={"invite_token": invite_token, "password": password},
    )
    assert redeem_status == 200, f"Invite redemption failed: {redeem_status} {redeem_body}"
    assert isinstance(redeem_body, dict)
    user_token: str = redeem_body["access_token"]

    yield SmokeUser(user_id=user_id, email=unique_email, token=user_token, password=password)

    authed_request(
        f"{base_url}/users/{user_id}",
        method="DELETE",
        token=admin_token,
    )


@pytest.fixture()
def user_token(test_user: SmokeUser) -> str:
    """Convenience fixture: just the token string from test_user."""
    return test_user.token


# ---------------------------------------------------------------------------
# Reachability guard
# ---------------------------------------------------------------------------


def _app_is_reachable(base_url: str) -> bool:
    """Return True if the app responds to /health."""
    try:
        with urlopen(f"{base_url}/health", timeout=3) as resp:
            return bool(200 <= resp.status < 500)
    except (URLError, OSError):
        return False


@pytest.fixture(autouse=True)
def _skip_if_unreachable(request: pytest.FixtureRequest, base_url: str) -> None:
    """Auto-skip workflow smoke tests when the app is not reachable.

    Only applies to tests in the new workflow modules (not test_env or
    test_helm_modes which handle their own reachability checks).
    """
    module_name = request.module.__name__
    exempt_modules = ("tests.smoke.test_env", "tests.smoke.test_helm_modes")
    if module_name in exempt_modules:
        return
    if not _app_is_reachable(base_url):
        pytest.skip(f"App not reachable at {base_url}")


# ---------------------------------------------------------------------------
# Shared import-and-wait helper
# ---------------------------------------------------------------------------

PETSTORE_URL = "https://petstore3.swagger.io/api/v3/openapi.json"
# The petstore spec carries no x-vendor or contact.name, so vendor can't be
# derived from its info block — supply an explicit override or the import fails
# with "cannot resolve api_identifier: missing vendor".
PETSTORE_VENDOR = "swagger"


def unique_vendor(prefix: str = "smoke") -> str:
    """A unique vendor slug so each import lands as its own API.

    Imports are keyed by (api_id, spec_digest); re-importing the identical
    petstore spec under the same vendor violates the
    uq_api_revisions_api_id_spec_digest constraint. Each test that imports must
    therefore use a distinct vendor to stay isolated from sibling tests and
    from prior runs against a persistent database.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def petstore_import_source(url: str = PETSTORE_URL, *, vendor: str | None = None) -> dict[str, Any]:
    """Build a url import source for the petstore spec with a vendor override."""
    return {"type": "url", "url": url, "vendor": vendor or PETSTORE_VENDOR}


def import_and_wait(
    base_url: str,
    token: str,
    *,
    source_url: str = PETSTORE_URL,
    vendor: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Trigger an API import and poll until the job reaches a terminal state.

    Returns the completed job body (dict with job_id, status, etc.).
    Raises AssertionError if the job fails or times out.

    A unique ``vendor`` is generated by default so repeated imports of the same
    spec don't collide on the (api_id, spec_digest) uniqueness constraint.
    """
    import_body, status = authed_request(
        f"{base_url}/apis",
        method="POST",
        token=token,
        body={"sources": [petstore_import_source(source_url, vendor=vendor or unique_vendor())]},
    )
    assert status == 202, f"Import request failed: {status} {import_body}"
    assert isinstance(import_body, dict)
    job_id: str = import_body["job_id"]

    job_body: dict[str, Any] | list[Any] | None = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job_body, job_status = authed_request(f"{base_url}/jobs/{job_id}", token=token)
        assert job_status == 200
        assert isinstance(job_body, dict)
        if job_body["status"] in ("completed", "failed"):
            break
        time.sleep(2)
    else:
        pytest.fail(f"Job {job_id} did not complete within {timeout}s")

    assert isinstance(job_body, dict)
    assert job_body["status"] == "completed", f"Job failed: {job_body}"
    job_body["job_id"] = job_id
    return job_body


# ---------------------------------------------------------------------------
# Agent (DCR) fixtures
# ---------------------------------------------------------------------------


@dataclass
class SmokeAgent:
    agent_id: str
    registration_access_token: str
    access_token: str
    owner_token: str


def generate_ed25519_jwks() -> tuple[Ed25519PrivateKey, dict[str, Any]]:
    """Generate an Ed25519 key pair and return (private_key, jwks_dict)."""
    private_key = Ed25519PrivateKey.generate()
    algo = OKPAlgorithm()
    pub_jwk: dict[str, Any] = json.loads(algo.to_jwk(private_key.public_key()))
    kid = uuid.uuid4().hex[:16]
    pub_jwk["kid"] = kid
    pub_jwk["use"] = "sig"
    pub_jwk["alg"] = "EdDSA"
    jwks = {"keys": [pub_jwk]}
    return private_key, jwks


def register_agent(base_url: str, client_name: str, jwks: dict[str, Any]) -> tuple[str, str]:
    """POST /register → (agent_id, registration_access_token)."""
    body, status = authed_request(
        f"{base_url}/register",
        method="POST",
        body={"client_name": client_name, "jwks": jwks},
    )
    assert status == 201, f"Agent registration failed: {status} {body}"
    assert isinstance(body, dict)
    return body["client_id"], body["registration_access_token"]


def approve_agent(base_url: str, agent_id: str, admin_token: str) -> None:
    """POST /agents/{agent_id}:approve using admin token."""
    _, status = authed_request(
        f"{base_url}/agents/{agent_id}:approve",
        method="POST",
        token=admin_token,
    )
    assert status == 200, f"Agent approval failed: {status}"


def agent_token_exchange(base_url: str, agent_id: str, private_key: Ed25519PrivateKey) -> str:
    """Build a JWT assertion and exchange it for an access token."""
    now = int(time.time())
    payload = {
        "iss": agent_id,
        "aud": f"{base_url}/oauth/token",
        "exp": now + 120,
        "jti": uuid.uuid4().hex,
    }
    algo = OKPAlgorithm()
    priv_jwk: dict[str, Any] = json.loads(algo.to_jwk(private_key))
    assertion = pyjwt.encode(payload, pyjwt.api_jwk.PyJWK(priv_jwk, "EdDSA").key, algorithm="EdDSA")

    body, status = authed_request(
        f"{base_url}/oauth/token",
        method="POST",
        body={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    assert status == 200, f"Token exchange failed: {status} {body}"
    assert isinstance(body, dict)
    access_token: str = body["access_token"]
    return access_token


@pytest.fixture()
def test_agent(base_url: str, admin_token: str) -> Generator[SmokeAgent]:
    """Register, approve, and token-exchange an agent; archive on teardown."""
    client_name = f"smoke-agent-{uuid.uuid4().hex[:12]}"
    private_key, jwks = generate_ed25519_jwks()

    agent_id, rat = register_agent(base_url, client_name, jwks)
    approve_agent(base_url, agent_id, admin_token)
    access_token = agent_token_exchange(base_url, agent_id, private_key)

    yield SmokeAgent(
        agent_id=agent_id,
        registration_access_token=rat,
        access_token=access_token,
        owner_token=admin_token,
    )

    authed_request(
        f"{base_url}/agents/{agent_id}",
        method="DELETE",
        token=admin_token,
    )


@pytest.fixture()
def agent_with_toolkit(base_url: str, test_agent: SmokeAgent) -> Generator[tuple[SmokeAgent, str]]:
    """Create a toolkit, bind it to the test agent, yield, then unbind and delete."""
    toolkit_name = f"smoke-toolkit-{uuid.uuid4().hex[:12]}"
    create_body, status = authed_request(
        f"{base_url}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"name": toolkit_name},
    )
    assert status == 201, f"Toolkit creation failed: {status} {create_body}"
    assert isinstance(create_body, dict)
    toolkit_id: str = create_body["toolkit"]["toolkit_id"]

    bind_body, bind_status = authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/toolkits",
        method="POST",
        token=test_agent.owner_token,
        body={"toolkit_id": toolkit_id},
    )
    assert bind_status == 201, f"Toolkit bind failed: {bind_status} {bind_body}"

    yield test_agent, toolkit_id

    authed_request(
        f"{base_url}/agents/{test_agent.agent_id}/toolkits/{toolkit_id}",
        method="DELETE",
        token=test_agent.owner_token,
    )
    authed_request(
        f"{base_url}/toolkits/{toolkit_id}",
        method="DELETE",
        token=test_agent.owner_token,
    )


# ---------------------------------------------------------------------------
# Smoke-upstream harness fixtures & helpers
# ---------------------------------------------------------------------------
#
# The broker reaches the harness via in-cluster DNS; that same host is baked
# into the ingested spec's servers[].url, so the broker proxy path must use it.
# The test runner reaches the harness directly via the kind host port (for
# sanity probes / inspecting echoed requests). Both are env-overridable so
# local-process runs work too.

SMOKE_UPSTREAM_SPEC_PATH = "/specs/live.json"


@pytest.fixture()
def upstream_incluster_url() -> str:
    """The harness URL as the broker sees it (in-cluster DNS).

    This is the host baked into the ingested spec's servers[].url and therefore
    the prefix the broker proxy path must use.
    """
    return os.environ.get("UPSTREAM_INCLUSTER_URL", "http://jentic-smoke-upstream:8084")


@pytest.fixture(scope="session")
def upstream_direct_url() -> str:
    """The harness URL as the test runner sees it (kind host port)."""
    return os.environ.get("UPSTREAM_DIRECT_URL", "http://localhost:8084")


@pytest.fixture(scope="session")
def _harness_deployed(upstream_direct_url: str) -> bool:
    """Probe the harness once per session from the test-runner side.

    Probes the live-spec path itself (the harness serves no /health): if the
    spec isn't fetchable, ingest cannot succeed either.
    """
    try:
        with urlopen(f"{upstream_direct_url}{SMOKE_UPSTREAM_SPEC_PATH}", timeout=3) as resp:
            return bool(200 <= resp.status < 300)
    except (URLError, OSError):
        return False


@pytest.fixture()
def broker_upstream_timeout_s() -> float:
    """The broker's configured upstream timeout (BrokerResilienceConfig default 30s).

    Env-overridable so a smoke overlay that lowers ``broker.upstream_timeout_s``
    keeps the slow-upstream test fast and correct.
    """
    return float(os.environ.get("BROKER_UPSTREAM_TIMEOUT_S", "30"))


def ingest_harness(
    base_url: str,
    token: str,
    incluster_url: str,
    *,
    vendor: str,
    timeout: int = 60,
) -> dict[str, Any]:
    """Import the harness live spec by URL and poll until the job completes.

    Thin wrapper over ``import_and_wait`` pointing at the harness's in-cluster
    live spec. The vendor is explicit (not random inside) so the caller can
    reuse it when provisioning credentials — broker credential resolution is
    keyed on the API vendor. Returns the completed job body (``job_id`` stamped).
    """
    return import_and_wait(
        base_url,
        token,
        source_url=f"{incluster_url.rstrip('/')}{SMOKE_UPSTREAM_SPEC_PATH}",
        vendor=vendor,
        timeout=timeout,
    )


@dataclass
class HarnessApi:
    vendor: str
    name: str
    version: str
    job_id: str


@pytest.fixture()
def harness_api(
    base_url: str,
    test_agent: SmokeAgent,
    upstream_incluster_url: str,
    _harness_deployed: bool,
) -> HarnessApi:
    """Ingest the harness live spec as the agent; return its API identity.

    Uses a unique vendor per test so each run lands as its own API (avoiding the
    uq_api_revisions_api_id_spec_digest collision). The same vendor is returned
    so credential provisioning can reuse it — broker credential resolution is
    keyed on (vendor, name, version).

    Every harness-dependent smoke test funnels through this fixture, so the
    deployment probe here gates them all: the smoke-upstream harness is not yet
    built/deployed by the CI flow (tools.deploy ci-smoke), and without it every
    ingest job dead-letters on DNS resolution after ~60s. Skipping keeps the
    matrix honest until the harness ships as a deployable image.
    """
    if not _harness_deployed:
        pytest.skip("smoke-upstream harness not deployed (no live spec at UPSTREAM_DIRECT_URL)")
    vendor = unique_vendor("smoke-upstream")
    job = ingest_harness(base_url, test_agent.access_token, upstream_incluster_url, vendor=vendor)

    result, status = authed_request(
        f"{base_url}/jobs/{job['job_id']}/result", token=test_agent.access_token
    )
    assert status == 200, f"Job result fetch failed: {status} {result}"
    assert isinstance(result, dict)
    api_ref = result["revisions"][0]["api"]
    return HarnessApi(
        vendor=api_ref["vendor"],
        name=api_ref["name"],
        version=api_ref["version"],
        job_id=job["job_id"],
    )


def broker_call(
    broker_url: str,
    upstream_url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    client_timeout: float = 30.0,
) -> tuple[bytes, int, dict[str, str]]:
    """Call an upstream operation through the broker proxy.

    The full upstream URL (in-cluster host) is appended to the broker base; the
    broker reconstructs it byte-exact and forwards. Returns
    ``(raw_body, status, response_headers)``.

    Unlike ``authed_request``: no forced Content-Type, raw byte body, header
    passthrough (``X-Mock-*``, ``Jentic-Toolkit-Id``, …), and it returns the
    response headers (needed for pagination ``Link`` / lifecycle ``Sunset``).

    ``client_timeout`` bounds the *test runner's* socket wait; raise it above the
    broker's own upstream timeout when driving a slow-upstream test so the broker
    (not urllib) is what times out and emits its 504 envelope.
    """
    proxied = f"{broker_url.rstrip('/')}/{upstream_url}"
    req = urllib.request.Request(proxied, data=body, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=client_timeout) as resp:
            return resp.read(), resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        # Non-2xx broker envelopes (403/409/424/…) carry a JSON body the caller
        # parses; surface body + code rather than raising.
        return exc.read(), exc.code, dict(exc.headers.items())


def provision_toolkit_and_credential(
    base_url: str,
    agent: SmokeAgent,
    *,
    credential_body: dict[str, Any],
    bind_credential_to_toolkit: bool = True,
) -> tuple[str, str]:
    """Bind a fresh toolkit to the agent and create a credential.

    Returns ``(toolkit_id, credential_id)``.

    The agent↔toolkit binding is required for ``select_toolkit``; the credential
    is required for ``inject``. Binding the credential to the toolkit is optional
    for injection (resolution is keyed on the API tuple + ``active``) — kept on
    by default to mirror the documented happy path.
    """
    tk, st = authed_request(
        f"{base_url}/toolkits",
        method="POST",
        token=agent.owner_token,
        body={"name": f"smoke-tk-{uuid.uuid4().hex[:12]}"},
    )
    assert st == 201 and isinstance(tk, dict), f"Toolkit creation failed: {st} {tk}"
    toolkit_id = tk["toolkit"]["toolkit_id"]

    _, st = authed_request(
        f"{base_url}/agents/{agent.agent_id}/toolkits",
        method="POST",
        token=agent.owner_token,
        body={"toolkit_id": toolkit_id},
    )
    assert st == 201, f"Toolkit bind failed: {st}"

    cred, st = authed_request(
        f"{base_url}/credentials",
        method="POST",
        token=agent.owner_token,
        body=credential_body,
    )
    assert st == 201 and isinstance(cred, dict), f"Credential creation failed: {st} {cred}"
    credential_id = cred["credential"]["credential_id"]

    if bind_credential_to_toolkit:
        _, st = authed_request(
            f"{base_url}/toolkits/{toolkit_id}/credentials",
            method="POST",
            token=agent.owner_token,
            body={"credential_id": credential_id},
        )
        assert st == 201, f"Credential→toolkit bind failed: {st}"
    return toolkit_id, credential_id


@dataclass
class ExecutableHarness:
    """An ingested harness API the agent can execute through the broker.

    Bundles the API identity with the toolkit binding and bearer credential so a
    proxied op passes the full broker pipeline: discovery → select_toolkit →
    credential inject. ``bearer_token`` is the secret the broker injects upstream;
    ``agent_token`` is the caller token broker auth expects on the proxy request.
    """

    api: HarnessApi
    toolkit_id: str
    credential_id: str
    bearer_token: str
    agent_token: str


@pytest.fixture()
def executable_harness(
    base_url: str,
    test_agent: SmokeAgent,
    harness_api: HarnessApi,
) -> ExecutableHarness:
    """Ingested harness + a bound toolkit + an active bearer credential.

    The minimum wiring for an execute-through-broker test: every proxied op
    (secured or not) needs an active credential for the API tuple, and the agent
    must be bound to exactly one toolkit for the API.
    """
    bearer_token = f"smoke-bearer-{uuid.uuid4().hex[:16]}"
    toolkit_id, credential_id = provision_toolkit_and_credential(
        base_url,
        test_agent,
        credential_body={
            "type": "bearer_token",
            "name": f"smoke-cred-{uuid.uuid4().hex[:8]}",
            "api": {
                "vendor": harness_api.vendor,
                "name": harness_api.name,
                "version": harness_api.version,
            },
            "provider": "static",
            "token": bearer_token,
        },
    )
    return ExecutableHarness(
        api=harness_api,
        toolkit_id=toolkit_id,
        credential_id=credential_id,
        bearer_token=bearer_token,
        agent_token=test_agent.access_token,
    )
