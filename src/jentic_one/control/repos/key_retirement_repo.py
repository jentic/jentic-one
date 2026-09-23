"""Repository for theme-5 Phase 4 key retirement — admin-DB writes, raw SQL.

The retirement job runs in the control module (toolkit keys, toolkits, and
permission rule sets live in the control DB) but creates the successor actor
in the **admin** DB (an agent since theme-8 Phase 1: its row, credential
hash, scope grant, and binding rows). The control module must not import
admin ORM models, so — like ``EffectsRepository`` — every admin-side
statement here is raw SQL, idempotent via natural keys (deterministic
successor name, ON CONFLICT DO NOTHING on uniquely-constrained binding
tables).
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.repos.service_account_migration_repo import SKIPPED_STAMP
from jentic_one.shared.db.ids import generate_ksuid

logger = structlog.get_logger(__name__)

# The job's system actor — stamped as created_by/registered_by/granted_by so
# every migrated row is attributable to the retirement run, mirroring the
# Phase-6a flattening provenance convention.
SYSTEM_ACTOR = "system:theme5-key-retirement"

_FIND_USER_BY_ID = text("SELECT id FROM users WHERE id = :user_id")
_FIND_USER_BY_EMAIL = text("SELECT id FROM users WHERE lower(email) = lower(:email)")

_FIND_AGENT_BY_NAME = text("SELECT id FROM agents WHERE name = :name")
_FIND_SERVICE_ACCOUNT_BY_NAME_SQL = (
    "SELECT id, migrated_to_actor_id FROM service_accounts WHERE name = :name"
)
_FIND_SERVICE_ACCOUNT_BY_NAME = text(_FIND_SERVICE_ACCOUNT_BY_NAME_SQL)
# Row lock on the remnant SA (Postgres): the SA→agent migration takes
# ``FOR UPDATE`` on the same row before it copies bindings and stamps, so the
# two serialise — either the migration waits for this transaction's binds to
# commit (and copies them), or this read blocks until the stamp commits (and
# sees it, redirecting instead of binding onto a stamped row).
_FIND_SERVICE_ACCOUNT_BY_NAME_FOR_UPDATE = text(_FIND_SERVICE_ACCOUNT_BY_NAME_SQL + " FOR UPDATE")

# Theme-8 Phase 1 (W8): the job mints **agents** for late ``jntc_live_``
# stragglers — after Phase 1 no code path may create a service-account row
# (the boot job must not be an SA producer the SA→agent migration then has to
# chase). Raw SQL, never AgentService.create()/approve(): the F1 constraint
# applies identically — a toolkit key was exactly ``capabilities:execute``,
# and the service path's DEFAULT_AGENT_SCOPES default would be a 1→13
# escalation.
_INSERT_AGENT = text(
    "INSERT INTO agents"
    " (id, name, description, owner_id, registered_by, status, created_by)"
    " VALUES (:id, :name, :description, :owner_id, :registered_by, 'active', :created_by)"
)

_INSERT_AGENT_CREDENTIAL = text(
    "INSERT INTO agent_credentials"
    " (id, agent_id, api_key_hash, created_by)"
    " VALUES (:id, :agent_id, :api_key_hash, :created_by)"
)

_INSERT_SCOPE_GRANT = text(
    "INSERT INTO actor_scope_grants"
    " (id, actor_id, actor_type, scope, granted_by, created_by)"
    " VALUES (:id, :actor_id, 'agent', :scope, :granted_by, :created_by)"
    " ON CONFLICT (actor_id, scope) DO NOTHING"
)

_INSERT_TOOLKIT_BINDING = text(
    "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id, created_by)"
    " VALUES (:id, :agent_id, :toolkit_id, :created_by)"
    " ON CONFLICT (agent_id, toolkit_id) DO NOTHING"
)

_INSERT_CREDENTIAL_BINDING = text(
    "INSERT INTO agent_credential_bindings"
    " (id, agent_id, credential_id, rule_set_id, created_by)"
    " VALUES (:id, :agent_id, :credential_id, :rule_set_id, :created_by)"
    " ON CONFLICT (agent_id, credential_id) DO NOTHING"
)


_SET_AGENT_STATUS = text("UPDATE agents SET status = :status WHERE id = :actor_id")
_SELECT_SA_STAMP = text("SELECT migrated_to_actor_id FROM service_accounts WHERE id = :actor_id")
# Stamp-guarded (theme-8 M4): raw SQL must not resurrect a row the migration
# job stamped — the guard mirrors the service-layer ServiceAccountMigratedError.
_SET_SERVICE_ACCOUNT_STATUS = text(
    "UPDATE service_accounts SET status = :status"
    " WHERE id = :actor_id AND migrated_to_actor_id IS NULL"
)


class KeyRetirementRepository:
    """Admin-DB write operations for the toolkit-key retirement job."""

    @staticmethod
    async def set_actor_status(session: AsyncSession, *, actor_id: str, status: str) -> None:
        """Cut (or restore) a migrated key's successor actor.

        Caller-less since the toolkit-key management surface is gone (theme-5
        Phase 5b). Kept with the rest of this repository until Phase 6b
        retires the toolkit tables, so operator tooling can still cut a
        successor.

        Theme-8 Phase 1 (M-E / IMPL-DECISION 8): dual-table during the
        coexistence window. After the migration job's re-stamp,
        ``toolkit_keys.migrated_actor_id`` holds ``agnt_`` ids, so ``agents``
        is tried first; a zero-row match falls back to ``service_accounts``
        (unmigrated stragglers whose keys retired before this boot's
        migration pass ran). Without the fallback the operator's
        compromise-response lever would silently no-op.

        Stamp-guarded (M4): a stamped SA row is never written — the update
        is REDIRECTED to the stamped successor agent (the stamp is
        authoritative; the successor is the live identity to cut or restore).
        A ``skipped`` stamp has no successor and the call is a logged no-op.
        """
        result = await session.execute(_SET_AGENT_STATUS, {"actor_id": actor_id, "status": status})
        if result.rowcount:  # type: ignore[attr-defined]
            return
        stamp_row = (await session.execute(_SELECT_SA_STAMP, {"actor_id": actor_id})).one_or_none()
        if stamp_row is not None and stamp_row.migrated_to_actor_id is not None:
            stamp = stamp_row.migrated_to_actor_id
            if stamp == SKIPPED_STAMP:
                logger.warning(
                    "key_retirement_status_skip_stamped_sa",
                    actor_id=actor_id,
                    detail="SA was skip-but-stamped (no successor); nothing to cut",
                )
                return
            # Redirect: the successor agent is the live identity (M4a).
            await session.execute(_SET_AGENT_STATUS, {"actor_id": stamp, "status": status})
            logger.info(
                "key_retirement_status_redirected_to_successor",
                service_account_id=actor_id,
                successor_agent_id=stamp,
                status=status,
            )
            return
        await session.execute(_SET_SERVICE_ACCOUNT_STATUS, {"actor_id": actor_id, "status": status})

    @staticmethod
    async def resolve_user(session: AsyncSession, *, candidate_ids: list[str]) -> str | None:
        """First candidate that is an existing user id, or None."""
        for candidate in candidate_ids:
            if not candidate:
                continue
            row = (await session.execute(_FIND_USER_BY_ID, {"user_id": candidate})).one_or_none()
            if row is not None:
                return str(row.id)
        return None

    @staticmethod
    async def resolve_user_by_email(session: AsyncSession, *, email: str) -> str | None:
        row = (await session.execute(_FIND_USER_BY_EMAIL, {"email": email})).one_or_none()
        return str(row.id) if row is not None else None

    @staticmethod
    async def find_successor_by_name(session: AsyncSession, *, name: str) -> str | None:
        """The name is the job's deterministic idempotency key (one per key row).

        Agents first (theme-8 Phase 1 successors); falls back to
        ``service_accounts`` for crash remnants of pre-theme-8 runs (an SA
        created but never stamped onto its key) — reusing it keeps this job
        access-preserving, and the SA→agent migration job then migrates it
        and re-stamps the key's pointer.

        Stamp-guarded (M4b): a stamped remnant SA must never be reused — the
        subsequent bind calls would insert fresh ``sva_``-keyed rows onto a
        row the migration already processed (post-stamp mutations, NF-3).
        A real ``agnt_`` stamp is REDIRECTED to the successor; a ``skipped``
        stamp returns None so the caller mints a fresh agent.

        The stamp is read under a row lock (L2): on Postgres the remnant is
        selected ``FOR UPDATE``, serialising with the migration's own
        ``FOR UPDATE`` re-read, so the stamp cannot land between this check
        and the caller's binds (same transaction; the caller's later control-DB
        ``toolkit_keys`` stamp is outside this lock and healed by the next
        migration run's re-stamp). SQLite needs no row lock —
        the caller's ``admin_db.transaction()`` is ``BEGIN IMMEDIATE`` and
        already holds the database write lock.
        """
        row = (await session.execute(_FIND_AGENT_BY_NAME, {"name": name})).one_or_none()
        if row is not None:
            return str(row.id)
        dialect = session.get_bind().dialect.name
        sa_stmt = (
            _FIND_SERVICE_ACCOUNT_BY_NAME_FOR_UPDATE
            if dialect == "postgresql"
            else _FIND_SERVICE_ACCOUNT_BY_NAME
        )
        row = (await session.execute(sa_stmt, {"name": name})).one_or_none()
        if row is None:
            return None
        stamp = row.migrated_to_actor_id
        if stamp is None:
            return str(row.id)
        if stamp == SKIPPED_STAMP:
            logger.warning(
                "key_retirement_stamped_sa_remnant_not_reused",
                service_account_id=str(row.id),
                detail="skip-but-stamped remnant; a fresh successor agent will be minted",
            )
            return None
        logger.info(
            "key_retirement_successor_redirected_from_stamped_sa",
            service_account_id=str(row.id),
            successor_agent_id=stamp,
        )
        return str(stamp)

    @staticmethod
    async def create_successor_agent(
        session: AsyncSession,
        *,
        name: str,
        description: str,
        owner_id: str,
        api_key_hash: str,
    ) -> str:
        """Create the successor actor: agent row + credential hash + execute grant.

        The credential row carries the retiring key's SHA-256 lookup digest,
        so the unchanged ``jntc_live_`` plaintext keeps authenticating — as
        this agent — through ``ApiKeyResolver`` (which hashes the presented
        key and matches ``api_key_hash`` regardless of prefix; agent-first
        since theme-8 Phase 1). The grant is exactly ``capabilities:execute``:
        a toolkit key *was* the execute capability and nothing else — never
        the default agent scope set (theme-5 plan, toolkit-keys decision;
        theme-8 F1).
        """
        agent_id = generate_ksuid("agnt")
        await session.execute(
            _INSERT_AGENT,
            {
                "id": agent_id,
                "name": name,
                "description": description,
                "owner_id": owner_id,
                "registered_by": SYSTEM_ACTOR,
                "created_by": SYSTEM_ACTOR,
            },
        )
        await session.execute(
            _INSERT_AGENT_CREDENTIAL,
            {
                "id": generate_ksuid("agc"),
                "agent_id": agent_id,
                "api_key_hash": api_key_hash,
                "created_by": SYSTEM_ACTOR,
            },
        )
        await session.execute(
            _INSERT_SCOPE_GRANT,
            {
                "id": generate_ksuid("asg"),
                "actor_id": agent_id,
                "scope": "capabilities:execute",
                "granted_by": SYSTEM_ACTOR,
                "created_by": SYSTEM_ACTOR,
            },
        )
        return agent_id

    @staticmethod
    async def bind_actor_to_toolkit(
        session: AsyncSession, *, actor_id: str, toolkit_id: str
    ) -> None:
        """Toolkit binding — keeps the holder deriving through the legacy
        toolkit path while ``direct_bindings_enabled`` is off; the Phase-6a
        flattening picks it up like any agent's binding."""
        await session.execute(
            _INSERT_TOOLKIT_BINDING,
            {
                "id": generate_ksuid("atb"),
                "agent_id": actor_id,
                "toolkit_id": toolkit_id,
                "created_by": SYSTEM_ACTOR,
            },
        )

    @staticmethod
    async def bind_actor_to_credential(
        session: AsyncSession,
        *,
        actor_id: str,
        credential_id: str,
        rule_set_id: str | None,
    ) -> None:
        """Direct binding — the flag-on twin of the toolkit binding, carrying
        the rule set copied from the pair's ``toolkit_permission_rules``. NULL
        ``rule_set_id`` (a rule-less pair) stays default-deny on both paths."""
        await session.execute(
            _INSERT_CREDENTIAL_BINDING,
            {
                "id": generate_ksuid("acb"),
                "agent_id": actor_id,
                "credential_id": credential_id,
                "rule_set_id": rule_set_id,
                "created_by": SYSTEM_ACTOR,
            },
        )
