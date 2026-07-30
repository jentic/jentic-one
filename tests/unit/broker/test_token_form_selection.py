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
from jentic_one.broker.services.auth.token_validation import _LOG_FIELD_MAXLEN
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
async def test_malformed_jwt_shaped_token_is_typed_401() -> None:
    """A 3-segment but undecodable token is refused typed, not a bare DecodeError (#880 review)."""
    # ``looks_like_jwt`` is True for this (three non-empty dot segments), so it
    # routes to the JWT path; ``get_unverified_header`` would raise jwt.DecodeError.
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))

    with pytest.raises(TokenValidationError, match="jwt_malformed"):
        await validator.validate("aaa.bbb.ccc")


@pytest.mark.asyncio
async def test_out_of_range_exp_is_typed_401() -> None:
    """A validly-signed token with an absurd ``exp`` is refused, not a 500 (#880 review)."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    token = _sign({"sub": "x", "exp": 10**20, "actor_type": "agent"})

    with pytest.raises(TokenValidationError, match="jwt_exp_invalid"):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_non_finite_exp_is_typed_401() -> None:
    """A signed ``exp: inf`` raises OverflowError inside PyJWT's decode, not fromtimestamp.

    PyJWT validates ``exp`` with ``int(payload["exp"])`` *before* our downstream
    guard runs, so this must be caught at the verifier and mapped to the typed
    error rather than escaping as a 500 (#880 review residual).
    """
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    token = _sign({"sub": "x", "exp": float("inf"), "actor_type": "agent"})

    with pytest.raises(TokenValidationError, match="jwt_invalid"):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_claim_refusal_logs_single_jwt_refused_event() -> None:
    """Claim-level refusals log exactly one ``jwt_refused`` with a ``reason`` (#874)."""
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
    assert event["event"] == "jwt_refused"
    assert event["reason"] == "jwt_actor_type_unknown"
    assert event["iss"] == "issuer-a"
    assert event["sub"] == "agnt_jwt"
    assert event["actor_type"] == "gibberish"


@pytest.mark.asyncio
async def test_verifier_refusal_logs_single_jwt_refused_event() -> None:
    """A verifier-level refusal (bad signature) uses the same event shape (#880 review)."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "x", "exp": exp, "actor_type": "agent"}, secret="wrong-secret")

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(TokenValidationError),
    ):
        await validator.validate(token)

    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["event"] == "jwt_refused"
    assert "reason" in warnings[0]


@pytest.mark.asyncio
async def test_oversized_claim_values_are_truncated_in_logs() -> None:
    """Attacker-influenced iss/sub/actor_type are bounded before hitting the log stream (#874)."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    big = "z" * 500
    token = _sign({"sub": big, "iss": big, "exp": exp, "actor_type": big})

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(TokenValidationError),
    ):
        await validator.validate(token)

    event = next(e for e in logs if e["log_level"] == "warning")
    assert len(event["iss"]) == _LOG_FIELD_MAXLEN
    assert len(event["sub"]) == _LOG_FIELD_MAXLEN
    assert len(event["actor_type"]) == _LOG_FIELD_MAXLEN


@pytest.mark.asyncio
async def test_jwt_path_disabled_when_no_verifier() -> None:
    """With ``jwt=None`` even a JWT-shaped token falls through to opaque resolution."""
    resolver = _StubResolver(_opaque_resolution())
    dual = DualTokenValidator(opaque=CachedTokenValidator(resolver=resolver), jwt=None)
    token = _sign({"sub": "x", "exp": 9999999999})

    await dual.validate(token)

    assert resolver.calls == [token]
