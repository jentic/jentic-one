"""Unit tests for EffectApplicator — all dispatch branches, idempotency, and fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.control.repos.effects_repo import BindTargetMissingError
from jentic_one.control.services.access_requests.effects import (
    EffectApplicator,
    EffectPhase,
    admin_effect_keys,
    classify_effect,
    is_admin_effect,
)
from jentic_one.control.services.access_requests.errors import (
    CredentialNotFoundForBindError,
    ProvisioningPlanNotFulfilledError,
    RequiredFieldMissingError,
    RulesNotSupportedForBindError,
    ToolkitNotVisibleError,
    ToolkitReferenceAmbiguousError,
    ToolkitReferenceUnresolvedError,
    UnsupportedScopeGrantError,
)
from jentic_one.control.services.access_requests.schemas.effects import (
    CredentialBindEffect,
    ScopeGrantEffect,
    SkippedEffect,
    ToolkitBindEffect,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.actors import ActorType, actor_type_from_id
from jentic_one.shared.scopes import GRANTABLE_SCOPES, ORG_ADMIN

_MODULE = "jentic_one.control.services.access_requests.effects"

# A grantable scope (in GRANTABLE_SCOPES) used by scope-grant tests.
_GRANTABLE = "capabilities:execute"


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    admin_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=admin_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    control_session = AsyncMock()
    ctx.control_db.session.return_value.__aenter__ = AsyncMock(return_value=control_session)
    ctx.control_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_session() -> AsyncMock:
    return AsyncMock()


def _make_identity(*, sub: str = "usr_admin", org_admin: bool = True) -> Identity:
    return Identity(sub=sub, permissions=[ORG_ADMIN] if org_admin else [])


def _make_item(
    *,
    resource_type: str = "credential",
    action: str = "bind",
    resource_id: str | None = "cred_001",
    resource_reference: dict[str, Any] | None = None,
    to_id: str | None = "tk_001",
    actor_id: str = "agnt_001",
    rules: list[dict[str, Any]] | None = None,
    item_id: str = "arqi_001",
) -> MagicMock:
    item = MagicMock()
    item.id = item_id
    item.resource_type = resource_type
    item.action = action
    item.resource_id = resource_id
    item.resource_reference = resource_reference
    item.to_id = to_id
    item.actor_id = actor_id
    item.rules = rules
    return item


# --- credential bind ---


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.ToolkitPermissionRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_credential_bind_happy_path(
    mock_effects_repo: MagicMock,
    mock_perm_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.bind_credential_to_toolkit = AsyncMock(return_value=("tcb_new_001", False))
    mock_perm_repo.replace_user_rules = AsyncMock(return_value=[])

    rules = [{"effect": "allow", "methods": ["GET"], "path": "^/pets"}]
    item = _make_item(rules=rules)
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, CredentialBindEffect)
    assert effects.binding_id == "tcb_new_001"
    assert effects.rules_applied == 1
    assert effects.already_bound is False
    mock_effects_repo.bind_credential_to_toolkit.assert_awaited_once()
    mock_perm_repo.replace_user_rules.assert_awaited_once()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.ToolkitPermissionRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_credential_bind_duplicate_idempotent(
    mock_effects_repo: MagicMock,
    mock_perm_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.bind_credential_to_toolkit = AsyncMock(
        return_value=("tcb_existing_001", True)
    )
    mock_perm_repo.replace_user_rules = AsyncMock(return_value=[])

    rules = [{"effect": "allow", "methods": ["GET"]}]
    item = _make_item(rules=rules)
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, CredentialBindEffect)
    assert effects.already_bound is True
    assert effects.binding_id == "tcb_existing_001"
    assert effects.rules_applied == 1


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.ToolkitPermissionRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_credential_bind_no_rules_skips_permission_call(
    mock_effects_repo: MagicMock,
    mock_perm_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.bind_credential_to_toolkit = AsyncMock(return_value=("tcb_new_002", False))

    item = _make_item(rules=None)
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, CredentialBindEffect)
    assert effects.rules_applied == 0
    mock_perm_repo.replace_user_rules.assert_not_called()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.ToolkitPermissionRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_credential_bind_lost_toctou_race_raises_domain_error(
    mock_effects_repo: MagicMock,
    mock_perm_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """A credential deleted between pre-validation and the bind write surfaces a
    422 CredentialNotFoundForBindError, not an AssertionError → 500. The repo
    detects the FK-violation race, attributes it to the credential, and raises
    BindTargetMissingError; apply must map it to the matching domain error
    (#649)."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.bind_credential_to_toolkit = AsyncMock(
        side_effect=BindTargetMissingError("credential", "cred_001")
    )

    item = _make_item(rules=None)
    applicator = EffectApplicator(ctx)
    with pytest.raises(CredentialNotFoundForBindError):
        await applicator.apply(item, identity=_make_identity(), control_session=session)
    # The permission-rules write must not run once the bind failed.
    mock_perm_repo.replace_user_rules.assert_not_called()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.ToolkitPermissionRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_credential_bind_lost_toctou_race_attributes_toolkit(
    mock_effects_repo: MagicMock,
    mock_perm_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """When the FK that vanished is the toolkit (not the credential), the repo
    attributes the race to the toolkit and apply maps it to ToolkitNotVisibleError,
    not CredentialNotFoundForBindError — the binding has two FKs and the error must
    name the one that actually failed."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.bind_credential_to_toolkit = AsyncMock(
        side_effect=BindTargetMissingError("toolkit", "tk_target")
    )

    item = _make_item(rules=None)
    applicator = EffectApplicator(ctx)
    with pytest.raises(ToolkitNotVisibleError):
        await applicator.apply(item, identity=_make_identity(), control_session=session)
    mock_perm_repo.replace_user_rules.assert_not_called()


# --- toolkit bind ---


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_happy_path(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.toolkit_visible_to_owners = AsyncMock(return_value=True)
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock(return_value=("atb_new_001", False))

    item = _make_item(resource_type="toolkit", action="bind", resource_id="tk_target")
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, ToolkitBindEffect)
    assert effects.binding_id == "atb_new_001"
    assert effects.already_bound is False
    mock_effects_repo.toolkit_visible_to_owners.assert_awaited_once()
    mock_effects_repo.bind_agent_to_toolkit.assert_awaited_once()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_rejects_rules(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """Defense-in-depth: a stored toolkit:bind carrying rules must fail loudly,
    never silently approve into an unrestricted binding."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock(return_value=("atb_x", False))

    rules = [{"effect": "allow", "methods": ["GET"]}]
    item = _make_item(resource_type="toolkit", action="bind", resource_id="tk_target", rules=rules)
    applicator = EffectApplicator(ctx)

    with pytest.raises(RulesNotSupportedForBindError):
        await applicator.apply(item, identity=_make_identity(), control_session=session)
    mock_effects_repo.bind_agent_to_toolkit.assert_not_called()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_duplicate_idempotent(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.toolkit_visible_to_owners = AsyncMock(return_value=True)
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock(return_value=("atb_existing_001", True))

    item = _make_item(resource_type="toolkit", action="bind", resource_id="tk_target")
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, ToolkitBindEffect)
    assert effects.already_bound is True
    assert effects.binding_id == "atb_existing_001"


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_explicit_id_not_visible_raises(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """A non-admin decider cannot bind to a toolkit they don't own."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.toolkit_visible_to_owners = AsyncMock(return_value=False)
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock()

    item = _make_item(resource_type="toolkit", action="bind", resource_id="tk_other_owner")
    applicator = EffectApplicator(ctx)

    with pytest.raises(ToolkitNotVisibleError):
        await applicator.apply(
            item, identity=_make_identity(sub="usr_op", org_admin=False), control_session=session
        )
    mock_effects_repo.bind_agent_to_toolkit.assert_not_called()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_resolves_reference_single_candidate(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_toolkits_for_api = AsyncMock(return_value=["tk_resolved"])
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock(return_value=("atb_new_002", False))

    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
    )
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, ToolkitBindEffect)
    assert effects.binding_id == "atb_new_002"
    # Resolution runs on the shared decision session (not a fresh control_db session),
    # and org:admin gets owner_ids=None (resolve across all owners). The vendor is
    # normalized to the registry slug form (httpbin.org -> httpbin-org) so it
    # matches the credential's stored api_vendor (#656).
    mock_effects_repo.resolve_toolkits_for_api.assert_awaited_once_with(
        session,
        vendor="httpbin-org",
        name="httpbin",
        version=None,
        owner_ids=None,
    )
    mock_effects_repo.bind_agent_to_toolkit.assert_awaited_once()
    bound_kwargs = mock_effects_repo.bind_agent_to_toolkit.await_args.kwargs
    assert bound_kwargs["toolkit_id"] == "tk_resolved"


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_reference_owner_scoped_for_non_admin(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """A non-admin decider's reference resolution is scoped to their own owner id."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_toolkits_for_api = AsyncMock(return_value=["tk_owned"])
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock(return_value=("atb_new_003", False))

    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
    )
    applicator = EffectApplicator(ctx)
    await applicator.apply(
        item, identity=_make_identity(sub="usr_op", org_admin=False), control_session=session
    )

    mock_effects_repo.resolve_toolkits_for_api.assert_awaited_once_with(
        session,
        vendor="httpbin-org",
        name="httpbin",
        version=None,
        owner_ids=["usr_op"],
    )


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_reference_no_candidates_raises(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_toolkits_for_api = AsyncMock(return_value=[])
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock()

    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
    )
    applicator = EffectApplicator(ctx)

    with pytest.raises(ToolkitReferenceUnresolvedError):
        await applicator.apply(item, identity=_make_identity(), control_session=session)
    mock_effects_repo.bind_agent_to_toolkit.assert_not_called()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_vendor_only_reference_message_omits_none(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    # A vendor-only reference (no name) must not surface a misleading
    # "vendor/None" in the error message (review P3-10).
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_toolkits_for_api = AsyncMock(return_value=[])
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock()

    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference={"vendor": "httpbin.org"},
    )
    applicator = EffectApplicator(ctx)

    with pytest.raises(ToolkitReferenceUnresolvedError) as excinfo:
        await applicator.apply(item, identity=_make_identity(), control_session=session)
    assert "None" not in str(excinfo.value)
    assert "httpbin.org" in str(excinfo.value)


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_toolkit_bind_reference_multiple_candidates_raises(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_toolkits_for_api = AsyncMock(return_value=["tk_a", "tk_b"])
    mock_effects_repo.bind_agent_to_toolkit = AsyncMock()

    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
    )
    applicator = EffectApplicator(ctx)

    with pytest.raises(ToolkitReferenceAmbiguousError) as excinfo:
        await applicator.apply(item, identity=_make_identity(), control_session=session)
    assert excinfo.value.candidates == ["tk_a", "tk_b"]
    mock_effects_repo.bind_agent_to_toolkit.assert_not_called()


# --- scope grant ---


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_scope_grant_happy_path(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.grant_scope_to_actor = AsyncMock(return_value=True)

    item = _make_item(resource_type="scope", action="grant", resource_id=_GRANTABLE)
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, ScopeGrantEffect)
    assert effects.scope == _GRANTABLE
    assert effects.already_granted is False
    mock_effects_repo.grant_scope_to_actor.assert_awaited_once()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_scope_grant_duplicate_idempotent(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.grant_scope_to_actor = AsyncMock(return_value=False)

    item = _make_item(resource_type="scope", action="grant", resource_id=_GRANTABLE)
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, ScopeGrantEffect)
    assert effects.already_granted is True
    assert effects.scope == _GRANTABLE


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_scope_grant_privileged_scope_rejected(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """A privileged scope (org:admin) cannot be granted via the self-service path."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.grant_scope_to_actor = AsyncMock()

    item = _make_item(resource_type="scope", action="grant", resource_id="org:admin")
    applicator = EffectApplicator(ctx)

    with pytest.raises(UnsupportedScopeGrantError):
        await applicator.apply(item, identity=_make_identity(), control_session=session)
    mock_effects_repo.grant_scope_to_actor.assert_not_called()


# --- validate() pre-pass ---


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_toolkit_reference_not_visible_raises(
    mock_effects_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_toolkits_for_api = AsyncMock(return_value=[])

    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
    )
    applicator = EffectApplicator(ctx)

    with pytest.raises(ToolkitReferenceUnresolvedError):
        await applicator.validate(item, identity=_make_identity(), control_session=session)


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_scope_grant_privileged_raises(
    mock_effects_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(resource_type="scope", action="grant", resource_id="org:admin")
    applicator = EffectApplicator(ctx)

    with pytest.raises(UnsupportedScopeGrantError):
        await applicator.validate(item, identity=_make_identity(), control_session=session)


async def test_validate_scope_grant_apis_write_passes() -> None:
    """Regression: ``apis:write`` is self-service grantable.

    An agent must be able to request ``apis:write`` (so it can ``jentic catalog
    import`` after a human approves) and have an owner approve it. validate()
    mirrors the file-time guard via ``GRANTABLE_SCOPES``, so it must NOT reject
    ``apis:write`` — before this change it raised ``UnsupportedScopeGrantError``.
    """
    assert "apis:write" in GRANTABLE_SCOPES
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(resource_type="scope", action="grant", resource_id="apis:write")
    applicator = EffectApplicator(ctx)

    await applicator.validate(item, identity=_make_identity(), control_session=session)


@patch(f"{_MODULE}.CredentialRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_credential_bind_visible_passes(
    mock_effects_repo: MagicMock,
    mock_credential_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.toolkit_visible_to_owners = AsyncMock()
    mock_effects_repo.resolve_toolkits_for_api = AsyncMock()
    mock_credential_repo.get_by_id = AsyncMock(return_value=MagicMock())
    item = _make_item(resource_type="credential", action="bind", resource_id="cred_001")
    applicator = EffectApplicator(ctx)
    # A visible credential passes validate() without touching the toolkit reads.
    await applicator.validate(item, identity=_make_identity(), control_session=session)
    mock_credential_repo.get_by_id.assert_awaited_once()
    mock_effects_repo.toolkit_visible_to_owners.assert_not_called()
    mock_effects_repo.resolve_toolkits_for_api.assert_not_called()


@patch(f"{_MODULE}.CredentialRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_credential_bind_missing_credential_raises(
    mock_effects_repo: MagicMock,
    mock_credential_repo: MagicMock,
) -> None:
    """A credential:bind naming a non-existent/invisible credential must fail
    validate() as a 422 CredentialNotFoundForBindError, not slip through to a
    500 from _apply_credential_bind / a downstream FK fault. See issue #649."""
    ctx = _make_ctx()
    session = _make_session()
    mock_credential_repo.get_by_id = AsyncMock(return_value=None)
    item = _make_item(resource_type="credential", action="bind", resource_id="cred_missing")
    applicator = EffectApplicator(ctx)

    with pytest.raises(CredentialNotFoundForBindError):
        await applicator.validate(item, identity=_make_identity(), control_session=session)


@patch(f"{_MODULE}.CredentialRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_credential_bind_missing_resource_id_raises(
    mock_effects_repo: MagicMock,
    mock_credential_repo: MagicMock,
) -> None:
    """A credential:bind with no resource_id must fail validate() up front, never
    reaching the credential lookup or the apply step's bare ValueError."""
    ctx = _make_ctx()
    session = _make_session()
    mock_credential_repo.get_by_id = AsyncMock(return_value=None)
    item = _make_item(resource_type="credential", action="bind", resource_id=None)
    applicator = EffectApplicator(ctx)

    with pytest.raises(RequiredFieldMissingError) as exc_info:
        await applicator.validate(item, identity=_make_identity(), control_session=session)
    assert exc_info.value.field == "resource_id"
    assert "<missing>" not in str(exc_info.value)
    mock_credential_repo.get_by_id.assert_not_called()


@patch(f"{_MODULE}.CredentialRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_credential_bind_missing_to_id_raises(
    mock_effects_repo: MagicMock,
    mock_credential_repo: MagicMock,
) -> None:
    """A credential:bind missing its to_id (the toolkit target) fails validate()
    with a clear missing-field error, not a misleading 'toolkit not visible'."""
    ctx = _make_ctx()
    session = _make_session()
    mock_credential_repo.get_by_id = AsyncMock(return_value=None)
    item = _make_item(resource_type="credential", action="bind", to_id=None, resource_id="cred_001")
    applicator = EffectApplicator(ctx)

    with pytest.raises(RequiredFieldMissingError) as exc_info:
        await applicator.validate(item, identity=_make_identity(), control_session=session)
    assert exc_info.value.field == "to_id"
    assert "<missing>" not in str(exc_info.value)
    mock_credential_repo.get_by_id.assert_not_called()


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_explicit_id_not_visible_raises(
    mock_effects_repo: MagicMock,
) -> None:
    """validate() is the security-critical guard: an explicit-id bind to a
    toolkit the decider can't see must fail up front, before any admin-DB write."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.toolkit_visible_to_owners = AsyncMock(return_value=False)

    item = _make_item(resource_type="toolkit", action="bind", resource_id="tk_other_owner")
    applicator = EffectApplicator(ctx)

    with pytest.raises(ToolkitNotVisibleError):
        await applicator.validate(
            item, identity=_make_identity(sub="usr_op", org_admin=False), control_session=session
        )


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_toolkit_reference_ambiguous_raises(
    mock_effects_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_toolkits_for_api = AsyncMock(return_value=["tk_a", "tk_b"])

    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
    )
    applicator = EffectApplicator(ctx)

    with pytest.raises(ToolkitReferenceAmbiguousError):
        await applicator.validate(item, identity=_make_identity(), control_session=session)


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_scope_grant_missing_resource_id_raises(
    mock_effects_repo: MagicMock,
) -> None:
    """A scope-grant item with no resource_id must fail validate() as a 422
    RequiredFieldMissingError, not slip through to a 500 mid-apply."""
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(resource_type="scope", action="grant", resource_id=None)
    applicator = EffectApplicator(ctx)

    with pytest.raises(RequiredFieldMissingError) as exc_info:
        await applicator.validate(item, identity=_make_identity(), control_session=session)
    assert exc_info.value.field == "resource_id"
    assert "<missing>" not in str(exc_info.value)


# --- unsupported combination ---


async def test_unsupported_combination_returns_skipped() -> None:
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(resource_type="unknown", action="magic")
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, SkippedEffect)
    assert effects.skipped is True
    assert "unsupported" in effects.reason


async def test_unsupported_combination_does_not_raise() -> None:
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(resource_type="future_thing", action="activate")
    applicator = EffectApplicator(ctx)
    effects = await applicator.apply(item, identity=_make_identity(), control_session=session)

    assert isinstance(effects, SkippedEffect)
    assert effects.skipped is True


# --- validation errors ---


async def test_credential_bind_raises_on_missing_to_id() -> None:
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(resource_type="credential", action="bind", to_id=None)
    applicator = EffectApplicator(ctx)

    with pytest.raises(ValueError, match="requires to_id"):
        await applicator.apply(item, identity=_make_identity(), control_session=session)


async def test_credential_bind_raises_on_missing_resource_id() -> None:
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(resource_type="credential", action="bind", resource_id=None)
    applicator = EffectApplicator(ctx)

    with pytest.raises(ValueError, match="requires resource_id"):
        await applicator.apply(item, identity=_make_identity(), control_session=session)


async def test_toolkit_bind_raises_on_missing_ids_and_reference() -> None:
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference=None,
    )
    applicator = EffectApplicator(ctx)

    with pytest.raises(ValueError, match="resource_reference with a vendor"):
        await applicator.apply(item, identity=_make_identity(), control_session=session)


async def test_scope_grant_raises_on_missing_resource_id() -> None:
    ctx = _make_ctx()
    session = _make_session()
    item = _make_item(resource_type="scope", action="grant", resource_id=None)
    applicator = EffectApplicator(ctx)

    with pytest.raises(ValueError, match="requires resource_id"):
        await applicator.apply(item, identity=_make_identity(), control_session=session)


# --- actor_type_from_id ---


def test_actor_type_from_id_user_prefix() -> None:
    assert actor_type_from_id("usr_abc123") == ActorType.USER


def test_actor_type_from_id_agent_prefix() -> None:
    assert actor_type_from_id("agnt_xyz789") == ActorType.AGENT


def test_actor_type_from_id_service_account_prefix() -> None:
    assert actor_type_from_id("sva_def456") == ActorType.SERVICE_ACCOUNT


def test_actor_type_from_id_unknown_prefix_raises() -> None:
    with pytest.raises(ValueError, match="unrecognised prefix"):
        actor_type_from_id("unknown_123")


# --- effect classifier ---


def test_classify_credential_bind_is_control_session() -> None:
    assert classify_effect("credential", "bind") is EffectPhase.CONTROL_SESSION


def test_classify_toolkit_bind_is_admin() -> None:
    assert classify_effect("toolkit", "bind") is EffectPhase.ADMIN


def test_classify_scope_grant_is_admin() -> None:
    assert classify_effect("scope", "grant") is EffectPhase.ADMIN


def test_classify_unknown_combination_is_unsupported() -> None:
    assert classify_effect("unknown", "magic") is EffectPhase.UNSUPPORTED


def test_is_admin_effect_true_for_admin_combinations() -> None:
    assert is_admin_effect(_make_item(resource_type="toolkit", action="bind")) is True
    assert is_admin_effect(_make_item(resource_type="scope", action="grant")) is True


def test_is_admin_effect_false_for_control_and_unsupported() -> None:
    assert is_admin_effect(_make_item(resource_type="credential", action="bind")) is False
    assert is_admin_effect(_make_item(resource_type="unknown", action="magic")) is False


def test_admin_effect_keys_are_exactly_the_admin_combinations() -> None:
    assert set(admin_effect_keys()) == {("toolkit", "bind"), ("scope", "grant")}


# --- provisioning-plan classification + guard (issues #619/#684) ---


def test_classify_toolkit_create_is_fulfilment_only() -> None:
    assert classify_effect("toolkit", "create") is EffectPhase.FULFILMENT_ONLY


def test_classify_credential_provision_is_fulfilment_only() -> None:
    assert classify_effect("credential", "provision") is EffectPhase.FULFILMENT_ONLY


def test_fulfilment_only_intents_are_not_admin_effects() -> None:
    # Inert intents must never be classified as admin effects, or they'd be
    # routed through the post-commit reconcile path instead of skipped.
    assert is_admin_effect(_make_item(resource_type="toolkit", action="create")) is False
    assert is_admin_effect(_make_item(resource_type="credential", action="provision")) is False


async def test_apply_fulfilment_only_intent_is_skipped() -> None:
    ctx = _make_ctx()
    session = _make_session()
    applicator = EffectApplicator(ctx)
    for resource_type, action in (("toolkit", "create"), ("credential", "provision")):
        item = _make_item(resource_type=resource_type, action=action, rules=None)
        effect = await applicator.apply(item, identity=_make_identity(), control_session=session)
        assert isinstance(effect, SkippedEffect)


async def test_validate_fulfilment_only_intent_with_rules_is_rejected() -> None:
    # A fulfilment-only intent can carry no enforceable rules (no binding key).
    ctx = _make_ctx()
    session = _make_session()
    applicator = EffectApplicator(ctx)
    item = _make_item(
        resource_type="toolkit",
        action="create",
        rules=[{"effect": "allow", "methods": ["GET"], "path": ".*"}],
    )
    with pytest.raises(RulesNotSupportedForBindError):
        await applicator.validate(item, identity=_make_identity(), control_session=session)


async def test_validate_credential_bind_in_plan_denies_when_unfulfilled() -> None:
    # In a provisioning plan a credential:bind that the wizard hasn't fulfilled
    # (missing to_id AND/OR resource_id) must be denied with the plan-aware error.
    ctx = _make_ctx()
    session = _make_session()
    applicator = EffectApplicator(ctx)
    for to_id, resource_id in ((None, None), ("tk_001", None), (None, "cred_001")):
        item = _make_item(action="bind", to_id=to_id, resource_id=resource_id)
        with pytest.raises(ProvisioningPlanNotFulfilledError):
            await applicator.validate(
                item,
                identity=_make_identity(),
                control_session=session,
                is_provisioning_plan=True,
            )


@patch(f"{_MODULE}.CredentialRepository")
async def test_validate_credential_bind_in_plan_passes_when_fulfilled(
    mock_cred_repo: MagicMock,
) -> None:
    # Both ids present → the plan guard passes and it proceeds to the DB
    # visibility validator (mocked to a visible credential), i.e. no
    # ProvisioningPlanNotFulfilledError.
    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock(created_by="agnt_001"))
    ctx = _make_ctx()
    session = _make_session()
    applicator = EffectApplicator(ctx)
    item = _make_item(action="bind", to_id="tk_001", resource_id="cred_001")
    # Should not raise the plan error (a DB-validator raise would be a different type).
    try:
        await applicator.validate(
            item,
            identity=_make_identity(),
            control_session=session,
            is_provisioning_plan=True,
        )
    except ProvisioningPlanNotFulfilledError:  # pragma: no cover - failure path
        pytest.fail("a fulfilled credential:bind must not raise ProvisioningPlanNotFulfilledError")


async def test_validate_toolkit_bind_in_plan_denies_when_unfulfilled() -> None:
    # A reference-only toolkit:bind in a plan (no resolved id) must be denied —
    # it can't resolve by the not-yet-visible credential->toolkit join.
    ctx = _make_ctx()
    session = _make_session()
    applicator = EffectApplicator(ctx)
    item = _make_item(
        resource_type="toolkit",
        action="bind",
        resource_id=None,
        to_id=None,
        resource_reference={"vendor": "acme", "name": "widgets"},
    )
    with pytest.raises(ProvisioningPlanNotFulfilledError):
        await applicator.validate(
            item,
            identity=_make_identity(),
            control_session=session,
            is_provisioning_plan=True,
        )
