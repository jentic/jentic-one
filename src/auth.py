"""API key middleware + human session authentication.

Two authentication mechanisms:
1. Human session (httpOnly JWT cookie):
   - Issued via POST /user/login
   - 30-day sliding window — renewed on every request older than 1 day
   - Required for admin operations (approve permission requests, etc.)
   - `request.state.is_human_session = True`

2. Agent key (X-Jentic-API-Key: tk_xxx):
   - Scoped to a toolkit's credentials and policy
   - IP restriction supported per key
   - `request.state.toolkit_id` set to the key's bound toolkit
   - `request.state.is_human_session = False`

Unauthenticated requests:
   - Broker paths (/{host}/{path}) and workflow execution are open
   - `request.state.toolkit_id = None`, `request.state.is_human_session = False`
   - Upstream auth is the upstream's problem

Human-session-only endpoints:
   - Defined in HUMAN_SESSION_ONLY set
   - Return 403 if called with an agent key

There is NO trusted-subnet admin bypass in the general middleware.
The subnet restriction applies only to POST /default-api-key/generate (first call).
CLI access (docker exec) is the only superuser path.

"""

import ipaddress
import json
import logging
import os
import re
import time
import uuid

import aiosqlite
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.agent_identity_gate import verify_registration_access_token
from src.agent_identity_util import hash_token
from src.config import JENTIC_TRUSTED_PROXY_HEADER, JENTIC_TRUSTED_PROXY_NETS
from src.db import DB_PATH, DEFAULT_TOOLKIT_ID, setup_state
from src.utils import route_path


logger = logging.getLogger("jentic.auth")

# ── JWT via PyJWT ─────────────────────────────────────────────────────────────
try:
    import jwt as _jwt
    from jwt.exceptions import InvalidTokenError as JWTError

    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False

JWT_ALGORITHM = "HS256"
JWT_TTL_SECONDS = 30 * 24 * 3600  # 30 days

MIN_PASSWORD_LENGTH = 8
JWT_REFRESH_AFTER = 24 * 3600  # re-issue after 1 day of age

# ── Paths that never need a key ───────────────────────────────────────────────
SKIP = {
    "/",
    "/health",
    "/llms.txt",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/favicon.ico",
    "/favicon.png",
    "/favicon.svg",
    "/login",  # React login page (SPA route)
    "/user/login",  # API login endpoint — must be public (bug fix: was missing)
    "/user/token",  # OAuth2 password grant for Swagger UI
    "/user/create",  # One-time root account creation — must be public before account exists
}
SKIP_PREFIXES = ("/static", "/assets", "/proxy", "/approve", "/docs", "/redoc", "/debug")

# ── Paths that allow unauthenticated agent access (no key = anonymous) ────────
# Broker and workflow execution: upstream auth is upstream's problem
OPEN_PREFIXES = (
    # workflow execution: POST /workflows/{slug}
    # workflow list/inspect: GET /workflows, GET /workflows/{slug}
)


def _is_public(path: str, method: str) -> bool:
    if path in SKIP or any(path.startswith(p) for p in SKIP_PREFIXES):
        return True
    # OAuth connect-callback: browser redirect from Pipedream, no auth context available
    if re.match(r"^/oauth-brokers/[^/]+/connect-callback", path) and method == "GET":
        return True
    # Agent identity (RFC 8414 / 7591) — unauthenticated endpoints
    if path == "/.well-known/oauth-authorization-server" and method == "GET":
        return True
    if path == "/register" and method == "POST":
        return True
    # POST /oauth/token must stay public: jwt-bearer grant has no prior access token (RFC 7523).
    if path == "/oauth/token" and method == "POST":
        return True
    return False


def _is_open_passthrough(path: str, method: str) -> bool:
    """Paths where no key = anonymous toolkit (upstream auth is upstream's problem)."""
    # Broker: first segment contains "." (e.g. /api.stripe.com/v1/...)
    first_segment = path.lstrip("/").split("/")[0]
    if "." in first_segment:
        return True
    # Workflow execution
    if re.match(r"^/workflows/[^/]+$", path) and method == "POST":
        return True
    # /user/me — check session but allow unauthenticated (returns logged_in: false)
    if path == "/user/me":
        return True
    return False


def client_ip(request: Request) -> str:
    """Best-effort real client IP (handles X-Forwarded-For from NPM)."""
    xff = request.headers.get("x-forwarded-for", "")
    raw = request.client.host if request.client else ""
    if xff:
        return xff.split(",")[0].strip()
    return raw


