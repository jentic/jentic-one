"""Unit + drift tests for the conceptual scope catalogue.

The catalogue (``GET /reference/endpoints.json`` ``scopes`` section, and the
committed ``docs/reference/endpoints.json``) is built from
``ALL_PERMISSIONS`` so it can never diverge from the enforced permission set.
These tests pin that invariant and the derived shape the docs SPA relies on.
"""

from __future__ import annotations

import pytest

from jentic_one.admin.core.permissions import ALL_PERMISSIONS, compute_implies_transitive
from jentic_one.shared.scopes import (
    DEFAULT_AGENT_SCOPES,
    OWNER_ACCESS_REQUESTS_READ,
    OWNER_AGENTS_READ,
    OWNER_CREDENTIALS_READ,
    OWNER_RESOURCES_READ,
    OWNER_SERVICE_ACCOUNTS_READ,
    OWNER_TOOLKITS_READ,
)
from jentic_one.shared.web.scope_catalog import (
    SCOPE_CATALOG_SCHEMA,
    build_scope_catalog,
)


@pytest.mark.arch
def test_catalog_covers_every_permission() -> None:
    """Every grantable permission appears exactly once in the flat scope list."""
    catalog = build_scope_catalog()
    names = [s["name"] for s in catalog["scopes"]]
    assert sorted(names) == sorted(ALL_PERMISSIONS)
    assert len(names) == len(set(names)), "no scope may appear twice"
    assert catalog["schema"] == SCOPE_CATALOG_SCHEMA
    assert catalog["total"] == len(ALL_PERMISSIONS)


@pytest.mark.arch
def test_descriptions_match_source_of_truth() -> None:
    catalog = build_scope_catalog()
    for scope in catalog["scopes"]:
        assert scope["description"] == ALL_PERMISSIONS[scope["name"]].description


@pytest.mark.arch
def test_implications_match_permission_map() -> None:
    """Direct + transitive implications mirror the permission definitions."""
    catalog = build_scope_catalog()
    by_name = {s["name"]: s for s in catalog["scopes"]}
    for name, perm in ALL_PERMISSIONS.items():
        assert by_name[name]["implies"] == sorted(perm.implies)
        assert by_name[name]["implies_transitive"] == sorted(compute_implies_transitive(name))


@pytest.mark.arch
def test_family_and_action_derivation() -> None:
    catalog = build_scope_catalog()
    by_name = {s["name"]: s for s in catalog["scopes"]}
    assert by_name["org:admin"]["family"] == "org"
    assert by_name["org:admin"]["action"] == "admin"
    assert by_name["org:admin"]["is_superuser"] is True
    assert by_name["agents:read"]["family"] == "agents"
    assert by_name["agents:read"]["action"] == "read"
    assert by_name["owner:credentials:read"]["family"] == "owner"
    assert by_name["owner:credentials:read"]["action"] == "read"


@pytest.mark.arch
def test_families_partition_scopes() -> None:
    """Every scope belongs to exactly one family, and families hold every scope."""
    catalog = build_scope_catalog()
    flat = {s["name"] for s in catalog["scopes"]}
    in_families = {s["name"] for fam in catalog["families"] for s in fam["scopes"]}
    assert flat == in_families
    for fam in catalog["families"]:
        assert fam["label"]
        for scope in fam["scopes"]:
            assert scope["family"] == fam["name"]


@pytest.mark.arch
def test_admin_scope_sorts_first_in_its_family() -> None:
    """org:admin (the superuser) leads its family for prominent display."""
    catalog = build_scope_catalog()
    org = next(f for f in catalog["families"] if f["name"] == "org")
    assert org["scopes"][0]["name"] == "org:admin"


@pytest.mark.arch
def test_default_agent_scopes_are_catalogued() -> None:
    """Every scope granted to a default agent must exist in the catalogue."""
    missing = set(DEFAULT_AGENT_SCOPES) - set(ALL_PERMISSIONS)
    assert not missing, f"DEFAULT_AGENT_SCOPES references uncatalogued scopes: {sorted(missing)}"


@pytest.mark.arch
def test_catalog_import_family_and_implications() -> None:
    """The catalog:import scope forms its own family and wires the expected graph."""
    catalog = build_scope_catalog()
    by_name = {s["name"]: s for s in catalog["scopes"]}
    catalog_import = by_name["catalog:import"]
    assert catalog_import["family"] == "catalog"
    assert catalog_import["action"] == "import"
    assert catalog_import["implies"] == ["apis:read"]

    fam = next(f for f in catalog["families"] if f["name"] == "catalog")
    assert fam["label"] == "Catalog"
    # apis:write ⇒ catalog:import ⇒ apis:read (transitive closure).
    assert "catalog:import" in by_name["apis:write"]["implies_transitive"]
    assert "apis:read" in by_name["catalog:import"]["implies_transitive"]


@pytest.mark.arch
def test_owner_shared_constants_are_catalogued() -> None:
    """Every OWNER_* shared scope constant must be a key in ALL_PERMISSIONS."""
    owner_constants = {
        OWNER_CREDENTIALS_READ,
        OWNER_ACCESS_REQUESTS_READ,
        OWNER_AGENTS_READ,
        OWNER_TOOLKITS_READ,
        OWNER_RESOURCES_READ,
        OWNER_SERVICE_ACCOUNTS_READ,
    }
    missing = owner_constants - set(ALL_PERMISSIONS)
    assert not missing, f"OWNER_* constants missing from the catalogue: {sorted(missing)}"
