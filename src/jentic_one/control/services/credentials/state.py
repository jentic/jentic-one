"""Signed connect-state codec for the OAuth connect flow."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

import jwt as pyjwt

from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.shared.auth.tokens import decode_jwt, issue_jwt


class StateError(Exception):
    """Raised when state verification fails."""


class StateExpiredError(StateError):
    """Raised when the state token has expired."""


class StateInvalidError(StateError):
    """Raised when the state token is malformed or tampered."""


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
