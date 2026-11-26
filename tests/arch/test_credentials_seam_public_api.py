"""Drift guard: the public ``jentic_one.control.credentials_seam`` API.

Extensions read stored credentials and keep managed credentials through this
module only, so its exports and their signatures are a compatibility contract.
This gate pins ``__all__``, the parameter shape of each function, the fields of
each exported dataclass, and that the re-exports are the internal objects
themselves (so an ``isinstance`` check or an access-filter provider comparing
``model is Credential`` keeps working).
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

import jentic_one.control.credentials_seam as seam
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.services.credentials.errors import (
    CredentialManagedError,
    CredentialNotFoundError,
    InvalidCredentialInputError,
)
from jentic_one.control.services.credentials.schemas.credentials import (
    CredentialCreate,
    CredentialUpdate,
    ManagedBy,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.shared.aws.sigv4 import SigV4Material, sign_request
from jentic_one.shared.models.credentials import CredentialType

pytestmark = pytest.mark.arch

_P = inspect.Parameter

EXPECTED_ALL = [
    "APIReference",
    "ApiKeyMaterial",
    "Credential",
    "CredentialCreate",
    "CredentialHeader",
    "CredentialManagedError",
    "CredentialNotFoundError",
    "CredentialType",
    "CredentialUnavailableError",
    "CredentialUpdate",
    "InvalidCredentialInputError",
    "ManagedBy",
    "SigV4Material",
    "create_managed_credential",
    "delete_managed_credential",
    "get_credential_header",
    "resolve_credential_material",
    "sign_request",
    "update_managed_credential",
]

EXPECTED_PARAMETERS: dict[str, list[tuple[str, inspect._ParameterKind]]] = {
    "get_credential_header": [
        ("ctx", _P.POSITIONAL_OR_KEYWORD),
        ("credential_id", _P.POSITIONAL_OR_KEYWORD),
    ],
    "resolve_credential_material": [
        ("ctx", _P.POSITIONAL_OR_KEYWORD),
        ("credential_id", _P.POSITIONAL_OR_KEYWORD),
        ("expected_type", _P.KEYWORD_ONLY),
    ],
    "create_managed_credential": [
        ("ctx", _P.POSITIONAL_OR_KEYWORD),
        ("payload", _P.POSITIONAL_OR_KEYWORD),
        ("identity", _P.KEYWORD_ONLY),
        ("managed_by", _P.KEYWORD_ONLY),
    ],
    "update_managed_credential": [
        ("ctx", _P.POSITIONAL_OR_KEYWORD),
        ("credential_id", _P.POSITIONAL_OR_KEYWORD),
        ("payload", _P.POSITIONAL_OR_KEYWORD),
        ("identity", _P.KEYWORD_ONLY),
        ("managed_by", _P.KEYWORD_ONLY),
    ],
    "delete_managed_credential": [
        ("ctx", _P.POSITIONAL_OR_KEYWORD),
        ("credential_id", _P.POSITIONAL_OR_KEYWORD),
        ("identity", _P.KEYWORD_ONLY),
        ("managed_by", _P.KEYWORD_ONLY),
    ],
}

EXPECTED_FIELDS: dict[str, list[str]] = {
    "ManagedBy": ["kind", "ref"],
    "CredentialHeader": [
        "id",
        "type",
        "name",
        "api_vendor",
        "created_by",
        "active",
        "state",
        "managed_by",
        "revision",
    ],
    "ApiKeyMaterial": ["key", "field_name", "location"],
}


def test_credentials_seam_all() -> None:
    """``__all__`` is exactly the documented contract, and every name resolves."""
    assert list(seam.__all__) == EXPECTED_ALL
    for name in EXPECTED_ALL:
        assert hasattr(seam, name), name


def test_credentials_seam_reexports_are_the_internal_objects() -> None:
    """Re-exports are identities, not copies."""
    assert seam.Credential is Credential
    assert seam.CredentialCreate is CredentialCreate
    assert seam.CredentialUpdate is CredentialUpdate
    assert seam.APIReference is APIReference
    assert seam.CredentialType is CredentialType
    assert seam.ManagedBy is ManagedBy
    assert seam.SigV4Material is SigV4Material
    assert seam.sign_request is sign_request
    assert seam.CredentialManagedError is CredentialManagedError
    assert seam.CredentialNotFoundError is CredentialNotFoundError
    assert seam.InvalidCredentialInputError is InvalidCredentialInputError


def test_credentials_seam_signatures() -> None:
    """Each seam function keeps its positional/keyword parameter contract and is async."""
    for name, expected in EXPECTED_PARAMETERS.items():
        fn = getattr(seam, name)
        assert inspect.iscoroutinefunction(fn), f"{name} must be async"
        actual = [(p.name, p.kind) for p in inspect.signature(fn).parameters.values()]
        assert actual == expected, f"credentials_seam.{name} signature drifted: {actual}"


def test_credentials_seam_dataclass_fields() -> None:
    """The exported dataclasses are frozen and keep their field order."""
    for name, expected in EXPECTED_FIELDS.items():
        cls = getattr(seam, name)
        assert dataclasses.is_dataclass(cls), name
        assert cls.__dataclass_params__.frozen, f"{name} must be frozen"  # type: ignore[attr-defined]
        assert [f.name for f in dataclasses.fields(cls)] == expected, name
