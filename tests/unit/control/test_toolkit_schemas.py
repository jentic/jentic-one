"""Tests for toolkit Pydantic schema validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jentic_one.control.web.schemas.toolkits import (
    PermissionRuleReadSchema,
    PermissionRuleSchema,
    PermissionsPatchRequest,
    ToolkitCreateRequest,
    ToolkitCredentialBindRequest,
    ToolkitKeyCreateRequest,
    ToolkitKeyUpdateRequest,
    ToolkitResponse,
    ToolkitUpdateRequest,
)


def test_toolkit_create_request_minimal() -> None:
    req = ToolkitCreateRequest(name="my-toolkit")
    assert req.name == "my-toolkit"
    assert req.description is None
    assert req.active is True
    assert req.credential_ids is None


def test_toolkit_create_request_full() -> None:
    # Toolkit-level ``permissions`` were dropped from the schema in issue
    # #655 — the field is silently ignored by Pydantic here (the router
    # rejects it with a raw-body peek at request time).
    req = ToolkitCreateRequest(
        name="full-toolkit",
        description="A toolkit",
        active=False,
        credential_ids=["cred_1", "cred_2"],
    )
    assert req.name == "full-toolkit"
    assert req.active is False
    assert req.credential_ids == ["cred_1", "cred_2"]


def test_toolkit_update_request_partial() -> None:
    req = ToolkitUpdateRequest(name="new-name")
    assert req.name == "new-name"
    assert req.description is None
    assert req.active is None


def test_toolkit_response_round_trip() -> None:
    now = datetime.now(UTC)
    resp = ToolkitResponse(
        toolkit_id="tk_abc123",
        name="test",
        active=True,
        key_count=2,
        credential_count=1,
        created_at=now,
    )
    data = resp.model_dump()
    assert data["toolkit_id"] == "tk_abc123"
    assert data["key_count"] == 2
    assert data["credential_count"] == 1
    # #655: toolkit-level ``permissions`` was dropped from the response —
    # the enforced surface is per-binding rules.
    assert "permissions" not in data


def test_permission_rule_schema_minimal() -> None:
    rule = PermissionRuleSchema(effect="allow", methods=["GET"])
    assert rule.effect == "allow"
    assert rule.methods == ["GET"]
    assert rule.path is None
    assert rule.operations is None


def test_permission_rule_schema_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        PermissionRuleSchema(effect="allow", methods=["GET"], unknown="x")  # type: ignore[call-arg]


def test_permission_rule_schema_condition_less_allow_rejected() -> None:
    with pytest.raises(ValidationError, match="must constrain at least one"):
        PermissionRuleSchema(effect="allow")


def test_permission_rule_schema_condition_less_deny_accepted() -> None:
    rule = PermissionRuleSchema(effect="deny")
    assert rule.effect == "deny"


def test_permission_rule_read_schema_system_fields() -> None:
    rule = PermissionRuleReadSchema.model_validate(
        {
            "effect": "deny",
            "methods": ["GET"],
            "_system": True,
            "_comment": "Auto-generated",
        }
    )
    assert rule.is_system is True
    assert rule.comment == "Auto-generated"
    dumped = rule.model_dump(by_alias=True, exclude_none=True)
    assert "_system" in dumped
    assert "_comment" in dumped


def test_permission_rule_read_schema_user_rule_excludes_system() -> None:
    rule = PermissionRuleReadSchema.model_validate(
        {"effect": "allow", "path": "/api/*", "_system": False}
    )
    assert rule.is_system is False


def test_permissions_patch_request() -> None:
    req = PermissionsPatchRequest(
        add=[PermissionRuleSchema(effect="allow", path="/new")],
        remove=[0, 2],
    )
    assert len(req.add) == 1  # type: ignore[arg-type]
    assert req.remove == [0, 2]


def test_toolkit_key_create_request_defaults() -> None:
    req = ToolkitKeyCreateRequest()
    assert req.label is None
    assert req.allowed_ips is None


def test_toolkit_key_update_request_revoke() -> None:
    req = ToolkitKeyUpdateRequest(revoked=True)
    assert req.revoked is True
    assert req.label is None


def test_toolkit_credential_bind_request() -> None:
    req = ToolkitCredentialBindRequest(
        credential_id="cred_123",
        permissions=[PermissionRuleSchema(effect="allow", methods=["GET", "POST"])],
    )
    assert req.credential_id == "cred_123"
    assert len(req.permissions) == 1  # type: ignore[arg-type]


def test_toolkit_create_request_missing_name_fails() -> None:
    with pytest.raises(ValidationError):
        ToolkitCreateRequest()  # type: ignore[call-arg]


def test_toolkit_create_request_no_longer_has_permissions_field() -> None:
    # #655 regression: the ``permissions`` field was silently accepted then
    # dropped by the broker. It must be gone from the model — the router's
    # raw-body peek is what catches a client still submitting the key.
    assert "permissions" not in ToolkitCreateRequest.model_fields


def test_toolkit_create_request_name_too_long() -> None:
    with pytest.raises(ValidationError, match="string_too_long"):
        ToolkitCreateRequest(name="x" * 256)


def test_permission_rule_invalid_effect() -> None:
    with pytest.raises(ValidationError):
        PermissionRuleSchema(effect="allow_all")  # type: ignore[arg-type]


def test_permission_rule_effect_allow() -> None:
    rule = PermissionRuleSchema(effect="allow", path="/api/*")
    assert rule.effect == "allow"


def test_permission_rule_effect_deny() -> None:
    rule = PermissionRuleSchema(effect="deny")
    assert rule.effect == "deny"


def test_toolkit_key_create_valid_ips() -> None:
    req = ToolkitKeyCreateRequest(allowed_ips=["192.168.1.0/24", "10.0.0.1", "::1"])
    assert req.allowed_ips == ["192.168.1.0/24", "10.0.0.1", "::1"]


def test_toolkit_key_create_invalid_ip() -> None:
    with pytest.raises(ValidationError, match="Invalid IP"):
        ToolkitKeyCreateRequest(allowed_ips=["not-an-ip"])


def test_toolkit_key_update_invalid_ip() -> None:
    with pytest.raises(ValidationError, match="Invalid IP"):
        ToolkitKeyUpdateRequest(allowed_ips=["../../etc"])