def _ip_allowed(client_ip: str, allowed_ips_json: str | None) -> bool:
    """Return True if client_ip is covered by the key's allowed_ips list.

    NULL / empty allowed_ips means the key was created without an explicit
    restriction — treat it as the default trusted subnets (not unrestricted).
    This preserves Option A's \"no unconstrained remote access\" guarantee.
    """
    if not allowed_ips_json:
        # NULL → fall back to trusted subnets default, not open access
        return is_trusted_ip(client_ip)
    try:
        allowed = json.loads(allowed_ips_json)
    except Exception:
        return is_trusted_ip(client_ip)
    if not allowed:
        return is_trusted_ip(client_ip)
    try:
        client = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowed:
        try:
            if "/" in str(entry):
                if client in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                if client == ipaddress.ip_address(entry):
                    return True
        except ValueError:
            continue
    return False


_DEFAULT_TRUSTED_SUBNETS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "::1/128",
]


def trusted_subnets() -> list[str]:
    """Return list of CIDR strings considered trusted.

    Always includes the RFC-1918 + loopback defaults.
    JENTIC_TRUSTED_SUBNETS adds extra subnets on top (e.g. 100.64.0.0/10 for Tailscale).
    Setting the env var never removes the built-in defaults.
    """
    extras = [s.strip() for s in os.getenv("JENTIC_TRUSTED_SUBNETS", "").split(",") if s.strip()]
    seen = set()
    result = []
    for cidr in _DEFAULT_TRUSTED_SUBNETS + extras:
        if cidr not in seen:
            seen.add(cidr)
            result.append(cidr)
    return result


def default_allowed_ips() -> list[str]:
    """Default IP allowlist applied to new toolkit keys.

    Always returns a non-empty list — keys are never created without IP
    restrictions (subnet is the perimeter).

    Returns the same set as trusted_subnets(): RFC-1918 + loopback + any
    extras from JENTIC_TRUSTED_SUBNETS.
    """
    return trusted_subnets()


def is_trusted_ip(client_ip: str) -> bool:
    """Return True if the client IP is in a trusted subnet."""
    try:
        client = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for cidr in trusted_subnets():
        try:
            if client in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


# ── Trusted-proxy CIDR helpers ────────────────────────────────────────────────


def trusted_proxy_nets() -> list[str]:
    """Parse JENTIC_TRUSTED_PROXY_NETS into a list of CIDR strings."""
    return [s.strip() for s in JENTIC_TRUSTED_PROXY_NETS.split(",") if s.strip()]


def is_proxy_trusted_peer(peer_ip: str) -> bool:
    """Return True if peer_ip falls inside JENTIC_TRUSTED_PROXY_NETS."""
    try:
        addr = ipaddress.ip_address(peer_ip)
    except ValueError:
        return False
    for cidr in trusted_proxy_nets():
        try:
            if addr in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


# ── JWT helpers ───────────────────────────────────────────────────────────────


def make_jwt(secret: str, username: str) -> str:
    now = int(time.time())
    payload = {"sub": "human", "iat": now, "exp": now + JWT_TTL_SECONDS}
    if username:
        payload["username"] = username
    return _jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def _decode_jwt(token: str, secret: str) -> dict | None:
    if not JWT_AVAILABLE:
        return None
    try:
        return _jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


# ── Human session guard (dependency) ──────────────────────────────────────────


def require_human_session(request: Request):
    """FastAPI dependency — raises 403 if caller is not a human session."""
    if not getattr(request.state, "is_human_session", False):
        raise build_human_only_error()


def build_human_only_error():
    return HTTPException(
        status_code=403,
        detail={
            "error": "human_session_required",
            "message": "This action requires a human session (browser login). Agent keys cannot perform this operation.",
            "hint": "Log in at /user/login, then retry with the session cookie.",
        },
    )


async def _authenticate_agent_access_token(request: Request, bearer: str) -> bool:
    """Validate opaque access token (at_…). Sets agent_client_id, granted_toolkit_ids, toolkit_id."""
    th = hash_token(bearer)
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT t.client_id, t.expires_at, t.consumed_at, a.status
               FROM agent_tokens t JOIN agents a ON a.client_id = t.client_id
               WHERE t.token_hash=? AND t.token_type='access'
                 AND a.deleted_at IS NULL""",
            (th,),
        ) as cur:
            row = await cur.fetchone()

    if not row or row["consumed_at"] is not None:
        return False
    if row["expires_at"] < now:
        return False
    if row["status"] != "approved":
        return False

    cid = row["client_id"]
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT g.toolkit_id FROM agent_toolkit_grants g
               JOIN toolkits tk ON tk.id = g.toolkit_id
               WHERE g.client_id=? AND tk.disabled = 0
               ORDER BY g.granted_at ASC""",
            (cid,),
        ) as cur:
            grant_rows = await cur.fetchall()
    grants = [r["toolkit_id"] for r in grant_rows]

    request.state.agent_client_id = cid
    request.state.granted_toolkit_ids = grants
    request.state.toolkit_id = grants[0] if grants else None
    request.state.is_admin = False
    request.state.is_human_session = False
    request.state.simulate = False
    return True


