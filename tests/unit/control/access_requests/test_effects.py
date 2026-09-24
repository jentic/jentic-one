"""Unit tests for EffectApplicator — validate/prepare/complete, plan governance, and classification.

Theme-5 Phase 3 ("governance collapse"): the toolkit vocabulary is retired.
``credential:bind`` now binds the item's actor (an agent) directly to a
credential, applied as a two-stage admin effect — ``prepare()`` writes the
binding's inline permission rules in the caller's control transaction,
``complete()`` writes the admin binding row afterwards — so a crash between
the stages leaves inert rules, never a live rule-less bind (hard problem 6).
``apply()`` survives only for fulfilment-only intents, and a retired/unknown
pair is a **hard failure** (UnsupportedAccessRequestItemError), never the old
silent skip.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.control.services.access_requests.effects import (
    UNGOVERNED_PLAN,
    EffectApplicator,
    EffectPhase,
    PlanGovernance,
    PreparedEffect,
    admin_effect_keys,
    classify_effect,
    is_admin_effect,
    plan_governance_for_items,
)
from jentic_one.control.services.access_requests.errors import (
    CredentialNotFoundForBindError,
    CredentialReferenceAmbiguousError,
    CredentialReferenceUnresolvedError,
    ProvisioningPlanNotFulfilledError,
    RequiredFieldMissingError,
    RuleSetNotFoundForBindError,
    RulesNotSupportedForBindError,
    RulesRequiredForBindError,
    UnsupportedAccessRequestItemError,
    UnsupportedScopeGrantError,
)
from jentic_one.control.services.access_requests.schemas.effects import (
    CredentialBindEffect,
    ScopeGrantEffect,
    SkippedEffect,
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
    admin_read_session = AsyncMock()
    ctx.admin_db.session.return_value.__aenter__ = AsyncMock(return_value=admin_read_session)
    ctx.admin_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
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
    actor_id: str = "agnt_001",
    rules: list[dict[str, Any]] | None = None,
    rule_set_id: str | None = None,
    item_id: str = "arqi_001",
    status: str = "pending",
) -> MagicMock:
    # rule_set_id must default to a REAL None: a bare MagicMock attribute is
    # truthy and would silently flip every bind onto the rule-set path.
    item = MagicMock()
    item.id = item_id
    item.resource_type = resource_type
    item.action = action
    item.resource_id = resource_id
    item.resource_reference = resource_reference
    item.actor_id = actor_id
    item.rules = rules
    item.rule_set_id = rule_set_id
    item.status = status
    return item


_RULES = [{"effect": "allow", "methods": ["GET"], "path": "^/pets"}]


# --- apply(): fulfilment-only intents ONLY -----------------------------------


async def test_apply_fulfilment_only_intent_is_skipped() -> None:
    """credential:provision is an inert placeholder — an audited no-op, not a write."""
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(resource_type="credential", action="provision", resource_id=None, rules=None)
    effect = await applicator.apply(
        item, identity=_make_identity(), control_session=_make_session()
    )
    assert isinstance(effect, SkippedEffect)
    assert effect.skipped is True
    assert "provisioned out-of-band" in effect.reason


async def test_apply_admin_effect_is_a_programming_error() -> None:
    """Admin effects MUST go through prepare()/complete() — the write ordering
    (rules durable before the binding exists) is the hard-problem-6 invariant,
    and apply() refuses to shortcut it."""
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    for resource_type, action in (("credential", "bind"), ("scope", "grant")):
        item = _make_item(resource_type=resource_type, action=action, rules=_RULES)
        with pytest.raises(ValueError, match="prepare"):
            await applicator.apply(item, identity=_make_identity(), control_session=_make_session())


async def test_apply_retired_pair_fails_loudly() -> None:
    """A stored toolkit-era item (toolkit:create / toolkit:bind) — or any
    unknown pair — must hard-fail, never the old silent skip which would
    approve-and-grant-nothing (the "hollow yes")."""
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    for resource_type, action in (
        ("toolkit", "create"),
        ("toolkit", "bind"),
        ("unknown", "magic"),
    ):
        item = _make_item(resource_type=resource_type, action=action, rules=None)
        with pytest.raises(UnsupportedAccessRequestItemError) as exc_info:
            await applicator.apply(item, identity=_make_identity(), control_session=_make_session())
        assert exc_info.value.resource_type == resource_type
        assert exc_info.value.action == action
        # The directive names the surviving verb so the caller can re-file.
        assert "credential" in str(exc_info.value)


# --- prepare(): stage 1 (control transaction) --------------------------------


@patch(f"{_MODULE}.AgentPermissionRuleRepository")
@patch(f"{_MODULE}.CredentialRepository")
async def test_prepare_credential_bind_writes_rules_on_control_session(
    mock_cred_repo: MagicMock,
    mock_rule_repo: MagicMock,
) -> None:
    """prepare() writes the binding's inline rules via replace_user_rules on the
    CALLER's control session — the rules-first half of the two-stage write."""
    ctx = _make_ctx()
    session = _make_session()
    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock())
    mock_rule_repo.replace_user_rules = AsyncMock(return_value=[])

    item = _make_item(resource_id="cred_001", rules=_RULES)
    applicator = EffectApplicator(ctx)
    prepared = await applicator.prepare(item, identity=_make_identity(), control_session=session)

    assert prepared == PreparedEffect(credential_id="cred_001", rules_applied=1)
    mock_rule_repo.replace_user_rules.assert_awaited_once_with(
        session, "agnt_001", "cred_001", _RULES, created_by="usr_admin"
    )


