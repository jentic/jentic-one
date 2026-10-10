"""Agent lanes and human-side helpers for the ask-tier e2e loop.

Adapted from the issue #1556 e2e lanes harness. Three agent lanes drive the
same backend:

- :class:`CliLane` — the Go ``jentic`` CLI built from this checkout, run with
  an isolated ``HOME`` and its own registered agent;
- :class:`GoMcpLane` — the Go stdio MCP daemon (``jentic mcp``) over
  newline-delimited JSON-RPC, on a CLI lane's identity;
- :class:`HttpMcpLane` — the Python Streamable HTTP mount at ``/mcp``, as an
  agent registered here (DCR + Ed25519 jwt-bearer).

:class:`Human` drives the admin/control API as an operator would from the
browser (review, decide). Nothing here reaches into the database except
:func:`psql`, which scenarios use only to read state or to fast-forward a
clock (an approval's ``expires_at``).
"""

from __future__ import annotations

import base64
import json
import os
import queue
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CAPS_META_KEY = "io.modelcontextprotocol/clientCapabilities"
VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
LEGACY_VERSION = "2025-06-18"
MODERN_VERSION = "2026-07-28"


@dataclass
class Env:
    app: str = os.environ.get("E2E_APP_URL", "http://127.0.0.1:55521")
    broker: str = os.environ.get("E2E_BROKER_URL", "http://127.0.0.1:55522")
    upstream: str = os.environ.get("E2E_UPSTREAM_URL", "http://127.0.0.1:55523")
    work: Path = Path(os.environ.get("E2E_DIR", "/tmp/ask-e2e"))
    pg_container: str = os.environ.get("PG_CONTAINER", "pg-ask-tier")
    db: str = os.environ.get("E2E_DB", "jentic_ask")

    @property
    def cli(self) -> Path:
        return Path(os.environ.get("E2E_CLI", str(self.work / "jentic")))


class StepError(AssertionError):
    pass


def expect(cond: bool, msg: str, detail: Any = None) -> None:
    if not cond:
        extra = "" if detail is None else f"\n    detail: {json.dumps(detail, default=str)[:3000]}"
        raise StepError(f"{msg}{extra}")


# ---------------------------------------------------------------------------
# Human (operator) side
# ---------------------------------------------------------------------------


