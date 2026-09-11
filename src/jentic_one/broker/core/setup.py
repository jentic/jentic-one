"""Broker auth wiring — installs token validation and binding enforcement on app state."""

from __future__ import annotations

from fastapi import FastAPI, Request

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.broker.repos import (
    AgentRuleEvaluator,
    ApiKeyResolver,
    CredentialBindingResolver,
    InProcessTokenResolver,
)
from jentic_one.broker.repos.caching_credential_deriver import CachingCredentialDeriver
from jentic_one.broker.services.auth import (
    CompositeTokenValidator,
    JwtTokenValidator,
    JwtVerifier,
    TokenVerifier,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.jwt_verification import TrustedIssuerVerifier
from jentic_one.shared.broker.protocols import CredentialDeriverProtocol
from jentic_one.shared.config import BrokerConfig
from jentic_one.shared.context import Context


def build_jwt_verifier(broker: BrokerConfig) -> TokenVerifier | None:
    """Pick the JWT verifier from config: hardened JWKS > HS256 secret > disabled.

    Trusted issuers take precedence — asymmetric, rotation-aware, with
    iss/aud/nbf + strict alg allowlist. The legacy HS256 shared secret remains for
    dev/test only. Neither configured ⇒ the JWT path is off (opaque tokens only).
    """
    jwt_cfg = broker.jwt_verification
    if jwt_cfg.trusted_issuers:
        return TrustedIssuerVerifier(jwt_cfg)
    if broker.jwt_secret is not None:
        return JwtVerifier(secret=broker.jwt_secret.get_secret_value())
    return None


def install_broker_auth(app: FastAPI, ctx: Context) -> None:
    """Wire token validation and credential-binding enforcement onto app state."""
    resolver = InProcessTokenResolver(ctx.admin_db)
    opaque = CachedTokenValidator(
        resolver=resolver,
        cache_ttl_seconds=ctx.config.broker.resolve_cache_ttl_seconds,
    )
    api_key_resolver = ApiKeyResolver(ctx.admin_db)
    api_key_cached = CachedTokenValidator(
        resolver=api_key_resolver,
        cache_ttl_seconds=ctx.config.broker.resolve_cache_ttl_seconds,
    )
    verifier = build_jwt_verifier(ctx.config.broker)
    jwt_validator = JwtTokenValidator(verifier=verifier) if verifier is not None else None
    triple = CompositeTokenValidator(
        opaque=opaque,
        api_key=api_key_cached,
        jwt=jwt_validator,
    )
    app.state.broker_token_validator = triple
    app.state.broker_api_key_resolver = api_key_resolver

    # Direct-binding authorization (theme-5) — the only path since Phase 6b
    # deleted toolkit derivation. ``toolkit_cache_ttl_s`` keeps its legacy
    # config key name (operator compatibility); it bounds this cache now.
    app.state.broker_agent_rule_evaluator = AgentRuleEvaluator(
        ctx.control_db,
        cache_ttl_seconds=ctx.config.broker.rule_cache_ttl_s,
        max_entries=ctx.config.broker.rule_cache_max_entries,
    )
    credential_deriver: CredentialDeriverProtocol = CredentialBindingResolver(
        ctx.admin_db, ctx.control_db
    )
    binding_cache_ttl_s = ctx.config.broker.toolkit_cache_ttl_s
    if binding_cache_ttl_s > 0:
        credential_deriver = CachingCredentialDeriver(
            credential_deriver, cache_ttl_seconds=binding_cache_ttl_s
        )
    app.state.broker_credential_deriver = credential_deriver

    async def _verify_token(token: str, request: Request) -> Identity:
        return await triple.validate(token)

    app.state.verify_token = _verify_token
