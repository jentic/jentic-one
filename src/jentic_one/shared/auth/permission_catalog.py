"""Static permission catalogue and implication map (tier-neutral).

Home of the permission *data* — the permission name constants, the `Permission`
dataclass, the `ALL_PERMISSIONS` catalogue, the derived `IMPLICATION_MAP`, and the
pure `compute_effective` / `compute_implies_transitive` expansion helpers. It lives
in ``shared/auth`` (not ``admin``) so any tier can expand grants through the
implication map without a layering inversion: ``shared`` callers
(``shared/web/deps.py``, ``shared/web/permission_catalog_payload.py``,
``shared/web/endpoint_permissions.py``, ``shared/auth/permissions.py``) and non-web
service callers alike import from here rather than reaching up into the admin tier
(#938). The broker is the reason the constants are tier-neutral: it cannot import
from admin, so the permission strings it shares with admin are defined here.

``admin.core.permissions`` re-exports everything here for backward compatibility,
so existing ``from jentic_one.admin.core.permissions import …`` call sites keep
working unchanged; admin stays the documented home for permission *concepts*, the
data just lives in shared. This module has no admin-tier import, which is what lets
the ``test_shared_does_not_import_admin_permissions`` arch guard pass
unconditionally.

**Vocabulary.** A *permission* is what an actor may do — the thing stored in
``actor_permission_grants``, carried on ``Identity.permissions``, and enforced by
route guards. A *scope* is the same string travelling over this deployment's own
OAuth2/OIDC plane. Constants named ``*_SCOPES`` below are therefore deliberate:
they describe the OAuth2 wire surface, not internal authorization. The two formats
are identical today, and the places that would break if they ever diverged are
:data:`MCP_TOOL_SCOPES` here and
:func:`jentic_one.shared.auth.verify.scopes_to_permissions`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CAPABILITIES_EXECUTE = "capabilities:execute"
CAPABILITIES_READ = "capabilities:read"
USERS_WRITE = "users:write"
USERS_READ = "users:read"
JOBS_WRITE = "jobs:write"
JOBS_READ = "jobs:read"
EVENTS_WRITE = "events:write"
EVENTS_READ = "events:read"
CREDENTIALS_READ = "credentials:read"
CREDENTIALS_WRITE = "credentials:write"
# Narrower than CREDENTIALS_WRITE: agents that hold CREDENTIALS_CONNECT can
# start and poll an integration connect session (the agent-driven SSO
# flow) but cannot read tokens or manage other credentials.
CREDENTIALS_CONNECT = "credentials:connect"
APIS_READ = "apis:read"
APIS_WRITE = "apis:write"
CATALOG_IMPORT = "catalog:import"
OVERLAYS_CONFIRM = "overlays:confirm"
EXECUTIONS_READ = "executions:read"
AUDIT_READ = "audit:read"
AGENTS_READ = "agents:read"
AGENTS_WRITE = "agents:write"
CONFIG_READ = "config:read"
CONFIG_WRITE = "config:write"
OAUTH_CLIENTS_READ = "oauth-clients:read"
OAUTH_CLIENTS_WRITE = "oauth-clients:write"
ORG_ADMIN = "org:admin"

OWNER_CREDENTIALS_READ = "owner:credentials:read"
OWNER_AGENTS_READ = "owner:agents:read"
OWNER_RESOURCES_READ = "owner:resources:read"

#: The permission every executing actor must hold for the broker data plane. An
#: alias rather than a second literal: the broker's requirement *is*
#: ``capabilities:execute``, and the named form is what broker and reference code
#: read so the requirement is greppable by intent.
BROKER_EXECUTE_PERMISSION = CAPABILITIES_EXECUTE

#: Permissions retired from the catalogue. Stored grants — user permission rows and
#: agent ``actor_permission_grants`` — still carry these strings, so every validation
#: path that rejects unknown permissions must accept-and-ignore members of this set:
#: a re-submit of a stored grant must never 422 just because it predates the
#: retirement. Holding a retired permission grants nothing (no route requires it and
#: the implication map no longer expands it).
#:
#: - The toolkit permissions retired in theme-5 Phase 5b (the toolkit management
#:   surface is gone; authorization runs on the agent↔credential axis); they —
#:   and the stored strings — are swept in Phase 6b.
#: - ``owner:access-requests:read`` retired in theme 7 (the access-request flow
#:   is gone; nothing is left to delegate reads over).
#: - The service-account permissions retired in theme-8 Phase 2 (the
#:   service-account surface is gone; every SA was migrated to a successor agent
#:   in Phase 1). Stored strings are swept with the Phase-4 table drops.
RETIRED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "toolkits:read",
        "toolkits:write",
        "owner:toolkits:read",
        "owner:access-requests:read",
        "service-accounts:read",
        "service-accounts:write",
        "owner:service-accounts:read",
    }
)

#: The permission baseline every new agent is granted.
DEFAULT_AGENT_PERMISSIONS: tuple[str, ...] = (
    CAPABILITIES_EXECUTE,
    CAPABILITIES_READ,
    APIS_READ,
    CATALOG_IMPORT,
    EXECUTIONS_READ,
    JOBS_READ,
    EVENTS_READ,
    OWNER_RESOURCES_READ,
    OWNER_AGENTS_READ,
    OWNER_CREDENTIALS_READ,
    # Lets an agent initiate the agent-driven SSO flow. Narrower than
    # `credentials:write` — cannot read tokens or manage other credentials.
    CREDENTIALS_CONNECT,
)

#: OIDC scopes passed through this deployment's own authorization server without
#: being treated as authorization grants. OAuth2 plane — genuinely scopes.
OIDC_PASSTHROUGH_SCOPES: frozenset[str] = frozenset({"openid", "email", "profile"})

#: Server-side cap for the `scope` a client claims at the anonymous DCR front
#: door (POST /oauth-clients): a DCR-registered client's `allowed_scopes` ceiling is
#: always ⊆ this set — never unrestricted. It is the MCP tool surface expressed as
#: scopes, which is exactly the default agent baseline: DCR clients are
#: `consent_model='agent'` (D6), so a grant's effective scopes are further
#: intersected with the bound agent's live permissions at consent. The OAuth
#: discovery documents (`scopes_supported`) must advertise this same set.
#:
#: This assignment is the scope↔permission identity in constant form: an OAuth2-plane
#: ceiling defined as the internal permission baseline. It and
#: :func:`jentic_one.shared.auth.verify.scopes_to_permissions` are the two places
#: that break if the two formats ever diverge.
MCP_TOOL_SCOPES: frozenset[str] = frozenset(DEFAULT_AGENT_PERMISSIONS)


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
                USERS_WRITE,
                USERS_READ,
                JOBS_WRITE,
                JOBS_READ,
                EVENTS_WRITE,
                EVENTS_READ,
                CREDENTIALS_READ,
                CREDENTIALS_WRITE,
                CREDENTIALS_CONNECT,
                APIS_READ,
                APIS_WRITE,
                CATALOG_IMPORT,
                OVERLAYS_CONFIRM,
                EXECUTIONS_READ,
                AUDIT_READ,
                AGENTS_WRITE,
                AGENTS_READ,
                CONFIG_WRITE,
                CONFIG_READ,
                OAUTH_CLIENTS_WRITE,
                OAUTH_CLIENTS_READ,
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
        description="Read capability metadata",
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
        implies=frozenset({CREDENTIALS_READ, CREDENTIALS_CONNECT}),
    ),
    CREDENTIALS_CONNECT: Permission(
        name=CREDENTIALS_CONNECT,
        description=(
            "Start and poll an integration connect session (agent-driven SSO). "
            "Cannot read tokens or manage other credentials."
        ),
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
    CONFIG_WRITE: Permission(
        name=CONFIG_WRITE,
        description="Create and update runtime platform configuration",
        implies=frozenset({CONFIG_READ}),
    ),
    CONFIG_READ: Permission(
        name=CONFIG_READ,
        description="Read runtime platform configuration",
    ),
    OAUTH_CLIENTS_WRITE: Permission(
        name=OAUTH_CLIENTS_WRITE,
        description="Create, update, disable, and rotate secrets of OAuth clients",
        implies=frozenset({OAUTH_CLIENTS_READ}),
    ),
    OAUTH_CLIENTS_READ: Permission(
        name=OAUTH_CLIENTS_READ,
        description="Read OAuth client registrations",
    ),
    OWNER_RESOURCES_READ: Permission(
        name=OWNER_RESOURCES_READ,
        description="Read resources owned by the agent's creator (umbrella)",
        implies=frozenset({OWNER_CREDENTIALS_READ, OWNER_AGENTS_READ}),
    ),
    OWNER_CREDENTIALS_READ: Permission(
        name=OWNER_CREDENTIALS_READ,
        description="Read credentials owned by the agent's creator",
    ),
    OWNER_AGENTS_READ: Permission(
        name=OWNER_AGENTS_READ,
        description="Read agents owned by the agent's creator",
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


#: Public surface re-exported verbatim by the ``admin.core.permissions`` shim. Kept
#: in sync with the shim's ``__all__`` (asserted by test_permission_catalog).
#:
#: The ``OWNER_*`` delegated-read permissions, the collections
#: (``DEFAULT_AGENT_PERMISSIONS``, ``RETIRED_PERMISSIONS``) and the OAuth2-plane sets
#: (``MCP_TOOL_SCOPES``, ``OIDC_PASSTHROUGH_SCOPES``) are intentionally excluded from
#: the admin shim: they are tier-neutral by construction and callers import them from
#: this module directly.
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
    "CREDENTIALS_CONNECT",
    "CREDENTIALS_READ",
    "CREDENTIALS_WRITE",
    "EVENTS_READ",
    "EVENTS_WRITE",
    "EXECUTIONS_READ",
    "IMPLICATION_MAP",
    "JOBS_READ",
    "JOBS_WRITE",
    "OAUTH_CLIENTS_READ",
    "OAUTH_CLIENTS_WRITE",
    "ORG_ADMIN",
    "OVERLAYS_CONFIRM",
    "USERS_READ",
    "USERS_WRITE",
    "Permission",
    "compute_effective",
    "compute_implies_transitive",
]
