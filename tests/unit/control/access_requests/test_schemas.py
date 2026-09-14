"""Unit tests for access-request web schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jentic_one.control.web.schemas.access_requests import (
    AccessRequestFileRequest,
    AccessRequestItemRequest,
    AmendItemSchema,
    AmendRequest,
    DecideItemSchema,
    DecideRequest,
    PermissionRuleSchema,
)


def test_permission_rule_valid_allow_effect() -> None:
    rule = PermissionRuleSchema(effect="allow", methods=["GET"])
    assert rule.effect == "allow"


def test_permission_rule_valid_deny_effect() -> None:
    rule = PermissionRuleSchema(effect="deny", path="/secrets")
    assert rule.effect == "deny"


def test_permission_rule_valid_require_approval_effect() -> None:
    rule = PermissionRuleSchema(effect="require-approval")
    assert rule.effect == "require-approval"


def test_permission_rule_condition_less_allow_rejected() -> None:
    with pytest.raises(ValidationError, match="must constrain at least one"):
        PermissionRuleSchema(effect="allow")


def test_permission_rule_condition_less_deny_accepted() -> None:
    # A catch-all deny is a legitimate default-deny construct.
    rule = PermissionRuleSchema(effect="deny")
    assert rule.effect == "deny"


def test_permission_rule_condition_less_require_approval_accepted() -> None:
    rule = PermissionRuleSchema(effect="require-approval")
    assert rule.effect == "require-approval"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"methods": ["GET"]},
        {"path": "/v1/users"},
        {"operations": ["getUser"]},
    ],
)
def test_permission_rule_constrained_allow_accepted(kwargs: dict[str, object]) -> None:
    rule = PermissionRuleSchema(effect="allow", **kwargs)  # type: ignore[arg-type]
    assert rule.effect == "allow"


def test_permission_rule_invalid_effect_rejected() -> None:
    with pytest.raises(ValidationError, match="effect"):
        PermissionRuleSchema(effect="invalid")  # type: ignore[arg-type]


def test_permission_rule_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError, match="extra"):
        PermissionRuleSchema(effect="allow", unknown_field="bad")  # type: ignore[call-arg]


def test_item_request_accepts_resource_id_only() -> None:
    item = AccessRequestItemRequest(
        resource_type="credential", action="bind", resource_id="cred_123"
    )
    assert item.resource_id == "cred_123"
    assert item.resource_reference is None


def test_item_request_accepts_resource_reference_only() -> None:
    item = AccessRequestItemRequest(
        resource_type="credential", action="bind", resource_reference={"vendor": "acme"}
    )
    assert item.resource_reference == {"vendor": "acme"}
    assert item.resource_id is None


def test_item_request_accepts_neither_resource_target() -> None:
    # A target-less credential:bind is filable; decide-time validation is what
    # rejects it (RequiredFieldMissingError) if it is never amended.
    item = AccessRequestItemRequest(resource_type="credential", action="bind")
    assert item.resource_id is None
    assert item.resource_reference is None


def test_item_request_rejects_both_resource_targets() -> None:
    with pytest.raises(ValidationError, match=r"resource_id.*resource_reference"):
        AccessRequestItemRequest(
            resource_type="credential",
            action="bind",
            resource_id="cred_123",
            resource_reference={"vendor": "x"},
        )


def test_item_request_accepts_rule_set_id_only() -> None:
    # The shared-set pointer is the alternative policy carrier for a bind.
    item = AccessRequestItemRequest(
        resource_type="credential", action="bind", resource_id="cred_123", rule_set_id="prs_1"
    )
    assert item.rule_set_id == "prs_1"
    assert item.rules is None


def test_item_request_rejects_rules_and_rule_set_id_together() -> None:
    # Inline rules and a shared-set pointer are mutually exclusive carriers —
    # a stored item carrying both would have an ambiguous effective policy.
    with pytest.raises(ValidationError, match=r"rules or rule_set_id"):
        AccessRequestItemRequest(
            resource_type="credential",
            action="bind",
            resource_id="cred_123",
            rules=[PermissionRuleSchema(effect="allow", methods=["GET"])],
            rule_set_id="prs_1",
        )


def test_item_request_rejects_toolkit_resource_type() -> None:
    # Theme-5 Phase 3: the toolkit vocabulary is retired at the Pydantic level —
    # a pre-Phase-3 client filing toolkit:create/toolkit:bind gets an immediate
    # 422, not a stored item that would hard-fail decide later.
    for action in ("create", "bind"):
        with pytest.raises(ValidationError, match="resource_type"):
            AccessRequestItemRequest(resource_type="toolkit", action=action)  # type: ignore[arg-type]


def test_item_request_rejects_retired_create_action() -> None:
    # "create" was only ever a toolkit verb; it is gone from the action Literal.
    with pytest.raises(ValidationError, match="action"):
        AccessRequestItemRequest(resource_type="credential", action="create")  # type: ignore[arg-type]


def test_item_request_rejects_unknown_resource_type() -> None:
    with pytest.raises(ValidationError, match="resource_type"):
        AccessRequestItemRequest(resource_type="api", action="bind")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("resource_type", "action"),
    [
        ("scope", "bind"),
        ("scope", "provision"),
        ("credential", "grant"),
    ],
)
def test_item_request_rejects_unsupported_combination(resource_type: str, action: str) -> None:
    # Both axes are individually valid Literals, but only the three known
    # combinations are meaningful — anything else is refused with immediate
    # feedback instead of a silent no-op.
    with pytest.raises(ValidationError, match="Unsupported resource_type/action"):
        AccessRequestItemRequest(resource_type=resource_type, action=action)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("resource_type", "action"),
    [
        ("credential", "bind"),
        ("scope", "grant"),
        ("credential", "provision"),
    ],
)
def test_item_request_accepts_supported_combinations(resource_type: str, action: str) -> None:
    item = AccessRequestItemRequest(resource_type=resource_type, action=action)  # type: ignore[arg-type]
    assert (item.resource_type, item.action) == (resource_type, action)


def test_file_request_valid_with_items() -> None:
    req = AccessRequestFileRequest(
        reason="Need access",
        items=[AccessRequestItemRequest(resource_type="credential", action="bind")],
    )
    assert len(req.items) == 1


def test_file_request_empty_items_rejected() -> None:
    with pytest.raises(ValidationError, match="items"):
        AccessRequestFileRequest(reason="Need access", items=[])


def test_decide_item_valid_approved() -> None:
    item = DecideItemSchema(item_id="arqi_123", decision="approved")
    assert item.decision == "approved"


def test_decide_item_valid_denied() -> None:
    item = DecideItemSchema(item_id="arqi_123", decision="denied")
    assert item.decision == "denied"


def test_decide_item_invalid_decision_rejected() -> None:
    with pytest.raises(ValidationError, match="decision"):
        DecideItemSchema(item_id="arqi_123", decision="maybe")  # type: ignore[arg-type]


def test_decide_request_valid() -> None:
    req = DecideRequest(items=[DecideItemSchema(item_id="arqi_1", decision="approved")])
    assert len(req.items) == 1


def test_decide_request_empty_items_rejected() -> None:
    with pytest.raises(ValidationError, match="items"):
        DecideRequest(items=[])


def test_amend_request_valid() -> None:
    req = AmendRequest(
        items=[
            AmendItemSchema(
                item_id="arqi_1", rules=[PermissionRuleSchema(effect="allow", methods=["GET"])]
            )
        ]
    )
    assert len(req.items) == 1


def test_amend_item_accepts_rule_set_id() -> None:
    item = AmendItemSchema(item_id="arqi_1", rule_set_id="prs_1")
    assert item.rule_set_id == "prs_1"
    assert item.rules is None


def test_amend_item_rejects_rules_and_rule_set_id_together() -> None:
    # The amend surface enforces the same mutual exclusion as filing, so the
    # back door can't stitch both policy carriers onto a stored item.
    with pytest.raises(ValidationError, match=r"rules or rule_set_id"):
        AmendItemSchema(
            item_id="arqi_1",
            rules=[PermissionRuleSchema(effect="allow", methods=["GET"])],
            rule_set_id="prs_1",
        )


def test_amend_item_has_no_to_id_field() -> None:
    # Theme-5 Phase 3: credential:bind has no assignment target (the agent axis
    # is the item's own actor); the amend schema dropped to_id so the wizard
    # can only stamp resource_id / policy carriers.
    assert "to_id" not in AmendItemSchema.model_fields
    assert set(AmendItemSchema.model_fields) == {"item_id", "rules", "rule_set_id", "resource_id"}


def test_amend_request_empty_items_rejected() -> None:
    with pytest.raises(ValidationError, match="items"):
        AmendRequest(items=[])
