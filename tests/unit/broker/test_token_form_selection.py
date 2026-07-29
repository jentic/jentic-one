"""Unit tests for dual-token form selection (opaque vs self-contained JWT)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from structlog.testing import capture_logs

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.services.auth import (
    DualTokenValidator,
    JwtTokenValidator,
    JwtVerifier,
    looks_like_jwt,
)
from jentic_one.broker.services.auth.token_validation import _MISSING_ACTOR_TYPE_SEEN
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
    token = _sign({"sub": "agnt_jwt", "exp": exp, "scopes": [BROKER_EXECUTE_SCOPE]})

    resolved = await dual.validate(token)

    assert resolved.sub == "agnt_jwt"
    assert resolved.permissions == [BROKER_EXECUTE_SCOPE]
    assert resolver.calls == []  # no opaque DB lookup for a JWT


@pytest.mark.asyncio
async def test_jwt_with_bad_signature_is_rejected() -> None:
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp}, secret="wrong-secret")

    with pytest.raises(ValueError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_jwt_with_disallowed_alg_is_rejected() -> None:
    """``alg:none`` (and any non-allowlisted alg) must be refused."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    token = jwt.encode({"sub": "x", "exp": 9999999999}, key="", algorithm="none")

    with pytest.raises(ValueError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_expired_jwt_is_rejected() -> None:
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp})

    with pytest.raises(ValueError):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_jwt_without_actor_type_defaults_to_agent() -> None:
    """Missing actor_type keeps the least-privileged AGENT default (#864).

    The broker's JWT producers include external trusted issuers with no
    published claims contract requiring actor_type, so the claim stays
    optional — unlike the shared admin gate, which fails closed (#862).
    """
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp, "scopes": [BROKER_EXECUTE_SCOPE]})

    resolved = await validator.validate(token)

    assert resolved.actor_type == ActorType.AGENT


@pytest.mark.asyncio
async def test_missing_actor_type_warning_is_deduped_per_issuer_sub() -> None:
    """The missing-claim warning fires once per (iss, sub), not per request."""
    _MISSING_ACTOR_TYPE_SEEN.clear()
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp, "iss": "https://idp.example"})

    with capture_logs() as logs:
        await validator.validate(token)
        await validator.validate(token)

    missing = [log for log in logs if log["event"] == "jwt_actor_type_missing"]
    assert len(missing) == 1
    assert missing[0]["iss"] == "https://idp.example"
    assert missing[0]["outcome"] == "defaulted_to_agent"


@pytest.mark.asyncio
async def test_jwt_with_explicit_actor_type_is_honoured() -> None:
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "usr_jwt", "exp": exp, "actor_type": "user"})

    resolved = await validator.validate(token)

    assert resolved.actor_type == ActorType.USER


@pytest.mark.parametrize(
    "bad_actor_type",
    [
        "definitely-not-a-real-actor",
        "AGENT",  # StrEnum values are case-sensitive
        "",  # empty string is unknown, not missing
        5,
        ["agent"],
    ],
)
@pytest.mark.asyncio
async def test_jwt_with_unknown_actor_type_is_rejected(bad_actor_type: object) -> None:
    """An unrecognised actor_type is refused deliberately (#864), with the same
    ValueError shape as any other invalid JWT — never a bare enum error."""
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET))
    exp = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    token = _sign({"sub": "agnt_jwt", "exp": exp, "actor_type": bad_actor_type})

    with pytest.raises(ValueError, match="jwt_actor_type_unknown"):
        await validator.validate(token)


@pytest.mark.asyncio
async def test_jwt_path_disabled_when_no_verifier() -> None:
    """With ``jwt=None`` even a JWT-shaped token falls through to opaque resolution."""
    resolver = _StubResolver(_opaque_resolution())
    dual = DualTokenValidator(opaque=CachedTokenValidator(resolver=resolver), jwt=None)
    token = _sign({"sub": "x", "exp": 9999999999})

    await dual.validate(token)

    assert resolver.calls == [token]
