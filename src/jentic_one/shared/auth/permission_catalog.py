"""Static permission catalogue and implication map (tier-neutral).

Home of the permission *data* — the `Permission` dataclass, the `ALL_PERMISSIONS`
catalogue, the derived `IMPLICATION_MAP`, and the pure `compute_effective` /
`compute_implies_transitive` expansion helpers. It lives in ``shared/auth`` (not
``admin``) so any tier can expand grants through the implication map without a
layering inversion: ``shared`` callers (``shared/web/deps.py``,
``shared/web/scope_catalog.py``, ``shared/web/endpoint_scopes.py``,
``shared/auth/permissions.py``) and non-web service callers alike import from here
rather than reaching up into the admin tier (#938).

``admin.core.permissions`` re-exports everything here for backward compatibility,
so existing ``from jentic_one.admin.core.permissions import …`` call sites keep
working unchanged; admin stays the documented home for permission *concepts*, the
data just lives in shared. This module depends only on ``shared/scopes.py`` — no
admin-tier import — which is what lets the ``test_shared_does_not_import_admin_permissions``
arch guard pass unconditionally.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jentic_one.shared.scopes import (
    OWNER_ACCESS_REQUESTS_READ,
    OWNER_AGENTS_READ,
    OWNER_CREDENTIALS_READ,
    OWNER_RESOURCES_READ,
    OWNER_SERVICE_ACCOUNTS_READ,
    OWNER_TOOLKITS_READ,
)

CAPABILITIES_EXECUTE = "capabilities:execute"
CAPABILITIES_READ = "capabilities:read"
TOOLKITS_WRITE = "toolkits:write"
TOOLKITS_READ = "toolkits:read"
USERS_WRITE = "users:write"
USERS_READ = "users:read"
JOBS_WRITE = "jobs:write"
JOBS_READ = "jobs:read"
EVENTS_WRITE = "events:write"
EVENTS_READ = "events:read"
CREDENTIALS_READ = "credentials:read"
CREDENTIALS_WRITE = "credentials:write"
APIS_READ = "apis:read"
APIS_WRITE = "apis:write"
CATALOG_IMPORT = "catalog:import"
OVERLAYS_CONFIRM = "overlays:confirm"
EXECUTIONS_READ = "executions:read"
AUDIT_READ = "audit:read"
AGENTS_READ = "agents:read"
AGENTS_WRITE = "agents:write"
SERVICE_ACCOUNTS_READ = "service-accounts:read"
SERVICE_ACCOUNTS_WRITE = "service-accounts:write"
CONFIG_READ = "config:read"
CONFIG_WRITE = "config:write"
WEBHOOKS_READ = "webhooks:read"
WEBHOOKS_WRITE = "webhooks:write"
ORG_ADMIN = "org:admin"


@dataclass(frozen=True)
class Permission:
    """A permission entry with metadata and implications."""

    name: str
    description: str
    implies: frozenset[str] = field(default_factory=frozenset)


ALL_PERMISSIONS: dict[str, Permission] = {
    ORG_ADMIN: Permission(
        name=ORG_ADMIN,
        description="Full organisation administrator access",
        implies=frozenset(
            {
                CAPABILITIES_EXECUTE,
                CAPABILITIES_READ,
                TOOLKITS_WRITE,
                TOOLKITS_READ,
                USERS_WRITE,
                USERS_READ,
                JOBS_WRITE,
                JOBS_READ,
                EVENTS_WRITE,
                EVENTS_READ,
                CREDENTIALS_READ,
                CREDENTIALS_WRITE,
                APIS_READ,
                APIS_WRITE,
                CATALOG_IMPORT,
                OVERLAYS_CONFIRM,
                EXECUTIONS_READ,
                AUDIT_READ,
                AGENTS_WRITE,
                AGENTS_READ,
                SERVICE_ACCOUNTS_WRITE,
                SERVICE_ACCOUNTS_READ,
                CONFIG_WRITE,
                CONFIG_READ,
                WEBHOOKS_WRITE,
                WEBHOOKS_READ,
            }
        ),
    ),
    CAPABILITIES_EXECUTE: Permission(
        name=CAPABILITIES_EXECUTE,
        description="Execute capabilities via the broker",
        implies=frozenset({CAPABILITIES_READ, APIS_READ, EXECUTIONS_READ}),
    ),
    CAPABILITIES_READ: Permission(
        name=CAPABILITIES_READ,
        description="Read capability and toolkit metadata",
    ),
    TOOLKITS_WRITE: Permission(
        name=TOOLKITS_WRITE,
        description="Create, update, and delete toolkits",
        implies=frozenset({TOOLKITS_READ}),
    ),
    TOOLKITS_READ: Permission(
        name=TOOLKITS_READ,
        description="Read toolkit configuration and status",
    ),
    USERS_WRITE: Permission(
        name=USERS_WRITE,
        description="Create, update, and disable users",
        implies=frozenset({USERS_READ}),
    ),
    USERS_READ: Permission(
        name=USERS_READ,
        description="Read user profiles and permissions",
    ),
    JOBS_WRITE: Permission(
        name=JOBS_WRITE,
        description="Cancel and manage async jobs",
        implies=frozenset({JOBS_READ}),
    ),
    JOBS_READ: Permission(
        name=JOBS_READ,
        description="Read job status and results",
    ),
    EVENTS_WRITE: Permission(
        name=EVENTS_WRITE,
        description="Acknowledge and manage platform events",
        implies=frozenset({EVENTS_READ}),
    ),
    EVENTS_READ: Permission(
        name=EVENTS_READ,
        description="Read platform events",
    ),
    CREDENTIALS_WRITE: Permission(
        name=CREDENTIALS_WRITE,
        description="Create, update, and delete credentials",
        implies=frozenset({CREDENTIALS_READ}),
    ),
    CREDENTIALS_READ: Permission(
        name=CREDENTIALS_READ,
        description="Read credential metadata",
    ),
    APIS_READ: Permission(
        name=APIS_READ,
        description="Read API definitions and metadata",
    ),
    APIS_WRITE: Permission(
        name=APIS_WRITE,
        description="Import, update, and delete API definitions and their revisions",
        implies=frozenset({APIS_READ, CATALOG_IMPORT}),
    ),
    CATALOG_IMPORT: Permission(
        name=CATALOG_IMPORT,
        description="Import an API from the public catalog into the local registry",
        implies=frozenset({APIS_READ}),
    ),
    OVERLAYS_CONFIRM: Permission(
        name=OVERLAYS_CONFIRM,
        description=(
            "Confirm a pending overlay, rewriting the API's served spec "
            "(operator action; not granted to agents by default)"
        ),
        implies=frozenset({APIS_READ}),
    ),
    EXECUTIONS_READ: Permission(
        name=EXECUTIONS_READ,
        description="Read execution records",
    ),
    AUDIT_READ: Permission(
        name=AUDIT_READ,
        description="Read audit log entries",
    ),
    AGENTS_WRITE: Permission(
        name=AGENTS_WRITE,
        description="Create, update, and delete agents",
        implies=frozenset({AGENTS_READ}),
    ),
    AGENTS_READ: Permission(
        name=AGENTS_READ,
        description="Read agent configuration and status",
    ),
    SERVICE_ACCOUNTS_WRITE: Permission(
        name=SERVICE_ACCOUNTS_WRITE,
        description="Create, update, and delete service accounts",
        implies=frozenset({SERVICE_ACCOUNTS_READ}),
    ),
    SERVICE_ACCOUNTS_READ: Permission(
        name=SERVICE_ACCOUNTS_READ,
        description="Read service account configuration and status",
    ),
    CONFIG_WRITE: Permission(
        name=CONFIG_WRITE,
        description="Create and update runtime platform configuration",
        implies=frozenset({CONFIG_READ}),
    ),
    CONFIG_READ: Permission(
        name=CONFIG_READ,
        description="Read runtime platform configuration",
    ),
    WEBHOOKS_WRITE: Permission(
        name=WEBHOOKS_WRITE,
        description=(
            "Create, rotate, and delete webhook endpoints. Privileged: an endpoint "
            "receives signed platform events at a destination URL, so configuring one "
            "is deliberately operator-only and never granted to agents by default"
        ),
        implies=frozenset({WEBHOOKS_READ}),
    ),
    WEBHOOKS_READ: Permission(
        name=WEBHOOKS_READ,
        description="Read webhook endpoint configuration and delivery history",
    ),
    OWNER_RESOURCES_READ: Permission(
        name=OWNER_RESOURCES_READ,
        description="Read resources owned by the agent's creator (umbrella)",
        implies=frozenset({OWNER_CREDENTIALS_READ, OWNER_AGENTS_READ, OWNER_TOOLKITS_READ}),
    ),
    OWNER_CREDENTIALS_READ: Permission(
        name=OWNER_CREDENTIALS_READ,
        description="Read credentials owned by the agent's creator",
    ),
    OWNER_AGENTS_READ: Permission(
        name=OWNER_AGENTS_READ,
        description="Read agents owned by the agent's creator",
    ),
    OWNER_TOOLKITS_READ: Permission(
        name=OWNER_TOOLKITS_READ,
        description="Read toolkits owned by the agent's creator",
    ),
    OWNER_ACCESS_REQUESTS_READ: Permission(
        name=OWNER_ACCESS_REQUESTS_READ,
        description="Read access requests filed by or for the agent's creator",
    ),
    OWNER_SERVICE_ACCOUNTS_READ: Permission(
        name=OWNER_SERVICE_ACCOUNTS_READ,
        description="Read service accounts owned by the agent's creator",
    ),
}

IMPLICATION_MAP: dict[str, set[str]] = {
    perm.name: set(perm.implies) for perm in ALL_PERMISSIONS.values() if perm.implies
}


def compute_implies_transitive(permission_name: str) -> set[str]:
    """Compute the full transitive closure of implied permissions."""
    result: set[str] = set()
    frontier = [permission_name]
    while frontier:
        current = frontier.pop()
        implied = IMPLICATION_MAP.get(current, set())
        for p in implied:
            if p not in result:
                result.add(p)
                frontier.append(p)
    return result


def compute_effective(grants: set[str]) -> set[str]:
    """Expand direct grants via the implication map to compute the full effective set."""
    effective = set(grants)
    frontier = list(grants)
    while frontier:
        perm = frontier.pop()
        implied = IMPLICATION_MAP.get(perm, set())
        for p in implied:
            if p not in effective:
                effective.add(p)
                frontier.append(p)
    return effective


#: Public surface re-exported verbatim by the ``admin.core.permissions`` shim. The
#: ``OWNER_*`` scope constants are intentionally excluded — their canonical home is
#: ``shared.scopes`` and callers import them from there, not via this catalogue. Kept
#: in sync with the shim's ``__all__`` (asserted by test_permission_catalog).
__all__ = [
    "AGENTS_READ",
    "AGENTS_WRITE",
    "ALL_PERMISSIONS",
    "APIS_READ",
    "APIS_WRITE",
    "AUDIT_READ",
    "CAPABILITIES_EXECUTE",
    "CAPABILITIES_READ",
    "CATALOG_IMPORT",
    "CONFIG_READ",
    "CONFIG_WRITE",
    "CREDENTIALS_READ",
    "CREDENTIALS_WRITE",
    "EVENTS_READ",
    "EVENTS_WRITE",
    "EXECUTIONS_READ",
    "IMPLICATION_MAP",
    "JOBS_READ",
    "JOBS_WRITE",
    "ORG_ADMIN",
    "OVERLAYS_CONFIRM",
    "SERVICE_ACCOUNTS_READ",
    "SERVICE_ACCOUNTS_WRITE",
    "TOOLKITS_READ",
    "TOOLKITS_WRITE",
    "USERS_READ",
    "USERS_WRITE",
    "WEBHOOKS_READ",
    "WEBHOOKS_WRITE",
    "Permission",
    "compute_effective",
    "compute_implies_transitive",
]
