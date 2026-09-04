"""The mounted ``/mcp`` ASGI app: gate, Origin check, bearer auth, transport.

A **stateless** Streamable HTTP endpoint (spec revision
2026-07-28 — no session state, no ``Mcp-Session-Id``, connection-independent
``tools/list``) built on the official ``mcp`` SDK's low-level ``Server`` +
``StreamableHTTPSessionManager(stateless=True)``, wrapped in a thin ASGI gate
that owns everything the platform — not the SDK — must decide:

- **The config gate.** ``server.mcp.enabled`` off keeps the path answering
  the framework's plain route-not-found 404 — or,
  when ``server.mcp.oauth.enabled`` is on, the RFC 9728 discovery challenge
  (401 + ``WWW-Authenticate: Bearer resource_metadata=…``) the challenge
  placeholder owned. The four on/off arms are pinned by tests
  (``tests/unit/mcp/test_mount_gate.py``).
- **Sub-path fall-through.** Only ``/mcp`` itself is served — the installer
  registers an exact-path ``Route``, never a prefix ``Mount``, so every
  sub-path (notably the ``/mcp/.well-known/…`` discovery probe variants,
  pinned by tests) keeps answering the framework's plain 404 in every arm
  and clients keep landing on the served RFC 8414 path-insertion documents.
- **Strict Origin validation** (spec §security, DNS-rebinding): a request
  carrying an ``Origin`` that is neither the config-derived canonical origin
  (``auth.canonical_base_url`` — the same source the discovery documents
  build absolute URLs from) nor loopback is refused with 403 before anything
  else runs. The request's own ``Host`` header is never trusted — in the
  rebinding attack it is attacker-controlled. Absent ``Origin`` (non-browser
  clients — every real MCP client today) passes.
- **Bearer auth** reusing the identity-resolution LOGIC — the app-state
  ``verify_token`` the auth surface installs (``make_superset_verifier``),
  which resolves ``jak_``/``sak_`` API keys, ``at_`` access tokens (including
  grant-channel bearers: actor=agent with ``oauth_grant_id``, resolved
  through the same gates as every REST call) — NOT the ``get_current_identity``
  FastAPI dependency: a Starlette mount is a separate ASGI app, so parent
  route dependencies never run here. A missing/invalid credential answers the
  same 401 challenge contract as the placeholder (the ``resource_metadata`` pointer riding
  only when the OAuth discovery surface is on to serve it).
- **The pre-auth whitelist**: ``tools/list``, the SDK's legacy ``initialize``
  fallback (+ ``notifications/initialized``), ``ping``, and the public
  resource listings are served without a credential — a client can always
  discover the tool surface before authenticating. Everything else
  requires a resolved identity.
- **Session telemetry**: each authenticated POST feeds the
  windowed ``mcp.session_started`` emit — key = (agent identity x ``_meta``
  clientInfo x window), fire-and-forget
  (:func:`jentic_one.shared.events.mcp_session.schedule_mcp_http_session_emit`).

The resolved identity/credential ride to the tool handlers on the request's
ASGI ``scope["state"]`` (the SDK transport attaches the Starlette ``Request``
to each handler's ``ServerRequestContext.request``) — never a contextvar: the
stateless session manager runs handlers on a task group created at startup,
whose context predates the request.
"""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import mcp.types as mcp_types
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import MCPError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import Receive, Scope, Send

from jentic_one.mcp.spec import served_tools
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events.mcp_session import (
    SESSION_ID_HEADER,
    schedule_mcp_http_session_emit,
    valid_session_id_or_none,
)
from jentic_one.shared.models.actors import Origin as ActorOrigin
from jentic_one.shared.web.links import deployment_base_url

#: The RFC 9728 protected-resource metadata path for the ``/mcp`` resource —
#: must stay in lockstep with ``auth/web/routers/discovery._MCP_PRM_PATH``.
MCP_PRM_PATH = "/.well-known/oauth-protected-resource/mcp"

