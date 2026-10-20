"""End to end: the governed git pipeline, driven by a real ``git`` client.

git talks to the real broker app (served by uvicorn) at the CLI proxy route
(``/_facade/proxy/https://github.com/<owner>/<repo>.git``) with a GitHub
invocation token minted through the real ``CliInvocationService``. The
broker's git upstream is pointed (in this test only) at a local stand-in for
github.com: ``git http-backend`` over bare repositories, which only accepts
git's Basic auth with the stored token, plus a stub of GitHub's compare API
answered from the same repositories with ``git merge-base``.

The binding carries the rules the pipeline is meant to express: fetch
``jentic/jentic-one-internal``; push only to ``refs/heads/jentic-e2e/*`` (and
create tags); force only on ``refs/heads/jentic-e2e/scratch/*``; plus a REST
path rule allowing everything, which must never let git through.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import zlib
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
import pytest
import uvicorn
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_permission_grants import ActorPermissionGrant
from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.cli_invocations import CliInvocation
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import ActorPermissionGrantRepository, AgentRepository, UserRepository
from jentic_one.auth.services.cli_invocation_service import CliInvocationService
from jentic_one.auth.services.schemas.cli_invocations import CliInvocationMintPayload
from jentic_one.auth.services.token_service import TokenService
from jentic_one.broker.adapters.runners.git import GitUpstream
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.broker.web.deps import get_git_upstream
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.config import CliFacadesConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorStatus, ActorType, StoredCredentialType
from jentic_one.shared.models.events import EventType
from jentic_one.wiring import install_broker_registry_resolver

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("git") is None, reason="needs a git client"),
]

_SEED_MARKER = "usr_git_pipeline_seed"
_OWNER_ID = "usr_git_pipeline_owner"
_OWNER = "jentic"
_REPO = "jentic-one-internal"
_SECRET = "ghp-git-pipeline-secret"  # pragma: allowlist secret
_GIT_ENV_BASE = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "Agent",
    "GIT_AUTHOR_EMAIL": "agent@example.test",
    "GIT_COMMITTER_NAME": "Agent",
    "GIT_COMMITTER_EMAIL": "agent@example.test",
    "LC_ALL": "C",
}


# ── the github.com stand-in ──────────────────────────────────────────────────


@dataclass
class _Seen:
    method: str
    path: str
    authorization: str
    user_agent: str
    git_protocol: str | None
    body_bytes: int


@dataclass
class _StandIn:
    root: Path
    seen: list[_Seen] = field(default_factory=list)
    compares: list[tuple[str, str]] = field(default_factory=list)
    port: int = 0

    def repo(self, owner: str = _OWNER, repo: str = _REPO) -> Path:
        return self.root / owner / f"{repo}.git"

    def ref(self, name: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.repo()), "rev-parse", "--verify", "--quiet", name],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() or None


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    if handler.headers.get("Transfer-Encoding", "").lower() == "chunked":
        out = bytearray()
        while True:
            size = int(handler.rfile.readline().split(b";")[0].strip(), 16)
            if size == 0:
                handler.rfile.readline()
                return bytes(out)
            out += handler.rfile.read(size)
            handler.rfile.readline()
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length) if length else b""


def _handler_for(state: _StandIn) -> type[BaseHTTPRequestHandler]:
    expected_basic = "Basic " + base64.b64encode(f"x-access-token:{_SECRET}".encode()).decode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, status: int, body: bytes, headers: dict[str, str]) -> None:
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            body = _read_body(self)
            parts = urlsplit(self.path)
            state.seen.append(
                _Seen(
                    method=method,
                    path=parts.path,
                    authorization=self.headers.get("Authorization", ""),
                    user_agent=self.headers.get("User-Agent", ""),
                    git_protocol=self.headers.get("Git-Protocol"),
                    body_bytes=len(body),
                )
            )
            if parts.path.startswith("/_api/"):
                self._compare(parts.path)
                return
            if self.headers.get("Authorization") != expected_basic:
                self._send(401, b"auth required\n", {"WWW-Authenticate": 'Basic realm="gh"'})
                return
            env = {
                "PATH": os.environ["PATH"],
                "GIT_PROJECT_ROOT": str(state.root),
                "GIT_HTTP_EXPORT_ALL": "1",
                "REMOTE_USER": "x-access-token",
                "REQUEST_METHOD": method,
                "PATH_INFO": unquote(parts.path),
                "QUERY_STRING": parts.query,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(len(body)),
                "GIT_CONFIG_NOSYSTEM": "1",
            }
            if self.headers.get("Content-Encoding"):
                env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
            result = subprocess.run(
                ["git", "http-backend"], input=body, capture_output=True, env=env, check=False
            )
            raw_headers, _, payload = result.stdout.partition(b"\r\n\r\n")
            status = 200
            headers: dict[str, str] = {}
            for line in raw_headers.decode("latin-1").split("\r\n"):
                if not line:
                    continue
                key, _, value = line.partition(":")
                if key.lower() == "status":
                    status = int(value.strip().split()[0])
                else:
                    headers[key.strip()] = value.strip()
            self._send(status, payload, headers)

        def _compare(self, path: str) -> None:
            if self.headers.get("Authorization") != f"Bearer {_SECRET}":
                self._send(401, b"{}", {"Content-Type": "application/json"})
                return
            # /_api/repos/<owner>/<repo>/compare/<base>...<head>
            segments = path.split("/")
            owner, repo, spec = segments[3], segments[4], segments[6]
            base, _, head = spec.partition("...")
            state.compares.append((base, head))
            git_dir = state.root / owner / f"{repo}.git"
            for sha in (base, head):
                exists = subprocess.run(
                    ["git", "-C", str(git_dir), "cat-file", "-e", f"{sha}^{{commit}}"],
                    capture_output=True,
                    check=False,
                )
                if exists.returncode != 0:
                    self._send(
                        404, b'{"message":"Not Found"}', {"Content-Type": "application/json"}
                    )
                    return

            def is_ancestor(a: str, b: str) -> bool:
                return (
                    subprocess.run(
                        ["git", "-C", str(git_dir), "merge-base", "--is-ancestor", a, b],
                        capture_output=True,
                        check=False,
                    ).returncode
                    == 0
                )

            if base == head:
                status = "identical"
            elif is_ancestor(base, head):
                status = "ahead"
            elif is_ancestor(head, base):
                status = "behind"
            else:
                status = "diverged"
            body = json.dumps({"base_commit": {"sha": base}, "status": status}).encode()
            self._send(200, body, {"Content-Type": "application/json"})

    return Handler


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env={**os.environ, **_GIT_ENV_BASE, **(env or {})},
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _seed_upstream(root: Path) -> None:
    """A bare repo: main, jentic-e2e/base, jentic-e2e/scratch/x, tag v1, a refs/jentic ref."""
    work = root / "_seed"
    work.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=work)
    (work / "README.md").write_text("hello\n")
    _git("add", ".", cwd=work)
    _git("commit", "-q", "-m", "first", cwd=work)
    (work / "README.md").write_text("hello again\n")
    _git("commit", "-q", "-am", "second", cwd=work)
    _git("tag", "v1", cwd=work)
    bare = root / _OWNER / f"{_REPO}.git"
    bare.parent.mkdir(parents=True)
    _git("clone", "-q", "--bare", str(work), str(bare))
    _git("-C", str(bare), "update-ref", "refs/heads/jentic-e2e/base", "main")
    _git("-C", str(bare), "update-ref", "refs/heads/jentic-e2e/scratch/x", "main")
    _git("-C", str(bare), "update-ref", "refs/jentic/hidden", "main~1")
    _git("-C", str(bare), "config", "http.receivepack", "true")
    # Makes receive-pack advertise push-cert, which the broker must withhold.
    _git("-C", str(bare), "config", "receive.certNonceSeed", "jentic-test-seed")
    shutil.rmtree(work)


# ── fixtures ─────────────────────────────────────────────────────────────────


@dataclass
class _Env:
    ctx: Context
    standin: _StandIn
    broker_url: str
    agent_id: str
    credential_id: str
    workdir: Path
    token: str = ""
    trace_id: str = ""

    @property
    def remote(self) -> str:
        return f"{self.broker_url}/_facade/proxy/https://github.com/{_OWNER}/{_REPO}.git"

    def git_env(self) -> dict[str, str]:
        return {"HOME": str(self.workdir / "home")}

    async def git(self, *args: str, cwd: Path | None = None) -> tuple[int, str]:
        """Run git with the invocation token on every request; returns (exit code, stderr)."""
        process = await asyncio.create_subprocess_exec(
            "git",
            "-c",
            f"http.extraHeader=Authorization: Bearer {self.token}",
            *args,
            cwd=cwd,
            env={**os.environ, **_GIT_ENV_BASE, **self.git_env()},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _out, err = await process.communicate()
        return process.returncode or 0, err.decode()


@pytest.fixture()
def git_ctx(integration_context: Context) -> Iterator[Context]:
    config = integration_context.config
    prior = (config.cli_facades, config.broker.rule_cache_ttl_s, config.broker.toolkit_cache_ttl_s)
    config.cli_facades = CliFacadesConfig.model_validate(
        {"enabled": True, "vendors": {"github": {"enabled": True, "host": "github.jentic.test"}}}
    )
    config.broker.rule_cache_ttl_s = 0
    config.broker.toolkit_cache_ttl_s = 0
    yield integration_context
    config.cli_facades, config.broker.rule_cache_ttl_s, config.broker.toolkit_cache_ttl_s = prior


async def _truncate(ctx: Context) -> None:
    async with ctx.admin_db.transaction() as session:
        await session.execute(delete(Event))
        await session.execute(delete(ExecutionRecord))
        await session.execute(delete(CliInvocation))
        await session.execute(delete(AccessToken))
        await session.execute(delete(RefreshToken))
        await session.execute(delete(AgentCredentialBinding))
        await session.execute(delete(ActorPermissionGrant))
        await session.execute(delete(Agent).where(Agent.created_by == _SEED_MARKER))
        await session.execute(delete(User).where(User.created_by == _SEED_MARKER))
    async with ctx.control_db.session() as session:
        await session.execute(delete(AgentPermissionRule))
        await session.execute(delete(TokenValueCredential))
        await session.execute(delete(Credential))
        await session.commit()


_RULES: list[dict[str, Any]] = [
    # A REST rule of the same credential: it must never decide a git request.
    {"effect": "allow", "methods": ["GET", "POST"], "path": ".*", "match_mode": "regex"},
    {"effect": "deny", "path": "force:jentic/jentic-one-internal:refs/heads/main"},
    {"effect": "allow", "path": "force:jentic/jentic-one-internal:refs/heads/jentic-e2e/scratch/*"},
    {"effect": "allow", "path": "push:jentic/jentic-one-internal:refs/heads/jentic-e2e/*"},
    {"effect": "allow", "path": "push:jentic/jentic-one-internal:refs/tags/*"},
    {"effect": "allow", "path": "fetch:jentic/jentic-one-internal"},
]


async def _seed(ctx: Context) -> tuple[str, str]:
    """An agent bound to a vendor-scoped GitHub token credential with :data:`_RULES`."""
    credential = Credential(
        type=StoredCredentialType.STATIC_BEARER_TOKEN,
        name="gh-git",
        api_vendor="github-com",
    )
    async with ctx.control_db.session() as session:
        session.add(credential)
        await session.flush()
        session.add(
            TokenValueCredential(
                credential_id=credential.id, encrypted_token_value=ctx.encryption.encrypt(_SECRET)
            )
        )
        await session.commit()
        credential_id = credential.id
    async with ctx.admin_db.transaction() as session:
        await UserRepository.create(
            session,
            id=_OWNER_ID,
            email=f"{_OWNER_ID}@git.test",
            first_name="Git",
            last_name="Owner",
            created_by=_SEED_MARKER,
        )
        agent = await AgentRepository.create(
            session,
            name="git-agent",
            owner_id=_OWNER_ID,
            registered_by=_OWNER_ID,
            created_by=_SEED_MARKER,
            status=ActorStatus.ACTIVE,
        )
        await ActorPermissionGrantRepository.grant(
            session,
            actor_id=agent.id,
            actor_type=ActorType.AGENT,
            permission=BROKER_EXECUTE_PERMISSION,
            granted_by=_OWNER_ID,
            created_by=_SEED_MARKER,
        )
        agent_id = agent.id
    async with ctx.admin_db.session() as session:
        session.add(
            AgentCredentialBinding(
                id=generate_ksuid("acb"), agent_id=agent_id, credential_id=credential_id
            )
        )
        await session.commit()
    async with ctx.control_db.session() as session:
        for sequence, rule in enumerate(_RULES, start=1):
            session.add(
                AgentPermissionRule(
                    agent_id=agent_id,
                    credential_id=credential_id,
                    effect=rule["effect"],
                    methods=rule.get("methods"),
                    path=rule["path"],
                    match_mode=rule.get("match_mode", "git"),
                    sequence=sequence,
                )
            )
        await session.commit()
    return agent_id, credential_id


async def _mint(ctx: Context, agent_id: str, *, vendor: str = "github") -> tuple[str, str]:
    access, _refresh = await TokenService(ctx).issue_pair(
        agent_id, ActorType.AGENT, [BROKER_EXECUTE_PERMISSION]
    )
    minted = await CliInvocationService(ctx).mint(
        CliInvocationMintPayload(vendor=vendor, command_title="git"),
        identity=Identity(sub=agent_id, actor_type=ActorType.AGENT),
        parent_token=access,
    )
    return minted.token, minted.invocation.trace_id


@pytest.fixture()
async def env(git_ctx: Context, tmp_path: Path) -> AsyncGenerator[_Env, None]:
    ctx = git_ctx
    await _truncate(ctx)
    agent_id, credential_id = await _seed(ctx)

    root = tmp_path / "upstream"
    root.mkdir()
    _seed_upstream(root)
    standin = _StandIn(root=root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(standin))
    standin.port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    broker = create_broker_app(ctx)
    install_broker_registry_resolver(broker, ctx)
    git_client = httpx.AsyncClient(follow_redirects=False)
    upstream_base = f"http://127.0.0.1:{standin.port}"
    upstream = GitUpstream(git_client, git_base=upstream_base, api_base=f"{upstream_base}/_api")
    broker.dependency_overrides[get_git_upstream] = lambda: upstream
    port = _free_port()
    uv = uvicorn.Server(
        uvicorn.Config(broker, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    task = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.02)

    (tmp_path / "home").mkdir()
    environment = _Env(
        ctx=ctx,
        standin=standin,
        broker_url=f"http://127.0.0.1:{port}",
        agent_id=agent_id,
        credential_id=credential_id,
        workdir=tmp_path,
    )
    environment.token, environment.trace_id = await _mint(ctx, agent_id)
    yield environment

    # The app's lifespan disconnects the context on shutdown: clean up first.
    await _truncate(ctx)
    uv.should_exit = True
    await task
    await git_client.aclose()
    server.shutdown()
    server.server_close()


async def _clone(env: _Env, name: str = "clone") -> Path:
    target = env.workdir / name
    code, err = await env.git("clone", "-q", env.remote, str(target))
    assert code == 0, err
    return target


def _commit(repo: Path, name: str, content: str | bytes = "x\n") -> str:
    path = repo / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    _git("add", name, cwd=repo)
    _git("commit", "-q", "-m", f"add {name}", cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo)


async def _records(ctx: Context, trace_id: str) -> list[ExecutionRecord]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(
            select(ExecutionRecord).where(ExecutionRecord.trace_id == trace_id)
        )
        return list(rows.scalars().all())


async def _denials(ctx: Context) -> list[dict[str, Any]]:
    async with ctx.admin_db.session() as session:
        rows = await session.execute(select(Event).where(Event.type == EventType.PBAC_DENIED))
        return [dict(e.data or {}) for e in rows.scalars().all()]


async def _settle() -> None:
    """Let the background tasks that persist execution records finish."""
    await asyncio.sleep(0.3)


# ── fetch ────────────────────────────────────────────────────────────────────


async def test_clone_and_fetch_through_jentic(env: _Env) -> None:
    clone = await _clone(env)

    assert (clone / "README.md").read_text() == "hello again\n"
    refs = _git("for-each-ref", "--format=%(refname)", cwd=clone)
    assert "jentic/hidden" not in refs
    code, err = await env.git("fetch", "-q", "origin", cwd=clone)
    assert code == 0, err

    expected_basic = "Basic " + base64.b64encode(f"x-access-token:{_SECRET}".encode()).decode()
    git_requests = [s for s in env.standin.seen if not s.path.startswith("/_api/")]
    assert git_requests
    for seen in git_requests:
        assert seen.authorization == expected_basic
        assert seen.path.startswith(f"/{_OWNER}/{_REPO}.git/")
        assert seen.user_agent.startswith("git/")
        assert seen.git_protocol is None
    await _settle()
    records = await _records(env.ctx, env.trace_id)
    assert {r.operation_id for r in records} >= {
        "github-git.info-refs",
        "github-git.upload-pack",
    }
    assert {r.api_name for r in records} == {"github-git"}
    assert {r.credential_id for r in records} == {env.credential_id}


async def test_the_advertisement_hides_refs_jentic_and_push_cert(env: _Env) -> None:
    async with httpx.AsyncClient() as client:
        for service in ("git-upload-pack", "git-receive-pack"):
            response = await client.get(
                f"{env.remote}/info/refs",
                params={"service": service},
                headers={"Authorization": f"Bearer {env.token}"},
            )
            assert response.status_code == 200, response.text
            assert response.headers["content-type"] == f"application/x-{service}-advertisement"
            assert b"refs/heads/main" in response.content
            assert b"refs/jentic/" not in response.content
            assert b"push-cert" not in response.content
            assert b"report-status" in response.content or service == "git-upload-pack"


async def test_url_credentials_answer_the_basic_challenge(env: _Env) -> None:
    host = env.broker_url.removeprefix("http://")
    remote = f"http://x-access-token:{env.token}@{host}/_facade/proxy/https://github.com/{_OWNER}/{_REPO}.git"
    process = await asyncio.create_subprocess_exec(
        "git",
        "ls-remote",
        remote,
        env={**os.environ, **_GIT_ENV_BASE, **env.git_env()},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    assert process.returncode == 0, err.decode()
    assert b"refs/heads/main" in out


async def test_fetch_of_a_repository_the_rules_do_not_name_is_refused(env: _Env) -> None:
    other = f"{env.broker_url}/_facade/proxy/https://github.com/{_OWNER}/other.git"

    code, err = await env.git("ls-remote", other)

    assert code != 0
    assert "remote: Jentic: The binding's git rules do not allow fetching jentic/other" in err
    assert env.standin.seen == []
    denials = await _denials(env.ctx)
    assert any(d.get("repository") == "jentic/other" and d["mode"] == "git" for d in denials)


async def test_a_token_for_another_vendor_is_refused(env: _Env) -> None:
    env.ctx.config.cli_facades.vendors.cloudflare.enabled = True
    env.token, _trace = await _mint(env.ctx, env.agent_id, vendor="cloudflare")

    code, err = await env.git("ls-remote", env.remote)

    assert code != 0
    assert "remote: Jentic: This invocation token is for cloudflare" in err
    assert env.standin.seen == []


# ── push ─────────────────────────────────────────────────────────────────────


async def test_push_to_an_allowed_branch(env: _Env) -> None:
    clone = await _clone(env)
    new = _commit(clone, "feature.txt")

    code, err = await env.git("push", "-q", "origin", "HEAD:refs/heads/jentic-e2e/feat", cwd=clone)

    assert code == 0, err
    assert env.standin.ref("refs/heads/jentic-e2e/feat") == new
    await _settle()
    records = await _records(env.ctx, env.trace_id)
    pushes = [r for r in records if r.operation_id == "github-git.receive-pack"]
    assert len(pushes) == 1
    assert pushes[0].status == "completed"


async def test_push_to_a_denied_ref_prints_the_jentic_reason(env: _Env) -> None:
    clone = await _clone(env)
    before = env.standin.ref("refs/heads/main")
    _commit(clone, "main.txt")

    code, err = await env.git("push", "origin", "HEAD:refs/heads/main", cwd=clone)

    assert code != 0
    assert "jentic: pushing to refs/heads/main is not allowed" in err
    assert "remote: Jentic refused this push" in err
    assert env.standin.ref("refs/heads/main") == before
    assert not any(s.method == "POST" and s.path.endswith("receive-pack") for s in env.standin.seen)
    denials = await _denials(env.ctx)
    (denial,) = [d for d in denials if d.get("ref") == "refs/heads/main"]
    assert denial["repository"] == "jentic/jentic-one-internal"
    assert denial["mode"] == "git"
    assert "not allowed" in denial["detail"]
    await _settle()
    records = await _records(env.ctx, env.trace_id)
    (push,) = [r for r in records if r.operation_id == "github-git.receive-pack"]
    assert push.status == "failed"


async def test_reserved_and_unpushable_refs_are_hard_denied(env: _Env) -> None:
    clone = await _clone(env)

    code, err = await env.git(
        "push", "origin", "HEAD:refs/jentic/mine", "HEAD:refs/notes/x", cwd=clone
    )

    assert code != 0
    assert "jentic: refs/jentic/ is reserved for Jentic" in err
    assert "only branches (refs/heads/) and tags (refs/tags/)" in err
    assert env.standin.ref("refs/notes/x") is None


async def test_non_fast_forward_push_is_denied(env: _Env) -> None:
    clone = await _clone(env)
    _git("checkout", "-q", "-b", "rewrite", "origin/jentic-e2e/base~1", cwd=clone)
    _commit(clone, "diverged.txt")
    before = env.standin.ref("refs/heads/jentic-e2e/base")

    code, err = await env.git(
        "push", "--force", "origin", "HEAD:refs/heads/jentic-e2e/base", cwd=clone
    )

    assert code != 0
    assert "jentic: non-fast-forward update of refs/heads/jentic-e2e/base" in err
    assert env.standin.ref("refs/heads/jentic-e2e/base") == before
    assert env.standin.compares, "the force-push check asked GitHub's compare API"


async def test_force_push_where_the_rules_allow_it(env: _Env) -> None:
    clone = await _clone(env)
    _git("checkout", "-q", "-b", "rewrite", "origin/jentic-e2e/scratch/x~1", cwd=clone)
    new = _commit(clone, "rewritten.txt")

    code, err = await env.git(
        "push", "--force", "origin", "HEAD:refs/heads/jentic-e2e/scratch/x", cwd=clone
    )

    assert code == 0, err
    assert env.standin.ref("refs/heads/jentic-e2e/scratch/x") == new
    assert env.standin.compares == []


async def test_fast_forward_to_a_commit_github_already_has(env: _Env) -> None:
    clone = await _clone(env)
    pushed = _commit(clone, "ahead.txt")
    code, err = await env.git("push", "-q", "origin", "HEAD:refs/heads/jentic-e2e/ahead", cwd=clone)
    assert code == 0, err
    _git(
        "update-ref", "refs/remotes/origin/jentic-e2e/base-old", "origin/jentic-e2e/base", cwd=clone
    )

    # No new objects: the update's new commit is outside the (empty) pack, so
    # the check asks GitHub whether it descends from the branch's old commit.
    code, err = await env.git(
        "push", "-q", "origin", f"{pushed}:refs/heads/jentic-e2e/base", cwd=clone
    )

    assert code == 0, err
    assert env.standin.ref("refs/heads/jentic-e2e/base") == pushed
    assert env.standin.compares


async def test_atomic_push_with_one_denied_ref_moves_nothing(env: _Env) -> None:
    clone = await _clone(env)
    _commit(clone, "atomic.txt")

    code, err = await env.git(
        "push",
        "--atomic",
        "origin",
        "HEAD:refs/heads/jentic-e2e/atomic",
        "HEAD:refs/heads/main",
        cwd=clone,
    )

    assert code != 0
    assert "jentic: pushing to refs/heads/main is not allowed" in err
    assert "jentic: not applied: another ref in this push was refused" in err
    assert env.standin.ref("refs/heads/jentic-e2e/atomic") is None


async def test_moving_a_tag_is_denied_but_creating_one_is_allowed(env: _Env) -> None:
    clone = await _clone(env)
    head = _commit(clone, "tagged.txt")
    _git("tag", "-f", "v1", cwd=clone)
    _git("tag", "v2", cwd=clone)
    before = env.standin.ref("refs/tags/v1")

    moved, moved_err = await env.git("push", "--force", "origin", "refs/tags/v1", cwd=clone)
    created, created_err = await env.git(
        "push", "origin", "HEAD:refs/heads/jentic-e2e/tags", "refs/tags/v2", cwd=clone
    )

    assert moved != 0
    assert "jentic: tags are immutable: moving or deleting refs/tags/v1" in moved_err
    assert env.standin.ref("refs/tags/v1") == before
    assert created == 0, created_err
    assert env.standin.ref("refs/tags/v2") == head


async def test_delete_only_pushes(env: _Env) -> None:
    clone = await _clone(env)

    denied, denied_err = await env.git("push", "origin", ":refs/heads/jentic-e2e/base", cwd=clone)
    allowed, allowed_err = await env.git(
        "push", "origin", ":refs/heads/jentic-e2e/scratch/x", cwd=clone
    )

    assert denied != 0
    assert "jentic: deleting refs/heads/jentic-e2e/base is not allowed" in denied_err
    assert env.standin.ref("refs/heads/jentic-e2e/base") is not None
    assert allowed == 0, allowed_err
    assert env.standin.ref("refs/heads/jentic-e2e/scratch/x") is None


async def test_a_large_push_streams_past_the_execute_body_cap(env: _Env) -> None:
    clone = await _clone(env)
    blob = os.urandom(24 * 1024 * 1024)
    new = _commit(clone, "big.bin", blob)

    code, err = await env.git(
        "-c",
        "http.postBuffer=1048576",
        "push",
        "-q",
        "origin",
        "HEAD:refs/heads/jentic-e2e/big",
        cwd=clone,
    )

    assert code == 0, err
    assert env.standin.ref("refs/heads/jentic-e2e/big") == new
    pushes = [s for s in env.standin.seen if s.path.endswith("/git-receive-pack")]
    assert max(s.body_bytes for s in pushes) > 24 * 1024 * 1024


async def test_a_push_over_the_size_limit_is_refused_for_every_ref(env: _Env) -> None:
    env.ctx.config.cli_facades.git.max_push_bytes = 64 * 1024
    clone = await _clone(env)
    _commit(clone, "big.bin", os.urandom(256 * 1024))

    code, err = await env.git("push", "origin", "HEAD:refs/heads/jentic-e2e/toobig", cwd=clone)

    assert code != 0
    assert "jentic: the push is larger than Jentic's 65536-byte limit" in err
    assert env.standin.ref("refs/heads/jentic-e2e/toobig") is None


async def test_no_cit_token_ever_reaches_the_upstream(env: _Env) -> None:
    clone = await _clone(env)
    _commit(clone, "leak.txt")
    code, err = await env.git("push", "-q", "origin", "HEAD:refs/heads/jentic-e2e/leak", cwd=clone)
    assert code == 0, err

    for seen in env.standin.seen:
        assert "cit_" not in seen.authorization
        assert env.token not in seen.authorization


# ── crafted requests ─────────────────────────────────────────────────────────


def _pkt(payload: bytes) -> bytes:
    return f"{len(payload) + 4:04x}".encode() + payload


def _thin_pack(base_name: str, base: bytes, target: bytes) -> bytes:
    """A one-object pack: ``target`` as a REF_DELTA against ``base`` (not in the pack)."""

    def varint(value: int) -> bytes:
        out = bytearray()
        while True:
            byte, value = value & 0x7F, value >> 7
            out.append(byte | (0x80 if value else 0))
            if not value:
                return bytes(out)

    delta = varint(len(base)) + varint(len(target))
    for start in range(0, len(target), 127):
        piece = target[start : start + 127]
        delta += bytes([len(piece)]) + piece
    size = len(delta)
    header = bytearray([(7 << 4) | (size & 0x0F) | (0x80 if size >> 4 else 0)])
    size >>= 4
    while size:
        header.append((size & 0x7F) | (0x80 if size >> 7 else 0))
        size >>= 7
    body = b"PACK" + (2).to_bytes(4, "big") + (1).to_bytes(4, "big")
    body += bytes(header) + bytes.fromhex(base_name) + zlib.compress(delta)
    return body + hashlib.sha1(body).digest()


async def test_a_commit_hidden_in_a_thin_pack_is_refused_with_a_retry_directive(
    env: _Env,
) -> None:
    repo = env.standin.repo()
    old = env.standin.ref("refs/heads/jentic-e2e/base")
    assert old is not None
    base = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "commit", old], capture_output=True, check=True
    ).stdout
    tree = base.split(b"\n", 1)[0]
    target = tree + f"\nparent {old}\n".encode() + b"author A <a@x> 1 +0000\n"
    target += b"committer A <a@x> 1 +0000\n\nhidden in a delta\n"
    new = _commit_name(target)
    commands = _pkt(
        f"{old} {new} refs/heads/jentic-e2e/base\0report-status side-band-64k\n".encode()
    )
    body = commands + b"0000" + _thin_pack(old, base, target)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{env.remote}/git-receive-pack",
            content=body,
            headers={
                "Authorization": f"Bearer {env.token}",
                "Content-Type": "application/x-git-receive-pack-request",
            },
        )

    assert response.status_code == 200, response.text
    assert b"pack.useThin=false" in response.content
    assert b"ng refs/heads/jentic-e2e/base jentic:" in response.content
    assert env.standin.ref("refs/heads/jentic-e2e/base") == old


def _commit_name(content: bytes) -> str:
    return hashlib.sha1(f"commit {len(content)}\0".encode() + content).hexdigest()


async def test_the_authentication_probe_is_answered_locally(env: _Env) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{env.remote}/git-receive-pack",
            content=b"0000",
            headers={
                "Authorization": f"Bearer {env.token}",
                "Content-Type": "application/x-git-receive-pack-request",
            },
        )

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == "application/x-git-receive-pack-result"
    assert env.standin.seen == []
