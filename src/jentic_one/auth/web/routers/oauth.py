"""OAuth token, revocation, introspection, and ephemeral minting endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from jentic_one.admin.repos.oauth_client_repo import OAuthClientRepository
from jentic_one.admin.services._support.passwords import verify_password
from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.assertion_service import AssertionService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.service_account_auth_service import ServiceAccountAuthService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.schemas.oauth import (
    IntrospectRequest,
    IntrospectResponse,
    MintRequest,
    MintResponse,
    RevokeRequest,
    TokenRequest,
    TokenResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import get_ctx

router = APIRouter()

_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_CLIENT_CREDENTIALS_GRANT = "client_credentials"
_AUTHORIZATION_CODE_GRANT = "authorization_code"


def get_token_service(ctx: Context = Depends(get_ctx)) -> TokenService:
    return TokenService(ctx)


def get_assertion_service(ctx: Context = Depends(get_ctx)) -> AssertionService:
    return AssertionService(ctx)


def get_sa_auth_service(ctx: Context = Depends(get_ctx)) -> ServiceAccountAuthService:
    return ServiceAccountAuthService(ctx)


def get_agent_auth_service(ctx: Context = Depends(get_ctx)) -> AgentAuthService:
    return AgentAuthService(ctx)


def get_authorize_service(ctx: Context = Depends(get_ctx)) -> AuthorizeService:
    return AuthorizeService(ctx)


@router.post("/oauth/token")
async def token_endpoint(
    body: TokenRequest,
    ctx: Context = Depends(get_ctx),
    token_svc: TokenService = Depends(get_token_service),
    assertion_svc: AssertionService = Depends(get_assertion_service),
    sa_auth_svc: ServiceAccountAuthService = Depends(get_sa_auth_service),
    agent_auth_svc: AgentAuthService = Depends(get_agent_auth_service),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> TokenResponse:
    """Exchange a refresh token, JWT assertion, authorization code, or client creds for tokens."""
    if body.grant_type == _AUTHORIZATION_CODE_GRANT:
        if not body.code or not body.code_verifier or not body.redirect_uri or not body.client_id:
            raise InvalidGrantError("code, code_verifier, redirect_uri, and client_id are required")
        is_platform = any(pc.client_id == body.client_id for pc in ctx.config.auth.platform_clients)
        if not is_platform:
            if not body.client_secret:
                raise InvalidGrantError("invalid_client")
            async with ctx.admin_db.session() as session:
                client = await OAuthClientRepository.get_by_client_id(session, body.client_id)
            if client is None or not verify_password(body.client_secret, client.client_secret_hash):
                raise InvalidGrantError("invalid_client")
        access_token, refresh_token, id_token = await authorize_svc.exchange_code(
            code=body.code,
            code_verifier=body.code_verifier,
            redirect_uri=body.redirect_uri,
            client_id=body.client_id,
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            token_type="bearer",
            expires_in=token_svc.access_ttl_seconds,
        )

    if body.grant_type == _JWT_BEARER_GRANT:
        if not body.assertion:
            raise InvalidGrantError("assertion is required for grant_type=jwt-bearer")
        access_token, refresh_token = await assertion_svc.verify_and_exchange(body.assertion)
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=token_svc.access_ttl_seconds,
        )

    if body.grant_type == _CLIENT_CREDENTIALS_GRANT:
        if not body.client_id or not body.client_secret:
            raise InvalidGrantError("client_id and client_secret are required")
        access_token, refresh_token, expires_in = await _authenticate_client_credentials(
            body.client_id, body.client_secret, sa_auth_svc, agent_auth_svc
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=expires_in,
        )

    if body.grant_type != "refresh_token":
        raise InvalidGrantError(f"unsupported grant_type: {body.grant_type}")

    if not body.refresh_token:
        raise InvalidGrantError("refresh_token is required for grant_type=refresh_token")

    access_token, refresh_token = await token_svc.refresh(body.refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=token_svc.access_ttl_seconds,
    )


async def _authenticate_client_credentials(
    client_id: str,
    client_secret: str,
    sa_auth_svc: ServiceAccountAuthService,
    agent_auth_svc: AgentAuthService,
) -> tuple[str, str, int]:
    """Try service account auth first, then agent auth."""
    try:
        access_token, refresh_token = await sa_auth_svc.authenticate_client_credentials(
            client_id, client_secret
        )
        return access_token, refresh_token, sa_auth_svc.access_ttl_seconds
    except InvalidGrantError:
        pass

    access_token, refresh_token = await agent_auth_svc.authenticate_client_credentials(
        client_id, client_secret
    )
    return access_token, refresh_token, agent_auth_svc.access_ttl_seconds


@router.post("/oauth/mint")
async def mint_endpoint(
    body: MintRequest,
    identity: Identity = get_current_identity(require_actor_type=ActorType.SERVICE_ACCOUNT),
    sa_auth_svc: ServiceAccountAuthService = Depends(get_sa_auth_service),
) -> MintResponse:
    """Mint a short-lived ephemeral token for a task agent.

    The caller must be an authenticated service account. The requested scopes
    must be a subset of the caller's own scopes.
    """
    requested_scopes = [s.strip() for s in body.scope.split() if s.strip()]
    ttl = body.ttl_seconds if body.ttl_seconds is not None else 300

    access_token = await sa_auth_svc.mint_task_token(
        host_sa_id=identity.sub,
        host_sa_scopes=identity.permissions,
        requested_scopes=requested_scopes,
        target_agent_id=body.target_agent_id,
        ttl_seconds=ttl,
    )

    return MintResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ttl,
    )


@router.post("/oauth/revoke", status_code=200)
async def revoke_endpoint(
    body: RevokeRequest,
    identity: Identity = get_current_identity(allow_expired_password=True),
    token_svc: TokenService = Depends(get_token_service),
) -> Response:
    """Revoke a token (RFC 7009). Always returns 200."""
    await token_svc.revoke(body.token, identity=identity)
    return Response(status_code=200)


@router.post("/oauth/introspect")
async def introspect_endpoint(
    body: IntrospectRequest,
    identity: Identity = get_current_identity(allow_expired_password=True),
    token_svc: TokenService = Depends(get_token_service),
) -> IntrospectResponse:
    """Introspect a token (RFC 7662)."""
    result = await token_svc.introspect(body.token)
    return IntrospectResponse.model_validate(result)