#: JSON-RPC methods served WITHOUT a credential: discovery of the tool
#: surface, the spec's ping, the legacy-``initialize`` fallback pair, and the
#: public resource listings. ``resources/read`` is deliberately NOT here: no
#: resources are served yet, so pre-declaring it would hand a future resource
#: registration a pre-auth ride nobody re-reviewed — when the public skill
#: resources land, add it back alongside a pin that the readable set
#: stays public-only.
PRE_AUTH_METHODS = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "ping",
        "tools/list",
        "resources/list",
        "resources/templates/list",
    }
)

#: Bound on a buffered request body. The SDK transport's own bound is 4 MiB
#: (``max_request_body_size``); this only guards the pre-auth sniff.
_MAX_BUFFERED_BODY = 8 << 20

#: The spec 2026-07-28 ``_meta`` key an MCP client SHOULD stamp its identity
#: under on every request (there is no ``initialize`` to carry it anymore).
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"


def _framework_404() -> Response:
    """Mirror the framework's route-not-found body exactly (the disabled-arm posture)."""
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


def challenge_header(ctx: Context, request: Request) -> str:
    """The ``WWW-Authenticate`` challenge for this deployment's gate arms.

    With the OAuth discovery surface on, the exact challenge contract:
    ``Bearer resource_metadata="{base}{PRM}"`` — the RFC 9728 pointer a
    spec-following client walks. With it off there is no discovery document to
    point at (it 404s), so the challenge is a bare ``Bearer``: bearer-only
    deployments (jak_* keys / agent tokens) get the scheme without a dangling
    pointer.
    """
    if not ctx.config.server.mcp.oauth.enabled:
        return "Bearer"
    base = deployment_base_url(ctx.config.auth, request)
    return f'Bearer resource_metadata="{base}{MCP_PRM_PATH}"'


def _unauthorized(ctx: Context, request: Request, method: str) -> Response:
    """The 401 challenge, preserving the placeholder's shape (HEAD: headers, no body)."""
    headers = {"WWW-Authenticate": challenge_header(ctx, request)}
    if method == "HEAD":
        return Response(status_code=401, headers=headers)
    return JSONResponse(status_code=401, content={"detail": "Unauthorized"}, headers=headers)


def _is_loopback_origin_host(hostname: str) -> bool:
    """``localhost`` or a literal loopback IP (``ipaddress``-parsed, so a
    public DNS name like ``127.0.0.1.evil.example`` never qualifies)."""
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _origin_key(scheme: str, hostname: str, port: int | None) -> tuple[str, str, int | None]:
    """One web origin as a comparable (scheme, host, effective-port) triple."""
    if port is None:
        port = {"http": 80, "https": 443}.get(scheme)
    return (scheme, hostname, port)