@patch(f"{_MODULE}.AgentPermissionRuleRepository")
@patch(f"{_MODULE}.CredentialRepository")
async def test_prepare_rule_set_bind_writes_no_control_rules(
    mock_cred_repo: MagicMock,
    mock_rule_repo: MagicMock,
) -> None:
    """A rule_set_id bind carries its policy as a pointer on the admin row —
    prepare() must NOT write inline control rules for it."""
    ctx = _make_ctx()
    session = _make_session()
    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock())
    mock_rule_repo.replace_user_rules = AsyncMock()

    item = _make_item(resource_id="cred_001", rules=None, rule_set_id="prs_001")
    applicator = EffectApplicator(ctx)
    prepared = await applicator.prepare(item, identity=_make_identity(), control_session=session)

    assert prepared == PreparedEffect(credential_id="cred_001", rules_applied=0)
    mock_rule_repo.replace_user_rules.assert_not_called()


@patch(f"{_MODULE}.AgentPermissionRuleRepository")
@patch(f"{_MODULE}.CredentialRepository")
async def test_prepare_rules_less_bind_raises(
    mock_cred_repo: MagicMock,
    mock_rule_repo: MagicMock,
) -> None:
    """The reconcile path re-drives prepare() without re-running validate(), so
    prepare() keeps its own guard: a rule-less bind can never be written."""
    ctx = _make_ctx()
    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock())
    item = _make_item(resource_id="cred_001", rules=None, rule_set_id=None)
    applicator = EffectApplicator(ctx)
    with pytest.raises(RulesRequiredForBindError):
        await applicator.prepare(item, identity=_make_identity(), control_session=_make_session())
    mock_rule_repo.replace_user_rules.assert_not_called()


@patch(f"{_MODULE}.CredentialRepository")
async def test_prepare_explicit_id_not_visible_raises(
    mock_cred_repo: MagicMock,
) -> None:
    """prepare() re-runs the same visibility gate as validate() so the two can
    never drift — a credential the decider can't see fails stage 1 up front."""
    ctx = _make_ctx()
    mock_cred_repo.get_by_id = AsyncMock(return_value=None)
    item = _make_item(resource_id="cred_foreign", rules=_RULES)
    applicator = EffectApplicator(ctx)
    with pytest.raises(CredentialNotFoundForBindError):
        await applicator.prepare(
            item,
            identity=_make_identity(sub="usr_op", org_admin=False),
            control_session=_make_session(),
        )


@patch(f"{_MODULE}.AgentPermissionRuleRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_prepare_resolves_reference_under_admin_owner_axis(
    mock_effects_repo: MagicMock,
    mock_rule_repo: MagicMock,
) -> None:
    """An org:admin decider resolves a reference unscoped (owner_ids=None,
    bound push-down skipped), and the vendor is slugified to match stored rows
    (#656)."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_credentials_for_api = AsyncMock(return_value=["cred_resolved"])
    mock_rule_repo.replace_user_rules = AsyncMock(return_value=[])

    item = _make_item(
        resource_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
        rules=_RULES,
    )
    applicator = EffectApplicator(ctx)
    prepared = await applicator.prepare(item, identity=_make_identity(), control_session=session)

    assert prepared.credential_id == "cred_resolved"
    mock_effects_repo.resolve_credentials_for_api.assert_awaited_once_with(
        session,
        vendor="httpbin-org",
        name="httpbin",
        version=None,
        owner_ids=None,
        bound_credential_ids=None,
    )
    # org:admin needs no admin-DB push-down session.
    ctx.admin_db.session.assert_not_called()


@patch(f"{_MODULE}.AgentPermissionRuleRepository")
@patch(f"{_MODULE}.EffectsRepository")
async def test_prepare_reference_owner_scoped_with_bound_pushdown_for_non_admin(
    mock_effects_repo: MagicMock,
    mock_rule_repo: MagicMock,
) -> None:
    """A non-admin decider's resolution is confined to their owner axis PLUS the
    admin-DB push-down list (credentials bound to agents they own) — hard
    problem 8's binding-widened visibility."""
    ctx = _make_ctx()
    session = _make_session()
    mock_effects_repo.resolve_credentials_for_api = AsyncMock(return_value=["cred_owned"])
    mock_effects_repo.list_bound_credential_ids_for_owned_agents = AsyncMock(
        return_value=["cred_via_binding"]
    )
    mock_rule_repo.replace_user_rules = AsyncMock(return_value=[])

    item = _make_item(
        resource_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
        rules=_RULES,
    )
    applicator = EffectApplicator(ctx)
    await applicator.prepare(
        item, identity=_make_identity(sub="usr_op", org_admin=False), control_session=session
    )

    mock_effects_repo.list_bound_credential_ids_for_owned_agents.assert_awaited_once()
    assert mock_effects_repo.list_bound_credential_ids_for_owned_agents.await_args.kwargs[
        "owner_ids"
    ] == ["usr_op"]
    mock_effects_repo.resolve_credentials_for_api.assert_awaited_once_with(
        session,
        vendor="httpbin-org",
        name="httpbin",
        version=None,
        owner_ids=["usr_op"],
        bound_credential_ids=["cred_via_binding"],
    )


