"""Unit tests for the shared permission catalogue (its tier-neutral home, #938).

Exercises compute_effective / compute_implies_transitive imported from
``shared.auth.permission_catalog`` directly — independent of the
``admin.core.permissions`` re-export shim — so the shared module has coverage that
does not route through admin.
"""

from __future__ import annotations

from jentic_one.admin.core import permissions as admin_perms
from jentic_one.shared.auth import permission_catalog
from jentic_one.shared.auth.permission_catalog import (
    ALL_PERMISSIONS,
    APIS_READ,
    CREDENTIALS_READ,
    CREDENTIALS_WRITE,
    ORG_ADMIN,
    OVERLAYS_CONFIRM,
    compute_effective,
    compute_implies_transitive,
)


def test_compute_effective_empty() -> None:
    assert compute_effective(set()) == set()


def test_compute_effective_single_implication() -> None:
    # credentials:write implies credentials:read.
    assert compute_effective({CREDENTIALS_WRITE}) == {CREDENTIALS_WRITE, CREDENTIALS_READ}


def test_compute_effective_org_admin_expands_all() -> None:
    effective = compute_effective({ORG_ADMIN})
    # org:admin expands to (at least) every directly-implied permission.
    assert OVERLAYS_CONFIRM in effective
    assert APIS_READ in effective


def test_compute_implies_transitive_closure() -> None:
    # overlays:confirm → apis:read (a leaf), so the transitive closure is {apis:read}.
    assert compute_implies_transitive(OVERLAYS_CONFIRM) == {APIS_READ}


def test_all_permissions_is_the_shared_catalogue() -> None:
    # The catalogue is genuinely defined here (not merely re-exported from admin).
    assert ORG_ADMIN in ALL_PERMISSIONS
    assert ALL_PERMISSIONS[ORG_ADMIN].name == ORG_ADMIN


def test_admin_shim_re_exports_the_entire_public_surface() -> None:
    # The shim's whole job is a complete, identity-preserving re-export. Assert the
    # public surface matches AND every symbol is the *same object*, so a future dropped
    # or diverged re-export (e.g. the unused-by-callers Permission dataclass) is caught
    # in one place rather than only when some call site happens to break.
    assert set(admin_perms.__all__) == set(permission_catalog.__all__)
    for name in permission_catalog.__all__:
        assert getattr(admin_perms, name) is getattr(permission_catalog, name), name