def origin_allowed(ctx: Context, request: Request) -> bool:
    """Strict ``Origin`` validation (403 on mismatch — spec DNS-rebinding rule).

    Absent ``Origin`` passes (non-browser clients never send one). A present
    one must match a trusted set derived from **server config only**: the
    canonical base URL's origin (``auth.canonical_base_url`` — the same source
    the discovery documents build their absolute URLs from), or a
    loopback/localhost origin for local dev. ``null`` and unparseable origins
    fail.

    The request's own ``Host`` header is deliberately NOT consulted: in the
    DNS-rebinding attack this check exists to stop, the browser resolves the
    attacker's name to this daemon's IP and sends a self-consistent
    ``Host`` + ``Origin`` pair — trusting ``Host`` would make the gate a
    no-op in exactly its threat model. (The SDK's ``TransportSecuritySettings``
    is a static exact-string allowlist that also enforces ``Host`` and runs
    inside the transport — after auth — so the platform gate keeps owning
    this check.)
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    parts = urlsplit(origin)
    if not parts.scheme or not parts.netloc:
        return False
    try:
        hostname, port = (parts.hostname or "").lower(), parts.port
    except ValueError:  # pragma: no cover - non-numeric port
        return False
    if _is_loopback_origin_host(hostname):
        return True
    canonical = ctx.config.auth.canonical_base_url
    if not canonical:
        return False
    canonical_parts = urlsplit(canonical)
    if not canonical_parts.scheme or not canonical_parts.netloc:
        return False
    return _origin_key(parts.scheme.lower(), hostname, port) == _origin_key(
        canonical_parts.scheme.lower(),
        (canonical_parts.hostname or "").lower(),
        canonical_parts.port,
    )


def _body_methods(body: bytes) -> list[str] | None:
    """The JSON-RPC method name(s) in one POST body, or ``None`` if unreadable."""
    try:
        decoded = json.loads(body)
    except ValueError:
        return None
    if isinstance(decoded, dict):
        method = decoded.get("method")
        return [method] if isinstance(method, str) else None
    if isinstance(decoded, list):
        methods = []
        for item in decoded:
            method = item.get("method") if isinstance(item, dict) else None
            if not isinstance(method, str):
                return None
            methods.append(method)
        return methods or None
    return None


def request_client_info(body: bytes) -> tuple[str | None, str | None]:
    """The ``_meta`` clientInfo (name, version) in one POST body, if any.

    Spec 2026-07-28: clientInfo optionally rides each request's ``params._meta``
    under :data:`CLIENT_INFO_META_KEY` (a SHOULD — absent means client
    unknown). The bytes-level containment check keeps the per-request cost of
    the common no-clientInfo case to one scan instead of a JSON parse; a
    malformed body or a non-string field degrades to unknown, never an error
    (telemetry must not affect serving). Batched bodies yield the first
    request that carries one.
    """
    if CLIENT_INFO_META_KEY.encode() not in body:
        return (None, None)
    try:
        decoded = json.loads(body)
    except ValueError:
        return (None, None)
    for item in decoded if isinstance(decoded, list) else [decoded]:
        if not isinstance(item, dict):
            continue
        params = item.get("params")
        if not isinstance(params, dict):
            continue
        meta = params.get("_meta")
        if not isinstance(meta, dict):
            continue
        client_info = meta.get(CLIENT_INFO_META_KEY)
        if not isinstance(client_info, dict):
            continue
        name = client_info.get("name")
        version = client_info.get("version")
        return (
            name if isinstance(name, str) else None,
            version if isinstance(version, str) else None,
        )
    return (None, None)


# ── the SDK server (stateless; handlers read identity off the request state) ──


def _call_env(ctx: Context, sctx: ServerRequestContext[Any, Any]) -> CallEnv:
    """Rebuild the per-call env the gate stashed on the request's ASGI state."""
    request = sctx.request
    state = getattr(request, "scope", {}).get("state", {}) if request is not None else {}
    identity = state.get("mcp_identity")
    credential = state.get("mcp_credential")
    if not isinstance(identity, Identity) or not isinstance(credential, str):
        # Structurally unreachable — the gate whitelists tools/call away from
        # the pre-auth path — but fail closed if a transport change breaks it.
        raise _auth_required_error()
    return CallEnv(
        ctx=ctx,
        identity=identity,
        credential=credential,
        base_url=state.get("mcp_base_url", ""),
        session_id=state.get("mcp_session_id"),
    )


def _auth_required_error() -> Exception:
    return MCPError(-32001, "authentication required")