async def test_prepare_scope_grant_checks_allow_list() -> None:
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    ok = _make_item(resource_type="scope", action="grant", resource_id=_GRANTABLE)
    prepared = await applicator.prepare(
        ok, identity=_make_identity(), control_session=_make_session()
    )
    assert prepared == PreparedEffect()

    bad = _make_item(resource_type="scope", action="grant", resource_id="org:admin")
    with pytest.raises(UnsupportedScopeGrantError):
        await applicator.prepare(bad, identity=_make_identity(), control_session=_make_session())


async def test_prepare_retired_pair_fails_loudly() -> None:
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(resource_type="toolkit", action="bind", resource_id="tk_old", rules=None)
    with pytest.raises(UnsupportedAccessRequestItemError):
        await applicator.prepare(item, identity=_make_identity(), control_session=_make_session())


# --- complete(): stage 2 (admin transaction) ----------------------------------


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_complete_credential_bind_happy_path(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    mock_effects_repo.bind_agent_to_credential = AsyncMock(return_value=("acb_new_001", False))

    item = _make_item(resource_id="cred_001", rules=_RULES)
    applicator = EffectApplicator(ctx)
    effect = await applicator.complete(
        item,
        identity=_make_identity(),
        prepared=PreparedEffect(credential_id="cred_001", rules_applied=1),
    )

    assert isinstance(effect, CredentialBindEffect)
    assert effect.binding_id == "acb_new_001"
    assert effect.credential_id == "cred_001"
    assert effect.rules_applied == 1
    assert effect.rule_set_id is None
    assert effect.already_bound is False
    bound_kwargs = mock_effects_repo.bind_agent_to_credential.await_args.kwargs
    assert bound_kwargs["agent_id"] == "agnt_001"
    assert bound_kwargs["credential_id"] == "cred_001"
    assert bound_kwargs["rule_set_id"] is None
    assert bound_kwargs["created_by"] == "usr_admin"
    mock_audit.assert_awaited_once()


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_complete_credential_bind_duplicate_idempotent(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """A retry converges on the existing binding (ON CONFLICT) — the effect
    records already_bound so the ack is honest about what happened."""
    ctx = _make_ctx()
    mock_effects_repo.bind_agent_to_credential = AsyncMock(return_value=("acb_existing", True))

    item = _make_item(resource_id="cred_001", rules=_RULES)
    applicator = EffectApplicator(ctx)
    effect = await applicator.complete(
        item,
        identity=_make_identity(),
        prepared=PreparedEffect(credential_id="cred_001", rules_applied=1),
    )
    assert isinstance(effect, CredentialBindEffect)
    assert effect.already_bound is True
    assert effect.binding_id == "acb_existing"


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_complete_rule_set_bind_carries_pointer_on_admin_row(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """The rule-set pointer rides on the admin binding row; the effect surfaces
    it (with rules_applied 0) so the ack names the policy carrier."""
    ctx = _make_ctx()
    mock_effects_repo.bind_agent_to_credential = AsyncMock(return_value=("acb_rs", False))

    item = _make_item(resource_id="cred_001", rules=None, rule_set_id="prs_001")
    applicator = EffectApplicator(ctx)
    effect = await applicator.complete(
        item,
        identity=_make_identity(),
        prepared=PreparedEffect(credential_id="cred_001", rules_applied=0),
    )
    assert isinstance(effect, CredentialBindEffect)
    assert effect.rule_set_id == "prs_001"
    assert effect.rules_applied == 0
    assert mock_effects_repo.bind_agent_to_credential.await_args.kwargs["rule_set_id"] == "prs_001"


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_complete_scope_grant_happy_path_and_idempotent(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(resource_type="scope", action="grant", resource_id=_GRANTABLE)

    mock_effects_repo.grant_scope_to_actor = AsyncMock(return_value=True)
    effect = await applicator.complete(item, identity=_make_identity(), prepared=PreparedEffect())
    assert isinstance(effect, ScopeGrantEffect)
    assert effect.scope == _GRANTABLE
    assert effect.already_granted is False

    mock_effects_repo.grant_scope_to_actor = AsyncMock(return_value=False)
    effect = await applicator.complete(item, identity=_make_identity(), prepared=PreparedEffect())
    assert isinstance(effect, ScopeGrantEffect)
    assert effect.already_granted is True


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
async def test_complete_scope_grant_privileged_scope_rejected(
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """Defense-in-depth: even at the last write, a privileged scope (org:admin)
    is refused — the confused-deputy guard cannot rely on validate() alone."""
    ctx = _make_ctx()
    mock_effects_repo.grant_scope_to_actor = AsyncMock()
    item = _make_item(resource_type="scope", action="grant", resource_id="org:admin")
    applicator = EffectApplicator(ctx)
    with pytest.raises(UnsupportedScopeGrantError):
        await applicator.complete(item, identity=_make_identity(), prepared=PreparedEffect())
    mock_effects_repo.grant_scope_to_actor.assert_not_called()


async def test_complete_retired_pair_fails_loudly() -> None:
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(resource_type="toolkit", action="create", resource_id=None, rules=None)
    with pytest.raises(UnsupportedAccessRequestItemError):
        await applicator.complete(item, identity=_make_identity(), prepared=PreparedEffect())


# --- rules-first write ordering (hard problem 6) ------------------------------


@patch(f"{_MODULE}.record_audit_best_effort", new_callable=AsyncMock)
@patch(f"{_MODULE}.EffectsRepository")
@patch(f"{_MODULE}.AgentPermissionRuleRepository")
@patch(f"{_MODULE}.CredentialRepository")
async def test_prepare_then_complete_orders_rules_before_binding(
    mock_cred_repo: MagicMock,
    mock_rule_repo: MagicMock,
    mock_effects_repo: MagicMock,
    mock_audit: AsyncMock,
) -> None:
    """The whole point of the two-stage split: the control-DB rules write
    (prepare) happens strictly BEFORE the admin-DB binding write (complete).
    Asserted via a shared call recorder so a refactor that flips the order —
    reintroducing the live-rule-less-bind crash window — fails here."""
    ctx = _make_ctx()
    calls: list[str] = []

    async def _record_rules(*args: object, **kwargs: object) -> list[object]:
        calls.append("rules_write")
        return []

    async def _record_bind(*args: object, **kwargs: object) -> tuple[str, bool]:
        calls.append("binding_write")
        return ("acb_ordered", False)

    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock())
    mock_rule_repo.replace_user_rules = AsyncMock(side_effect=_record_rules)
    mock_effects_repo.bind_agent_to_credential = AsyncMock(side_effect=_record_bind)

    item = _make_item(resource_id="cred_001", rules=_RULES)
    applicator = EffectApplicator(ctx)
    prepared = await applicator.prepare(
        item, identity=_make_identity(), control_session=_make_session()
    )
    effect = await applicator.complete(item, identity=_make_identity(), prepared=prepared)

    assert calls == ["rules_write", "binding_write"]
    assert isinstance(effect, CredentialBindEffect)
    assert effect.credential_id == "cred_001"


# --- validate() pre-pass -------------------------------------------------------


@patch(f"{_MODULE}.CredentialRepository")
async def test_validate_credential_bind_visible_id_passes(
    mock_cred_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock())
    item = _make_item(resource_id="cred_001", rules=_RULES)
    applicator = EffectApplicator(ctx)
    await applicator.validate(item, identity=_make_identity(), control_session=_make_session())
    mock_cred_repo.get_by_id.assert_awaited_once()


@patch(f"{_MODULE}.CredentialRepository")
async def test_validate_credential_bind_missing_credential_raises(
    mock_cred_repo: MagicMock,
) -> None:
    """A credential:bind naming a non-existent/invisible credential must fail
    validate() as a 422 CredentialNotFoundForBindError, not slip through to a
    FK fault mid-apply (issue #649)."""
    ctx = _make_ctx()
    mock_cred_repo.get_by_id = AsyncMock(return_value=None)
    item = _make_item(resource_id="cred_missing", rules=_RULES)
    applicator = EffectApplicator(ctx)
    with pytest.raises(CredentialNotFoundForBindError):
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_reference_unresolved_raises(
    mock_effects_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    mock_effects_repo.resolve_credentials_for_api = AsyncMock(return_value=[])
    item = _make_item(
        resource_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
        rules=_RULES,
    )
    applicator = EffectApplicator(ctx)
    with pytest.raises(CredentialReferenceUnresolvedError):
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_reference_unresolved_message_omits_none(
    mock_effects_repo: MagicMock,
) -> None:
    """A vendor-only reference (no name) must not surface a misleading
    'vendor/None' in the error message."""
    ctx = _make_ctx()
    mock_effects_repo.resolve_credentials_for_api = AsyncMock(return_value=[])
    item = _make_item(resource_id=None, resource_reference={"vendor": "httpbin.org"}, rules=_RULES)
    applicator = EffectApplicator(ctx)
    with pytest.raises(CredentialReferenceUnresolvedError) as excinfo:
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())
    assert "None" not in str(excinfo.value)
    assert "httpbin.org" in str(excinfo.value)


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_reference_ambiguous_raises(
    mock_effects_repo: MagicMock,
) -> None:
    """Several covering credentials → the approver must disambiguate by amending
    an explicit resource_id; the raise keeps the request pending."""
    ctx = _make_ctx()
    mock_effects_repo.resolve_credentials_for_api = AsyncMock(return_value=["cred_a", "cred_b"])
    item = _make_item(
        resource_id=None,
        resource_reference={"vendor": "httpbin.org", "name": "httpbin"},
        rules=_RULES,
    )
    applicator = EffectApplicator(ctx)
    with pytest.raises(CredentialReferenceAmbiguousError) as excinfo:
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())
    assert excinfo.value.candidates == ["cred_a", "cred_b"]


async def test_validate_vendor_less_bind_raises_missing_field() -> None:
    """No resource_id and no usable reference: a clear missing-field error, not
    a bare ValueError mid-apply."""
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    for reference in (None, {"name": "widgets"}):
        item = _make_item(resource_id=None, resource_reference=reference, rules=_RULES)
        with pytest.raises(RequiredFieldMissingError) as exc_info:
            await applicator.validate(
                item, identity=_make_identity(), control_session=_make_session()
            )
        assert exc_info.value.field == "resource_id"


@patch(f"{_MODULE}.CredentialRepository")
async def test_validate_rules_less_bind_raises(
    mock_cred_repo: MagicMock,
) -> None:
    """A bind with neither rules nor rule_set_id is a live default-deny the
    operator believes granted — validate() raises (keeping the request pending
    for amendment) BEFORE any target resolution runs."""
    ctx = _make_ctx()
    mock_cred_repo.get_by_id = AsyncMock()
    item = _make_item(resource_id="cred_001", rules=None, rule_set_id=None)
    applicator = EffectApplicator(ctx)
    with pytest.raises(RulesRequiredForBindError):
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())
    mock_cred_repo.get_by_id.assert_not_called()


@patch(f"{_MODULE}.PermissionRuleSetRepository")
@patch(f"{_MODULE}.CredentialRepository")
async def test_validate_dangling_rule_set_raises(
    mock_cred_repo: MagicMock,
    mock_rule_set_repo: MagicMock,
) -> None:
    """rule_set_id is FK-less across the control/admin seam, so validate() must
    prove the set exists — approving past a dangling pointer would create a
    live default-deny binding (the hollow-yes shape again)."""
    ctx = _make_ctx()
    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock())
    mock_rule_set_repo.get_by_id = AsyncMock(return_value=None)
    item = _make_item(resource_id="cred_001", rules=None, rule_set_id="prs_missing")
    applicator = EffectApplicator(ctx)
    with pytest.raises(RuleSetNotFoundForBindError) as exc_info:
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())
    assert exc_info.value.rule_set_id == "prs_missing"


