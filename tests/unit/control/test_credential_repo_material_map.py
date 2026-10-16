"""Drift guard for ``CredentialRepository``'s header-only / type-aware material loading (#1698)."""

from __future__ import annotations

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.credential_repo import _CHILD_RELATIONSHIPS, _MATERIAL_RELATIONSHIP
from jentic_one.shared.models.credentials import StoredCredentialType


def test_child_relationships_cover_every_secret_child_of_credential() -> None:
    """A new ``Credential`` child relationship must be added to the raiseload list."""
    mapped = set(Credential.__mapper__.relationships.keys()) - {"oauth_app_registration"}
    assert set(_CHILD_RELATIONSHIPS) == mapped


def test_child_relationships_excluded_from_raiseload_are_raise_by_default() -> None:
    """The one relationship left off the list never lazy-loads on its own."""
    assert Credential.__mapper__.relationships["oauth_app_registration"].lazy == "raise"


def test_every_stored_type_except_no_auth_has_a_material_relationship() -> None:
    """A new stored type must declare which child the broker resolver reads."""
    assert set(_MATERIAL_RELATIONSHIP) == set(StoredCredentialType) - {StoredCredentialType.NO_AUTH}
    assert set(_MATERIAL_RELATIONSHIP.values()) <= set(_CHILD_RELATIONSHIPS)