async def _human_session_response(request: Request, call_next, jwt_token: str | None):
    """If jwt_token is a valid human session JWT, run the request and refresh the cookie when due."""
    if not jwt_token or not JWT_AVAILABLE:
        return None
    state = await setup_state()
    claims = _decode_jwt(jwt_token, state["jwt_secret"])
    if not claims or claims.get("sub") != "human":
        return None
    request.state.is_human_session = True
    request.state.is_admin = True
    request.state.toolkit_id = DEFAULT_TOOLKIT_ID
    request.state.username = claims.get("username") or None
    response = await call_next(request)
    issued_at = claims.get("iat", 0)
    if time.time() - issued_at > JWT_REFRESH_AFTER:
        new_token = make_jwt(state["jwt_secret"], claims.get("username") or "")
        response.set_cookie(
            "jentic_session",
            new_token,
            httponly=True,
            samesite="strict",
            max_age=JWT_TTL_SECONDS,
            path=request.scope.get("root_path") or "/",
        )
    return response


async def _trusted_proxy_response(request: Request, call_next):
    """Trusted-proxy identity auth path.

    Activated only when both JENTIC_TRUSTED_PROXY_HEADER and
    JENTIC_TRUSTED_PROXY_NETS are non-empty. Returns None to hand off to the
    next auth step when the feature is inactive or no trusted-peer+header
    combination is present.

    Peer IP is read from request.client.host (ASGI scope) only — never from
    X-Forwarded-For, so the CIDR check cannot be spoofed by the agent.
    """
    if not JENTIC_TRUSTED_PROXY_HEADER or not JENTIC_TRUSTED_PROXY_NETS:
        return None

    # Agent keys are always authoritative over the proxy identity path.
    # A tk_ key must not be silently upgraded to an admin human session.
    if request.headers.get("X-Jentic-API-Key", "").startswith("tk_"):
        return None

    peer_ip = request.client.host if request.client else ""
    trusted = is_proxy_trusted_peer(peer_ip)
    header_value = request.headers.get(JENTIC_TRUSTED_PROXY_HEADER, "").strip()

    if not trusted:
        if header_value:
            # Log header name only — not its value — to avoid persisting identity tokens.
            logger.warning(
                "PROXY_AUTH untrusted_peer=%s header=%s ignored",
                peer_ip,
                JENTIC_TRUSTED_PROXY_HEADER,
            )
        return None

    if not header_value:
        return None

    # JIT-provision the user; INSERT OR IGNORE handles concurrent first requests.
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, created_via) "
            "VALUES (?, ?, NULL, 'trusted_proxy')",
            (str(uuid.uuid4()), header_value),
        )
        await db.commit()

    request.state.is_human_session = True
    request.state.is_admin = True
    request.state.toolkit_id = DEFAULT_TOOLKIT_ID
    request.state.username = header_value
    return await call_next(request)


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = route_path(request.scope)
        method = request.method

        # ── Default state ─────────────────────────────────────────────────────
        request.state.toolkit_id = None
        request.state.is_admin = False
        request.state.is_human_session = False
        request.state.simulate = False
        request.state.agent_client_id = None
        request.state.granted_toolkit_ids = None

        # Resolve client IP early — used by multiple auth steps below
        req_ip = client_ip(request)

        # ── Public paths — no auth needed ─────────────────────────────────────
        if _is_public(path, method):
            return await call_next(request)

        # ── Open passthrough paths — unauthenticated = anonymous ──────────────
        # We still check for a key and set toolkit context if present,
        # but we DON'T reject if absent.
        is_open = _is_open_passthrough(path, method)

        cookie = request.cookies.get("jentic_session")
        auth_hdr = request.headers.get("Authorization", "")
        bearer = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else None

        # GET /register/{client_id} — Bearer rat_, matching at_, or human session
        m_reg = re.match(r"^/register/([^/]+)$", path)
        if method == "GET" and m_reg:
            cid = m_reg.group(1)
            if bearer and bearer.startswith("rat_"):
                if not await verify_registration_access_token(cid, bearer):
                    return JSONResponse(
                        {
                            "detail": "Unauthorised",
                            "hint": "Invalid or expired registration_access_token.",
                        },
                        status_code=401,
                        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                    )
                return await call_next(request)
            if bearer and bearer.startswith("at_"):
                if await _authenticate_agent_access_token(request, bearer):
                    if request.state.agent_client_id == cid:
                        return await call_next(request)
                return JSONResponse(
                    {
                        "detail": "Unauthorised",
                        "hint": "Invalid agent access token or client_id mismatch.",
                    },
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )
            jwt_human = cookie or (
                bearer if bearer and not bearer.startswith(("at_", "rat_")) else None
            )
            human_resp = await _human_session_response(request, call_next, jwt_human)
            if human_resp is not None:
                return human_resp
            return JSONResponse(
                {
                    "detail": "Unauthorised",
                    "hint": ("Provide Bearer rat_…, a matching at_…, or an admin session cookie."),
                },
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )

        # ── Agent access token (Authorization: Bearer at_…) ─────────────────────
        if bearer and bearer.startswith("at_"):
            if await _authenticate_agent_access_token(request, bearer):
                return await call_next(request)
            if not is_open:
                return JSONResponse(
                    {"detail": "Unauthorised", "hint": "Invalid or expired agent access token."},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
                )

        # ── 0. Human session JWT (cookie or Authorization: Bearer human JWT) ──
        # Do not treat rat_/at_ prefixes as human JWT (avoids noisy decode attempts).
        jwt_token = cookie or (
            bearer if bearer and not bearer.startswith(("at_", "rat_")) else None
        )
        human_resp = await _human_session_response(request, call_next, jwt_token)
        if human_resp is not None:
            return human_resp

        # ── 0a. Trusted-proxy forwarded identity ──────────────────────────────
        proxy_resp = await _trusted_proxy_response(request, call_next)
        if proxy_resp is not None:
            return proxy_resp

        # ── 1. Check for agent key (X-Jentic-API-Key: tk_xxx) ─────────────────
        # Key check runs FIRST — even on trusted subnets. A request with a valid
        # tk_ key gets the toolkit identity that key is bound to. Trusted-subnet
        # passthrough (admin) is only the fallback when no key is provided.
        provided_key = request.headers.get("X-Jentic-API-Key") or ""

        if provided_key.startswith("tk_"):
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        """SELECT ck.id, ck.toolkit_id, ck.allowed_ips, c.simulate
                           FROM toolkit_keys ck
                           JOIN toolkits c ON c.id = ck.toolkit_id
                           WHERE ck.api_key = ? AND ck.revoked_at IS NULL""",
                        (provided_key,),
                    ) as cur:
                        row = await cur.fetchone()
            except Exception:
                row = None

            if row:
                allowed = _ip_allowed(req_ip, row["allowed_ips"])
                logger.debug(
                    "KEY AUTH: key=%s toolkit=%s client_ip=%r allowed_ips=%s → %s",
                    provided_key[:12] + "…",
                    row["toolkit_id"],
                    req_ip,
                    row["allowed_ips"],
                    "ALLOW" if allowed else "DENY",
                )
                if not allowed:
                    return JSONResponse(
                        {
                            "error": "ip_not_allowed",
                            "message": f"This API key is not valid from {req_ip}.",
                            "hint": (
                                "Add your IP/subnet to this key's allowed_ips, "
                                "or add it to JENTIC_TRUSTED_SUBNETS for global access."
                            ),
                        },
                        status_code=403,
                    )
                request.state.toolkit_id = row["toolkit_id"]
                request.state.toolkit_key_id = row["id"]
                request.state.is_admin = False
                request.state.simulate = bool(row["simulate"])
                return await call_next(request)
            else:
                # Unrecognised key — reject unless open passthrough
                if not is_open:
                    return JSONResponse(
                        {"detail": "Unauthorised", "hint": "Unknown or revoked API key."},
                        status_code=401,
                    )
                # Open path: fall through as anonymous

        # ── 2. No valid auth — allow open paths, reject everything else ────────
        if is_open:
            # Anonymous — toolkit_id stays None, broker skips credentials
            return await call_next(request)

        # Determine a helpful hint based on the rejection reason
        # IP check takes priority — if the request isn't from a trusted subnet
        # and has no key, that's the real reason it was rejected.
        if not is_trusted_ip(req_ip):
            hint = (
                f"Requests from {req_ip} are not permitted. "
                f"Add your IP/subnet to JENTIC_TRUSTED_SUBNETS, or use an API key whose allowed_ips covers your IP."
            )
        else:
            try:
                state = await setup_state()
                if not state["account_created"]:
                    hint = (
                        "Visit /user/create to set up your admin account. "
                        "Agents should use GET /.well-known/oauth-authorization-server and POST /register."
                    )
                else:
                    hint = (
                        "Provide Authorization: Bearer with an agent access token (at_…), "
                        "or X-Jentic-API-Key with a toolkit key (tk_…), or log in at /user/login."
                    )
            except Exception:
                hint = "Provide Authorization: Bearer (at_…) or X-Jentic-API-Key (tk_…)."

        return JSONResponse(
            {"detail": "Unauthorised", "hint": hint},
            status_code=401,
        )