@patch(f"{_MODULE}.PermissionRuleSetRepository")
@patch(f"{_MODULE}.CredentialRepository")
async def test_validate_existing_rule_set_passes(
    mock_cred_repo: MagicMock,
    mock_rule_set_repo: MagicMock,
) -> None:
    ctx = _make_ctx()
    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock())
    mock_rule_set_repo.get_by_id = AsyncMock(return_value=MagicMock())
    item = _make_item(resource_id="cred_001", rules=None, rule_set_id="prs_001")
    applicator = EffectApplicator(ctx)
    await applicator.validate(item, identity=_make_identity(), control_session=_make_session())
    mock_rule_set_repo.get_by_id.assert_awaited_once()


async def test_validate_scope_grant_privileged_raises() -> None:
    ctx = _make_ctx()
    item = _make_item(resource_type="scope", action="grant", resource_id="org:admin")
    applicator = EffectApplicator(ctx)
    with pytest.raises(UnsupportedScopeGrantError):
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())


async def test_validate_scope_grant_missing_resource_id_raises() -> None:
    """A scope-grant item with no resource_id must fail validate() as a 422
    RequiredFieldMissingError, not slip through to a 500 mid-apply."""
    ctx = _make_ctx()
    item = _make_item(resource_type="scope", action="grant", resource_id=None)
    applicator = EffectApplicator(ctx)
    with pytest.raises(RequiredFieldMissingError) as exc_info:
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())
    assert exc_info.value.field == "resource_id"