def build_mcp_server(ctx: Context) -> Server[Any]:
    """Assemble the low-level SDK server for this deployment.

    Tools come from the pinned tool-surface spec (``jentic_one.mcp.spec``);
    ``tools/list`` is connection-independent (stateless — the same list for
    every caller). Resource listings are served empty until the
    skill-resources slice lands.
    """

    async def on_list_tools(
        sctx: ServerRequestContext[Any, Any],
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListToolsResult:
        return mcp_types.ListToolsResult(tools=served_tools())

    async def on_call_tool(
        sctx: ServerRequestContext[Any, Any],
        params: mcp_types.CallToolRequestParams,
    ) -> mcp_types.CallToolResult:
        env = _call_env(ctx, sctx)
        return await dispatch_tool_call(env, params.name, params.arguments)

    async def on_list_resources(
        sctx: ServerRequestContext[Any, Any],
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListResourcesResult:
        return mcp_types.ListResourcesResult(resources=[])

    async def on_list_resource_templates(
        sctx: ServerRequestContext[Any, Any],
        params: mcp_types.PaginatedRequestParams | None,
    ) -> mcp_types.ListResourceTemplatesResult:
        return mcp_types.ListResourceTemplatesResult(resource_templates=[])

    version = _package_version()
    return Server(
        "jentic-mcp",
        title="Jentic One",
        version=version,
        instructions=(
            "Jentic One tool server (daemon-native HTTP endpoint). Call whoami to see "
            "the agent identity, status, scopes, and toolkit bindings before "
            "requesting access or executing operations. Every tool result carries a "
            "top-level `instance` key identifying the Jentic One instance it came "
            "from. The flow is whoami → search_apis → inspect_operation → execute; "
            "never execute an operation just to probe whether you have access."
        ),
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_list_resource_templates=on_list_resource_templates,
    )


def _package_version() -> str:
    try:
        return version("jentic-one")
    except PackageNotFoundError:  # pragma: no cover - dev checkouts always resolve
        return "0.0.0"


class McpChallengePlaceholder:
    """The challenge-placeholder contract for shapes serving auth WITHOUT control.

    The real mount rides control-plane shapes only, but the
    RFC 8414/9728 discovery documents ride the auth surface — a standalone
    auth deployment would serve the discovery documents while answering plain
    404 on ``/mcp`` itself, leaving the ``resource_metadata`` pointers
    dangling. This preserves exactly what the placeholder ships on the
    auth surface: with ``server.mcp.oauth.enabled`` every probe answers the
    discovery-chain 401 challenge; without it, the framework's plain 404.
    Never a transport — the resource itself lives where control lives.
    """

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - routes only see http
            raise RuntimeError(f"McpChallengePlaceholder cannot handle {scope['type']!r}")
        request = Request(scope, receive)
        if self.ctx.config.server.mcp.oauth.enabled:
            response: Response = _unauthorized(self.ctx, request, scope["method"].upper())
        else:
            response = _framework_404()
        await response(scope, receive, send)


class McpMount:
    """The ASGI app mounted at ``/mcp``: gate → Origin → auth → SDK transport.

    Instantiated once per process by the installer; the session manager's task
    group is started/stopped by the container lifespan
    (:func:`jentic_one.mcp.installer.mcp_lifespan`).
    """

    def __init__(self, ctx: Context, parent_app: Any) -> None:
        self.ctx = ctx
        self.parent_app = parent_app
        self.server = build_mcp_server(ctx)
        self.session_manager = StreamableHTTPSessionManager(
            app=self.server,
            json_response=True,
            stateless=True,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # pragma: no cover - mounts only see http
            raise RuntimeError(f"McpMount cannot handle scope type {scope['type']!r}")
        response = await self._gate(scope, receive)
        if isinstance(response, Response):
            await response(scope, receive, send)
            return
        await self.session_manager.handle_request(scope, response, send)

    async def _gate(self, scope: Scope, receive: Receive) -> Response | Receive:
        """Run the platform checks; return a Response to short-circuit, or the
        (possibly replaying) receive to delegate to the SDK transport."""
        server_cfg = self.ctx.config.server.mcp
        request = Request(scope, receive)
        method = scope["method"].upper()

        if not server_cfg.enabled:
            if server_cfg.oauth.enabled:
                # The challenge-placeholder contract, verbatim: any probe answers
                # the discovery-chain 401 challenge.
                return _unauthorized(self.ctx, request, method)
            return _framework_404()

        if not origin_allowed(self.ctx, request):
            return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})

        credential = self._extract_credential(request)
        body: bytes | None = None
        if method == "POST":
            body = await self._buffered_body(receive)
            if body is None:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})

        if credential is None:
            methods = _body_methods(body) if body is not None else None
            pre_auth = (
                method == "POST"
                and methods is not None
                and all(m in PRE_AUTH_METHODS for m in methods)
            )
            if not pre_auth:
                return _unauthorized(self.ctx, request, method)
        else:
            identity = await self._resolve_identity(credential, request)
            if identity is None:
                return _unauthorized(self.ctx, request, method)
            self._stash_call_state(scope, request, identity, credential)
            if body is not None:
                # Every authenticated POST may start a
                # (windowed) MCP session — the emit key is (agent identity x
                # clientInfo x window), clientInfo from this request's
                # ``_meta`` (absent → client unknown, mirroring stdio lane D).
                # Fire-and-forget; within a window the repeat cost is one
                # set-membership check, and distinct clientInfo keys are
                # capped per (agent, window) so varying ``_meta`` per request
                # cannot mint a row per request.
                client_name, client_version = request_client_info(body)
                schedule_mcp_http_session_emit(
                    self.ctx,
                    client_name=client_name,
                    client_version=client_version,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                )

        if method == "GET":
            # Never reaches the SDK transport: in stateless mode its
            # GET-as-SSE arm opens a stream no server-initiated message will
            # ever ride and that never ends — each such request would pin a
            # connection + task for the life of the process. json_response
            # mode governs POST responses only, so the gate owns this refusal.
            return JSONResponse(
                status_code=405,
                content={"detail": "Method Not Allowed"},
                headers={"Allow": "POST"},
            )

        if body is None:
            return receive
        return _replay_receive(body)

    def _extract_credential(self, request: Request) -> str | None:
        """API key header first, then Bearer (``shared.web.auth`` order)."""
        api_key = request.headers.get("x-jentic-api-key")
        if api_key:
            return api_key
        authorization = request.headers.get("authorization")
        if authorization and authorization.startswith("Bearer "):
            return authorization.removeprefix("Bearer ")
        return None

    async def _resolve_identity(self, credential: str, request: Request) -> Identity | None:
        """The REST resolvers' verification logic, mount-side.

        Delegates to the app-state ``verify_token`` (the superset verifier the
        auth surface installs) so every platform token shape — jak_/sak_ keys,
        ``at_`` access tokens including grant-channel bearers — resolves
        through exactly the gates the REST routes apply. Any failure is an
        invalid credential (401 challenge), mirroring ``resolve_identity``.
        """
        verify_token = getattr(self.parent_app.state, "verify_token", None)
        if verify_token is None:  # pragma: no cover - installer requires auth wiring
            return None
        try:
            identity: Identity = await verify_token(credential, request)
        except Exception:
            return None
        return identity

    def _stash_call_state(
        self, scope: Scope, request: Request, identity: Identity, credential: str
    ) -> None:
        """Ride the per-request call state to the handlers on ``scope.state``."""
        identity.origin = ActorOrigin.MCP
        state = scope.setdefault("state", {})
        state["mcp_identity"] = identity
        state["mcp_credential"] = credential
        state["mcp_base_url"] = deployment_base_url(self.ctx.config.auth, request)
        state["mcp_session_id"] = valid_session_id_or_none(request.headers.get(SESSION_ID_HEADER))

    async def _buffered_body(self, receive: Receive) -> bytes | None:
        """Read the full request body (``None`` when it exceeds the bound)."""
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # pragma: no cover - disconnect race
                break
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > _MAX_BUFFERED_BODY:
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        return b"".join(chunks)


def _replay_receive(body: bytes) -> Receive:
    """A receive channel replaying an already-buffered body to the transport."""
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


__all__ = [
    "MCP_PRM_PATH",
    "PRE_AUTH_METHODS",
    "McpChallengePlaceholder",
    "McpMount",
    "build_mcp_server",
]
