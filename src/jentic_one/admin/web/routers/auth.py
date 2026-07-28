"""Auth router — login, self-service, and invite redemption."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.invite_service import InviteService
from jentic_one.admin.services.schemas.auth import (
    ChangePasswordPayload,
    LoginPayload,
)
from jentic_one.admin.services.user_service import UserService
from jentic_one.admin.web.deps import (
    get_auth_service,
    get_invite_service,
    get_user_service,
)
from jentic_one.admin.web.schemas.auth import (
    ChangePasswordRequest,
    CreateAdminRequest,
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    RedeemInviteRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.auth import extract_credential
from jentic_one.shared.web.openapi_responses import PUBLIC_ERROR_RESPONSES, gone

router = APIRouter()


@router.post("/auth/login", summary="Log in")
async def login(
    body: LoginRequest,
    auth_svc: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """Authenticate and return a JWT token bundle."""
    bundle = await auth_svc.login(LoginPayload(email=body.email, password=body.password))
    return LoginResponse(
        access_token=bundle.access_token,
        token_type=bundle.token_type,
        expires_in=bundle.expires_in,
        must_change_password=bundle.must_change_password,
    )


@router.post("/auth/refresh", summary="Refresh session token")
async def refresh_session(
    request: Request,
    identity: Identity = get_current_identity(allow_expired_password=True),
    auth_svc: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """Re-mint the caller's login JWT before it expires (sliding session).

    Only user login JWTs are refreshable; permissions and the
    ``must_change_password`` gate are re-read from the database at re-mint.
    Refusal modes: 401 ``session_expired`` once the original authentication is
    older than the absolute window (``admin.auth.session_ttl_seconds``), 401
    ``invalid_credentials`` for deactivated users, non-user tokens, or opaque
    (non-JWT) credentials. Clients should then send the user back to login.
    """
    _ = identity  # Auth gate only; the service re-derives everything it needs.
    bundle = await auth_svc.refresh(extract_credential(request))
    return LoginResponse(
        access_token=bundle.access_token,
        token_type=bundle.token_type,
        expires_in=bundle.expires_in,
        must_change_password=bundle.must_change_password,
    )


@router.get("/users/me", summary="Get current user")
async def get_current_user(
    identity: Identity = get_current_identity(allow_expired_password=True),
    user_svc: UserService = Depends(get_user_service),
) -> CurrentUserResponse:
    """Return the authenticated user's profile."""
    view = await user_svc.get_self(identity.sub)
    return CurrentUserResponse(
        id=view.id,
        email=view.email,
        first_name=view.first_name,
        last_name=view.last_name,
        active=view.active,
        permissions=[e.name for e in view.effective],
        must_change_password=view.must_change_password,
        created_at=view.created_at,
        updated_at=view.updated_at,
    )


@router.post("/users/me:change-password", summary="Change own password")
async def change_password(
    body: ChangePasswordRequest,
    identity: Identity = get_current_identity(allow_expired_password=True),
    auth_svc: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """Change the authenticated user's password and return a fresh token.

    A new token is required because the caller's current token still carries the
    stale ``must_change_password`` claim; returning a re-minted token is what
    actually clears the rotation gate client-side.
    """
    bundle = await auth_svc.change_own_password(
        ChangePasswordPayload(
            current_password=body.current_password,
            new_password=body.new_password,
        ),
        identity=identity,
    )
    return LoginResponse(
        access_token=bundle.access_token,
        token_type=bundle.token_type,
        expires_in=bundle.expires_in,
        must_change_password=bundle.must_change_password,
    )


@router.post(
    "/users:create-admin",
    summary="Create first admin (one-time setup)",
    responses={
        **PUBLIC_ERROR_RESPONSES,
        **gone("Setup already complete — the first admin exists and this endpoint is closed."),
    },
)
async def create_admin(
    body: CreateAdminRequest,
    auth_svc: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """First-run setup: create the first admin user and auto-login.

    Unauthenticated by design — there is no admin to authenticate as yet. The
    operation self-closes once any user exists (returns 410 ``setup_already_complete``
    thereafter), so it is safe to expose only during first boot.
    """
    bundle = await auth_svc.bootstrap_admin(
        email=body.email,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    return LoginResponse(
        access_token=bundle.access_token,
        token_type=bundle.token_type,
        expires_in=bundle.expires_in,
        must_change_password=bundle.must_change_password,
    )


@router.post("/users:redeem-invite", summary="Redeem invite")
async def redeem_invite(
    body: RedeemInviteRequest,
    invite_svc: InviteService = Depends(get_invite_service),
) -> LoginResponse:
    """Redeem an invite token, set password, and return a JWT."""
    bundle = await invite_svc.redeem(body.invite_token, body.password)
    return LoginResponse(
        access_token=bundle.access_token,
        token_type=bundle.token_type,
        expires_in=bundle.expires_in,
        must_change_password=bundle.must_change_password,
    )
