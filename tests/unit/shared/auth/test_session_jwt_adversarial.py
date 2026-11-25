"""Tests for session-JWT claim validation and algorithm enforcement."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from jentic_one.control.services.credentials.schemas.connect import ConnectState
from jentic_one.control.services.credentials.state import encode_state, generate_nonce
from jentic_one.shared.auth.tokens import InvalidTokenError, decode_jwt, issue_jwt
from jentic_one.shared.auth.verify import verify_token
from jentic_one.shared.config import AppConfig, DatabaseConfig, DatabasesConfig
from jentic_one.shared.context import Context

_SECRET = "a-valid-32-byte-signing-secret!!"  # pragma: allowlist secret


def test_decode_jwt_accepts_valid_token_with_required_claims() -> None:
    token = issue_jwt({"sub": "usr_123", "actor_type": "user"}, _SECRET, ttl_seconds=3600)
    claims = decode_jwt(token, _SECRET)
    assert claims["sub"] == "usr_123"
    assert "exp" in claims
    assert "iat" in claims


@pytest.mark.parametrize("missing_claim", ["exp", "iat", "sub"])
def test_decode_jwt_rejects_token_missing_required_claim(missing_claim: str) -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": "usr_123",
        "iat": now,
        "exp": now + timedelta(seconds=3600),
    }
    del payload[missing_claim]
    token = jwt.encode(payload, _SECRET, algorithm="HS256")
    with pytest.raises(InvalidTokenError):
        decode_jwt(token, _SECRET)


def test_decode_jwt_rejects_alg_none() -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": "usr_123",
        "iat": now,
        "exp": now + timedelta(seconds=3600),
    }
    token = jwt.encode(payload, "", algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_jwt(token, _SECRET)


def test_decode_jwt_rejects_hs512_with_same_secret() -> None:
    now = datetime.now(UTC)
    secret_64 = "b" * 64
    payload = {
        "sub": "usr_123",
        "iat": now,
        "exp": now + timedelta(seconds=3600),
    }
    token = jwt.encode(payload, secret_64, algorithm="HS512")
    with pytest.raises(InvalidTokenError):
        decode_jwt(token, secret_64)


def test_decode_jwt_rejects_connect_state_token_signed_with_state_secret() -> None:
    state_secret = "separate-32-byte-connect-secret!"  # pragma: allowlist secret
    state = ConnectState(
        credential_id="cred_1",
        provider="direct_oauth2",
        actor_id="usr_123",
        issued_at=datetime.now(UTC),
        nonce=generate_nonce(),
    )
    state_token = encode_state(state_secret, state, ttl_seconds=600)
    with pytest.raises(InvalidTokenError):
        decode_jwt(state_token, _SECRET)


def _encode_raw_hs256(payload: dict[str, object], secret: str) -> str:
    """Sign an arbitrary JSON payload as compact HS256 JWS without client-side claim checks."""
    header_b64 = (
        base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        .rstrip(b"=")
        .decode()
    )
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig_b64 = (
        base64.urlsafe_b64encode(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
        .rstrip(b"=")
        .decode()
    )
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _make_claim_only_ctx() -> Context:
    sqlite_mem = DatabaseConfig(backend="sqlite", path=":memory:")
    cfg = AppConfig(
        databases=DatabasesConfig(registry=sqlite_mem, admin=sqlite_mem, control=sqlite_mem)
    )
    return Context(cfg, allowed_dbs=set())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sub_claim",
    ["__OMIT__", None, "", "   ", "\t\n", 0, 123, True, ["usr_1"], {"id": "usr_1"}],
    ids=[
        "missing",
        "null",
        "empty",
        "spaces",
        "whitespace_tabs",
        "zero_int",
        "int",
        "bool",
        "list",
        "dict",
    ],
)
async def test_verify_token_rejects_adversarial_sub_claims(sub_claim: object) -> None:
    """Signed JWTs with missing, blank, or non-string ``sub`` fail with ``InvalidTokenError``."""
    now = int(datetime.now(UTC).timestamp())
    payload: dict[str, object] = {
        "actor_type": "user",
        "permissions": [],
        "iat": now,
        "exp": now + 3600,
    }
    if sub_claim != "__OMIT__":
        payload["sub"] = sub_claim
    token = _encode_raw_hs256(payload, _SECRET)
    ctx = _make_claim_only_ctx()

    with pytest.raises(InvalidTokenError):
        await verify_token(token, secret=_SECRET, ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sgen_claim",
    [True, False, "0", "1", -1, 0.0, [0], None],
    ids=["true", "false", "str_0", "str_1", "negative", "float_0", "list", "null"],
)
async def test_verify_token_rejects_adversarial_sgen_claims(sgen_claim: object) -> None:
    """Signed user JWTs with boolean, non-int, null, or negative ``sgen`` fail closed."""
    now = int(datetime.now(UTC).timestamp())
    token = _encode_raw_hs256(
        {
            "sub": "usr_123",
            "actor_type": "user",
            "permissions": [],
            "sgen": sgen_claim,
            "iat": now,
            "exp": now + 3600,
        },
        _SECRET,
    )
    ctx = _make_claim_only_ctx()

    with pytest.raises(InvalidTokenError, match="invalid session generation"):
        await verify_token(token, secret=_SECRET, ctx=ctx)
