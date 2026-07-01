"""Unit tests for admin permission implication expansion."""

from __future__ import annotations

from jentic_one.admin.core.permissions import (
    AGENTS_READ,
    AGENTS_WRITE,
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
    JOBS_READ,
    JOBS_WRITE,
    ORG_ADMIN,
    SERVICE_ACCOUNTS_READ,
    SERVICE_ACCOUNTS_WRITE,
    TOOLKITS_READ,
    TOOLKITS_WRITE,
    USERS_READ,
    USERS_WRITE,
    compute_effective,
)


def test_compute_effective_empty_input() -> None:
    assert compute_effective(set()) == set()


def test_compute_effective_single_leaf_permission() -> None:
    result = compute_effective({TOOLKITS_READ})
    assert result == {TOOLKITS_READ}


def test_compute_effective_single_direct_implication() -> None:
    result = compute_effective({TOOLKITS_WRITE})
    assert result == {TOOLKITS_WRITE, TOOLKITS_READ}


def test_compute_effective_transitive_expansion() -> None:
    result = compute_effective({ORG_ADMIN})
    assert USERS_WRITE in result
    assert USERS_READ in result
    assert TOOLKITS_WRITE in result
    assert TOOLKITS_READ in result
    assert CAPABILITIES_EXECUTE in result
    assert CAPABILITIES_READ in result
    assert JOBS_WRITE in result
    assert JOBS_READ in result
    assert EVENTS_WRITE in result
    assert EVENTS_READ in result
    assert AUDIT_READ in result
    assert CREDENTIALS_READ in result
    assert CREDENTIALS_WRITE in result
    assert APIS_READ in result
    assert APIS_WRITE in result
    assert EXECUTIONS_READ in result


def test_compute_effective_org_admin_expands_all() -> None:
    result = compute_effective({ORG_ADMIN})
    expected = {
        ORG_ADMIN,
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
        EXECUTIONS_READ,
        AUDIT_READ,
        AGENTS_WRITE,
        AGENTS_READ,
        SERVICE_ACCOUNTS_WRITE,
        SERVICE_ACCOUNTS_READ,
        CONFIG_WRITE,
        CONFIG_READ,
    }
    assert result == expected


def test_compute_effective_multiple_grants() -> None:
    result = compute_effective({TOOLKITS_WRITE, EVENTS_WRITE})
    assert result == {TOOLKITS_WRITE, TOOLKITS_READ, EVENTS_WRITE, EVENTS_READ}


def test_compute_effective_idempotency() -> None:
    first = compute_effective({ORG_ADMIN})
    second = compute_effective(first)
    assert first == second


def test_compute_effective_redundant_grants() -> None:
    result = compute_effective({USERS_WRITE, USERS_READ})
    assert result == {USERS_WRITE, USERS_READ}


def test_compute_effective_capabilities_execute_implies_apis_and_executions() -> None:
    result = compute_effective({CAPABILITIES_EXECUTE})
    assert CAPABILITIES_READ in result
    assert APIS_READ in result
    assert EXECUTIONS_READ in result


def test_compute_effective_credentials_write_implies_read() -> None:
    result = compute_effective({CREDENTIALS_WRITE})
    assert CREDENTIALS_READ in result
