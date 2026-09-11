"""Theme-5 Phase 4 — the toolkit-key retirement job.

Converts every resolvable ``jntc_live_`` toolkit key into a **service
account** (never an agent — the plan's toolkit-keys decision: a key carries
exactly ``capabilities:execute``, and the default agent scope set would be a
1→13 escalation). The presented plaintext keeps working unchanged: the
service-account credential row carries the key's SHA-256 lookup digest, and
``ApiKeyResolver`` matches by digest regardless of prefix, so migration is
zero-touch for headless callers.

Access parity is preserved on **both** authorization paths:

- flag **off** (``direct_bindings_enabled=False``): an
  ``agent_toolkit_bindings`` row keeps the holder deriving through its
  toolkit exactly as the key did, until the Phase-6a flattening;
- flag **on**: ``agent_credential_bindings`` rows mirror the toolkit's
  credential bindings, each carrying a rule set copied per
  ``(toolkit, credential)`` pair from ``toolkit_permission_rules``
  (rule-less pairs bind with no set — default-deny on both paths).

Idempotency: each key's successor account has a deterministic name
(``toolkit-key:<key id>``); a re-run reuses it, and a stamped
``migrated_actor_id`` short-circuits the key entirely. Keys that cannot
authenticate today (revoked, inactive toolkit, no lookup hash) are **not**
migrated — retirement never widens access — and each is a report line.

The job logs one structured line per key (``toolkit_key_retirement``); the
``retire-toolkit-keys`` CLI additionally emits each outcome as JSONL on
stdout so operators can archive the run (the Phase-6a report embeds the same
identifiers). The combined/control server also runs the job once at startup
(best-effort, idempotent) so an upgrade migrates resolvable keys without an
operator step — the CLI remains the recovery path for keys needing
``--owner``.

Lives directly under ``control/services/`` since theme-5 Phase 5b deleted the
toolkits service package; the job itself runs until Phase 6b retires the
toolkit tables.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import structlog

from jentic_one.control.repos.key_retirement_repo import SYSTEM_ACTOR, KeyRetirementRepository
from jentic_one.control.repos.permission_rule_set_repo import PermissionRuleSetRepository
from jentic_one.control.repos.toolkit_binding_repo import ToolkitBindingRepository
from jentic_one.control.repos.toolkit_key_repo import ToolkitKeyRepository
from jentic_one.control.repos.toolkit_permission_repo import ToolkitPermissionRepository
from jentic_one.shared.context import Context

if TYPE_CHECKING:
    from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
    from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
    from jentic_one.control.core.schema.toolkits import Toolkit

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class KeyRetirementOutcome:
    """One report line: what happened to one toolkit key."""

    key_id: str
    toolkit_id: str
    toolkit_name: str
    action: str  # migrated | already_migrated | skipped
    reason: str | None = None  # revoked | toolkit_inactive | no_lookup_hash | owner_unresolved
    service_account_id: str | None = None
    bound_credential_ids: tuple[str, ...] = ()
    rule_less_credential_ids: tuple[str, ...] = ()


def _rule_set_name(toolkit_id: str, credential_id: str) -> str:
    """Deterministic per-pair name — the job's control-side idempotency key."""
    return f"theme5-key-retirement:{toolkit_id}:{credential_id}"


def _service_account_name(key_id: str) -> str:
    """Deterministic per-key name — the job's admin-side idempotency key."""
    return f"toolkit-key:{key_id}"