class Human:
    """A browser-side user driving the API with a login JWT."""

    def __init__(self, env: Env, token: str, user_id: str, email: str) -> None:
        self.env = env
        self.token = token
        self.user_id = user_id
        self.email = email
        self.http = httpx.Client(base_url=env.app, timeout=60.0)

    def _h(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.http.get(path, headers=self._h(), **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> httpx.Response:
        return self.http.post(path, headers=self._h(), json=body, **kw)

    def put(self, path: str, body: Any) -> httpx.Response:
        return self.http.put(path, headers=self._h(), json=body)

    def patch(self, path: str, body: Any) -> httpx.Response:
        return self.http.patch(path, headers=self._h(), json=body)

    # -- approvals ---------------------------------------------------------

    def approvals(self, **params: Any) -> list[dict[str, Any]]:
        r = self.get("/executions/approvals", params=params)
        expect(r.status_code == 200, f"list approvals -> {r.status_code}", r.text)
        return list(r.json()["data"])

    def approval(self, approval_id: str) -> httpx.Response:
        return self.get(f"/executions/approvals/{approval_id}")

    def decide(self, approval_id: str, decision: str, reason: str | None = None) -> httpx.Response:
        body: dict[str, Any] = {"decision": decision}
        if reason is not None:
            body["reason"] = reason
        return self.post(f"/executions/approvals/{approval_id}:decide", body)


def bootstrap_admin(env: Env) -> Human:
    http = httpx.Client(base_url=env.app, timeout=60.0)
    email, password = "admin@ask-e2e.test", "Admin-Passw0rd-123!"  # pragma: allowlist secret
    health = http.get("/admin/health").json()
    if health.get("setup_required"):
        r = http.post(
            "/users:create-admin",
            json={"email": email, "password": password, "first_name": "Ada", "last_name": "Admin"},
        )
        expect(r.status_code == 200, "create-admin", r.text)
    r = http.post("/auth/login", json={"email": email, "password": password})
    expect(r.status_code == 200, "admin login", r.text)
    token = r.json()["access_token"]
    me = http.get("/users/me", headers={"authorization": f"Bearer {token}"}).json()
    return Human(env, token, me["id"], email)


def make_user(env: Env, admin: Human, email: str, permissions: list[str]) -> Human:
    password = "User-Passw0rd-123!"  # pragma: allowlist secret
    http = httpx.Client(base_url=env.app, timeout=60.0)
    r = http.post("/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        r = admin.post(
            "/users",
            {
                "email": email,
                "first_name": email.split("@")[0],
                "last_name": "E2e",
                "permissions": permissions,
            },
        )
        expect(r.status_code == 201, f"create user {email}", r.text)
        invite = r.json()["invite_token"]
        r = http.post("/users:redeem-invite", json={"invite_token": invite, "password": password})
        expect(r.status_code == 200, f"redeem invite {email}", r.text)
    token = r.json()["access_token"]
    me = http.get("/users/me", headers={"authorization": f"Bearer {token}"}).json()
    return Human(env, token, me["id"], email)


def psql(env: Env, sql: str) -> str:
    out = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            env.pg_container,
            "psql",
            "-U",
            "postgres",
            "-d",
            env.db,
            "-tAc",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def upstream_calls(env: Env) -> list[dict[str, Any]]:
    return list(httpx.get(f"{env.upstream}/_calls", timeout=10).json())


def reset_upstream(env: Env) -> None:
    httpx.post(f"{env.upstream}/_reset", timeout=10)


# ---------------------------------------------------------------------------
# Agent lanes
# ---------------------------------------------------------------------------


def mcp_payload(resp: dict[str, Any]) -> tuple[bool, Any]:
    """``(is_error, payload)`` of an MCP ``tools/call`` response."""
    if "error" in resp:
        return True, {"jsonrpc_error": resp["error"]}
    res = resp["result"]
    if res.get("resultType") == "input_required" or "inputRequests" in res:
        return False, res
    payload: Any = res.get("structuredContent")
    if payload is None:
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        try:
            payload = json.loads(texts[0]) if texts else {}
        except ValueError:
            payload = {"text": texts}
    return bool(res.get("isError")), payload


class CliLane:
    """The ``jentic`` CLI with its own HOME and registered agent identity."""

    def __init__(self, env: Env, admin: Human, name: str) -> None:
        self.env = env
        self.agent_name = name
        self.home = env.work / f"home-{name}"
        self.home.mkdir(parents=True, exist_ok=True)
        self.agent_id = self._ensure_registered(admin)

    def _env(self) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "JENTIC_"))}
        e.update({"HOME": str(self.home), "JENTIC_MODE": "agent", "NO_COLOR": "1"})
        return e

    def run(self, *args: str, timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.env.cli), *args],
            env=self._env(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def popen(self, *args: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [str(self.env.cli), *args],
            env=self._env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _ensure_registered(self, admin: Human) -> str:
        who = self.run("whoami")
        if who.returncode == 0:
            try:
                return str(json.loads(who.stdout)["id"])
            except (ValueError, KeyError):
                pass
        proc = subprocess.Popen(
            [
                str(self.env.cli),
                *("register", "--url", self.env.app, "--broker-url", self.env.broker),
                *("--name", self.agent_name, "-y", "--timeout", "2m"),
            ],
            env=self._env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        agent_id = None
        deadline = time.time() + 60
        while time.time() < deadline and agent_id is None:
            rows = admin.get("/agents", params={"limit": 200}).json()["data"]
            for row in rows:
                if row["name"] == self.agent_name and row["status"] == "pending":
                    agent_id = row["id"]
            time.sleep(0.5)
        expect(agent_id is not None, f"{self.agent_name}: pending agent never appeared")
        r = admin.post(f"/agents/{agent_id}:approve")
        expect(r.status_code == 200, "approve agent", r.text)
        out, _ = proc.communicate(timeout=120)
        expect(proc.returncode == 0, f"{self.agent_name}: register failed", out)
        return str(agent_id)

    @staticmethod
    def parse(text: str) -> Any:
        text = text.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            obj, _ = json.JSONDecoder().raw_decode(text)
            return obj


class GoMcpLane:
    """``jentic mcp`` over stdio, using a CLI lane's HOME (its own agent).

    ``capabilities`` are the client capabilities declared at ``initialize``
    (e.g. ``{"elicitation": {"url": {}}}`` for URL elicitation).
    """

    def __init__(self, cli: CliLane, capabilities: dict[str, Any] | None = None) -> None:
        self.cli = cli
        self.agent_id = cli.agent_id
        self.proc = cli.popen("mcp")
        self._id = 0
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self.init = self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": capabilities or {},
                "clientInfo": {"name": "ask-e2e", "version": "1"},
            },
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put("")

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def rpc(self, method: str, params: dict[str, Any], timeout: float = 180) -> dict[str, Any]:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                line = self._lines.get(timeout=timeout)
            except queue.Empty:
                raise StepError(f"jentic mcp: no answer to {method} within {timeout}s") from None
            if not line:
                err = self.proc.stderr.read() if self.proc.stderr else ""
                raise StepError(f"jentic mcp exited: {err}")
            msg = json.loads(line)
            if msg.get("id") == rid:
                return dict(msg)
            if "method" in msg and "id" in msg:
                # A server-to-client request (e.g. elicitation/create): decline.
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"action": "decline"}})

    def call(self, tool: str, args: dict[str, Any], **params: Any) -> dict[str, Any]:
        return self.rpc("tools/call", {"name": tool, "arguments": args, **params})

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.terminate()