async def test_validate_scope_grant_apis_write_passes() -> None:
    """Regression: ``apis:write`` is self-service grantable.

    An agent must be able to request ``apis:write`` (so it can ``jentic catalog
    import`` after a human approves) and have an owner approve it. validate()
    mirrors the file-time guard via ``GRANTABLE_SCOPES``, so it must NOT reject
    ``apis:write``.
    """
    assert "apis:write" in GRANTABLE_SCOPES
    ctx = _make_ctx()
    item = _make_item(resource_type="scope", action="grant", resource_id="apis:write")
    applicator = EffectApplicator(ctx)
    await applicator.validate(item, identity=_make_identity(), control_session=_make_session())


async def test_validate_scope_grant_overlays_confirm_rejected() -> None:
    """``overlays:confirm`` is an operator scope and must NOT be self-service grantable.

    Confirming an overlay rewrites an API's served spec, so an agent must never obtain
    the scope through a ``scope:grant`` access request. The shared ``GRANTABLE_SCOPES``
    guard (used by both the file-time and decide-time checks) is the safety anchor for the
    purpose-scoped downgrade from ``org:admin``; assert it here so a future addition to the
    allow-list can't silently open the escalation path.
    """
    assert "overlays:confirm" not in GRANTABLE_SCOPES
    ctx = _make_ctx()
    item = _make_item(resource_type="scope", action="grant", resource_id="overlays:confirm")
    applicator = EffectApplicator(ctx)
    with pytest.raises(UnsupportedScopeGrantError):
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())


async def test_validate_fulfilment_only_intent_with_rules_is_rejected() -> None:
    """A fulfilment-only intent (credential:provision) can carry no enforceable
    rules — there is no binding key to attach them to."""
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(
        resource_type="credential", action="provision", resource_id=None, rules=_RULES
    )
    with pytest.raises(RulesNotSupportedForBindError):
        await applicator.validate(item, identity=_make_identity(), control_session=_make_session())


async def test_validate_fulfilment_only_intent_without_rules_passes() -> None:
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(resource_type="credential", action="provision", resource_id=None, rules=None)
    await applicator.validate(item, identity=_make_identity(), control_session=_make_session())


async def test_validate_retired_pair_fails_loudly() -> None:
    """A stored legacy toolkit item hard-fails the decision (422) — never the
    old UNSUPPORTED silent skip."""
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    for resource_type, action in (("toolkit", "create"), ("toolkit", "bind"), ("api", "invoke")):
        item = _make_item(resource_type=resource_type, action=action, rules=None)
        with pytest.raises(UnsupportedAccessRequestItemError):
            await applicator.validate(
                item, identity=_make_identity(), control_session=_make_session()
            )


# --- validate() under plan governance -----------------------------------------


