"""Effect applicator — translates approved access-request items into authorization artifacts."""

from __future__ import annotations

import enum
from typing import Any

import structlog

from jentic_one.control.core.schema.access_request_items import AccessRequestItem
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos.credential_repo import CredentialRepository
from jentic_one.control.repos.effects_repo import BindTargetMissingError, EffectsRepository
from jentic_one.control.repos.toolkit_permission_repo import ToolkitPermissionRepository
from jentic_one.control.scoping.filters import build_access_filters, toolkit_owner_scope
from jentic_one.control.services.access_requests.errors import (
    CredentialNotFoundForBindError,
    RequiredFieldMissingError,
    RulesNotSupportedForBindError,
    ToolkitNotVisibleError,
    ToolkitReferenceAmbiguousError,
    ToolkitReferenceUnresolvedError,
    assert_grantable_scope,
)
from jentic_one.control.services.access_requests.schemas.effects import (
    CredentialBindEffect,
    ScopeGrantEffect,
    SkippedEffect,
    ToolkitBindEffect,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.actors import actor_type_from_id
from jentic_one.shared.slug import slugify_identifier

logger = structlog.get_logger(__name__)

EffectResult = CredentialBindEffect | ToolkitBindEffect | ScopeGrantEffect | SkippedEffect


class EffectPhase(enum.Enum):
    """Which transaction phase applies an effect.

    ``CONTROL_SESSION`` effects are written in the caller's control-DB
    transaction and are therefore atomic with the decision. ``ADMIN`` effects
    write to the admin DB in their own independent transaction and are applied
    after the control commit (reconcilable on retry). ``FULFILMENT_ONLY`` items
    (``toolkit:create``, ``credential:provision``) are provisioning-plan
    placeholders: the applicator never mutates state for them — a human fulfils
    them out-of-band via the existing create endpoints — so approving one is a
    recorded no-op. ``UNSUPPORTED`` is a no-op (skipped) effect for an unknown
    ``(resource_type, action)`` pair.
    """

    CONTROL_SESSION = "control_session"
    ADMIN = "admin"
    FULFILMENT_ONLY = "fulfilment_only"
    UNSUPPORTED = "unsupported"


# Single source of truth for routing a (resource_type, action) pair to its phase.
# ``apply()`` and the service both consult this so the dispatch knowledge lives
# in one place.
_EFFECT_PHASES: dict[tuple[str, str], EffectPhase] = {
    ("credential", "bind"): EffectPhase.CONTROL_SESSION,
    ("toolkit", "bind"): EffectPhase.ADMIN,
    ("scope", "grant"): EffectPhase.ADMIN,
    ("toolkit", "create"): EffectPhase.FULFILMENT_ONLY,
    ("credential", "provision"): EffectPhase.FULFILMENT_ONLY,
}


def classify_effect(resource_type: str, action: str) -> EffectPhase:
    """Return the phase in which the effect for ``(resource_type, action)`` is applied."""
    return _EFFECT_PHASES.get((resource_type, action), EffectPhase.UNSUPPORTED)


def is_admin_effect(item: AccessRequestItem) -> bool:
    """True when the item's effect is applied in a separate admin-DB transaction."""
    return classify_effect(item.resource_type, item.action) is EffectPhase.ADMIN


def admin_effect_keys() -> tuple[tuple[str, str], ...]:
    """Return all ``(resource_type, action)`` pairs applied as admin-DB effects."""
    return tuple(key for key, phase in _EFFECT_PHASES.items() if phase is EffectPhase.ADMIN)


class EffectApplicator:
    """Applies authorization effects for approved access-request items."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def apply(
        self,
        item: AccessRequestItem,
        *,
        identity: Identity,
        control_session: Any,
    ) -> EffectResult:
        """Dispatch on (resource_type, action) to create the appropriate artifacts.

        The ``control_session`` is the caller's existing control-DB session so
        that credential-bind effects — and the toolkit-reference resolution —
        participate in the same transaction/snapshot as the decision. Admin-DB
        effects (toolkit bind, scope grant) write through their own transactions
        but still resolve/authorize their target on ``control_session``.

        ``identity`` is the deciding operator; its owner scope confines
        ``toolkit:bind`` to toolkits the decider can actually see.
        """
        decided_by = identity.sub
        phase = classify_effect(item.resource_type, item.action)

        if phase is EffectPhase.CONTROL_SESSION:
            return await self._apply_credential_bind(
                item, decided_by=decided_by, session=control_session
            )
        if phase is EffectPhase.ADMIN:
            key = (item.resource_type, item.action)
            if key == ("toolkit", "bind"):
                return await self._apply_toolkit_bind(
                    item, identity=identity, control_session=control_session
                )
            return await self._apply_scope_grant(item, decided_by=decided_by)

        if phase is EffectPhase.FULFILMENT_ONLY:
            # A provisioning-plan placeholder (toolkit:create / credential:provision).
            # The applicator never mutates state for these — a human fulfils them
            # via the existing create endpoints and writes the resulting ids onto
            # the downstream bind items (amend). Record an explicit, non-null
            # skipped effect so an approved intent is an audited no-op rather than
            # a silent one.
            return SkippedEffect(
                reason=(
                    f"fulfilment-only intent {item.resource_type}:{item.action} "
                    "is provisioned out-of-band; no effect applied"
                ),
            )

        logger.warning(
            "unsupported_effect_combination",
            resource_type=item.resource_type,
            action=item.action,
            item_id=item.id,
        )
        return SkippedEffect(
            reason=f"unsupported resource_type={item.resource_type} action={item.action}",
        )

    async def validate(
        self,
        item: AccessRequestItem,
        *,
        identity: Identity,
        control_session: Any,
    ) -> None:
        """Validate an approved item's effect can be applied — without writing.

        Run for every approved item *before* the first effect is applied so that
        a resolution/visibility/scope failure aborts the whole decision before
        any admin-DB write commits. This is the guard against cross-DB partial
        commits: admin-DB effects (toolkit bind, scope grant) commit in their own
        transactions and cannot be rolled back by the control-DB transaction, so
        the only safe place to fail is up front.
        """
        key = (item.resource_type, item.action)
        if key == ("credential", "bind"):
            # credential:bind writes only to the shared control-session and so
            # rolls back cleanly with the decision. We still pre-validate that the
            # named credential exists and is visible to the decider, so a bad
            # ``resource_id`` fails here as a 422 rather than slipping through to
            # _apply_credential_bind's bare ValueError / a downstream FK fault (a
            # 500). See issue #649.
            await self._validate_credential_bind_target(
                item, identity=identity, session=control_session
            )
        elif key == ("toolkit", "bind"):
            # Rules can't be enforced on an agent↔toolkit binding (no credential
            # key); fail up front rather than dropping them on apply. See
            # _apply_toolkit_bind for the full rationale.
            if item.rules:
                raise RulesNotSupportedForBindError(item.resource_type, item.action)
            await self._resolve_toolkit_bind_target(
                item, identity=identity, session=control_session
            )
        elif key == ("scope", "grant"):
            # Mirror _apply_scope_grant's guard so a bad scope-grant item fails
            # here (422) rather than mid-apply with a bare ValueError (500).
            assert_grantable_scope(item.resource_id)
        elif (
            classify_effect(item.resource_type, item.action) is EffectPhase.FULFILMENT_ONLY
            and item.rules
        ):
            # Fulfilment-only intents (toolkit:create, credential:provision) are
            # inert placeholders — the applicator never mutates state for them.
            # They still cannot carry enforceable rules (there is no binding key
            # to attach them to), so reject rules up front, consistent with the
            # file/amend-time guard. Everything else validates cleanly.
            raise RulesNotSupportedForBindError(item.resource_type, item.action)

    async def _validate_credential_bind_target(
        self, item: AccessRequestItem, *, identity: Identity, session: Any
    ) -> None:
        """Pre-validate a credential:bind item without writing.

        Confirms the item carries the required ids and that ``resource_id``
        resolves to a credential visible to the decider, raising the appropriate
        422 domain error otherwise. The visibility filters mirror
        :func:`build_access_filters` for ``Credential`` so this read sees exactly
        what the apply step's write would (same control session). See issue #649.
        """
        # ``to_id`` is the toolkit (bind target); ``resource_id`` is the
        # credential. Attribute a missing id to the right side so the 422 names
        # the actual problem instead of always blaming the credential.
        if not item.to_id:
            raise RequiredFieldMissingError(
                "to_id", context="credential:bind requires a target toolkit"
            )
        if not item.resource_id:
            raise RequiredFieldMissingError(
                "resource_id", context="credential:bind requires a credential"
            )
        filters = build_access_filters(identity, Credential)
        credential = await CredentialRepository.get_by_id(
            session, item.resource_id, filters=filters
        )
        if credential is None:
            raise CredentialNotFoundForBindError(item.resource_id)

    async def _apply_credential_bind(
        self, item: AccessRequestItem, *, decided_by: str, session: Any
    ) -> CredentialBindEffect:
        """Bind a credential to a toolkit and set permission rules."""
        if not item.to_id:
            raise ValueError(f"credential-bind effect requires to_id, item={item.id}")
        if not item.resource_id:
            raise ValueError(f"credential-bind effect requires resource_id, item={item.id}")

        binding_id, already_bound = await self._bind_credential_with_race_guard(
            item, decided_by=decided_by, session=session
        )

        rules_applied = 0
        if item.rules:
            await ToolkitPermissionRepository.replace_user_rules(
                session,
                item.to_id,
                item.resource_id,
                item.rules,
                created_by=decided_by,
            )
            rules_applied = len(item.rules)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.GRANT,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=binding_id,
            actor_type=actor_type_from_id(decided_by),
            actor_id=decided_by,
            origin=None,
        )

        return CredentialBindEffect(
            binding_id=binding_id,
            rules_applied=rules_applied,
            already_bound=already_bound,
        )

    async def _bind_credential_with_race_guard(
        self, item: AccessRequestItem, *, decided_by: str, session: Any
    ) -> tuple[str, bool]:
        """Bind the credential, translating a lost TOCTOU race into a 422.

        Pre-validation (`_validate_credential_bind_target`) already confirmed the
        credential was visible, but either FK target could be deleted between that
        read and this write (a same-/cross-transaction race). The binding has two
        foreign keys (toolkit and credential); the repo detects the lost race,
        attributes it to whichever target vanished, and raises a neutral
        ``BindTargetMissingError`` (keeping ``sqlalchemy.exc`` out of this service
        layer). We map that to the matching 422 domain error rather than letting a
        bare FK fault surface as a 500. See issue #649.
        """
        assert item.to_id is not None  # guarded by _apply_credential_bind above.
        assert item.resource_id is not None
        try:
            return await EffectsRepository.bind_credential_to_toolkit(
                session,
                toolkit_id=item.to_id,
                credential_id=item.resource_id,
                created_by=decided_by,
            )
        except BindTargetMissingError as exc:
            if exc.target == "toolkit":
                raise ToolkitNotVisibleError(exc.target_id) from exc
            raise CredentialNotFoundForBindError(exc.target_id) from exc

    async def _apply_toolkit_bind(
        self, item: AccessRequestItem, *, identity: Identity, control_session: Any
    ) -> ToolkitBindEffect:
        """Bind an agent to a toolkit.

        The toolkit is named either directly (``resource_id``/``to_id`` carrying a
        ``tk_…`` id) or by reference (``resource_reference`` carrying
        ``{vendor, name[, version]}``). Agents file by reference because they
        discover APIs by their vendor/name via ``search`` and cannot see toolkit
        ids; the reference is resolved here, at decide time, where the approver
        has the privilege the agent lacks.

        Resolution and the explicit-id visibility check run on the shared
        ``control_session`` (the decision's transaction/snapshot) and are scoped
        to the deciding operator's owner visibility, so an approver can only bind
        an agent to a toolkit they themselves can see. This closes the
        cross-owner escalation a *public* ``vendor/name`` reference would
        otherwise allow.
        """
        # Defense-in-depth: broker rules are keyed per (toolkit_id, credential_id)
        # (see broker/repos/rule_evaluator.py); an agent↔toolkit binding has no
        # credential key, so rules here can't be enforced. The service rejects them
        # at file/amend time, but a legacy/pre-existing stored item must fail loudly
        # rather than silently approve into an unrestricted binding.
        if item.rules:
            raise RulesNotSupportedForBindError(item.resource_type, item.action)
        decided_by = identity.sub
        toolkit_id = await self._resolve_toolkit_bind_target(
            item, identity=identity, session=control_session
        )

        async with self._ctx.admin_db.transaction() as session:
            binding_id, already_bound = await EffectsRepository.bind_agent_to_toolkit(
                session,
                agent_id=item.actor_id,
                toolkit_id=toolkit_id,
                created_by=decided_by,
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.GRANT,
            target_type=AuditTargetType.TOOLKIT,
            target_id=toolkit_id,
            actor_type=actor_type_from_id(decided_by),
            actor_id=decided_by,
            origin=None,
        )

        return ToolkitBindEffect(binding_id=binding_id, already_bound=already_bound)

    async def _resolve_toolkit_bind_target(
        self, item: AccessRequestItem, *, identity: Identity, session: Any
    ) -> str:
        """Resolve (and authorize) the toolkit id a ``toolkit:bind`` item targets.

        Shared by ``validate()`` (which discards the id, using this only as the
        side-effect-free visibility/resolution guard) and ``_apply_toolkit_bind``
        (which binds to the returned id) so the two stay in lock-step. Scoped to
        the deciding operator's owner visibility: an approver can only bind an
        agent to a toolkit they themselves can see. Raises ``ToolkitNotVisibleError``
        for an explicit id the decider can't see, and the resolution errors for a
        ``resource_reference`` that resolves to zero or many visible toolkits.
        """
        owner_ids = toolkit_owner_scope(identity)
        explicit_id = item.resource_id or item.to_id
        if explicit_id:
            visible = await EffectsRepository.toolkit_visible_to_owners(
                session, toolkit_id=explicit_id, owner_ids=owner_ids
            )
            if not visible:
                raise ToolkitNotVisibleError(explicit_id)
            return explicit_id
        return await self._resolve_toolkit_reference(item, session=session, owner_ids=owner_ids)

    async def _resolve_toolkit_reference(
        self, item: AccessRequestItem, *, session: Any, owner_ids: list[str] | None
    ) -> str:
        """Resolve a ``toolkit:bind`` ``resource_reference`` to a single toolkit id.

        Resolves on the decision's ``session`` (shared snapshot) and within the
        decider's ``owner_ids`` scope. Raises ``ValueError`` when neither a direct
        id nor a usable reference is present, ``ToolkitReferenceUnresolvedError``
        when no *visible* toolkit serves the API, and
        ``ToolkitReferenceAmbiguousError`` when several do.
        """
        reference = item.resource_reference or {}
        vendor = reference.get("vendor")
        if not vendor:
            raise ValueError(
                "toolkit-bind effect requires resource_id, to_id, or a "
                f"resource_reference with a vendor, item={item.id}"
            )

        # Normalize vendor/name to the registry's slug form (dots -> dashes) so
        # the reference matches the credential's stored, normalized api_vendor.
        # Agents file references from discovered vendor/name that may be raw
        # domains (e.g. httpbin.org); credentials store the slug (httpbin-org),
        # so an un-normalized join would find no toolkit and deny a satisfiable
        # bind. See issue #656 (same mismatch the credential store fixes).
        raw_name = reference.get("name")
        candidates = await EffectsRepository.resolve_toolkits_for_api(
            session,
            vendor=slugify_identifier(str(vendor)),
            name=slugify_identifier(str(raw_name)) if raw_name else raw_name,
            version=reference.get("version"),
            owner_ids=owner_ids,
        )

        if not candidates:
            raise ToolkitReferenceUnresolvedError(reference)
        if len(candidates) > 1:
            raise ToolkitReferenceAmbiguousError(reference, candidates)
        return candidates[0]

    async def _apply_scope_grant(
        self, item: AccessRequestItem, *, decided_by: str
    ) -> ScopeGrantEffect:
        """Grant a scope to the actor.

        Only scopes in the self-service allow-list (``GRANTABLE_SCOPES``) may be
        granted this way; privileged scopes such as ``org:admin`` are rejected to
        prevent a confused-deputy escalation by an owner with ``agents:write``.
        """
        if not item.resource_id:
            raise ValueError(f"scope-grant effect requires resource_id, item={item.id}")
        scope = item.resource_id
        assert_grantable_scope(scope)
        async with self._ctx.admin_db.transaction() as session:
            created = await EffectsRepository.grant_scope_to_actor(
                session,
                actor_id=item.actor_id,
                actor_type=actor_type_from_id(item.actor_id),
                scope=scope,
                granted_by=decided_by,
                created_by=decided_by,
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.GRANT,
            target_type=AuditTargetType.AGENT,
            target_id=item.actor_id,
            actor_type=actor_type_from_id(decided_by),
            actor_id=decided_by,
            origin=None,
        )

        return ScopeGrantEffect(scope=scope, already_granted=not created)
