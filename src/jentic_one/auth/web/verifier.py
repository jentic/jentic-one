"""The control-plane identity verifier chain.

One chain resolves every platform token shape a signed-in caller can present on
the control plane. ``shared.web.app_factory`` imports this module (through one
named dynamic seam, so the shared layer has no static import of a surface) and
installs the chain on the combined app and on every standalone control-plane
surface, so no process shape accepts a narrower or wider set of tokens than
another. The broker keeps its own data-plane validator (``broker.core.setup``).

The chain lives in the auth surface's web layer because it speaks HTTP: it reads
the request path for problem details and raises ``Unauthorized``.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from jentic.problem_details import Unauthorized

from jentic_one.auth.services.token_service import ACCESS_TOKEN_PREFIX, TokenService
from jentic_one.shared.auth.api_key_resolver import (
    AGENT_API_KEY_PREFIX,
    RETIRED_SERVICE_ACCOUNT_KEY_DETAIL,
    ApiKeyResolver,
    is_retired_service_account_key,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OIDC_PASSTHROUGH_SCOPES
from jentic_one.shared.auth.verify import resolve_permissions_for_actor, verify_token
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType


def make_superset_verifier(ctx: Context) -> Any:
    """Build the full-taxonomy token verifier for combined/standalone apps.

    Resolves every platform token shape a signed-in caller can present:
    agent API keys (``jak_``; a retired ``sak_`` key is refused with a 401
    naming the replacement, and a retired ``jntc_live_`` key is accepted only
    by the broker), opaque ``at_`` access
    tokens (DB-resolved, live permissions), and HS256 web-session JWTs. This is
    the one control-plane chain: ``shared.web.app_factory`` installs it on the
    combined app and on every standalone control-plane surface
    (``install_platform_token_verifier``); other assemblers can call it
    directly.
    """
    return _make_auth_verifier(ctx)


def _make_auth_verifier(ctx: Context) -> Any:
    """Build the auth token verifier (API keys + opaque access tokens + JWT)."""
    api_key_resolver = ApiKeyResolver(ctx.admin_db)

    async def _verify(token: str, request: Request) -> Identity:
        if is_retired_service_account_key(token):
            await api_key_resolver.resolve(token)  # logs the refusal; never resolves
            raise Unauthorized(
                detail=RETIRED_SERVICE_ACCOUNT_KEY_DETAIL,
                instance=request.url.path,
                type="unauthorized",
            )
        if token.startswith(AGENT_API_KEY_PREFIX):
            resolved = await api_key_resolver.resolve(token)
            if resolved is None or not resolved.active:
                raise Unauthorized(
                    detail="Invalid or expired API key",
                    instance=request.url.path,
                    type="unauthorized",
                )
            return resolved

        if token.startswith(ACCESS_TOKEN_PREFIX):
            token_svc = TokenService(ctx)
            resolved = await token_svc.resolve_access_token(token)
            if resolved is None or not resolved.active:
                raise Unauthorized(
                    detail="Invalid or expired token",
                    instance=request.url.path,
                    type="unauthorized",
                )

            permissions, parent_permissions = await resolve_permissions_for_actor(
                ctx, resolved.actor_type, resolved.sub, resolved.parent_actor_id
            )
            if resolved.actor_type == ActorType.AGENT:
                # The access-token row carries the scopes minted from the agent's
                # live actor_permission_grants (TokenService.issue_pair via the
                # jwt-bearer exchange). Trust those as the agent's permissions:
                # the AGENT branch of resolve_permissions_for_actor is an
                # unimplemented stub that returns [], which silently drops every
                # granted scope — an approved capabilities:read then 403s and
                # a token re-mint can never take effect. This mirrors the
                # broker's InProcessTokenResolver, which already reads row.scopes.
                # parent_permissions (owner inheritance) is still resolved above.
                permissions = list(resolved.permissions)
            elif resolved.oauth_client_id is not None:
                consented = set(resolved.permissions)
                permissions = [p for p in permissions if p in consented]
                oidc = [s for s in consented if s in OIDC_PASSTHROUGH_SCOPES]
                permissions.extend(oidc)

            return Identity(
                sub=resolved.sub,
                email="",
                permissions=permissions,
                parent_permissions=parent_permissions,
                actor_type=resolved.actor_type,
                parent_actor_id=resolved.parent_actor_id,
                oauth_client_id=resolved.oauth_client_id,
                oauth_grant_id=resolved.oauth_grant_id,
            )
        return await verify_token(
            token, secret=ctx.config.admin.auth.jwt_secret.get_secret_value(), ctx=ctx
        )

    return _verify
