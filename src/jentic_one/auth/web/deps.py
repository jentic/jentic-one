"""FastAPI dependencies for the auth web layer."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Form

from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.service_account_auth_service import ServiceAccountAuthService
from jentic_one.auth.services.service_account_service import ServiceAccountService
from jentic_one.auth.web.schemas.oauth import IntrospectRequest, RevokeRequest, TokenRequest
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_ctx


def form_token_request(
    grant_type: Annotated[str, Form()],
    refresh_token: Annotated[str | None, Form()] = None,
    assertion: Annotated[str | None, Form()] = None,
    client_id: Annotated[str | None, Form()] = None,
    client_secret: Annotated[str | None, Form()] = None,
    code: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
) -> TokenRequest:
    return TokenRequest(
        grant_type=grant_type,
        refresh_token=refresh_token,
        assertion=assertion,
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def form_revoke_request(
    token: Annotated[str, Form()],
    token_type_hint: Annotated[str | None, Form()] = None,
) -> RevokeRequest:
    return RevokeRequest(token=token, token_type_hint=token_type_hint)


def form_introspect_request(
    token: Annotated[str, Form()],
    token_type_hint: Annotated[str | None, Form()] = None,
) -> IntrospectRequest:
    return IntrospectRequest(token=token, token_type_hint=token_type_hint)


def get_user_service(ctx: Context = Depends(get_ctx)) -> UserService:
    return UserService(ctx)


def get_agent_service(ctx: Context = Depends(get_ctx)) -> AgentService:
    return AgentService(ctx)


def get_agent_auth_service(ctx: Context = Depends(get_ctx)) -> AgentAuthService:
    return AgentAuthService(ctx)


def get_service_account_service(ctx: Context = Depends(get_ctx)) -> ServiceAccountService:
    return ServiceAccountService(ctx)


def get_service_account_auth_service(ctx: Context = Depends(get_ctx)) -> ServiceAccountAuthService:
    return ServiceAccountAuthService(ctx)
