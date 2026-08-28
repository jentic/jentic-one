"""Static permission catalogue and implication map (admin-tier re-export).

The permission *data* now lives tier-neutrally in
:mod:`jentic_one.shared.auth.permission_catalog` so ``shared`` callers can expand
grants without a ``shared → admin`` layering inversion (#938). This module
re-exports it unchanged so existing ``from jentic_one.admin.core.permissions
import …`` call sites keep working, and admin remains the documented home for
permission concepts. Import new admin-tier code from here as before; ``shared``
code must import from ``shared.auth.permission_catalog`` directly (enforced by the
``test_shared_does_not_import_admin_permissions`` arch guard).
"""

from __future__ import annotations

from jentic_one.shared.auth.permission_catalog import (
    AGENTS_READ,
    AGENTS_WRITE,
    ALL_PERMISSIONS,
    APIS_READ,
    APIS_WRITE,
    AUDIT_READ,
    CAPABILITIES_EXECUTE,
    CAPABILITIES_READ,
    CATALOG_IMPORT,
    CONFIG_READ,
    CONFIG_WRITE,
    CREDENTIALS_READ,
    CREDENTIALS_WRITE,
    EVENTS_READ,
    EVENTS_WRITE,
    EXECUTIONS_READ,
    IMPLICATION_MAP,
    JOBS_READ,
    JOBS_WRITE,
    OAUTH_CLIENTS_READ,
    OAUTH_CLIENTS_WRITE,
    ORG_ADMIN,
    OVERLAYS_CONFIRM,
    SERVICE_ACCOUNTS_READ,
    SERVICE_ACCOUNTS_WRITE,
    TOOLKITS_READ,
    TOOLKITS_WRITE,
    USERS_READ,
    USERS_WRITE,
    Permission,
    compute_effective,
    compute_implies_transitive,
)

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
    "OAUTH_CLIENTS_READ",
    "OAUTH_CLIENTS_WRITE",
    "ORG_ADMIN",
    "OVERLAYS_CONFIRM",
    "SERVICE_ACCOUNTS_READ",
    "SERVICE_ACCOUNTS_WRITE",
    "TOOLKITS_READ",
    "TOOLKITS_WRITE",
    "USERS_READ",
    "USERS_WRITE",
    "Permission",
    "compute_effective",
    "compute_implies_transitive",
]
