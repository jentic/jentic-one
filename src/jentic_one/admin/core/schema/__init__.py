"""Admin module schema package — Alembic import target for model discovery."""

from __future__ import annotations

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.actor_scope_grants import ActorScopeGrant
from jentic_one.admin.core.schema.agent_credentials import AgentCredential
from jentic_one.admin.core.schema.agent_toolkit_bindings import AgentToolkitBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.instance_identity import InstanceIdentity
from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.provider_configs import ProviderConfigRecord
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.core.schema.setup_sentinel import SetupSentinel
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.shared.db.base import AdminBase

__all__ = [
    "AccessToken",
    "ActorScopeGrant",
    "AdminBase",
    "Agent",
    "AgentCredential",
    "AgentToolkitBinding",
    "AuditEntry",
    "AuthorizationCode",
    "Event",
    "ExecutionRecord",
    "ExternalIdentity",
    "InstanceIdentity",
    "InviteToken",
    "Job",
    "JobResult",
    "ProviderConfigRecord",
    "RefreshToken",
    "ServiceAccount",
    "SetupSentinel",
    "User",
    "UserPermissionGrant",
    "UserSecret",
]
