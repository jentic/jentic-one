"""Repository for theme-8 Phase 1 service-account → agent migration.

The migration job runs in the control module (it copies the per-binding
inline permission rules in the control DB) but does nearly all of its work in
the **admin** DB (successor agents, credential digests, grant and
binding twins, token revocation, the stamp, the verify queries, and the
acknowledgement sentinel). The control module must not import admin ORM
models, so every admin-side statement
here is raw SQL (F1 is also served by this: successor creation must never go
through ``AgentService.create()``/``approve()``, whose empty-scope default is
``DEFAULT_AGENT_SCOPES``).

Concurrency (H-A x F6): the caller wraps each SA in one admin transaction
(``BEGIN IMMEDIATE`` on SQLite via ``DatabaseSession.transaction``);
``acquire_migration_lock`` adds a Postgres ``pg_advisory_xact_lock`` fast
path (documented no-op on SQLite). The real double-mint backstop is the
``uq_agent_credentials_api_key_hash`` unique partial index — a losing
concurrent insert fails the transaction, never a partial write.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.shared.auth.permission_catalog import (
    AGENTS_WRITE,
    CONFIG_WRITE,
    CREDENTIALS_WRITE,
    OAUTH_CLIENTS_WRITE,
    ORG_ADMIN,
    USERS_WRITE,
)
from jentic_one.shared.db.ids import generate_ksuid

# The job's system actor — stamped as created_by/registered_by/granted_by so
# every migrated row is attributable to the run (theme-5 provenance shape).
SYSTEM_ACTOR = "system:theme8-sa-migration"

#: Stamp value for skip-but-stamp rows (IMPL-DECISION 3): non-active SAs are
#: stamped done without a successor. Unambiguous — real values start ``agnt_``.
SKIPPED_STAMP = "skipped"

#: Scopes retired by theme 8 itself (Phase 2): stored SA grants carrying them
#: get no successor twin — they are left behind for the sweep, never carried.
#: E2 cross-reference: every member is also in ``shared.scopes.RETIRED_SCOPES``
#: (Phase 2 retired them) — pinned by ``test_retired_scopes.py``.
THEME8_RETIRED_SCOPES: frozenset[str] = frozenset(
    {
        "service-accounts:read",
        "service-accounts:write",
        "owner:service-accounts:read",
    }
)

#: Admin-level scopes: the ones that let a holder manage other principals,
#: their grants or credentials, or platform configuration. A successor agent
#: that inherits one of these from its service account is reported to the
#: operator (report line + WARNING log + ``verify`` finding) — never stripped,
#: because a legitimate automation SA may well have held it.
ADMIN_LEVEL_SCOPES: frozenset[str] = frozenset(
    {ORG_ADMIN, USERS_WRITE, AGENTS_WRITE, CREDENTIALS_WRITE, CONFIG_WRITE, OAUTH_CLIENTS_WRITE}
)

_LIST_SERVICE_ACCOUNTS = text(
    "SELECT sa.id, sa.name, sa.description, sa.owner_id, sa.status,"
    " sa.migrated_to_actor_id, sa.migrated_at,"
    " sac.api_key_hash, sac.client_secret_hash"
    " FROM service_accounts sa"
    " LEFT JOIN service_account_credentials sac ON sac.service_account_id = sa.id"
    " ORDER BY sa.id"
)

#: Criterion 4 (#1416): fully-migrated, unswept SAs whose successor does not
#: hold the SA's still-live digest. LEFT JOINs so a missing successor agent
#: or credential row surfaces as drift instead of silently dropping out
#: (``agent_credentials.agent_id`` is unique — at most one row per agent).
_SUCCESSOR_DIGEST_DRIFT_FROM = (
    " FROM service_account_credentials sac"
    " JOIN service_accounts sa ON sa.id = sac.service_account_id"
    " LEFT JOIN agents a ON a.id = sa.migrated_to_actor_id"
    " LEFT JOIN agent_credentials ac ON ac.agent_id = sa.migrated_to_actor_id"
    " WHERE sac.api_key_hash IS NOT NULL"
    " AND sa.migrated_to_actor_id IS NOT NULL"
    " AND sa.migrated_to_actor_id != 'skipped'"
    " AND (ac.api_key_hash IS NULL OR ac.api_key_hash != sac.api_key_hash)"
)
_SUCCESSOR_DIGEST_DRIFT_SQL = (
    "SELECT sa.id AS service_account_id, sa.migrated_to_actor_id AS successor_agent_id"
    + _SUCCESSOR_DIGEST_DRIFT_FROM
)

#: The successor was legitimately superseded after the stamp: archived, or its
#: key rotated/revoked (every ``AgentCredentialRepository`` writer stamps
#: ``rotated_at``). Written NULL-safe — a missing agent row, a missing
#: credential row, or a NULL ``rotated_at``/``migrated_at`` evaluates to FALSE,
#: never NULL, so ``NOT (...)`` keeps those rows counted (fail-closed).
_SUCCESSOR_SUPERSEDED_PREDICATE = (
    "(a.id IS NOT NULL AND (a.status = 'archived'"
    " OR (ac.rotated_at IS NOT NULL AND sa.migrated_at IS NOT NULL"
    " AND ac.rotated_at > sa.migrated_at)))"
)

_SELECT_SERVICE_ACCOUNT_SQL = (
    "SELECT sa.id, sa.name, sa.description, sa.owner_id, sa.status,"
    " sa.migrated_to_actor_id, sa.migrated_at,"
    " sac.api_key_hash, sac.client_secret_hash"
    " FROM service_accounts sa"
    " LEFT JOIN service_account_credentials sac ON sac.service_account_id = sa.id"
    " WHERE sa.id = :id"
)
_SELECT_SERVICE_ACCOUNT = text(_SELECT_SERVICE_ACCOUNT_SQL)
# ``FOR UPDATE OF sa``: Postgres refuses FOR UPDATE on the nullable side of an
# outer join. Locking the SA row is enough — every SA-side writer that the
# migration must serialise with (the service-layer stamp guards, key
# rotation) takes ``get_by_id_for_update`` on this same row first.
_SELECT_SERVICE_ACCOUNT_FOR_UPDATE = text(_SELECT_SERVICE_ACCOUNT_SQL + " FOR UPDATE OF sa")

_INSERT_AGENT = text(
    "INSERT INTO agents (id, name, description, owner_id, registered_by, status, created_by)"
    " VALUES (:id, :name, :description, :owner_id, :registered_by, :status, :created_by)"
)

_INSERT_AGENT_CREDENTIAL = text(
    "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by)"
    " VALUES (:id, :agent_id, :api_key_hash, :created_by)"
)

_SELECT_GRANTS = text(
    "SELECT scope, granted_by FROM actor_scope_grants"
    " WHERE actor_id = :actor_id AND actor_type = 'service_account'"
    " ORDER BY scope"
)

_INSERT_GRANT_TWIN = text(
    "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope, granted_by, created_by)"
    " VALUES (:id, :actor_id, 'agent', :scope, :granted_by, :created_by)"
    " ON CONFLICT (actor_id, scope) DO NOTHING"
)

_SELECT_CREDENTIAL_BINDINGS = text(
    "SELECT credential_id, rule_set_id, suspended, suspended_reason"
    " FROM agent_credential_bindings WHERE agent_id = :actor_id"
)

_INSERT_CREDENTIAL_BINDING_TWIN = text(
    "INSERT INTO agent_credential_bindings"
    " (id, agent_id, credential_id, rule_set_id, suspended, suspended_reason, created_by)"
    " VALUES (:id, :agent_id, :credential_id, :rule_set_id, :suspended, :suspended_reason,"
    " :created_by)"
    " ON CONFLICT (agent_id, credential_id) DO NOTHING"
)

_REVOKE_ACCESS_TOKENS = text(
    "UPDATE access_tokens SET revoked_at = :now"
    " WHERE actor_id = :actor_id AND actor_type = 'service_account'"
    " AND revoked_at IS NULL"
)

_REVOKE_REFRESH_TOKENS = text(
    "UPDATE refresh_tokens SET revoked_at = :now"
    " WHERE actor_id = :actor_id AND actor_type = 'service_account'"
    " AND revoked_at IS NULL"
)

# ``--diff-only`` preview: the rows ``revoke_tokens`` would touch (same WHERE).
_COUNT_REVOCABLE_ACCESS_TOKENS = text(
    "SELECT count(*) AS n FROM access_tokens"
    " WHERE actor_id = :actor_id AND actor_type = 'service_account'"
    " AND revoked_at IS NULL"
)

_COUNT_REVOCABLE_REFRESH_TOKENS = text(
    "SELECT count(*) AS n FROM refresh_tokens"
    " WHERE actor_id = :actor_id AND actor_type = 'service_account'"
    " AND revoked_at IS NULL"
)

_STAMP = text(
    "UPDATE service_accounts"
    " SET migrated_to_actor_id = :stamp, migrated_at = :now"
    " WHERE id = :id AND migrated_to_actor_id IS NULL"
)


class ServiceAccountMigrationRepository:
    """Admin-DB (and a few control-DB) operations for the SA-migration job.

    Control-DB methods (``copy_permission_rules``,
    ``list_service_account_rule_holders``, ``count_permission_rules_by_binding``)
    must be called with a **control** session; everything else is admin.
    """

    @staticmethod
    async def acquire_migration_lock(session: AsyncSession, service_account_id: str) -> None:
        """Postgres advisory fast path; documented no-op on SQLite.

        Transaction-scoped (released at commit/rollback) — the
        ``oauth_token_repo.acquire_refresh_lock`` precedent. The unique
        partial index remains the enforcing mechanism on both dialects.
        """
        dialect = session.bind.dialect.name if session.bind else "sqlite"
        if dialect == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:sid))"),
                {"sid": service_account_id},
            )

    @staticmethod
    async def list_service_accounts(session: AsyncSession) -> list[Any]:
        """Every SA row joined to its (single) credential row, stable order."""
        return list((await session.execute(_LIST_SERVICE_ACCOUNTS)).all())

    @staticmethod
    async def get_service_account(
        session: AsyncSession, service_account_id: str, *, for_update: bool = False
    ) -> Any | None:
        """One SA row joined to its credential row — the in-transaction re-read.

        Same column shape as :meth:`list_service_accounts`. With
        ``for_update`` the SA row is locked on Postgres (``FOR UPDATE OF
        sa``); SQLite needs no row lock — the caller's ``BEGIN IMMEDIATE``
        already holds the database write lock.
        """
        stmt = _SELECT_SERVICE_ACCOUNT
        if for_update:
            dialect = session.bind.dialect.name if session.bind else "sqlite"
            if dialect == "postgresql":
                stmt = _SELECT_SERVICE_ACCOUNT_FOR_UPDATE
        return (await session.execute(stmt, {"id": service_account_id})).one_or_none()

    @staticmethod
    async def create_successor_agent(
        session: AsyncSession,
        *,
        service_account_id: str,
        sa_name: str,
        owner_id: str,
        status: str,
        api_key_hash: str | None,
    ) -> str:
        """Create the successor agent + (optional) credential digest copy.

        Raw SQL, NEVER ``AgentService.create()``/``approve()`` (F1) — both
        default-grant ``DEFAULT_AGENT_SCOPES`` on empty scope sets, and a
        zero-grant SA must yield a zero-grant successor. ``status`` is
        ``active`` or ``disabled`` (OQ-1) — never ``pending``. The digest is
        a COPY: the SA-side digest stays live until the sweep (F6/H-B). A
        duplicate digest violates ``uq_agent_credentials_api_key_hash`` and
        fails this transaction whole — never a partial write.
        ``client_secret_hash`` is NOT copied (D3); holders are report lines.
        """
        agent_id = generate_ksuid("agnt")
        await session.execute(
            _INSERT_AGENT,
            {
                "id": agent_id,
                "name": f"service-account:{service_account_id}",
                "description": (
                    f"Successor of service account {sa_name!r}"
                    f" ({service_account_id}) — theme-8 Phase 1"
                ),
                "owner_id": owner_id,
                "registered_by": SYSTEM_ACTOR,
                "status": status,
                "created_by": SYSTEM_ACTOR,
            },
        )
        if api_key_hash is not None:
            await session.execute(
                _INSERT_AGENT_CREDENTIAL,
                {
                    "id": generate_ksuid("agc"),
                    "agent_id": agent_id,
                    "api_key_hash": api_key_hash,
                    "created_by": SYSTEM_ACTOR,
                },
            )
        return agent_id

    @staticmethod
    async def list_copyable_grants(
        session: AsyncSession, *, service_account_id: str
    ) -> list[tuple[str, str | None]]:
        """The SA's stored grants a migration copies: ``(scope, granted_by)``.

        Stored rows only, theme-8-retired ``service-accounts:*`` scopes
        excluded (left for the sweep). ``granted_by`` is the ORIGINAL grantor
        — the twin is re-stamped with the job's system actor, so this is the
        only place the report can recover it from.
        """
        rows = (await session.execute(_SELECT_GRANTS, {"actor_id": service_account_id})).all()
        return [
            (str(row.scope), row.granted_by)
            for row in rows
            if row.scope not in THEME8_RETIRED_SCOPES
        ]

    @staticmethod
    async def copy_scope_grants(
        session: AsyncSession, *, service_account_id: str, agent_id: str
    ) -> list[tuple[str, str | None]]:
        """COPY stored grant rows onto the successor; keep the originals (N1).

        Stored rows only — the resolve-time closure stays resolve-time; an
        empty set stays empty (F1). Theme-8-retired ``service-accounts:*``
        scopes get no twin (left for the sweep). Returns the copied
        ``(scope, original granted_by)`` pairs, in scope order.
        """
        grants = await ServiceAccountMigrationRepository.list_copyable_grants(
            session, service_account_id=service_account_id
        )
        for scope, _granted_by in grants:
            await session.execute(
                _INSERT_GRANT_TWIN,
                {
                    "id": generate_ksuid("asg"),
                    "actor_id": agent_id,
                    "scope": scope,
                    "granted_by": SYSTEM_ACTOR,
                    "created_by": SYSTEM_ACTOR,
                },
            )
        return grants

    @staticmethod
    async def copy_bindings(
        session: AsyncSession, *, service_account_id: str, agent_id: str
    ) -> int:
        """Twin the SA's credential bindings onto the successor; return the count.

        Coexistence is legal — uniqueness is per ``(agent_id, credential_id)``
        pair (N1), so the ``sva_``-keyed originals stay until the sweep. (The
        toolkit-binding twin was deleted with ``agent_toolkit_bindings`` in
        theme-5 Phase 6b.)
        """
        credential_rows = (
            await session.execute(_SELECT_CREDENTIAL_BINDINGS, {"actor_id": service_account_id})
        ).all()
        for row in credential_rows:
            await session.execute(
                _INSERT_CREDENTIAL_BINDING_TWIN,
                {
                    "id": generate_ksuid("acb"),
                    "agent_id": agent_id,
                    "credential_id": row.credential_id,
                    "rule_set_id": row.rule_set_id,
                    "suspended": row.suspended,
                    "suspended_reason": row.suspended_reason,
                    "created_by": SYSTEM_ACTOR,
                },
            )
        return len(credential_rows)

    @staticmethod
    async def count_copy_candidates(
        session: AsyncSession, *, service_account_id: str
    ) -> tuple[int, int]:
        """``--diff-only`` preview: ``(scopes, credential bindings)`` that
        :meth:`copy_scope_grants` / :meth:`copy_bindings` would copy — the same
        source queries and retired-scope filter, no writes."""
        grants = (await session.execute(_SELECT_GRANTS, {"actor_id": service_account_id})).all()
        credential_rows = (
            await session.execute(_SELECT_CREDENTIAL_BINDINGS, {"actor_id": service_account_id})
        ).all()
        scopes = sum(1 for row in grants if row.scope not in THEME8_RETIRED_SCOPES)
        return scopes, len(credential_rows)

    @staticmethod
    async def count_revocable_tokens(
        session: AsyncSession, *, service_account_id: str
    ) -> tuple[int, int]:
        """``--diff-only`` preview: ``(access, refresh)`` rows
        :meth:`revoke_tokens` would revoke (same predicate, no writes)."""
        params = {"actor_id": service_account_id}
        access = (await session.execute(_COUNT_REVOCABLE_ACCESS_TOKENS, params)).one()
        refresh = (await session.execute(_COUNT_REVOCABLE_REFRESH_TOKENS, params)).one()
        return int(access.n), int(refresh.n)

    @staticmethod
    async def revoke_tokens(
        session: AsyncSession, *, service_account_id: str, now: datetime
    ) -> tuple[int, int]:
        """Revoke the SA's outstanding opaque sessions (H-1), raw-SQL family-revoke.

        ``/oauth/mint`` output is agent-keyed and <= 3600 s — outside this
        sweep (F9).
        """
        access = await session.execute(
            _REVOKE_ACCESS_TOKENS, {"actor_id": service_account_id, "now": now}
        )
        refresh = await session.execute(
            _REVOKE_REFRESH_TOKENS, {"actor_id": service_account_id, "now": now}
        )
        return access.rowcount or 0, refresh.rowcount or 0  # type: ignore[attr-defined]

    @staticmethod
    async def stamp(
        session: AsyncSession, *, service_account_id: str, value: str, now: datetime
    ) -> bool:
        """Write the stamp; False means a concurrent winner already stamped."""
        result = await session.execute(
            _STAMP, {"id": service_account_id, "stamp": value, "now": now}
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    async def copy_permission_rules(
        session: AsyncSession, *, service_account_id: str, agent_id: str
    ) -> int:
        """Control-DB twin of the SA's per-binding inline permission rules.

        ``agent_permission_rules`` is keyed ``(agent_id, credential_id)`` with
        no FK to the admin DB, so the ``sva_``-keyed rows would silently stop
        applying once the key resolves as the successor. Copy them onto the
        successor (originals stay until the sweep, N1).

        Idempotent at **binding** granularity: a binding the successor already
        holds any rule for is skipped whole — a re-run (the ``already_migrated``
        heal path) never merges into, or resurrects rules into, a list the
        operator has since edited on the successor. ``ON CONFLICT (agent_id,
        credential_id, sequence) DO NOTHING`` (``uq_agent_permission_rules_
        binding_seq``) is the belt for a concurrent copier. Returns the number
        of rule rows inserted.
        """
        source = list(
            (
                await session.execute(
                    select(AgentPermissionRule)
                    .where(AgentPermissionRule.agent_id == service_account_id)
                    .order_by(AgentPermissionRule.credential_id, AgentPermissionRule.sequence)
                )
            )
            .scalars()
            .all()
        )
        if not source:
            return 0
        already = set(
            (
                await session.execute(
                    select(AgentPermissionRule.credential_id)
                    .where(AgentPermissionRule.agent_id == agent_id)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        inserted = 0
        for rule in source:
            if rule.credential_id in already:
                continue
            stmt = (
                pg_insert(AgentPermissionRule)
                .values(
                    id=generate_ksuid("apr"),
                    agent_id=agent_id,
                    credential_id=rule.credential_id,
                    effect=rule.effect,
                    methods=rule.methods,
                    path=rule.path,
                    match_mode=rule.match_mode,
                    operations=rule.operations,
                    is_system=rule.is_system,
                    comment=rule.comment,
                    sequence=rule.sequence,
                    created_by=SYSTEM_ACTOR,
                )
                .on_conflict_do_nothing(index_elements=["agent_id", "credential_id", "sequence"])
            )
            result = await session.execute(stmt)
            inserted += result.rowcount or 0  # type: ignore[attr-defined]
        await session.flush()
        return inserted

    @staticmethod
    async def list_service_account_rule_holders(session: AsyncSession) -> set[str]:
        """Control DB: every ``sva_``-keyed actor id still holding inline rules."""
        rows = await session.execute(
            select(AgentPermissionRule.agent_id)
            .where(AgentPermissionRule.agent_id.startswith("sva_", autoescape=True))
            .distinct()
        )
        return set(rows.scalars().all())

    @staticmethod
    async def count_permission_rules_by_binding(
        session: AsyncSession, actor_ids: list[str]
    ) -> dict[tuple[str, str], int]:
        """Control DB: ``{(agent_id, credential_id): rule count}`` for ``actor_ids``."""
        if not actor_ids:
            return {}
        rows = await session.execute(
            select(
                AgentPermissionRule.agent_id,
                AgentPermissionRule.credential_id,
                func.count().label("n"),
            )
            .where(AgentPermissionRule.agent_id.in_(actor_ids))
            .group_by(AgentPermissionRule.agent_id, AgentPermissionRule.credential_id)
        )
        return {(r.agent_id, r.credential_id): int(r.n) for r in rows.all()}

    # ------------------------------------------------------------------ sweep

    @staticmethod
    async def list_sweepable(
        session: AsyncSession, *, stamped_before: datetime | None
    ) -> list[Any]:
        """Stamped rows that still have anything to sweep; optionally age-gated (N3).

        ``stamped_before=None`` ignores the age gate (``--sweep-migrated``).
        Skip-but-stamp rows are swept too (OQ-1): archive + delete the
        twin-less grant/binding rows. Rows already ``archived`` at migration
        time are NOT excluded by status (M2 — their lingering ``sva_``-keyed
        grant/binding/digest rows would block the Phase-4 drop); instead the
        filter is "still has SA-keyed satellite rows OR is not yet archived",
        which also keeps repeated sweeps from re-processing finished rows.
        """
        clause = " AND migrated_at <= :stamped_before" if stamped_before is not None else ""
        stmt = text(
            "SELECT sa.id, sa.migrated_to_actor_id, sa.migrated_at, sa.status"
            " FROM service_accounts sa"
            " WHERE sa.migrated_to_actor_id IS NOT NULL"
            " AND (sa.status != 'archived'"
            "  OR EXISTS (SELECT 1 FROM actor_scope_grants g"
            "   WHERE g.actor_id = sa.id AND g.actor_type = 'service_account')"
            "  OR EXISTS (SELECT 1 FROM agent_credential_bindings cb WHERE cb.agent_id = sa.id)"
            "  OR EXISTS (SELECT 1 FROM service_account_credentials sac"
            "   WHERE sac.service_account_id = sa.id AND sac.api_key_hash IS NOT NULL))" + clause
        )
        params: dict[str, Any] = {}
        if stamped_before is not None:
            params["stamped_before"] = stamped_before
        return list((await session.execute(stmt, params)).all())

    @staticmethod
    async def list_stamped(
        session: AsyncSession, *, stamped_before: datetime | None
    ) -> dict[str, str]:
        """``{sa_id: stamp}`` for every stamped SA (skip-stamped included),
        optionally age-gated (N3).

        Drives the control-DB half of the sweep, which must also reach rows
        whose admin-side satellites are already gone (a crash between the
        admin sweep commit and the control-DB rule delete).
        """
        clause = " AND migrated_at <= :stamped_before" if stamped_before is not None else ""
        params: dict[str, Any] = {}
        if stamped_before is not None:
            params["stamped_before"] = stamped_before
        rows = await session.execute(
            text(
                "SELECT id, migrated_to_actor_id FROM service_accounts"
                " WHERE migrated_to_actor_id IS NOT NULL" + clause
            ),
            params,
        )
        return {r.id: r.migrated_to_actor_id for r in rows.all()}

    @staticmethod
    async def list_migrated_pairs(session: AsyncSession) -> list[tuple[str, str]]:
        """``(service_account_id, successor_agent_id)`` for every fully-migrated SA."""
        rows = await session.execute(
            text(
                "SELECT id, migrated_to_actor_id FROM service_accounts"
                " WHERE migrated_to_actor_id IS NOT NULL AND migrated_to_actor_id != 'skipped'"
                " ORDER BY id"
            )
        )
        return [(r.id, r.migrated_to_actor_id) for r in rows.all()]

    @staticmethod
    async def list_successor_admin_grants(session: AsyncSession) -> list[Any]:
        """Admin-level grants the migration carried onto successor agents.

        Informational ``verify`` read (never a failing criterion): every
        :data:`ADMIN_LEVEL_SCOPES` grant still held by a migration-created
        successor (``registered_by`` = the job's system actor, name
        ``service-account:<sva_ id>``) with the job as its grantor — a scope an
        operator has since re-granted themselves is no longer "carried over".
        Rows: ``agent_id, agent_name, owner_id, status, scope``.
        """
        scope_params = {f"scope_{i}": s for i, s in enumerate(sorted(ADMIN_LEVEL_SCOPES))}
        placeholders = ", ".join(f":{name}" for name in scope_params)
        rows = await session.execute(
            text(
                "SELECT a.id AS agent_id, a.name AS agent_name, a.owner_id, a.status, g.scope"
                " FROM actor_scope_grants g"
                " JOIN agents a ON a.id = g.actor_id"
                " WHERE g.actor_type = 'agent'"
                " AND a.registered_by = :system_actor"
                " AND a.name LIKE 'service-account:%'"
                " AND g.granted_by = :system_actor"
                f" AND g.scope IN ({placeholders})"
                " ORDER BY a.id, g.scope"
            ),
            {"system_actor": SYSTEM_ACTOR, **scope_params},
        )
        return list(rows.all())

    @staticmethod
    async def sweep_service_account(session: AsyncSession, *, service_account_id: str) -> bool:
        """Delete the SA-keyed originals, NULL the digest, archive the row.

        Raw SQL, never ``ServiceAccountService.archive()`` — its
        ``revoke_all`` + audit shape assumes an operator identity, and the
        W6 guard refuses stamped rows (the guard-ordering trap).

        Returns whether the archive UPDATE changed the row (L3: guarded with
        ``AND status != 'archived'`` as the in-transaction re-check against a
        concurrent sweep; the caller skips the ARCHIVE audit row on False).
        """
        await session.execute(
            text(
                "DELETE FROM actor_scope_grants"
                " WHERE actor_id = :sid AND actor_type = 'service_account'"
            ),
            {"sid": service_account_id},
        )
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE agent_id = :sid"),
            {"sid": service_account_id},
        )
        await session.execute(
            text(
                "UPDATE service_account_credentials SET api_key_hash = NULL"
                " WHERE service_account_id = :sid"
            ),
            {"sid": service_account_id},
        )
        result = await session.execute(
            text(
                "UPDATE service_accounts SET status = 'archived'"
                " WHERE id = :sid AND status != 'archived'"
            ),
            {"sid": service_account_id},
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    # ----------------------------------------------------------------- verify

    @staticmethod
    async def count_unstamped(session: AsyncSession) -> int:
        """Criterion 1: zero *unstamped* rows (skip-but-stamp rows pass, OQ-1)."""
        row = (
            await session.execute(
                text(
                    "SELECT count(*) AS n FROM service_accounts WHERE migrated_to_actor_id IS NULL"
                )
            )
        ).one()
        return int(row.n)

    @staticmethod
    async def count_grant_twin_missing(session: AsyncSession) -> int:
        """Criterion 2: every non-retired SA grant has its agent twin."""
        # E2: bound parameters, never f-string interpolation, even for a
        # frozen constant. THEME8_RETIRED_SCOPES ⊆ RETIRED_SCOPES is pinned by
        # tests/unit/shared/test_retired_scopes.py.
        scope_params = {f"scope_{i}": s for i, s in enumerate(sorted(THEME8_RETIRED_SCOPES))}
        placeholders = ", ".join(f":{name}" for name in scope_params)
        row = (
            await session.execute(
                text(
                    "SELECT count(*) AS n FROM actor_scope_grants g"
                    " JOIN service_accounts sa ON sa.id = g.actor_id"
                    " WHERE g.actor_type = 'service_account'"
                    f" AND g.scope NOT IN ({placeholders})"
                    " AND sa.migrated_to_actor_id IS NOT NULL"
                    " AND sa.migrated_to_actor_id != 'skipped'"
                    " AND NOT EXISTS ("
                    "  SELECT 1 FROM actor_scope_grants t"
                    "  WHERE t.actor_id = sa.migrated_to_actor_id"
                    "  AND t.actor_type = 'agent' AND t.scope = g.scope)"
                ),
                scope_params,
            )
        ).one()
        return int(row.n)

    @staticmethod
    async def count_unrevoked_tokens(session: AsyncSession, *, now: datetime) -> int:
        """Criterion 3: zero live (unexpired, unrevoked) SA token rows."""
        total = 0
        for table in ("access_tokens", "refresh_tokens"):
            row = (
                await session.execute(
                    text(
                        f"SELECT count(*) AS n FROM {table}"
                        " WHERE actor_type = 'service_account'"
                        " AND revoked_at IS NULL AND expires_at > :now"
                    ),
                    {"now": now},
                )
            ).one()
            total += int(row.n)
        return total

    @staticmethod
    async def list_digest_mismatches(session: AsyncSession) -> list[Any]:
        """Criterion 4: successor digest equals the (still-live) SA digest.

        Scoped to fully-migrated SAs whose SA-side digest is still non-NULL —
        after the sweep the SA side is NULLed by design (copy-then-sweep),
        so swept rows are excluded rather than false-failed.

        A successor that no longer holds the copied digest because it was
        legitimately *superseded* is not drift (#1416) and is excluded — see
        :meth:`list_superseded_successor_digests`. Everything else fails
        closed: a missing successor agent row, a missing credential row, or a
        digest that changed without the credential writers' ``rotated_at``
        stamp (a raw-SQL writer bypassing the service).

        Rows: ``service_account_id, successor_agent_id``.
        """
        rows = await session.execute(
            text(
                _SUCCESSOR_DIGEST_DRIFT_SQL
                + f" AND NOT {_SUCCESSOR_SUPERSEDED_PREDICATE}"
                + " ORDER BY sa.id"
            )
        )
        return list(rows.all())

    @staticmethod
    async def list_superseded_successor_digests(session: AsyncSession) -> list[Any]:
        """Informational ``verify`` read: SA digests the successor superseded.

        A fully-migrated SA whose still-live digest the successor no longer
        holds because the operator acted on the successor after the stamp —
        its key was rotated or revoked (``agent_credentials.rotated_at`` after
        ``migrated_at``; every credential writer stamps it) or the agent was
        archived. The stale SA-side digest authenticates nothing (the resolver
        fails closed on stamped rows) and ``--sweep-migrated`` NULLs it.

        Rows: ``service_account_id, successor_agent_id, reason`` where
        ``reason`` is ``successor_archived`` or ``successor_key_rotated``.
        """
        rows = await session.execute(
            text(
                "SELECT sa.id AS service_account_id,"
                " sa.migrated_to_actor_id AS successor_agent_id,"
                " CASE WHEN a.status = 'archived' THEN 'successor_archived'"
                " ELSE 'successor_key_rotated' END AS reason"
                + _SUCCESSOR_DIGEST_DRIFT_FROM
                + f" AND {_SUCCESSOR_SUPERSEDED_PREDICATE}"
                + " ORDER BY sa.id"
            )
        )
        return list(rows.all())

    @staticmethod
    async def count_post_stamp_mutations(session: AsyncSession) -> int:
        """Criterion 5 (NF-3 scope): no re-created SA grant rows, no
        ``api_key_hash`` rotation, and no fresh ``sva_``-keyed binding rows
        (M4 — raw-SQL writers bypassing the service guards) after the stamp
        timestamp."""
        grants = (
            await session.execute(
                text(
                    "SELECT count(*) AS n FROM actor_scope_grants g"
                    " JOIN service_accounts sa ON sa.id = g.actor_id"
                    " WHERE g.actor_type = 'service_account'"
                    " AND sa.migrated_at IS NOT NULL"
                    " AND g.created_at > sa.migrated_at"
                )
            )
        ).one()
        rotations = (
            await session.execute(
                text(
                    "SELECT count(*) AS n FROM service_account_credentials sac"
                    " JOIN service_accounts sa ON sa.id = sac.service_account_id"
                    " WHERE sa.migrated_at IS NOT NULL"
                    " AND sac.rotated_at IS NOT NULL"
                    " AND sac.rotated_at > sa.migrated_at"
                )
            )
        ).one()
        bindings = (
            await session.execute(
                text(
                    "SELECT count(*) AS n FROM agent_credential_bindings b"
                    " JOIN service_accounts sa ON sa.id = b.agent_id"
                    " WHERE sa.migrated_at IS NOT NULL"
                    " AND b.created_at > sa.migrated_at"
                )
            )
        ).one()
        return int(grants.n) + int(rotations.n) + int(bindings.n)

    # --------------------------------------------------------------- sentinel

    @staticmethod
    async def record_acknowledgement(
        session: AsyncSession,
        *,
        acknowledged_at: datetime,
        unstamped_count: int,
        grant_twin_missing_count: int,
        unrevoked_token_count: int,
        digest_mismatch_count: int,
        post_stamp_mutation_count: int,
        report_finding_count: int,
        tool_version: str,
    ) -> str:
        """Insert the Phase-4 gate row — only call after a passed verification.

        Raw SQL (the table lives in the admin DB and this is the control
        module); the id is generated in Python so SQLite needs no server
        default.
        """
        ack_id = generate_ksuid("smak")
        await session.execute(
            text(
                "INSERT INTO service_account_migration_acks"
                " (id, acknowledged_at, unstamped_count, grant_twin_missing_count,"
                "  unrevoked_token_count, digest_mismatch_count,"
                "  post_stamp_mutation_count, report_finding_count, tool_version,"
                "  created_by)"
                " VALUES (:id, :acknowledged_at, :unstamped, :twin_missing,"
                "  :unrevoked, :digest_mismatch, :post_stamp, :findings, :version,"
                "  :created_by)"
            ),
            {
                "id": ack_id,
                "acknowledged_at": acknowledged_at,
                "unstamped": unstamped_count,
                "twin_missing": grant_twin_missing_count,
                "unrevoked": unrevoked_token_count,
                "digest_mismatch": digest_mismatch_count,
                "post_stamp": post_stamp_mutation_count,
                "findings": report_finding_count,
                "version": tool_version,
                "created_by": SYSTEM_ACTOR,
            },
        )
        return ack_id
