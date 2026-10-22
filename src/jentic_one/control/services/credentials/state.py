"""Signed connect-state codec for the OAuth connect flow."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from jentic_one.control.repos.connect_nonce_repo import ConnectNonceRepository
from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.shared.auth.tokens import decode_jwt, issue_jwt
from jentic_one.shared.context import Context

# The control surface mounts the credentials router with an empty prefix in
# both combined and standalone deploy modes, so this path is the same public
# suffix everywhere. Shared between the router registration and the connect
# flow's redirect_uri derivation so the two can never drift.
OAUTH_CALLBACK_PATH = "/credentials/oauth/callback"


class StateError(Exception):
    """Raised when state verification fails."""


class StateExpiredError(StateError):
    """Raised when the state token has expired."""


class StateInvalidError(StateError):
    """Raised when the state token is malformed or tampered."""


class StateMissingActorError(StateError):
    """Raised when a decoded state carries no initiating subject.

    A callback state MUST bind to the actor that started the flow —
    audit + downstream authorisation both key off ``actor_id``.
    """


class StateReplayedError(StateError):
    """Raised when a callback state's nonce has already been consumed.

    One-shot enforcement: an attacker replaying a captured callback URL
    (or a browser refresh landing the same ``?code=&state=`` twice)
    lands here on the second attempt.
    """


class StateBrowserMismatchError(StateError):
    """Raised when the callback did not arrive from the browser that started the flow.

    The binding cookie is missing, does not match the state's ``bnd`` claim,
    or the state carries no ``bnd`` claim at all. Checked before the nonce is
    consumed, so the flow stays completable from the right browser.
    """


#: Prefix of the per-flow browser-binding cookie. The full name appends the
#: first 16 hex characters of the binding hash, so concurrent connects in one
#: browser each keep their own cookie.
BROWSER_BINDING_COOKIE_PREFIX = "jentic_connect_"


@dataclass(frozen=True, slots=True)
class BrowserBindingClaim:
    """What a connect flow signs into its state to bind the callback to a browser.

    ``digest`` is the SHA-256 (hex) of the cookie value; ``host`` is the host
    the browser called the API on, where the cookie lives.
    """

    digest: str
    host: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserBinding:
    """A random per-flow value set as a cookie; only its SHA-256 enters the state.

    ``value`` is a secret: it is written to the initiating browser as an
    ``HttpOnly`` cookie and never persisted or logged server-side.
    """

    value: str = field(repr=False)
    digest: str
    host: str | None = None

    @property
    def cookie_name(self) -> str:
        return browser_binding_cookie_name(self.digest)

    @property
    def claim(self) -> BrowserBindingClaim:
        return BrowserBindingClaim(digest=self.digest, host=self.host)


def hash_browser_binding(value: str) -> str:
    """SHA-256 hex digest of a browser-binding cookie value."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_browser_binding(host: str | None = None) -> BrowserBinding:
    """Mint a fresh browser binding (32 random bytes, URL-safe)."""
    value = secrets.token_urlsafe(32)
    return BrowserBinding(value=value, digest=hash_browser_binding(value), host=host)


def browser_binding_cookie_name(digest: str) -> str:
    """Cookie name for the binding whose hash is ``digest``."""
    return f"{BROWSER_BINDING_COOKIE_PREFIX}{digest[:16]}"


def browser_binding_matches(state: ConnectState, cookie_value: str | None) -> bool:
    """Whether ``cookie_value`` is the binding whose hash the state carries.

    A state without a ``bnd`` claim never matches: every connect flow mints
    one, so an unbound state is refused rather than accepted unbound.
    """
    if not state.browser_binding_hash or not cookie_value:
        return False
    return hmac.compare_digest(hash_browser_binding(cookie_value), state.browser_binding_hash)


def generate_nonce() -> str:
    """Generate a cryptographically random nonce for state binding."""
    return secrets.token_urlsafe(24)


