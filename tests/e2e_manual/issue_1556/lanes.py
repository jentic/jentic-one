"""Agent lanes and human-side helpers for the issue #1556 e2e loop.

Three agent lanes drive the same backend:

- :class:`CliLane` — the Go ``jentic`` CLI, run with an isolated ``HOME``;
- :class:`GoMcpLane` — the Go stdio MCP daemon (``jentic mcp``) over
  newline-delimited JSON-RPC, sharing a CLI-registered identity;
- :class:`HttpMcpLane` — the Python Streamable HTTP mount at ``/mcp``, with an
  agent registered here (DCR + Ed25519 jwt-bearer).

Each lane exposes the same verbs (``import_api``, ``execute``, ``connect``,
``whoami``) normalised to :class:`Result`, so a scenario runs unchanged on every
lane. :class:`Human` drives the control API as an operator would from the
browser (review, confirm, reject) — never with the agent's poll token.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import secrets
import ssl
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


@dataclass
class Env:
    app: str = os.environ.get("E2E_APP_URL", "http://127.0.0.1:55600")
    broker: str = os.environ.get("E2E_BROKER_URL", "http://127.0.0.1:55601")
    upstream: str = os.environ.get("E2E_UPSTREAM_URL", "http://127.0.0.1:55602")
    fake_as: str = os.environ.get("E2E_FAKE_AS_URL", "https://127.0.0.1:55603")
    work: Path = Path(os.environ.get("E2E_DIR", "/tmp/e2e-lanes"))
    cli: Path = Path(os.environ.get("E2E_CLI", "/tmp/e2e-lanes/jentic"))
    pg_container: str = os.environ.get("PG_CONTAINER", "pg-e2e-lanes")
    db: str = "jentic_lanes_on"


class StepError(AssertionError):
    pass


def expect(cond: bool, msg: str, detail: Any = None) -> None:
    if not cond:
        extra = "" if detail is None else f"\n    detail: {json.dumps(detail, default=str)[:3000]}"
        raise StepError(f"{msg}{extra}")


@dataclass
class Result:
    """One agent call, normalised across lanes.

    ``ok`` — the call succeeded (execute: upstream status known; connect: a
    session came back). ``status`` — HTTP status (execute) or the error status.
    ``data`` — the success payload. ``error`` — the error payload (problem
    body for the CLI, the MCP envelope for both MCP lanes).
    """

    ok: bool
    status: int | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


# ---------------------------------------------------------------------------
# Human (operator) side
# ---------------------------------------------------------------------------


class Human:
    """A browser-side user driving the control API with a login JWT."""

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

    def patch(self, path: str, body: Any) -> httpx.Response:
        return self.http.patch(path, headers=self._h(), json=body)

    def delete(self, path: str) -> httpx.Response:
        return self.http.delete(path, headers=self._h())

    def review(self, sid: str) -> httpx.Response:
        return self.get(f"/connect-sessions/{sid}")

    def confirm(self, sid: str, body: dict[str, Any]) -> httpx.Response:
        return self.post(f"/connect-sessions/{sid}:confirm", body)

    def confirm_reviewed(self, sid: str, body: dict[str, Any]) -> httpx.Response:
        """Review the session, then confirm echoing its digest and agent."""
        rv = self.review(sid)
        expect(rv.status_code == 200, f"review {sid} -> {rv.status_code}", rv.text)
        data = rv.json()
        full = {
            "digest": data["digest"],
            "expected_agent_id": (data.get("agent") or {}).get("agent_id"),
            "permission_rules": data.get("requested_permission_rules") or [],
            **body,
        }
        return self.confirm(sid, full)

    def reject(self, sid: str) -> httpx.Response:
        return self.post(f"/connect-sessions/{sid}:reject")

    def status(self, sid: str) -> httpx.Response:
        return self.get(f"/connect-sessions/{sid}/status")


def follow_authorize(env: Env, authorize_url: str, **extra: str) -> httpx.Response:
    """Play the browser: hit the fake AS, then deliver its redirect to the callback."""
    url = authorize_url
    if extra:
        url += "&" + "&".join(f"{k}={v}" for k, v in extra.items())
    ca = str(env.work / "tls" / "ca.pem")
    with httpx.Client(
        timeout=60.0, follow_redirects=False, verify=ssl.create_default_context(cafile=ca)
    ) as c:
        r = c.get(url)
        expect(r.status_code == 302, f"fake AS authorize -> {r.status_code}", r.text)
        cb = r.headers["location"]
        r2 = c.get(cb)
        return r2


def bootstrap_admin(env: Env) -> Human:
    http = httpx.Client(base_url=env.app, timeout=60.0)
    email, password = "admin@e2e.test", "Admin-Passw0rd-123!"  # pragma: allowlist secret
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


# ---------------------------------------------------------------------------
# Agent lanes
# ---------------------------------------------------------------------------


def _mcp_result_to_result(resp: dict[str, Any]) -> Result:
    """Normalise an MCP ``tools/call`` response (both mounts share the envelope)."""
    if "error" in resp:
        return Result(ok=False, error={"jsonrpc_error": resp["error"]}, raw=resp)
    res = resp["result"]
    payload: Any = res.get("structuredContent")
    if payload is None:
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        try:
            payload = json.loads(texts[0]) if texts else {}
        except ValueError:
            payload = {"text": texts}
    if res.get("isError"):
        status = payload.get("status") if isinstance(payload, dict) else None
        return Result(ok=False, status=status, error=payload, raw=resp)
    status = payload.get("status") if isinstance(payload, dict) else None
    return Result(ok=True, status=status, data=payload, raw=resp)


class Lane:
    name: str
    agent_id: str

    def import_api(self, api_id: str) -> Result:
        raise NotImplementedError

    def execute(self, target: str) -> Result:
        raise NotImplementedError

    def connect(self, **kw: Any) -> Result:
        raise NotImplementedError

    def whoami(self) -> Result:
        raise NotImplementedError

    def close(self) -> None:
        return None


class CliLane(Lane):
    """The ``jentic`` CLI with its own HOME and registered agent identity."""

    def __init__(self, env: Env, admin: Human, name: str, lane: str = "cli") -> None:
        self.env = env
        self.name = lane
        self.agent_name = name
        self.home = env.work / f"home-{name}"
        self.home.mkdir(parents=True, exist_ok=True)
        self.agent_id = self._ensure_registered(admin)

    def _env(self) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "JENTIC_"))}
        e.update({"HOME": str(self.home), "JENTIC_MODE": "agent", "NO_COLOR": "1"})
        return e

    def run(self, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
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
                "register",
                "--url",
                self.env.app,
                "--broker-url",
                self.env.broker,
                "--name",
                self.agent_name,
                "-y",
                "--timeout",
                "2m",
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
    def _json(text: str) -> Any:
        text = text.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            # The CLI may print a JSON document followed by human hints.
            dec = json.JSONDecoder()
            obj, _ = dec.raw_decode(text)
            return obj

    def import_api(self, api_id: str) -> Result:
        p = self.run("catalog", "import", api_id, "--json")
        data = self._json(p.stdout) if p.stdout.strip() else {}
        return Result(ok=p.returncode == 0, data=data, raw=p)

    def execute(self, target: str) -> Result:
        p = self.run("execute", target, "--json")
        data = self._json(p.stdout) if p.stdout.strip() else {}
        status = data.get("status") if isinstance(data, dict) else None
        if p.returncode == 0:
            return Result(ok=True, status=status, data=data, raw=p)
        body = data.get("body") if isinstance(data, dict) else None
        return Result(ok=False, status=status, error=body or data, raw=p)

    def connect(self, **kw: Any) -> Result:
        args = ["connect"]
        if kw.get("vendor"):
            args.append(kw["vendor"])
        if kw.get("api"):
            a = kw["api"]
            args += ["--api", f"{a['vendor']}/{a['name']}/{a['version']}"]
        if kw.get("auth_type"):
            args += ["--auth-type", kw["auth_type"]]
        if kw.get("rules") is not None:
            args += ["--rules", json.dumps(kw["rules"])]
        if kw.get("scopes"):
            args += ["--scopes", ",".join(kw["scopes"])]
        if kw.get("registration"):
            args += ["--registration", kw["registration"]]
        args += ["--reason", kw.get("reason", "e2e lane test")]
        p = self.run(*args)
        data = self._json(p.stdout) if p.stdout.strip() else {}
        if p.returncode == 0:
            return Result(ok=True, data=data, raw=p)
        err = self._json(p.stderr) if p.stderr.strip().startswith("{") else data
        return Result(ok=False, error=err or {"stderr": p.stderr, "stdout": p.stdout}, raw=p)

    def whoami(self) -> Result:
        p = self.run("whoami")
        return Result(ok=p.returncode == 0, data=self._json(p.stdout), raw=p)


class GoMcpLane(Lane):
    """``jentic mcp`` over stdio, using a CLI lane's HOME (its own agent)."""

    def __init__(self, cli: CliLane) -> None:
        self.cli = cli
        self.name = "gomcp"
        self.agent_id = cli.agent_id
        self.proc = cli.popen("mcp")
        self._id = 0
        self._lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "e2e-1556", "version": "1"},
            },
        )
        self._notify("notifications/initialized")

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put("")

    def _send(self, msg: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                line = self._lines.get(timeout=180)
            except queue.Empty:
                raise StepError(f"jentic mcp: no answer to {method} within 180s") from None
            if not line:
                raise StepError(
                    f"jentic mcp exited: {self.proc.stderr.read() if self.proc.stderr else ''}"
                )
            msg = json.loads(line)
            if msg.get("id") == rid:
                return dict(msg)

    def call(self, tool: str, args: dict[str, Any]) -> Result:
        return _mcp_result_to_result(self._rpc("tools/call", {"name": tool, "arguments": args}))

    def import_api(self, api_id: str) -> Result:
        return _import_until_done(lambda: self.call("import_api", {"api_id": api_id}))

    def execute(self, target: str) -> Result:
        return self.call("execute", {"operation_id": target})

    def connect(self, **kw: Any) -> Result:
        return self.call("request_connection", _mcp_connect_args(kw))

    def whoami(self) -> Result:
        return self.call("whoami", {})

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.terminate()


def _mcp_connect_args(kw: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {"reason": kw.get("reason", "e2e lane test")}
    for src, dst in (
        ("vendor", "vendor"),
        ("api", "api"),
        ("auth_type", "auth_type"),
        ("rules", "requested_permission_rules"),
        ("scopes", "requested_scopes"),
        ("registration", "oauth_app_registration_id"),
    ):
        if kw.get(src) is not None:
            args[dst] = kw[src]
    if "raw" in kw:
        args = kw["raw"]
    return args


def _import_until_done(call: Any) -> Result:
    """Re-call import_api until the job is terminal (re-importing converges)."""
    res: Result = call()
    for _ in range(30):
        if not res.ok or res.data.get("status") in ("completed", "failed"):
            return res
        time.sleep(1)
        res = call()
    return res


class HttpMcpLane(Lane):
    """The Python ``/mcp`` mount, as an agent registered via DCR here."""

    def __init__(self, env: Env, admin: Human, name: str = "httpmcp-agent") -> None:
        self.env = env
        self.name = "httpmcp"
        self.http = httpx.Client(base_url=env.app, timeout=120.0)
        self.agent_id, self.token = register_dcr_agent(env, admin, name)
        self._id = 0

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._id += 1
        r = self.http.post(
            "/mcp",
            headers={
                "authorization": f"Bearer {self.token}",
                "accept": "application/json, text/event-stream",
                "content-type": "application/json",
                "mcp-protocol-version": "2025-06-18",
            },
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

    def call(self, tool: str, args: dict[str, Any]) -> Result:
        return _mcp_result_to_result(self.rpc("tools/call", {"name": tool, "arguments": args}))

    def import_api(self, api_id: str) -> Result:
        return _import_until_done(lambda: self.call("import_api", {"api_id": api_id}))

    def execute(self, target: str) -> Result:
        return self.call("execute", {"operation_id": target})

    def connect(self, **kw: Any) -> Result:
        return self.call("request_connection", _mcp_connect_args(kw))

    def whoami(self) -> Result:
        return self.call("whoami", {})


def register_dcr_agent(env: Env, admin: Human, name: str) -> tuple[str, str]:
    """Register, approve and mint a token for a fresh agent (the UI e2e path)."""
    http = httpx.Client(base_url=env.app, timeout=60.0)
    key = Ed25519PrivateKey.generate()
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kid = f"e2e-{secrets.token_hex(4)}"
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
    token = mint_agent_token(env, agent_id, key, kid)
    return agent_id, token


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
