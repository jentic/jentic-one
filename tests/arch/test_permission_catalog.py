"""Unit + drift tests for the conceptual permission catalogue.

The catalogue (``GET /reference/endpoints.json`` ``permissions`` section, and the
committed ``docs/reference/endpoints.json``) is built from
``ALL_PERMISSIONS`` so it can never diverge from the enforced permission set.
These tests pin that invariant and the derived shape the docs SPA relies on.
"""

from __future__ import annotations

import pytest

from jentic_one.admin.core.permissions import ALL_PERMISSIONS, compute_implies_transitive
from jentic_one.shared.auth.permission_catalog import (
    DEFAULT_AGENT_PERMISSIONS,
    OWNER_AGENTS_READ,
    OWNER_CREDENTIALS_READ,
    OWNER_RESOURCES_READ,
    RETIRED_PERMISSIONS,
)
from jentic_one.shared.web.permission_catalog_payload import (
    PERMISSION_CATALOG_SCHEMA,
    build_permission_catalog,
)


@pytest.mark.arch
def test_catalog_covers_every_permission() -> None:
    """Every grantable permission appears exactly once in the flat permission list."""
    catalog = build_permission_catalog()
    names = [p["name"] for p in catalog["permissions"]]
    assert sorted(names) == sorted(ALL_PERMISSIONS)
    assert len(names) == len(set(names)), "no permission may appear twice"
    assert catalog["schema"] == PERMISSION_CATALOG_SCHEMA
    assert catalog["total"] == len(ALL_PERMISSIONS)


@pytest.mark.arch
def test_descriptions_match_source_of_truth() -> None:
    catalog = build_permission_catalog()
    for permission in catalog["permissions"]:
        assert permission["description"] == ALL_PERMISSIONS[permission["name"]].description


@pytest.mark.arch
def test_implications_match_permission_map() -> None:
    """Direct + transitive implications mirror the permission definitions."""
    catalog = build_permission_catalog()
    by_name = {p["name"]: p for p in catalog["permissions"]}
    for name, perm in ALL_PERMISSIONS.items():
        assert by_name[name]["implies"] == sorted(perm.implies)
        assert by_name[name]["implies_transitive"] == sorted(compute_implies_transitive(name))


@pytest.mark.arch
def test_family_and_action_derivation() -> None:
    catalog = build_permission_catalog()
    by_name = {p["name"]: p for p in catalog["permissions"]}
    assert by_name["org:admin"]["family"] == "org"
    assert by_name["org:admin"]["action"] == "admin"
    assert by_name["org:admin"]["is_superuser"] is True
    assert by_name["agents:read"]["family"] == "agents"
    assert by_name["agents:read"]["action"] == "read"
    assert by_name["owner:credentials:read"]["family"] == "owner"
    assert by_name["owner:credentials:read"]["action"] == "read"


@pytest.mark.arch
def test_families_partition_permissions() -> None:
    """Every permission belongs to exactly one family, and families hold them all."""
    catalog = build_permission_catalog()
    flat = {p["name"] for p in catalog["permissions"]}
    in_families = {p["name"] for fam in catalog["families"] for p in fam["permissions"]}
    assert flat == in_families
    for fam in catalog["families"]:
        assert fam["label"]
        for permission in fam["permissions"]:
            assert permission["family"] == fam["name"]


@pytest.mark.arch
def test_admin_permission_sorts_first_in_its_family() -> None:
    """org:admin (the superuser) leads its family for prominent display."""
    catalog = build_permission_catalog()
    org = next(f for f in catalog["families"] if f["name"] == "org")
    assert org["permissions"][0]["name"] == "org:admin"


@pytest.mark.arch
def test_default_agent_permissions_are_catalogued() -> None:
    """Every permission granted to a default agent must exist in the catalogue."""
    missing = set(DEFAULT_AGENT_PERMISSIONS) - set(ALL_PERMISSIONS)
    assert not missing, (
        f"DEFAULT_AGENT_PERMISSIONS references uncatalogued permissions: {sorted(missing)}"
    )


@pytest.mark.arch
def test_catalog_import_family_and_implications() -> None:
    """The catalog:import permission forms its own family and wires the expected graph."""
    catalog = build_permission_catalog()
    by_name = {p["name"]: p for p in catalog["permissions"]}
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
def test_overlays_confirm_family_and_implications() -> None:
    """overlays:confirm forms its own family and implies apis:read."""
    catalog = build_permission_catalog()
    by_name = {p["name"]: p for p in catalog["permissions"]}
    confirm = by_name["overlays:confirm"]
    assert confirm["family"] == "overlays"
    assert confirm["action"] == "confirm"
    assert confirm["implies"] == ["apis:read"]
    assert confirm["is_superuser"] is False

    fam = next(f for f in catalog["families"] if f["name"] == "overlays")
    assert fam["label"] == "Overlays"
    # org:admin ⇒ overlays:confirm (so existing admins keep confirm), which ⇒ apis:read.
    assert "overlays:confirm" in by_name["org:admin"]["implies_transitive"]
    assert "apis:read" in confirm["implies_transitive"]


@pytest.mark.arch
def test_owner_shared_constants_are_catalogued() -> None:
    """Every OWNER_* shared permission constant must be a key in ALL_PERMISSIONS."""
    owner_constants = {
        OWNER_CREDENTIALS_READ,
        OWNER_AGENTS_READ,
        OWNER_RESOURCES_READ,
    }
    missing = owner_constants - set(ALL_PERMISSIONS)
    assert not missing, f"OWNER_* constants missing from the catalogue: {sorted(missing)}"


@pytest.mark.arch
def test_retired_permissions_stay_out_of_the_catalogue() -> None:
    """Retired permissions (theme-5 toolkits, theme-7 access requests, theme-8
    service accounts) never reappear.

    They are tolerated on stored-grant re-validation (``RETIRED_PERMISSIONS``) but
    must not be grantable, defaulted, or implied — reintroducing one here would
    silently resurrect a deleted surface's authorization tier.
    """
    assert not RETIRED_PERMISSIONS & set(ALL_PERMISSIONS)
    assert not RETIRED_PERMISSIONS & set(DEFAULT_AGENT_PERMISSIONS)
    catalog = build_permission_catalog()
    assert not RETIRED_PERMISSIONS & {p["name"] for p in catalog["permissions"]}


@pytest.mark.arch
def test_service_account_family_is_gone_from_the_catalogue() -> None:
    """Theme-8 Phase 2: no ``service-accounts`` family, permission, or implication survives."""
    catalog = build_permission_catalog()
    assert "service-accounts" not in {f["name"] for f in catalog["families"]}
    by_name = {p["name"]: p for p in catalog["permissions"]}
    assert not [n for n in by_name if "service-accounts" in n]
    assert not [n for n in by_name["org:admin"]["implies_transitive"] if "service-accounts" in n]
