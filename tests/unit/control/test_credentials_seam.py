"""Unit tests for the credentials seam's value types and the HTTP create body."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from jentic_one.control import credentials_seam as seam
from jentic_one.control.web.schemas.credentials import CredentialCreateRequest

_KEY = "sk-unit-seam-secret-0001"  # pragma: allowlist secret


def test_api_key_material_repr_elides_the_key() -> None:
    material = seam.ApiKeyMaterial(key=_KEY, field_name="X-Api-Key", location="header")
    assert _KEY not in repr(material)
    assert "X-Api-Key" in repr(material)
    assert _KEY not in str(material)


def test_credential_unavailable_error_names_id_and_reason() -> None:
    exc = seam.CredentialUnavailableError("cred_1", "inactive")
    assert exc.credential_id == "cred_1"
    assert exc.reason == "inactive"
    assert "cred_1" in str(exc)


@pytest.mark.parametrize(
    ("kind", "ref"),
    [("", "ref"), ("kind", ""), ("k" * 51, "ref"), ("kind", "r" * 256)],
)
def test_managed_by_rejects_empty_or_oversized_values(kind: str, ref: str) -> None:
    with pytest.raises(ValueError, match="managed_by"):
        seam.ManagedBy(kind=kind, ref=ref)


def test_managed_by_is_a_value() -> None:
    assert seam.ManagedBy(kind="a", ref="b") == seam.ManagedBy(kind="a", ref="b")
    assert seam.ManagedBy(kind="a", ref="b") != seam.ManagedBy(kind="a", ref="c")


def test_http_create_body_drops_a_managed_by_field() -> None:
    """No request model accepts the marker: a client cannot set it."""
    body = TypeAdapter(CredentialCreateRequest).validate_python(
        {
            "type": "api_key",
            "name": "Forged",
            "api": {"vendor": "example.com"},
            "key": _KEY,
            "location": "header",
            "field_name": "X-Api-Key",
            "managed_by": {"kind": "x", "ref": "y"},
            "managed_by_kind": "x",
            "managed_by_ref": "y",
        }
    )
    dumped = body.model_dump()
    assert "managed_by" not in dumped
    assert "managed_by_kind" not in dumped
    assert "managed_by_ref" not in dumped
    assert "managed_by" not in seam.CredentialCreate.model_fields
