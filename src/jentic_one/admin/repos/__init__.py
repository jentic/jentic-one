"""Admin repository package."""

from jentic_one.admin.repos.access_token_repo import AccessTokenRepository
from jentic_one.admin.repos.actor_directory_repo import ActorDirectoryRepository
from jentic_one.admin.repos.actor_scope_grant_repo import ActorScopeGrantRepository
from jentic_one.admin.repos.agent_credential_repo import AgentCredentialRepository
from jentic_one.admin.repos.agent_repo import AgentRepository
from jentic_one.admin.repos.agent_toolkit_binding_repo import AgentToolkitBindingRepository
from jentic_one.admin.repos.audit_repo import AuditRepository
from jentic_one.admin.repos.authorization_code_repo import AuthorizationCodeRepository
from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.admin.repos.execution_record_repo import ExecutionRecordRepository
from jentic_one.admin.repos.external_identity_repo import ExternalIdentityRepository
from jentic_one.admin.repos.invite_token_repo import InviteTokenRepository
from jentic_one.admin.repos.job_repo import JobRepository
from jentic_one.admin.repos.job_result_repo import JobResultRepository
from jentic_one.admin.repos.monitoring_repo import MonitoringRepository
from jentic_one.admin.repos.oauth_client_repo import OAuthClientRepository
from jentic_one.admin.repos.provider_config_repo import ProviderConfigRepository
from jentic_one.admin.repos.refresh_token_repo import RefreshTokenRepository
from jentic_one.admin.repos.service_account_credential_repo import (
    ServiceAccountCredentialRepository,
)
from jentic_one.admin.repos.service_account_repo import ServiceAccountRepository
from jentic_one.admin.repos.user_permission_grant_repo import UserPermissionGrantRepository
from jentic_one.admin.repos.user_repo import UserRepository
from jentic_one.admin.repos.user_secret_repo import UserSecretRepository

__all__ = [
    "AccessTokenRepository",
    "ActorDirectoryRepository",
    "ActorScopeGrantRepository",
    "AgentCredentialRepository",
    "AgentRepository",
    "AgentToolkitBindingRepository",
    "AuditRepository",
    "AuthorizationCodeRepository",
    "EventRepository",
    "ExecutionRecordRepository",
    "ExternalIdentityRepository",
    "InviteTokenRepository",
    "JobRepository",
    "JobResultRepository",
    "MonitoringRepository",
    "OAuthClientRepository",
    "ProviderConfigRepository",
    "RefreshTokenRepository",
    "ServiceAccountCredentialRepository",
    "ServiceAccountRepository",
    "UserPermissionGrantRepository",
    "UserRepository",
    "UserSecretRepository",
]
