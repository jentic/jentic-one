"""Audit-related enums shared across modules."""

from enum import StrEnum


class AuditAction(StrEnum):
    """Actions that produce audit entries."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    PROMOTE = "promote"
    DEMOTE = "demote"
    ENABLE = "enable"
    DISABLE = "disable"
    REVOKE = "revoke"
    GRANT = "grant"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    APPROVE = "approve"
    DENY = "deny"
    ARCHIVE = "archive"
    ROTATE = "rotate"
    REFRESH = "refresh"
    CONFIRM = "confirm"
    DEPRECATE = "deprecate"
    REGISTER = "register"
    CLAIM = "claim"


class AuditReason(StrEnum):
    """Structured reasons for audited credential operations."""

    API_KEY_ROTATED = "api_key_rotated"
    API_KEY_REVOKED = "api_key_revoked"
    CLIENT_SECRET_ROTATED = "client_secret_rotated"


class AuditTargetType(StrEnum):
    """Entity types that can be the target of an audited action."""

    REVISION = "revision"
    PERMISSION = "permission"
    USER = "user"
    CREDENTIAL = "credential"
    TOOLKIT = "toolkit"
    AGENT = "agent"
    JOB = "job"
    ORGANISATION = "organisation"
    INVITE_TOKEN = "invite_token"
    EVENT = "event"
    EXECUTION_RECORD = "execution_record"
    SERVICE_ACCOUNT = "service_account"
    TOKEN = "token"
    OVERLAY = "overlay"
    NOTE = "note"
    API = "api"
    ACCESS_REQUEST = "access_request"
    TOOLKIT_KEY = "toolkit_key"
    CREDENTIAL_BINDING = "credential_binding"
    SESSION = "session"
    PROVIDER_CONFIG = "provider_config"
