"""Signed connect-state codec for the OAuth connect flow."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from jentic_one.control.repos.connect_nonce_repo import ConnectNonceRepository
from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.shared.auth.tokens import decode_jwt, issue_jwt
from jentic_one.shared.context import Context


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
    """
    claims: dict[str, object] = {
        "cid": state.credential_id,
        "prv": state.provider,
        "aid": state.actor_id,
        "act": state.actor_type,
        "sat": state.issued_at.timestamp(),
        "nonce": state.nonce,
    }
    if state.session_id is not None:
        claims["sid"] = state.session_id
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
    )


async def consume_callback_state(ctx: Context, raw_state: str) -> ConnectState:
    """Decode + verify + one-shot-consume a callback state JWT.

    This is the shared prologue for every OAuth callback path — the
    standalone-credential ``ConnectService.complete`` and the
    connect-session ``ConnectSessionService.complete_from_callback``
    both call through here so replay protection stays in one place.
    Introducing a second callback path without this helper is how the
    session-mode nonce-consume was originally missed.

    Enforces (in order):

      * JWT signature + TTL (via ``decode_state``) → ``StateExpiredError``
        / ``StateInvalidError``.
      * ``actor_id`` present on the state → ``StateMissingActorError``.
        The connect flow binds the initiating subject into the signed
        state at ``begin``; a state without it cannot be attributed.
      * Nonce not previously consumed → ``StateReplayedError``. Atomic
        via ``ConnectNonceRepository.consume`` (Postgres
        ``ON CONFLICT DO NOTHING`` on the ``nonce`` unique index) —
        two concurrent callbacks race on the DB, only one wins.
    """
    state_secret = ctx.config.credentials.connect.state_secret.get_secret_value()
    state = decode_state(state_secret, raw_state)
    if state.actor_id is None:
        raise StateMissingActorError("Connect state is missing the initiating subject")
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