async def test_validate_governed_bind_without_id_denies_with_plan_reason() -> None:
    """A governed credential:bind can only be satisfied by the credential id the
    wizard stamps — a plain approval must surface the plan-aware reason naming
    the awaiting intent(s) and API, not a cryptic resolution error."""
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(
        resource_id=None,
        resource_reference={"vendor": "acme", "name": "widgets"},
        rules=_RULES,
    )
    plan_governance = PlanGovernance(
        governing_intent_ids=frozenset({"arqi_intent_1"}),
        governing_api=("acme", "widgets"),
    )
    with pytest.raises(ProvisioningPlanNotFulfilledError) as exc_info:
        await applicator.validate(
            item,
            identity=_make_identity(),
            control_session=_make_session(),
            plan_governance=plan_governance,
        )
    assert exc_info.value.governing_intent_ids == frozenset({"arqi_intent_1"})
    assert exc_info.value.governing_api == ("acme", "widgets")
    assert "acme/widgets" in str(exc_info.value)
    assert "arqi_intent_1" in str(exc_info.value)


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_governed_bind_never_half_wires_to_preexisting_credential(
    mock_effects_repo: MagicMock,
) -> None:
    """Even when a PRE-EXISTING credential covers the same API, a governed bind
    must NOT resolve to it: approving it plain would wire the agent to the old
    credential while the plan's provision intent is still unfulfilled — a
    half-wired grant. The plan-aware denial must win, without resolution ever
    being attempted."""
    mock_effects_repo.resolve_credentials_for_api = AsyncMock(return_value=["cred_preexisting"])
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(
        resource_id=None,
        resource_reference={"vendor": "acme", "name": "widgets"},
        rules=_RULES,
    )
    with pytest.raises(ProvisioningPlanNotFulfilledError):
        await applicator.validate(
            item,
            identity=_make_identity(),
            control_session=_make_session(),
            plan_governance=PlanGovernance(
                governing_intent_ids=frozenset({"arqi_intent_1"}),
                governing_api=("acme", "widgets"),
            ),
        )
    mock_effects_repo.resolve_credentials_for_api.assert_not_awaited()


@patch(f"{_MODULE}.CredentialRepository")
async def test_validate_governed_bind_with_stamped_id_passes_plan_guard(
    mock_cred_repo: MagicMock,
) -> None:
    """A wizard-stamped resource_id satisfies the plan contract: the guard
    passes and validation proceeds to the ordinary visibility check."""
    mock_cred_repo.get_by_id = AsyncMock(return_value=MagicMock())
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(resource_id="cred_stamped", rules=_RULES)
    plan_governance = PlanGovernance(governing_intent_ids=frozenset({"arqi_intent_1"}))
    await applicator.validate(
        item,
        identity=_make_identity(),
        control_session=_make_session(),
        plan_governance=plan_governance,
    )
    mock_cred_repo.get_by_id.assert_awaited_once()


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_ungoverned_bind_in_composite_passes_when_reference_resolves(
    mock_effects_repo: MagicMock,
) -> None:
    """A composite request can mix plan chains with PLAIN reference binds to
    credentials that already exist. A bind whose API no chain provisions gets
    UNGOVERNED_PLAN from decide() and is satisfiable exactly as filed — the
    plan context must not auto-deny it (the mixed-composite fix)."""
    mock_effects_repo.resolve_credentials_for_api = AsyncMock(return_value=["cred_existing"])
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(
        resource_id=None,
        resource_reference={"vendor": "acme", "name": "widgets"},
        rules=_RULES,
    )
    await applicator.validate(
        item,
        identity=_make_identity(),
        control_session=_make_session(),
        plan_governance=UNGOVERNED_PLAN,
    )


@patch(f"{_MODULE}.EffectsRepository")
async def test_validate_ungoverned_bind_in_composite_keeps_ambiguity_pending(
    mock_effects_repo: MagicMock,
) -> None:
    """An AMBIGUOUS plain reference inside a composite keeps the documented
    non-plan semantics: it raises (so the item stays pending for amendment)
    instead of being converted into a misleading plan denial."""
    mock_effects_repo.resolve_credentials_for_api = AsyncMock(return_value=["cred_1", "cred_2"])
    ctx = _make_ctx()
    applicator = EffectApplicator(ctx)
    item = _make_item(
        resource_id=None,
        resource_reference={"vendor": "acme", "name": "widgets"},
        rules=_RULES,
    )
    with pytest.raises(CredentialReferenceAmbiguousError):
        await applicator.validate(
            item,
            identity=_make_identity(),
            control_session=_make_session(),
            plan_governance=UNGOVERNED_PLAN,
        )


# --- effect classifier ---------------------------------------------------------


def test_classify_credential_bind_is_admin() -> None:
    # Theme-5 Phase 3: credential:bind moved to the ADMIN phase (the binding
    # row lives in the admin DB) with a control-first rules prologue.
    assert classify_effect("credential", "bind") is EffectPhase.ADMIN


def test_classify_scope_grant_is_admin() -> None:
    assert classify_effect("scope", "grant") is EffectPhase.ADMIN


def test_classify_credential_provision_is_fulfilment_only() -> None:
    assert classify_effect("credential", "provision") is EffectPhase.FULFILMENT_ONLY


def test_classify_retired_toolkit_pairs_are_unsupported() -> None:
    # The retired vocabulary must classify UNSUPPORTED (hard failure), so a
    # stored pre-Phase-3 row can never be silently skipped or half-applied.
    assert classify_effect("toolkit", "create") is EffectPhase.UNSUPPORTED
    assert classify_effect("toolkit", "bind") is EffectPhase.UNSUPPORTED


