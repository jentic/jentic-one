"""Canonical scope constants shared across module boundaries.

The broker cannot import from admin, so scope strings used by both modules
are defined here in shared.
"""

from __future__ import annotations

BROKER_EXECUTE_SCOPE = "capabilities:execute"

AGENTS_WRITE = "agents:write"
ORG_ADMIN = "org:admin"

OWNER_CREDENTIALS_READ = "owner:credentials:read"
OWNER_AGENTS_READ = "owner:agents:read"
OWNER_RESOURCES_READ = "owner:resources:read"

# Scopes retired from the catalogue. Stored grants — user permission rows and
# agent ``actor_scope_grants`` — still carry these strings, so every validation
# path that rejects unknown scopes must accept-and-ignore members of this set:
# a re-submit of a stored grant must never 422 just because it predates the
# retirement. Holding a retired scope grants nothing (no route requires it and
# the implication map no longer expands it).
#
# - The toolkit scopes retired in theme-5 Phase 5b (the toolkit management
#   surface is gone; authorization runs on the agent↔credential axis); they —
#   and the stored strings — are swept in Phase 6b.
# - ``owner:access-requests:read`` retired in theme 7 (the access-request flow
#   is gone; nothing is left to delegate reads over).
# - The service-account scopes retired in theme-8 Phase 2 (the service-account
#   surface is gone; every SA was migrated to a successor agent in Phase 1).
#   Stored strings are swept with the Phase-4 table drops.
RETIRED_SCOPES: frozenset[str] = frozenset(
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

DEFAULT_AGENT_SCOPES: tuple[str, ...] = (
    "capabilities:execute",
    "capabilities:read",
    "apis:read",
    "catalog:import",
    "executions:read",
    "jobs:read",
    "events:read",
    "owner:resources:read",
    "owner:agents:read",
    "owner:credentials:read",
    # Lets an agent initiate the agent-driven SSO flow. Narrower than
    # `credentials:write` — cannot read tokens or manage other credentials.
    "credentials:connect",
)

OIDC_PASSTHROUGH_SCOPES: frozenset[str] = frozenset({"openid", "email", "profile"})

# Server-side cap for the `scope` a client claims at the anonymous DCR front
# door (POST /oauth-clients): a DCR-registered client's
# `allowed_scopes` ceiling is always ⊆ this set — never unrestricted. It is the
# MCP tool surface expressed as scopes, which is exactly the
# default agent baseline: DCR clients are `consent_model='agent'` (D6), so a
# grant's effective scopes are further intersected with the bound agent's live
# scopes at consent. The OAuth discovery documents (`scopes_supported`)
# must advertise this same set.
MCP_TOOL_SCOPES: frozenset[str] = frozenset(DEFAULT_AGENT_SCOPES)
