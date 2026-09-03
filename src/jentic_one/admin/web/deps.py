"""FastAPI dependencies for the admin web layer."""

from __future__ import annotations

from fastapi import Depends, Request

from jentic_one.admin.services.actor_service import ActorService
from jentic_one.admin.services.audit_service import AuditService
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.event_service import EventService
from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.execution_service import ExecutionService
from jentic_one.admin.services.health_service import HealthService
from jentic_one.admin.services.invite_service import InviteService
from jentic_one.admin.services.job_result_service import JobResultService
from jentic_one.admin.services.job_service import JobService
from jentic_one.admin.services.monitoring_service import MonitoringService
from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.oauth_grant_admin_service import OAuthGrantAdminService
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.admin.services.provider_config_service import ProviderConfigService
from jentic_one.admin.services.user_service import UserService
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_ctx


def get_actor_service(ctx: Context = Depends(get_ctx)) -> ActorService:
    return ActorService(ctx)


def get_audit_service(ctx: Context = Depends(get_ctx)) -> AuditService:
    return AuditService(ctx)


def get_health_service(ctx: Context = Depends(get_ctx)) -> HealthService:
    return HealthService(ctx)


def get_auth_service(ctx: Context = Depends(get_ctx)) -> AuthService:
    return AuthService(ctx)


def get_user_service(ctx: Context = Depends(get_ctx)) -> UserService:
    return UserService(ctx)


def get_permission_service(ctx: Context = Depends(get_ctx)) -> PermissionService:
    return PermissionService(ctx)


def get_provider_config_service(ctx: Context = Depends(get_ctx)) -> ProviderConfigService:
    return ProviderConfigService(ctx)


def get_invite_service(ctx: Context = Depends(get_ctx)) -> InviteService:
    return InviteService(ctx)


def get_execution_service(ctx: Context = Depends(get_ctx)) -> ExecutionService:
    return ExecutionService(ctx)


def get_job_service(ctx: Context = Depends(get_ctx)) -> JobService:
    return JobService(ctx)


def get_job_result_service(ctx: Context = Depends(get_ctx)) -> JobResultService:
    return JobResultService(ctx)


def get_event_service(ctx: Context = Depends(get_ctx)) -> EventService:
    return EventService(ctx)


def get_event_stream_service(ctx: Context = Depends(get_ctx)) -> EventStreamService:
    return EventStreamService(ctx)


def get_oauth_client_service(ctx: Context = Depends(get_ctx)) -> OAuthClientService:
    return OAuthClientService(ctx)


def get_oauth_grant_admin_service(ctx: Context = Depends(get_ctx)) -> OAuthGrantAdminService:
    return OAuthGrantAdminService(ctx)


def get_monitoring_service(request: Request) -> MonitoringService:
    app = request.app
    svc: MonitoringService | None = getattr(app.state, "_monitoring_service", None)
    if svc is None:
        ctx: Context = app.state.ctx
        svc = MonitoringService(ctx)
        app.state._monitoring_service = svc
    return svc