class KeyRetirementService:
    """Orchestrates the retirement run across the control and admin databases."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def run(self, *, fallback_owner_email: str | None = None) -> list[KeyRetirementOutcome]:
        """Retire every resolvable toolkit key; return one outcome per key."""
        fallback_owner_id: str | None = None
        if fallback_owner_email is not None:
            async with self._ctx.admin_db.session() as admin_session:
                fallback_owner_id = await KeyRetirementRepository.resolve_user_by_email(
                    admin_session, email=fallback_owner_email
                )
            if fallback_owner_id is None:
                msg = f"--owner {fallback_owner_email!r} does not match any user"
                raise ValueError(msg)

        outcomes: list[KeyRetirementOutcome] = []
        async with self._ctx.control_db.session() as control_session:
            keys = await ToolkitKeyRepository.list_all_with_toolkits(control_session)

        for key, toolkit in keys:
            outcome = await self._retire_key(key, toolkit, fallback_owner_id=fallback_owner_id)
            outcomes.append(outcome)
            logger.info("toolkit_key_retirement", **asdict(outcome))
        return outcomes

    async def _retire_key(
        self, key: ToolkitKey, toolkit: Toolkit, *, fallback_owner_id: str | None
    ) -> KeyRetirementOutcome:
        def skipped(reason: str) -> KeyRetirementOutcome:
            return KeyRetirementOutcome(
                key_id=key.id,
                toolkit_id=toolkit.id,
                toolkit_name=toolkit.name,
                action="skipped",
                reason=reason,
            )

        if key.migrated_actor_id is not None:
            return KeyRetirementOutcome(
                key_id=key.id,
                toolkit_id=toolkit.id,
                toolkit_name=toolkit.name,
                action="already_migrated",
                service_account_id=key.migrated_actor_id,
            )
        # A key that cannot authenticate today must not gain access by being
        # migrated — retirement is access-preserving, never access-widening.
        if key.revoked:
            return skipped("revoked")
        if not toolkit.active:
            return skipped("toolkit_inactive")
        if key.lookup_hash is None:
            return skipped("no_lookup_hash")

        # Owner: the SA's owner_id is a NOT NULL FK to users. Prefer the
        # key's creator, then the toolkit's, then the operator-supplied
        # fallback; a key with no resolvable owner is reported, not guessed.
        async with self._ctx.admin_db.session() as admin_session:
            owner_id = await KeyRetirementRepository.resolve_user(
                admin_session,
                candidate_ids=[key.created_by or "", toolkit.created_by or ""],
            )
        if owner_id is None:
            owner_id = fallback_owner_id
        if owner_id is None:
            return skipped("owner_unresolved")

        # Control side first (rule sets), admin side second (actor + bindings),
        # stamp last — mirroring the Phase-6a "rules control-first, binding
        # admin-last" ordering so a crash between transactions leaves
        # re-runnable state, never a bound actor whose rules are missing.
        async with self._ctx.control_db.transaction() as control_session:
            pairs = await self._load_credential_pairs(control_session, toolkit.id)
            rule_sets: dict[str, str | None] = {}
            rule_less: list[str] = []
            for credential_id, rules in pairs:
                if not rules:
                    rule_sets[credential_id] = None
                    rule_less.append(credential_id)
                    continue
                rule_sets[credential_id] = await self._ensure_rule_set(
                    control_session, toolkit_id=toolkit.id, credential_id=credential_id, rules=rules
                )

        async with self._ctx.admin_db.transaction() as admin_session:
            name = _service_account_name(key.id)
            service_account_id = await KeyRetirementRepository.find_service_account_by_name(
                admin_session, name=name
            )
            if service_account_id is None:
                label = f" ({key.label})" if key.label else ""
                service_account_id = await KeyRetirementRepository.create_service_account(
                    admin_session,
                    name=name,
                    description=(
                        f"Retired jntc_live_ key{label} of toolkit {toolkit.name!r}"
                        f" ({toolkit.id}) — theme-5 Phase 4"
                    ),
                    owner_id=owner_id,
                    api_key_hash=key.lookup_hash,
                )
            await KeyRetirementRepository.bind_actor_to_toolkit(
                admin_session, actor_id=service_account_id, toolkit_id=toolkit.id
            )
            for credential_id, rule_set_id in rule_sets.items():
                await KeyRetirementRepository.bind_actor_to_credential(
                    admin_session,
                    actor_id=service_account_id,
                    credential_id=credential_id,
                    rule_set_id=rule_set_id,
                )

        async with self._ctx.control_db.transaction() as control_session:
            await ToolkitKeyRepository.stamp_migrated_actor(
                control_session, key.id, service_account_id
            )

        return KeyRetirementOutcome(
            key_id=key.id,
            toolkit_id=toolkit.id,
            toolkit_name=toolkit.name,
            action="migrated",
            service_account_id=service_account_id,
            bound_credential_ids=tuple(rule_sets),
            rule_less_credential_ids=tuple(rule_less),
        )

    @staticmethod
    async def _load_credential_pairs(
        # ``Any`` because the arch rule forbids sqlalchemy imports in control
        # services (tests/arch/test_no_direct_db.py); the repos it's passed to
        # type it as AsyncSession.
        session: Any,
        toolkit_id: str,
    ) -> list[tuple[str, list[ToolkitPermissionRule]]]:
        """The toolkit's credential bindings, each with its ordered pair rules."""
        credential_ids = await ToolkitBindingRepository.list_credential_ids(session, toolkit_id)
        pairs: list[tuple[str, list[ToolkitPermissionRule]]] = []
        for credential_id in credential_ids:
            rules = await ToolkitPermissionRepository.list_rules(session, toolkit_id, credential_id)
            pairs.append((credential_id, rules))
        return pairs

    @staticmethod
    async def _ensure_rule_set(
        # ``Any`` for the same arch-rule reason as ``_load_credential_pairs``.
        session: Any,
        *,
        toolkit_id: str,
        credential_id: str,
        rules: list[ToolkitPermissionRule],
    ) -> str:
        """Copy the pair's rules into a shared rule set (reused across re-runs
        and across several keys of the same toolkit)."""
        name = _rule_set_name(toolkit_id, credential_id)
        existing = await PermissionRuleSetRepository.get_by_name(session, name)
        if existing is not None:
            return existing.id
        rule_set = await PermissionRuleSetRepository.create(
            session,
            name=name,
            description=(
                f"Rules of toolkit {toolkit_id} / credential {credential_id},"
                " copied by the theme-5 Phase 4 key-retirement job"
            ),
            created_by=SYSTEM_ACTOR,
        )
        await PermissionRuleSetRepository.replace_user_rules(
            session,
            rule_set.id,
            [
                {
                    "effect": rule.effect,
                    "methods": rule.methods,
                    "path": rule.path,
                    "match_mode": rule.match_mode,
                    "operations": rule.operations,
                    "comment": rule.comment,
                }
                for rule in rules
            ],
            created_by=SYSTEM_ACTOR,
        )
        return rule_set.id
