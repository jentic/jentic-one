"""Admin service Pydantic schemas."""

from jentic_one.admin.services.schemas.auth import (
    ChangePasswordPayload,
    Identity,
    LoginPayload,
    TokenBundle,
)
from jentic_one.admin.services.schemas.events import (
    EventFilter,
    EventView,
    Heartbeat,
)
from jentic_one.admin.services.schemas.executions import (
    ExecutionFilter,
    ExecutionView,
)
from jentic_one.admin.services.schemas.health import HealthView
from jentic_one.admin.services.schemas.invites import InviteIssued, RedeemPayload
from jentic_one.admin.services.schemas.jobs import (
    JobFilter,
    JobResultView,
    JobView,
)
from jentic_one.admin.services.schemas.permissions import (
    AssignedPermissionsPayload,
    PermissionCatalogueEntry,
    PermissionsView,
)
from jentic_one.admin.services.schemas.users import (
    UserCreatedView,
    UserCreatePayload,
    UserUpdatePayload,
    UserView,
)

__all__ = [
    "AssignedPermissionsPayload",
    "ChangePasswordPayload",
    "EventFilter",
    "EventView",
    "ExecutionFilter",
    "ExecutionView",
    "HealthView",
    "Heartbeat",
    "Identity",
    "InviteIssued",
    "JobFilter",
    "JobResultView",
    "JobView",
    "LoginPayload",
    "PermissionCatalogueEntry",
    "PermissionsView",
    "RedeemPayload",
    "TokenBundle",
    "UserCreatePayload",
    "UserCreatedView",
    "UserUpdatePayload",
    "UserView",
]