class HttpMcpLane:
    """The Python ``/mcp`` mount, as an agent registered via DCR here."""

    def __init__(self, env: Env, admin: Human, name: str) -> None:
        self.env = env
        self.http = httpx.Client(base_url=env.app, timeout=180.0)
        self.agent_id, self.token = register_dcr_agent(env, admin, name)
        self._id = 0

    def rpc(
        self, method: str, params: dict[str, Any], version: str = LEGACY_VERSION
    ) -> dict[str, Any]:
        self._id += 1
        headers = {
            "authorization": f"Bearer {self.token}",
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "mcp-protocol-version": version,
        }
        if version == MODERN_VERSION:
            # The 2026-07-28 wire repeats the routing fields as headers.
            headers["mcp-method"] = method
            if isinstance(params.get("name"), str):
                headers["mcp-name"] = params["name"]
        r = self.http.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
        )
        expect(r.status_code == 200, f"/mcp {method} -> {r.status_code}", r.text)
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    msg = json.loads(line[5:].strip())
                    if msg.get("id") == self._id:
                        return dict(msg)
            raise StepError(f"no response in SSE stream: {r.text[:500]}")
        return dict(r.json())

    def call(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        caps: dict[str, Any] | None = None,
        modern: bool = False,
        **params: Any,
    ) -> dict[str, Any]:
        """``tools/call``; ``modern`` speaks the 2026-07-28 per-request envelope."""
        p: dict[str, Any] = {"name": tool, "arguments": args, **params}
        meta: dict[str, Any] = {}
        if caps is not None:
            meta[CAPS_META_KEY] = caps
        if modern:
            meta[VERSION_META_KEY] = MODERN_VERSION
            meta[CLIENT_INFO_META_KEY] = {"name": "ask-e2e", "version": "1"}
            meta.setdefault(CAPS_META_KEY, {})
        if meta:
            p["_meta"] = meta
        return self.rpc("tools/call", p, MODERN_VERSION if modern else LEGACY_VERSION)


def register_dcr_agent(env: Env, admin: Human, name: str) -> tuple[str, str]:
    """Register, approve and mint a token for a fresh agent (the UI e2e path)."""
    http = httpx.Client(base_url=env.app, timeout=60.0)
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kid = f"ask-{secrets.token_hex(4)}"
    jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
        "kid": kid,
        "alg": "EdDSA",
        "use": "sig",
    }
    r = http.post("/register", json={"client_name": name, "jwks": {"keys": [jwk]}})
    expect(r.status_code == 201, "DCR register", r.text)
    agent_id = r.json()["client_id"]
    r = admin.post(f"/agents/{agent_id}:approve")
    expect(r.status_code == 200, "approve DCR agent", r.text)
    return agent_id, mint_agent_token(env, agent_id, key, kid)


def mint_agent_token(env: Env, agent_id: str, key: Ed25519PrivateKey, kid: str) -> str:
    http = httpx.Client(base_url=env.app, timeout=60.0)
    token_endpoint = http.get("/.well-known/oauth-authorization-server").json()["token_endpoint"]
    now = int(time.time())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    assertion = jwt.encode(
        {
            "iss": agent_id,
            "sub": agent_id,
            "aud": token_endpoint,
            "jti": secrets.token_hex(8),
            "iat": now,
            "exp": now + 120,
        },
        pem,
        algorithm="EdDSA",
        headers={"kid": kid},
    )
    r = http.post("/oauth/token", json={"grant_type": JWT_BEARER, "assertion": assertion})
    expect(r.status_code == 200, "jwt-bearer mint", r.text)
    return str(r.json()["access_token"])


@dataclass
class ScenarioResult:
    name: str
    status: str  # pass | fail | gap
    notes: list[str] = field(default_factory=list)
