"""FastAPI dependencies for the auth web layer."""

from __future__ import annotations

from fastapi import Depends

from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.legacy_service_account_identity_service import (
    LegacyServiceAccountIdentityService,
)
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_ctx


def get_user_service(ctx: Context = Depends(get_ctx)) -> UserService:
    return UserService(ctx)


def get_agent_service(ctx: Context = Depends(get_ctx)) -> AgentService:
    return AgentService(ctx)


def get_agent_auth_service(ctx: Context = Depends(get_ctx)) -> AgentAuthService:
    return AgentAuthService(ctx)


def get_legacy_service_account_identity_service(
    ctx: Context = Depends(get_ctx),
) -> LegacyServiceAccountIdentityService:
    return LegacyServiceAccountIdentityService(ctx)
