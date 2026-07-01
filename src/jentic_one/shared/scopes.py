"""Canonical scope constants shared across module boundaries.

The broker cannot import from admin, so scope strings used by both modules
are defined here in shared.
"""

from __future__ import annotations

BROKER_EXECUTE_SCOPE = "capabilities:execute"

AGENTS_WRITE = "agents:write"
ORG_ADMIN = "org:admin"

OWNER_CREDENTIALS_READ = "owner:credentials:read"
OWNER_ACCESS_REQUESTS_READ = "owner:access-requests:read"
OWNER_AGENTS_READ = "owner:agents:read"
OWNER_TOOLKITS_READ = "owner:toolkits:read"
OWNER_RESOURCES_READ = "owner:resources:read"
OWNER_SERVICE_ACCOUNTS_READ = "owner:service-accounts:read"

DEFAULT_AGENT_SCOPES: tuple[str, ...] = (
    "capabilities:execute",
    "capabilities:read",
    "apis:read",
    "catalog:import",
    "executions:read",
    "jobs:read",
    "events:read",
    "owner:resources:read",
    "owner:toolkits:read",
    "owner:agents:read",
    "owner:credentials:read",
    "owner:access-requests:read",
)

# Scopes an agent may obtain through a self-service ``scope:grant`` access
# request: the safe agent baseline (its default reads/execute) plus a small set
# of elevations a human owner can approve without handing over administrative
# power.
#
# ``apis:write`` is included so an agent can request the broader ability to
# import, update, and delete arbitrary API definitions (URL/inline import via
# ``POST /apis``) and have a human approve it. Importing an already-cataloged
# API is now a default agent capability via ``catalog:import`` (in
# ``DEFAULT_AGENT_SCOPES``), so agents no longer need to file a request just to
# run ``jentic catalog import``. ``apis:write`` is deliberately NOT in
# ``DEFAULT_AGENT_SCOPES``: the agent must file the request and an owner must
# approve it.
#
# Still excludes the truly privileged scopes (``org:admin``, ``agents:write``)
# so an owner with ``agents:write`` cannot self-escalate an agent to admin via
# the access-request path (confused-deputy).
GRANTABLE_SCOPES: frozenset[str] = frozenset(DEFAULT_AGENT_SCOPES) | {"apis:write"}
