"""Unit tests for dual-token form selection (opaque vs self-contained JWT)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
import structlog

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import (
    DualTokenValidator,
    JwtTokenValidator,
    JwtVerifier,
    looks_like_jwt,
)
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE

_SECRET = "test-secret"  # pragma: allowlist secret


def _sign(claims: dict[str, object], *, secret: str = _SECRET, alg: str = "HS256") -> str:
    return jwt.encode(claims, secret, algorithm=alg)


class _StubResolver:
    """Opaque-token resolver that records the token it was asked to resolve."""

    def __init__(self, result: Identity | None) -> None:
        self.result = result
        self.calls: list[str] = []

    async def resolve_access_token(self, token: str) -> Identity | None:
        self.calls.append(token)
        return self.result


def _opaque_resolution() -> Identity:
    return Identity(
        sub="agnt_opaque",
        actor_type=ActorType.AGENT,
        permissions=[BROKER_EXECUTE_SCOPE],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=True,
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        (_sign({"sub": "x", "exp": 9999999999}), True),
        ("opaque_token_abc", False),
        ("only.two", False),
        ("a..c", False),  # empty middle segment
        ("", False),
    ],
)
def test_looks_like_jwt(token: str, expected: bool) -> None:
    assert looks_like_jwt(token) is expected


@pytest.mark.asyncio
async def test_dispatcher_routes_opaque_to_cached_validator() -> None:
    resolver = _StubResolver(_opaque_resolution())
    dual = DualTokenValidator(
        opaque=CachedTokenValidator(resolver=resolver),
        jwt=JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET)),
    )

    resolved = await dual.validate("opaque_token_abc")

    assert resolved.sub == "agnt_opaque"
    assert resolver.calls == ["opaque_token_abc"]


@pytest.mark.asyncio
async def test_dispatcher_routes_jwt_to_verifier_without_lookup() -> None:
    resolver = _StubResolver(None)
    dual = DualTokenValidator(
        opaque=CachedTokenValidator(resolver=resolver),
        jwt=JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET)),
    )
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign(
        {
            "sub": "agnt_jwt",
            "exp": exp,
            "actor_type": ActorType.AGENT.value,
            "scopes": [BROKER_EXECUTE_SCOPE],
        }
    )

    resolved = await dual.validate(token)

    assert resolved.sub == "agnt_jwt"
    assert resolved.permissions == [BROKER_EXECUTE_SCOPE]
    assert resolver.calls == []  # no opaque DB lookup for a JWT


@pytest.mark.asyncio
async def test_jwt_with_bad_signature_is_rejected() -> None:
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp}, secret="wrong-secret")

    with pytest.raises(TokenValidationError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_jwt_with_disallowed_alg_is_rejected() -> None:
    """``alg:none`` (and any non-allowlisted alg) must be refused."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    token = jwt.encode({"sub": "x", "exp": 9999999999}, key="", algorithm="none")

    with pytest.raises(TokenValidationError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_expired_jwt_is_rejected() -> None:
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp})

    with pytest.raises(TokenValidationError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_missing_required_claims_rejected() -> None:
    """A signed token without ``sub``/``exp`` is refused (not a 500)."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    token = _sign({"exp": 9999999999})  # no sub

    with pytest.raises(TokenValidationError, match="jwt_missing_required_claims"):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_missing_actor_type_fails_closed() -> None:
    """A validly-signed token without ``actor_type`` is refused, not assumed AGENT (#864)."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp})

    with pytest.raises(TokenValidationError, match="jwt_actor_type_missing"):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_unknown_actor_type_is_typed_rejection() -> None:
    """An unrecognised ``actor_type`` raises the typed error, never a bare enum ValueError."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp, "actor_type": "gibberish"})

    with pytest.raises(TokenValidationError, match="jwt_actor_type_unknown"):
        await validator.validate(token)


@pytest.mark.parametrize("actor_type", ["toolkit", "user"])
@pytest.mark.asyncio
async def test_disallowed_actor_type_rejected(actor_type: str) -> None:
    """toolkit/user identities can't be minted by a bare signed claim (#868)."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "x", "exp": exp, "actor_type": actor_type})

    with pytest.raises(TokenValidationError, match="jwt_actor_type_not_allowed"):
        await validator.validate(token)


@pytest.mark.parametrize(
    ("actor_type", "expected"),
    [("agent", ActorType.AGENT), ("service_account", ActorType.SERVICE_ACCOUNT)],
)
@pytest.mark.asyncio
async def test_allowed_actor_types_validate(actor_type: str, expected: ActorType) -> None:
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "x", "exp": exp, "actor_type": actor_type})

    resolved = await validator.validate(token)

    assert resolved.actor_type is expected


@pytest.mark.asyncio
async def test_refusal_is_logged_without_token_material() -> None:
    """Each refusal emits one WARNING with structured context, never the raw token (#874)."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "iss": "issuer-a", "exp": exp, "actor_type": "gibberish"})

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(TokenValidationError),
    ):
        await validator.validate(token)

    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert len(warnings) == 1
    event = warnings[0]
    assert event["event"] == "jwt_actor_type_unknown"
    assert event["iss"] == "issuer-a"
    assert event["sub"] == "agnt_jwt"
    assert event["actor_type"] == "gibberish"
    assert not any(token in str(v) for v in event.values())


@pytest.mark.asyncio
async def test_jwt_path_disabled_when_no_verifier() -> None:
    """With ``jwt=None`` even a JWT-shaped token falls through to opaque resolution."""
    resolver = _StubResolver(_opaque_resolution())
    dual = DualTokenValidator(opaque=CachedTokenValidator(resolver=resolver), jwt=None)
    token = _sign({"sub": "x", "exp": 9999999999})

    await dual.validate(token)

    assert resolver.calls == [token]