def encode_state(secret: str, state: ConnectState, ttl_seconds: int) -> str:
    """Encode a ConnectState into a signed JWT.

    The optional ``sid`` claim carries a connect-session id when the state
    was signed by the connect-session flow (agent-driven integrations); at
    callback time its presence routes completion to
    ``ConnectSessionService`` instead of the standalone-credential path.
    Absent for legacy credential-connect states — those keep working as-is.
    The optional ``sag`` claim carries ``sole_agent_id`` (a re-authorize's
    only permitted bound agent). The ``bnd`` claim carries the SHA-256 of
    the browser-binding cookie value (see :class:`BrowserBinding`).
    """
    claims: dict[str, object] = {
        "cid": state.credential_id,
        "prv": state.provider,
        "aid": state.actor_id,
        "act": state.actor_type,
        "sat": state.issued_at.timestamp(),
        "nonce": state.nonce,
        "ruri": state.redirect_uri,
    }
    if state.session_id is not None:
        claims["sid"] = state.session_id
    if state.sole_agent_id is not None:
        claims["sag"] = state.sole_agent_id
    if state.browser_binding_hash is not None:
        claims["bnd"] = state.browser_binding_hash
    if state.browser_host is not None:
        claims["bho"] = state.browser_host
    return issue_jwt(claims, secret, ttl_seconds)


def decode_state(secret: str, raw: str) -> ConnectState:
    """Decode and verify a signed state token. Raises StateError on failure."""
    try:
        claims = decode_jwt(raw, secret)
    except pyjwt.ExpiredSignatureError as exc:
        raise StateExpiredError("Connect state has expired") from exc
    except pyjwt.InvalidTokenError as exc:
        raise StateInvalidError("Connect state is invalid or tampered") from exc

    return ConnectState(
        credential_id=claims["cid"],
        provider=claims["prv"],
        actor_id=claims.get("aid"),
        actor_type=claims.get("act"),
        issued_at=datetime.fromtimestamp(claims["sat"], tz=UTC),
        nonce=claims["nonce"],
        session_id=claims.get("sid"),
        redirect_uri=claims.get("ruri"),
        sole_agent_id=claims.get("sag"),
        browser_binding_hash=claims.get("bnd"),
        browser_host=claims.get("bho"),
    )


async def consume_callback_state(
    ctx: Context, raw_state: str, *, browser_binding: str | None
) -> ConnectState:
    """Decode + verify + one-shot-consume a callback state JWT.

    This is the shared prologue for every OAuth callback path — the
    standalone-credential ``ConnectService.complete`` and the
    connect-session ``ConnectSessionService.complete_from_callback``
    both call through here so replay protection stays in one place.
    Introducing a second callback path without this helper is how the
    session-mode nonce-consume was originally missed.

    ``browser_binding`` is the value of the browser-binding cookie the
    callback request carried (``None`` when absent).

    Enforces (in order):

      * JWT signature + TTL (via ``decode_state``) → ``StateExpiredError``
        / ``StateInvalidError``.
      * ``actor_id`` present on the state → ``StateMissingActorError``.
        The connect flow binds the initiating subject into the signed
        state at ``begin``; a state without it cannot be attributed.
      * The callback comes from the browser that started the flow →
        ``StateBrowserMismatchError``. The cookie's SHA-256 must equal the
        state's ``bnd`` claim (constant-time compare); a state without the
        claim is refused. Runs before the nonce is consumed, so a refused
        callback writes nothing and the flow stays completable from the
        right browser.
      * Nonce not previously consumed → ``StateReplayedError``. Atomic
        via ``ConnectNonceRepository.consume`` (Postgres
        ``ON CONFLICT DO NOTHING`` on the ``nonce`` unique index) —
        two concurrent callbacks race on the DB, only one wins.
    """
    state_secret = ctx.config.credentials.connect.state_secret.get_secret_value()
    state = decode_state(state_secret, raw_state)
    if state.actor_id is None:
        raise StateMissingActorError("Connect state is missing the initiating subject")
    if not browser_binding_matches(state, browser_binding):
        raise StateBrowserMismatchError("Connect callback did not come from the starting browser")
    state_ttl = ctx.config.credentials.connect.state_ttl_seconds
    nonce_expires_at = state.issued_at + timedelta(seconds=state_ttl)
    async with ctx.control_db.transaction() as session:
        consumed = await ConnectNonceRepository.consume(
            session,
            nonce=state.nonce,
            credential_id=state.credential_id,
            expires_at=nonce_expires_at,
            created_by=state.actor_id,
        )
    if not consumed:
        raise StateReplayedError("Connect state already used")
    return state