def test_classify_unknown_combination_is_unsupported() -> None:
    assert classify_effect("unknown", "magic") is EffectPhase.UNSUPPORTED


def test_is_admin_effect_true_for_admin_combinations() -> None:
    assert is_admin_effect(_make_item(resource_type="credential", action="bind")) is True
    assert is_admin_effect(_make_item(resource_type="scope", action="grant")) is True


def test_is_admin_effect_false_for_fulfilment_and_unsupported() -> None:
    # Inert intents must never be classified as admin effects, or they'd be
    # routed through the post-commit reconcile path instead of skipped.
    assert is_admin_effect(_make_item(resource_type="credential", action="provision")) is False
    assert is_admin_effect(_make_item(resource_type="unknown", action="magic")) is False


def test_admin_effect_keys_are_exactly_the_admin_combinations() -> None:
    assert set(admin_effect_keys()) == {("credential", "bind"), ("scope", "grant")}


# --- actor_type_from_id --------------------------------------------------------


def test_actor_type_from_id_user_prefix() -> None:
    assert actor_type_from_id("usr_abc123") == ActorType.USER


def test_actor_type_from_id_agent_prefix() -> None:
    assert actor_type_from_id("agnt_xyz789") == ActorType.AGENT


def test_actor_type_from_id_service_account_prefix() -> None:
    assert actor_type_from_id("sva_def456") == ActorType.SERVICE_ACCOUNT


def test_actor_type_from_id_unknown_prefix_raises() -> None:
    with pytest.raises(ValueError, match="unrecognised prefix"):
        actor_type_from_id("unknown_123")


# --- plan governance (issue #778, Phase 3 shape) -------------------------------
#
# ``plan_governance_for_items`` computes per-item governance from a request's
# live fulfilment intents. Phase 3 collapsed a plan to the 2-item
# ``credential:provision`` + ``credential:bind`` chain, and made credential:bind
# governance **API-scoped**: a live intent for (vendor, name) governs a
# reference-matching bind with no explicit resource_id. These tests exercise:
#
#   1. an intent for API X does not govern an independent bind for API Y;
#   2. non-live (withdrawn/denied) intents govern nothing;
#   3. an explicit-id (wizard-stamped) bind is never governed;
#   4. an unattributable bind (no vendor and no id) is governed conservatively;
#   5. slug normalization ("httpbin.org" vs "httpbin-org") does not defeat
#      governance;
#   6. version is not part of the key — an intent covers all versions.


def _intent(
    *,
    vendor: str | None = "acme",
    name: str | None = "widgets",
    item_id: str = "arqi_intent",
    status: str = "pending",
) -> MagicMock:
    ref: dict[str, Any] | None = None
    if vendor is not None:
        ref = {"vendor": vendor}
        if name is not None:
            ref["name"] = name
    return _make_item(
        resource_type="credential",
        action="provision",
        resource_id=None,
        resource_reference=ref,
        item_id=item_id,
        status=status,
    )


def _bind(
    *,
    vendor: str | None = "acme",
    name: str | None = "widgets",
    version: str | None = None,
    resource_id: str | None = None,
    item_id: str = "arqi_bind",
    status: str = "pending",
) -> MagicMock:
    ref: dict[str, Any] | None = None
    if vendor is not None or name is not None:
        ref = {}
        if vendor is not None:
            ref["vendor"] = vendor
        if name is not None:
            ref["name"] = name
        if version is not None:
            ref["version"] = version
    return _make_item(
        resource_type="credential",
        action="bind",
        resource_id=resource_id,
        resource_reference=ref,
        rules=_RULES,
        item_id=item_id,
        status=status,
    )


def test_plan_governance_empty_when_no_intents() -> None:
    assert plan_governance_for_items([_bind(item_id="arqi_bind_1")]) == {}


def test_plan_governance_matches_two_item_chain() -> None:
    """The CLI's 2-item plan chain: provision + reference bind for the same API.
    The bind is governed, citing the intent and the canonical API tuple."""
    items = [
        _intent(vendor="acme", name="widgets", item_id="arqi_i1"),
        _bind(vendor="acme", name="widgets", item_id="arqi_b1"),
    ]
    governance = plan_governance_for_items(items)
    assert set(governance.keys()) == {"arqi_b1"}
    assert governance["arqi_b1"].is_governed
    assert governance["arqi_b1"].governing_intent_ids == frozenset({"arqi_i1"})
    assert governance["arqi_b1"].governing_api == ("acme", "widgets")


def test_plan_governance_leaves_independent_bind_ungoverned() -> None:
    """#778 core case: an intent for API X + an independent reference-only
    credential:bind for API Y must not flip the Y bind onto the plan contract."""
    items = [
        _intent(vendor="acme", name="widgets", item_id="arqi_intent"),
        _bind(vendor="other", name="thing", item_id="arqi_independent_bind"),
    ]
    governance = plan_governance_for_items(items)
    assert "arqi_independent_bind" not in governance


def test_plan_governance_skips_explicit_id_bind() -> None:
    """A wizard-stamped bind with an explicit resource_id and no reference
    resolves by its id under the plain contract — it isn't the wizard's to
    satisfy (it may have been stamped by a previous wizard pass)."""
    items = [
        _intent(vendor="acme", name="widgets", item_id="arqi_i1"),
        _bind(vendor=None, name=None, resource_id="cred_stamped", item_id="arqi_stamped"),
    ]
    assert plan_governance_for_items(items) == {}


