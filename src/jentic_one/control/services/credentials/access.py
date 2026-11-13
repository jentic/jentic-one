"""Credential binding and shared permission rule-set access policy."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, NamedTuple

import structlog

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.permission_rule_sets import (
    PermissionRuleSet,
    PermissionRuleSetRule,
)
from jentic_one.control.repos import (
    AgentPermissionRuleRepository,
    CredentialRepository,
    OAuthTokenRepository,
    PermissionRuleSetRepository,
)
from jentic_one.control.repos.prerequisite_repo import (
    AgentCredentialBindingRow,
    AgentVisibility,
    CredentialBoundAgentRow,
    PrerequisiteRepository,
)
from jentic_one.control.scoping.filters import (
    bound_agent_visibility,
    build_access_filters,
    own_agent_visibility,
)
from jentic_one.control.services.credentials.errors import (
    AgentBindingNotFoundError,
    CredentialNotFoundError,
    RuleSetAccessDeniedError,
    RuleSetAttachDeniedError,
    RuleSetInUseError,
    RuleSetNameConflictError,
    RuleSetNotFoundError,
)
from jentic_one.control.services.credentials.schemas.permission_test import PermissionTestResult
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import ORG_ADMIN
from jentic_one.shared.events import emit_event_best_effort, summary_label
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import decode_cursor, encode_cursor
from jentic_one.shared.permissions.evaluation import (
    PermissionRule,
    first_matching_rule,
    normalize_methods,
)
from jentic_one.shared.permissions.matching import compile_matcher
from jentic_one.shared.url_path import has_ambiguous_traversal, normalize_path

if TYPE_CHECKING:
    from jentic_one.shared.context import Context


class BindingAccess(NamedTuple):
    """What a direct ``(agent, credential)`` binding grants (``get_binding_access``)."""

    rules: list[AgentPermissionRule]
    rule_set: PermissionRuleSet | None
    rule_set_rules: list[PermissionRuleSetRule]
    granted_scopes: list[str] | None
    suspended: bool


logger = structlog.get_logger()


class _CredentialAccessMixin:
    """Binding visibility, per-binding permission rules, and shared rule-set operations."""

    if TYPE_CHECKING:
        _ctx: Context

        async def _bound_credential_ids(self, identity: Identity) -> list[str]: ...
        async def _owned_agent_ids(self, identity: Identity) -> list[str]: ...

    async def list_agents(
        self,
        credential_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        identity: Identity,
    ) -> tuple[list[CredentialBoundAgentRow], bool, str | None]:
        """List agents directly bound to a credential. Returns (data, has_more, next_cursor).

        The reverse lookup for the credential-detail "Agents" view (theme 5
        phase 1). Visibility is the same gate as ``get``: the caller must be
        able to see the credential itself before enumerating who is bound to
        it (hard problem 7/9 owner-gating; existence never leaks past the
        filters).

        ``org:admin`` and the credential's creator see every bound agent. Any
        other caller who can see the credential (a bound agent, a delegated
        agent, a shared-read grant) sees only the agents it can see itself:
        see :meth:`_bound_agent_visibility`.
        """
        access_filters = build_access_filters(
            identity,
            Credential,
            bound_credential_ids=await self._bound_credential_ids(identity),
            owned_agent_ids=await self._owned_agent_ids(identity),
            include_shared=True,
        )
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None:
                raise CredentialNotFoundError(credential_id)

        decoded_cursor = decode_cursor(cursor, id_kind="str") if cursor is not None else None

        async with self._ctx.admin_db.session() as session:
            rows = await PrerequisiteRepository.list_agents_for_credential(
                session,
                credential_id=credential_id,
                cursor=decoded_cursor,
                limit=limit + 1,
                visible_to=self._bound_agent_visibility(credential, identity),
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.bound_at, last.binding_id)

        return rows, has_more, next_cursor

    @staticmethod
    def _bound_agent_visibility(
        credential: Credential, identity: Identity
    ) -> AgentVisibility | None:
        """Bound agents the caller may enumerate; ``None`` means all of them."""
        return bound_agent_visibility(credential.created_by, identity)

    # --- Per-binding permission rules (theme 5 phase 1) ---

    @staticmethod
    def _may_write_binding_rules(credential: Credential, agent_id: str, identity: Identity) -> bool:
        """Owner-or-admin write gate for a binding's permission rules.

        The rules bound what an agent may do with the credential, so changing
        them is the credential owner's call: ``org:admin`` or the identity
        that created the credential. Read visibility is deliberately not
        enough — neither being the bound agent itself, nor an owner-delegation
        read scope, nor an extension's shared-read grant widens this gate.

        The bound agent never edits its own binding's rules, even when it is
        the credential's ``created_by`` (an agent-initiated connect records
        the agent as creator): the rules exist to constrain that agent, and
        the human-approved set is what it runs under.
        """
        if ORG_ADMIN in identity.permissions:
            return True
        return (
            credential.created_by is not None
            and credential.created_by == identity.sub
            and agent_id != identity.sub
        )

    async def _require_visible_binding(
        self,
        credential_id: str,
        agent_id: str,
        *,
        identity: Identity,
        for_write: bool = False,
    ) -> AgentCredentialBindingRow:
        """Gate the per-binding rules endpoints on both axes (hard problems 7/9).

        The caller must see the credential (same filters as ``get`` — a miss
        is a uniform 404 that never confirms existence), and the direct
        ``(agent, credential)`` binding must exist (admin-DB row; the rules
        themselves live control-side, so this is the cross-DB seam). Returns
        the binding row so callers can see its attached ``rule_set_id``.

        The bound agent must also be one the caller may see
        (:meth:`_bound_agent_visibility`, the same rule as ``list_agents``):
        a binding of an agent outside that set answers the same 404 as a
        binding that does not exist.

        ``for_write`` additionally requires :meth:`_may_write_binding_rules`;
        a caller who can see the credential but not write its rules gets the
        same 404 as one who cannot see it at all. Writes are therefore
        owner-or-admin only, and both of those callers see every bound agent.
        """
        access_filters = build_access_filters(
            identity,
            Credential,
            bound_credential_ids=await self._bound_credential_ids(identity),
            owned_agent_ids=await self._owned_agent_ids(identity),
            include_shared=True,
        )
        async with self._ctx.control_db.session() as session:
            credential = await CredentialRepository.get_by_id(
                session, credential_id, filters=access_filters
            )
            if credential is None or (
                for_write and not self._may_write_binding_rules(credential, agent_id, identity)
            ):
                raise CredentialNotFoundError(credential_id)
        async with self._ctx.admin_db.session() as session:
            binding = await PrerequisiteRepository.get_agent_credential_binding(
                session,
                agent_id=agent_id,
                credential_id=credential_id,
                visible_to=self._bound_agent_visibility(credential, identity),
            )
        if binding is None:
            raise AgentBindingNotFoundError(credential_id, agent_id)
        return binding._replace(credential_name=credential.name)

    async def _record_rules_change(
        self,
        credential_id: str,
        agent_id: str,
        binding: AgentCredentialBindingRow,
        *,
        identity: Identity,
        reason: str,
    ) -> None:
        """Audit + telemetry for a rules mutation (mirrors the toolkit path).

        ``binding`` is the row :meth:`_require_visible_binding` returned; its
        agent and credential names label the event summary.
        """
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=agent_id,
            reason=reason,
            origin=identity.origin.value,
        )
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.CREDENTIAL_PERMISSION_RULE_SET,
                    severity=EventSeverity.INFO,
                    summary=(
                        "Permission rules set on agent "
                        f"{summary_label(binding.agent_name, agent_id)} for credential "
                        f"{summary_label(binding.credential_name, credential_id)}"
                    ),
                    data={"agent_id": agent_id, "credential_id": credential_id},
                    # Subject is the agent, so its owner sees the rules change.
                    created_by=agent_id,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                )
        except Exception:
            logger.warning(
                "telemetry_emit_failed",
                event_type=EventType.CREDENTIAL_PERMISSION_RULE_SET,
                exc_info=True,
            )

    async def list_agent_permissions(
        self, credential_id: str, agent_id: str, *, identity: Identity
    ) -> list[AgentPermissionRule]:
        """List the ordered PBAC rules for a direct ``(agent, credential)`` binding."""
        await self._require_visible_binding(credential_id, agent_id, identity=identity)
        async with self._ctx.control_db.session() as session:
            return await AgentPermissionRuleRepository.list_rules(session, agent_id, credential_id)

    async def get_binding_access(
        self, credential_id: str, agent_id: str, *, identity: Identity
    ) -> BindingAccess:
        """Everything a direct binding grants: its rules, rule set, scopes and suspension.

        Same visibility as :meth:`list_agent_permissions`, so the bound agent
        reads its own binding. ``rules`` are the inline rules, dormant while a
        shared ``rule_set`` is attached. ``granted_scopes`` is the credential's
        OAuth grant; ``None`` when it has no platform-visible scopes.
        """
        binding = await self._require_visible_binding(credential_id, agent_id, identity=identity)
        async with self._ctx.control_db.session() as session:
            rules = await AgentPermissionRuleRepository.list_rules(session, agent_id, credential_id)
            rule_set = None
            rule_set_rules: list[PermissionRuleSetRule] = []
            if binding.rule_set_id is not None:
                rule_set = await PermissionRuleSetRepository.get_by_id(session, binding.rule_set_id)
                rule_set_rules = await PermissionRuleSetRepository.list_rules(
                    session, binding.rule_set_id
                )
            token = await OAuthTokenRepository.get_by_credential(session, credential_id)
        return BindingAccess(
            rules=rules,
            rule_set=rule_set,
            rule_set_rules=rule_set_rules,
            granted_scopes=token.scope.split() if token is not None and token.scope else None,
            suspended=binding.suspended,
        )

    async def replace_agent_permissions(
        self,
        credential_id: str,
        agent_id: str,
        rules: list[dict[str, object]],
        *,
        identity: Identity,
    ) -> list[AgentPermissionRule]:
        """Replace the full user-rule list for a binding (idempotent PUT)."""
        binding = await self._require_visible_binding(
            credential_id, agent_id, identity=identity, for_write=True
        )
        async with self._ctx.control_db.transaction() as session:
            # The same lock a connect session's confirm takes: this write is a
            # delete and re-insert too, so without it a confirm's locked read
            # of these rows is overwritten after it passed.
            await AgentPermissionRuleRepository.acquire_binding_lock(
                session, agent_id, credential_id
            )
            result = await AgentPermissionRuleRepository.replace_user_rules(
                session, agent_id, credential_id, rules, created_by=identity.sub
            )
        await self._record_rules_change(
            credential_id, agent_id, binding, identity=identity, reason="replace permission rules"
        )
        self._ctx.invalidate_broker_caches(agent_id=agent_id, credential_id=credential_id)
        return result

    async def patch_agent_permissions(
        self,
        credential_id: str,
        agent_id: str,
        *,
        identity: Identity,
        add: list[dict[str, object]] | None = None,
        remove: list[int] | None = None,
    ) -> list[AgentPermissionRule]:
        """Additively add and/or remove user rules on a binding."""
        binding = await self._require_visible_binding(
            credential_id, agent_id, identity=identity, for_write=True
        )
        async with self._ctx.control_db.transaction() as session:
            await AgentPermissionRuleRepository.acquire_binding_lock(
                session, agent_id, credential_id
            )
            result = await AgentPermissionRuleRepository.patch_rules(
                session, agent_id, credential_id, add=add, remove=remove, created_by=identity.sub
            )
        await self._record_rules_change(
            credential_id, agent_id, binding, identity=identity, reason="patch permission rules"
        )
        self._ctx.invalidate_broker_caches(agent_id=agent_id, credential_id=credential_id)
        return result

    async def test_agent_permissions(
        self,
        credential_id: str,
        agent_id: str,
        *,
        method: str,
        path: str,
        operation_id: str | None,
        identity: Identity,
    ) -> PermissionTestResult:
        """Dry-run permission evaluation for a ``(method, path, operation_id)`` triple.

        Unlike the toolkit ``:test`` there is **no vendor pooling**: the direct
        binding's rules are a single ordered first-match-wins list, which is
        the point of the per-binding model. When the binding points at a
        shared ``permission_rule_set`` the set's list is what gets evaluated —
        inline rules are dormant while a set is attached, and the dry-run must
        not lie about that. Default-deny when nothing matches. The broker's
        condition-less skip is honoured so a bare ``allow`` or
        ``require-approval`` with no constraints matches a dry-run no more than
        it matches a real request.
        """
        binding = await self._require_visible_binding(credential_id, agent_id, identity=identity)
        async with self._ctx.control_db.session() as session:
            if binding.rule_set_id is not None:
                rules: Sequence[
                    AgentPermissionRule | PermissionRuleSetRule
                ] = await PermissionRuleSetRepository.list_rules(session, binding.rule_set_id)
            else:
                rules = await AgentPermissionRuleRepository.list_rules(
                    session, agent_id, credential_id
                )

        # The broker refuses a path whose dot segments are spelled ambiguously
        # (``ambiguous_path``) before any rule runs; the dry-run must not
        # report such a path as allowed.
        if has_ambiguous_traversal(path):
            return PermissionTestResult(
                allowed=False,
                matched=False,
                effect=None,
                rule_index=None,
                credential_id=None,
                is_system=None,
            )
        compiled = [
            PermissionRule(
                effect=rule.effect,
                methods=normalize_methods(rule.methods),
                path=compile_matcher(rule.path, str(rule.match_mode or "regex")),
                operations=tuple(rule.operations) if rule.operations is not None else None,
            )
            for rule in rules
        ]
        # The broker enforces on the normalized server-relative path; normalize
        # the dry-run input the same way so ``/%61dmin`` previews as ``/admin``.
        idx = first_matching_rule(
            compiled,
            method=method,
            path=normalize_path(path),
            operation_id=operation_id,
            binding=f"{agent_id}:{credential_id}",
        )
        if idx is not None:
            rule = rules[idx]
            return PermissionTestResult(
                allowed=rule.effect.lower() == "allow",
                matched=True,
                effect=rule.effect,
                rule_index=idx,
                credential_id=credential_id,
                is_system=rule.is_system,
            )
        return PermissionTestResult(
            allowed=False,
            matched=False,
            effect=None,
            rule_index=None,
            credential_id=None,
            is_system=None,
        )

    # --- Shared permission rule sets (theme 5 phase 1, Q-04) ---

    async def attach_binding_rule_set(
        self,
        credential_id: str,
        agent_id: str,
        rule_set_id: str,
        *,
        identity: Identity,
    ) -> None:
        """Point a direct binding at a shared rule set.

        While attached, the set's ordered list is the binding's effective
        policy and its inline rules are dormant (they survive untouched for
        when the set is detached). The set must exist — the pointer is FK-less
        across the DB seam, so this check plus the delete-time
        ``rule_set_in_use`` refusal are the integrity guard.

        The caller must also be allowed to attach the set
        (:meth:`_may_attach_rule_set`), because whoever may edit the set then
        decides what the agent may do. Re-attaching the set a binding already
        points at is an idempotent no-op that skips that check, so an existing
        attachment keeps working for the binding's owner.
        """
        binding = await self._require_visible_binding(
            credential_id, agent_id, identity=identity, for_write=True
        )
        async with self._ctx.control_db.session() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
        if rule_set is None:
            raise RuleSetNotFoundError(rule_set_id)
        if binding.rule_set_id == rule_set_id:
            return
        if not self._may_attach_rule_set(rule_set, identity):
            raise RuleSetAttachDeniedError(rule_set_id)
        async with self._ctx.admin_db.transaction() as session:
            await PrerequisiteRepository.set_binding_rule_set(
                session, agent_id=agent_id, credential_id=credential_id, rule_set_id=rule_set_id
            )
        await self._record_rules_change(
            credential_id,
            agent_id,
            binding,
            identity=identity,
            reason=f"attach rule set {rule_set_id}",
        )
        self._ctx.invalidate_broker_caches(agent_id=agent_id, credential_id=credential_id)

    async def detach_binding_rule_set(
        self, credential_id: str, agent_id: str, *, identity: Identity
    ) -> None:
        """Detach the binding's shared rule set — inline rules apply again.

        Idempotent: detaching a binding that already runs on inline rules is
        a no-op, not an error.
        """
        binding = await self._require_visible_binding(
            credential_id, agent_id, identity=identity, for_write=True
        )
        if binding.rule_set_id is None:
            return
        async with self._ctx.admin_db.transaction() as session:
            await PrerequisiteRepository.set_binding_rule_set(
                session, agent_id=agent_id, credential_id=credential_id, rule_set_id=None
            )
        await self._record_rules_change(
            credential_id,
            agent_id,
            binding,
            identity=identity,
            reason=f"detach rule set {binding.rule_set_id}",
        )
        self._ctx.invalidate_broker_caches(agent_id=agent_id, credential_id=credential_id)

    @staticmethod
    def _may_mutate_rule_set(rule_set: PermissionRuleSet, identity: Identity) -> bool:
        """Write gate for a shared rule set: ``org:admin``, or the creator of a non-curated set.

        Everyone passing the route's read permission may *see* a shared set —
        it carries policy, not secrets, and a binding pointing at it makes
        its contents the binding owner's business. Editing a set changes the
        policy of every binding attached to it, so a curated set (which any
        ``credentials:write`` holder may attach) is editable only by
        ``org:admin``, including after its creator loses that permission.
        """
        if ORG_ADMIN in identity.permissions:
            return True
        return not rule_set.curated and rule_set.created_by == identity.sub

    @staticmethod
    def _may_attach_rule_set(rule_set: PermissionRuleSet, identity: Identity) -> bool:
        """Attach gate for a shared rule set: ``org:admin``, the creator, or any caller if curated.

        The caller already holds ``credentials:write`` (the route requires it)
        and may write the binding's rules. Attaching another user's
        non-curated set is refused: its creator could later rewrite the
        attached agent's policy.
        """
        if ORG_ADMIN in identity.permissions or rule_set.curated:
            return True
        return rule_set.created_by is not None and rule_set.created_by == identity.sub

    async def create_rule_set(
        self,
        *,
        name: str,
        description: str | None,
        rules: list[dict[str, object]],
        identity: Identity,
    ) -> tuple[PermissionRuleSet, list[PermissionRuleSetRule]]:
        """Create a named shared rule set, optionally with its initial ordered rules.

        A set an ``org:admin`` creates is recorded as curated (see
        :meth:`_may_attach_rule_set`).
        """
        async with self._ctx.control_db.transaction() as session:
            if await PermissionRuleSetRepository.get_by_name(session, name) is not None:
                raise RuleSetNameConflictError(name)
            rule_set = await PermissionRuleSetRepository.create(
                session,
                name=name,
                description=description,
                created_by=identity.sub,
                curated=ORG_ADMIN in identity.permissions,
            )
            set_rules = await PermissionRuleSetRepository.replace_user_rules(
                session, rule_set.id, rules, created_by=identity.sub
            )
        await self._record_rule_set_change(rule_set.id, identity=identity, reason="create rule set")
        return rule_set, set_rules

    async def _visible_binding_count_for_rule_set(
        self, rule_set_id: str, *, identity: Identity
    ) -> int:
        """Count bindings referencing ``rule_set_id`` that ``identity`` may read (#1858).

        Applies the same credential and agent visibility gates as
        :meth:`list_agents` and :meth:`_require_visible_binding`: ``org:admin``
        sees every referencing binding, a credential's creator sees every
        binding on that credential, and any other reader of a credential sees
        only bindings on agents matching :func:`own_agent_visibility`.
        """
        if ORG_ADMIN in identity.permissions:
            async with self._ctx.admin_db.session() as session:
                return await PrerequisiteRepository.count_bindings_for_rule_set(
                    session, rule_set_id
                )

        async with self._ctx.admin_db.session() as session:
            counts_by_cred = await PrerequisiteRepository.count_bindings_for_rule_set_by_credential(
                session,
                rule_set_id,
                visible_to=own_agent_visibility(identity),
            )
            if not counts_by_cred:
                return 0
            bound_credential_ids = (
                await PrerequisiteRepository.list_credential_ids_for_agent(
                    session, agent_id=identity.sub
                )
                if identity.actor_type == ActorType.AGENT and identity.sub
                else []
            )
            owned_agent_ids = (
                await PrerequisiteRepository.list_agent_ids_owned_by(session, owner_id=identity.sub)
                if identity.actor_type == ActorType.USER and identity.sub
                else []
            )

        access_filters = build_access_filters(
            identity,
            Credential,
            bound_credential_ids=bound_credential_ids,
            owned_agent_ids=owned_agent_ids,
            include_shared=True,
        )
        async with self._ctx.control_db.session() as session:
            creators = await CredentialRepository.get_creators_by_ids(
                session, list(counts_by_cred), filters=access_filters
            )

        total = 0
        for cred_id, created_by in creators.items():
            total_count, agent_visible_count = counts_by_cred[cred_id]
            if created_by is not None and created_by == identity.sub:
                total += total_count
            else:
                total += agent_visible_count
        return total

    async def get_rule_set(
        self, rule_set_id: str, *, identity: Identity
    ) -> tuple[PermissionRuleSet, list[PermissionRuleSetRule], int]:
        """Return a rule set, its ordered rules, and its caller-visible binding count."""
        async with self._ctx.control_db.session() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
            if rule_set is None:
                raise RuleSetNotFoundError(rule_set_id)
            rules = await PermissionRuleSetRepository.list_rules(session, rule_set_id)
        binding_count = await self._visible_binding_count_for_rule_set(
            rule_set_id, identity=identity
        )
        return rule_set, rules, binding_count

    async def update_rule_set(
        self,
        rule_set_id: str,
        *,
        identity: Identity,
        name: str | None = None,
        description: str | None = None,
    ) -> PermissionRuleSet:
        """Rename or re-describe a rule set (see :meth:`_may_mutate_rule_set`)."""
        async with self._ctx.control_db.transaction() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
            if rule_set is None:
                raise RuleSetNotFoundError(rule_set_id)
            if not self._may_mutate_rule_set(rule_set, identity):
                raise RuleSetAccessDeniedError(rule_set_id, curated=rule_set.curated)
            if name is not None and name != rule_set.name:
                if await PermissionRuleSetRepository.get_by_name(session, name) is not None:
                    raise RuleSetNameConflictError(name)
                rule_set.name = name
            if description is not None:
                rule_set.description = description
            await session.flush()
        await self._record_rule_set_change(rule_set_id, identity=identity, reason="update rule set")
        return rule_set

    async def replace_rule_set_rules(
        self,
        rule_set_id: str,
        rules: list[dict[str, object]],
        *,
        identity: Identity,
    ) -> list[PermissionRuleSetRule]:
        """Replace a set's ordered user-rule list (idempotent PUT).

        This is the single-place edit rule grouping exists for: every binding
        pointing at the set picks the new list up at once.
        """
        async with self._ctx.control_db.transaction() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
            if rule_set is None:
                raise RuleSetNotFoundError(rule_set_id)
            if not self._may_mutate_rule_set(rule_set, identity):
                raise RuleSetAccessDeniedError(rule_set_id, curated=rule_set.curated)
            # The lock a confirm writing this set takes: one list, every
            # binding pointing at the set, replaced by a delete and re-insert.
            await PermissionRuleSetRepository.acquire_rules_lock(session, rule_set_id)
            result = await PermissionRuleSetRepository.replace_user_rules(
                session, rule_set_id, rules, created_by=identity.sub
            )
        await self._record_rule_set_change(
            rule_set_id, identity=identity, reason="replace rule set rules"
        )
        self._ctx.invalidate_broker_caches(rule_set_id=rule_set_id)
        return result

    async def delete_rule_set(self, rule_set_id: str, *, identity: Identity) -> None:
        """Delete a rule set nothing references (409 rule_set_in_use otherwise).

        The binding's ``rule_set_id`` pointer is FK-less across the DB seam,
        so this application-level check is what keeps a set from vanishing
        under bindings that still evaluate through it.
        """
        async with self._ctx.control_db.session() as session:
            rule_set = await PermissionRuleSetRepository.get_by_id(session, rule_set_id)
            if rule_set is None:
                raise RuleSetNotFoundError(rule_set_id)
            if not self._may_mutate_rule_set(rule_set, identity):
                raise RuleSetAccessDeniedError(rule_set_id, curated=rule_set.curated)
        async with self._ctx.admin_db.session() as session:
            binding_count = await PrerequisiteRepository.count_bindings_for_rule_set(
                session, rule_set_id
            )
        if binding_count:
            raise RuleSetInUseError(rule_set_id, binding_count)
        async with self._ctx.control_db.transaction() as session:
            deleted = await PermissionRuleSetRepository.delete_by_id(session, rule_set_id)
        if not deleted:
            raise RuleSetNotFoundError(rule_set_id)
        await self._record_rule_set_change(rule_set_id, identity=identity, reason="delete rule set")
        self._ctx.invalidate_broker_caches(rule_set_id=rule_set_id)

    async def _record_rule_set_change(
        self, rule_set_id: str, *, identity: Identity, reason: str
    ) -> None:
        """Audit + telemetry for a rule-set mutation."""
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.PERMISSION_RULE_SET,
            target_id=rule_set_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            reason=reason,
            origin=identity.origin.value,
        )
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=EventType.CREDENTIAL_PERMISSION_RULE_SET,
                    severity=EventSeverity.INFO,
                    summary=f"Permission rule set {rule_set_id}: {reason}",
                    created_by=identity.sub,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                )
        except Exception:
            logger.warning(
                "telemetry_emit_failed",
                event_type=EventType.CREDENTIAL_PERMISSION_RULE_SET,
                exc_info=True,
            )