def test_plan_governance_stamped_reference_bind_passes_via_validate_exemption() -> None:
    """A reference bind the wizard stamped keeps its reference, so it stays in
    the governance mapping — but validate()'s explicit-id exemption is what
    lets it through. The mapping records the fact; the id is the release."""
    items = [
        _intent(vendor="acme", name="widgets", item_id="arqi_i1"),
        _bind(vendor="acme", name="widgets", resource_id="cred_stamped", item_id="arqi_stamped"),
    ]
    governance = plan_governance_for_items(items)
    assert governance["arqi_stamped"].is_governed


def test_plan_governance_governs_unattributable_bind_conservatively() -> None:
    """A bind with a vendor-less (or missing) reference AND no explicit id can
    never resolve by reference — the plain contract would surface a bare
    resolution error stranding the operator. Inside a plan it is governed by
    every live intent so the denial is legible and plan-aware."""
    items = [
        _intent(vendor="acme", name="widgets", item_id="arqi_i1"),
        _intent(vendor="beta", name="gadgets", item_id="arqi_i2"),
        _bind(vendor=None, name="widgets", item_id="arqi_vendorless_bind"),
    ]
    governance = plan_governance_for_items(items)
    assert governance["arqi_vendorless_bind"].is_governed
    assert governance["arqi_vendorless_bind"].governing_intent_ids == frozenset(
        {"arqi_i1", "arqi_i2"}
    )
    assert governance["arqi_vendorless_bind"].governing_api is None


def test_plan_governance_untargeted_intent_governs_matching_reference_binds() -> None:
    """An intent with no usable reference can't be tied to one API, so it
    conservatively backs every reference-carrying bind alongside any API-scoped
    intents."""
    items = [
        _intent(vendor=None, item_id="arqi_untargeted"),
        _bind(vendor="acme", name="widgets", item_id="arqi_b1"),
    ]
    governance = plan_governance_for_items(items)
    assert governance["arqi_b1"].governing_intent_ids == frozenset({"arqi_untargeted"})


def test_plan_governance_ignores_withdrawn_intents() -> None:
    """An abandoned plan must not carry over: subsequent binds revert to the
    plain contract on the next decide()."""
    items = [
        _intent(item_id="arqi_dead", status="withdrawn"),
        _bind(vendor="acme", name="widgets", item_id="arqi_b1"),
    ]
    assert plan_governance_for_items(items) == {}


def test_plan_governance_ignores_denied_intents() -> None:
    items = [
        _intent(item_id="arqi_dead", status="denied"),
        _bind(vendor="acme", name="widgets", item_id="arqi_b1"),
    ]
    assert plan_governance_for_items(items) == {}


def test_plan_governance_approved_intent_still_governs() -> None:
    """An APPROVED (but not yet fulfilled) intent is still live: the wizard has
    yet to stamp the bind, so the plan contract must keep holding it."""
    items = [
        _intent(item_id="arqi_live", status="approved"),
        _bind(vendor="acme", name="widgets", item_id="arqi_b1"),
    ]
    governance = plan_governance_for_items(items)
    assert governance["arqi_b1"].governing_intent_ids == frozenset({"arqi_live"})


def test_plan_governance_normalizes_slug_vs_raw_domain() -> None:
    """An agent files a reference with a raw domain (``httpbin.org``); the
    intent may have been stored slugified. Governance must match either way,
    and ``governing_api`` records the *slug* form so a diagnostic renders the
    canonical tuple rather than whichever spelling first arrived."""
    items = [
        _intent(vendor="httpbin-org", name=None, item_id="arqi_i1"),
        _bind(vendor="httpbin.org", name=None, item_id="arqi_b1"),
    ]
    governance = plan_governance_for_items(items)
    assert set(governance.keys()) == {"arqi_b1"}
    assert governance["arqi_b1"].governing_api == ("httpbin-org", None)


def test_plan_governance_intent_covers_all_versions() -> None:
    """Version is excluded from the governance key — an intent for (vendor,
    name) covers a bind for any version of that API."""
    items = [
        _intent(vendor="acme", name="widgets", item_id="arqi_i1"),
        _bind(vendor="acme", name="widgets", version="1.0.0", item_id="arqi_b1"),
    ]
    governance = plan_governance_for_items(items)
    assert set(governance.keys()) == {"arqi_b1"}
    assert governance["arqi_b1"].governing_intent_ids == frozenset({"arqi_i1"})


def test_plan_governance_excludes_already_decided_binds() -> None:
    """decide() only re-validates PENDING or APPROVED items; a DENIED/WITHDRAWN
    bind is never re-validated so its governance doesn't matter and we omit
    it to keep the mapping tight against actual decide() behaviour."""
    items = [
        _intent(vendor="acme", name="widgets", item_id="arqi_i1"),
        _bind(vendor="acme", name="widgets", item_id="arqi_dead_bind", status="denied"),
    ]
    assert plan_governance_for_items(items) == {}


def test_plan_governance_default_is_not_governed() -> None:
    """Sanity: the empty-default value is what ``validate()`` branches on for the plain path."""
    assert not UNGOVERNED_PLAN.is_governed
    assert not PlanGovernance().is_governed
    assert PlanGovernance(governing_intent_ids=frozenset({"arqi_1"})).is_governed
