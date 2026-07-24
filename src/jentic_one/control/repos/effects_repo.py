"""Repository for cross-database effect operations.

Uses raw SQL for admin-DB writes to avoid importing admin ORM models — the
control module must not import from the admin module. Uses ON CONFLICT DO NOTHING
for idempotent inserts without requiring rollback.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.repos.toolkit_binding_repo import ToolkitBindingRepository
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models.api_identity import credential_coverage_where, slugify_api_field


class BindTargetMissingError(Exception):
    """A credential:bind insert failed a foreign-key check: a target row vanished.

    Raised by :meth:`EffectsRepository.bind_credential_to_toolkit` when the
    binding insert hits an FK violation — the toolkit or credential was deleted
    between the caller's pre-validation and this write (a TOCTOU race). ``target``
    is ``"toolkit"`` or ``"credential"`` so the control *service* can surface the
    precise 422 without importing DB internals (``sqlalchemy.exc``) itself — which
    ``tests/arch/test_no_direct_db.py`` forbids outside the repository layer.
    """

    def __init__(self, target: str, target_id: str) -> None:
        super().__init__(f"{target} '{target_id}' not found for credential bind")
        self.target = target
        self.target_id = target_id


class EffectsRepository:
    """Write operations for approval effects, using raw SQL for cross-DB tables."""

    @staticmethod
    def _owner_in_clause(
        owner_ids: list[str] | None,
        params: dict[str, object],
        *,
        column: str,
    ) -> str | None:
        """Build a parameterized ``AND <column> IN (...)`` owner-scope fragment.

        Mutates ``params`` in place with ``owner_0..owner_n`` bindings and returns
        the SQL fragment (with a leading space) to splice into the query. Returns:
        - ``""`` when ``owner_ids is None`` (org:admin — no owner restriction),
        - ``None`` when ``owner_ids`` is an empty list (sentinel: caller must
          short-circuit to "no rows" without running the query).

        Centralizing this keeps the two callers (``resolve_toolkits_for_api`` and
        ``toolkit_visible_to_owners``) from re-deriving the placeholder/param
        plumbing and guarantees both stay parameterized (no string interpolation
        of owner ids).
        """
        if owner_ids is None:
            return ""
        if not owner_ids:
            return None
        placeholders = ", ".join(f":owner_{i}" for i in range(len(owner_ids)))
        for i, oid in enumerate(owner_ids):
            params[f"owner_{i}"] = oid
        return f"  AND {column} IN ({placeholders}) "

    @staticmethod
    async def bind_credential_to_toolkit(
        session: AsyncSession,
        *,
        toolkit_id: str,
        credential_id: str,
        created_by: str,
    ) -> tuple[str, bool]:
        """Create a toolkit-credential binding idempotently.

        Returns (binding_id, already_bound).
        """
        existing = await ToolkitBindingRepository.get(session, toolkit_id, credential_id)
        if existing is not None:
            return existing.id, True

        try:
            async with session.begin_nested():
                binding = await ToolkitBindingRepository.bind(
                    session,
                    toolkit_id=toolkit_id,
                    credential_id=credential_id,
                    created_by=created_by,
                )
                return binding.id, False
        except IntegrityError as exc:
            # Two distinct IntegrityError causes converge here:
            #  - a concurrent insert of the SAME (toolkit, credential) pair — the
            #    unique constraint fired and the row now exists, so this is a
            #    benign idempotent hit; OR
            #  - a foreign-key violation because the toolkit/credential was
            #    deleted between pre-validation and this write (a same-/cross-tx
            #    TOCTOU race). Here no row exists, and previously a bare `assert`
            #    turned that into an AssertionError → HTTP 500.
            existing = await ToolkitBindingRepository.get(session, toolkit_id, credential_id)
            if existing is not None:
                return existing.id, True
            # Real FK violation: attribute it to the side that vanished so the
            # service can raise the matching 422. owner_ids=None is a pure
            # existence check (no owner restriction) — used only to attribute the
            # failure, never to authorize. Raising a neutral BindTargetMissingError
            # keeps sqlalchemy.exc out of the control service. See issue #649.
            toolkit_present = await EffectsRepository.toolkit_visible_to_owners(
                session, toolkit_id=toolkit_id, owner_ids=None
            )
            if toolkit_present:
                raise BindTargetMissingError("credential", credential_id) from exc
            raise BindTargetMissingError("toolkit", toolkit_id) from exc

    @staticmethod
    async def bind_agent_to_toolkit(
        session: AsyncSession,
        *,
        agent_id: str,
        toolkit_id: str,
        created_by: str,
    ) -> tuple[str, bool]:
        """Create an agent-toolkit binding idempotently via raw SQL.

        Returns (binding_id, already_bound).
        """
        binding_id = generate_ksuid("atb")
        result = await session.execute(
            text(
                "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id, created_by) "
                "VALUES (:id, :agent_id, :toolkit_id, :created_by) "
                "ON CONFLICT (agent_id, toolkit_id) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": binding_id,
                "agent_id": agent_id,
                "toolkit_id": toolkit_id,
                "created_by": created_by,
            },
        )
        inserted_id = result.scalar_one_or_none()
        if inserted_id is not None:
            await session.flush()
            return inserted_id, False

        existing = await session.execute(
            text(
                "SELECT id FROM agent_toolkit_bindings "
                "WHERE agent_id = :agent_id AND toolkit_id = :toolkit_id LIMIT 1"
            ),
            {"agent_id": agent_id, "toolkit_id": toolkit_id},
        )
        return existing.scalar_one(), True

    @staticmethod
    async def resolve_toolkits_for_api(
        session: AsyncSession,
        *,
        vendor: str,
        name: str | None,
        version: str | None,
        owner_ids: list[str] | None = None,
    ) -> list[str]:
        """Return toolkit IDs whose bound credential serves the given API identity.

        The toolkit↔API relationship runs through credentials
        (toolkit → credential → API). An empty ``name``/``version`` means "any";
        a NULL ``api_name``/``api_version`` on the credential means it covers all
        names/versions for the vendor.

        ``owner_ids`` scopes the result to toolkits owned by one of those ids
        (the deciding identity's own id and, when delegated, its parent). It is
        ``None`` only for an ``org:admin`` decider, who may resolve across all
        owners. Passing an empty list returns no candidates. Without this scope a
        reference (a *public* ``vendor/name``) could resolve to another owner's
        toolkit — see ``_resolve_toolkit_reference``.

        To avoid widening a name-specific reference into a vendor-wide
        (``api_name IS NULL``) credential, an **exact** name/version match is
        preferred: NULL-wildcard credentials only contribute when no exact match
        exists for the requested name.

        The ``vendor``/``name`` reference axes are canonicalized (slugified) here
        before binding, because stored rows are canonical (the credential service
        slugifies on write and the backfill migration re-slugs legacy rows). The
        shared SQL fragment compares bind params verbatim against those canonical
        rows, so a raw reference like ``GitHub.com`` must be slugified to
        ``github-com`` here or it would match nothing. ``version`` is trimmed but
        never slugified, matching ``canonical_credential_scope``.
        """
        params: dict[str, object] = {"vendor": slugify_api_field(vendor)}
        if name:
            params["name"] = slugify_api_field(name)
        if version:
            params["version"] = version.strip()
        name_scoped = bool(name)
        version_scoped = bool(version)
        owner_clause = EffectsRepository._owner_in_clause(owner_ids, params, column="tk.created_by")
        if owner_clause is None:
            return []

        # Shared coverage rule (see shared/models/api_identity.credential_coverage_where):
        # a wildcard *reference* axis (empty name/version at bind time) omits that
        # axis so it matches anything; a scoped axis matches NULL-wildcard or exact.
        coverage = credential_coverage_where(name_scoped=name_scoped, version_scoped=version_scoped)
        # Prefer an exact name match only when the reference names one — otherwise
        # there is no exactness to rank on.
        name_exact = "(CASE WHEN c.api_name = :name THEN 1 ELSE 0 END)" if name_scoped else "0"
        base_query = (
            f"SELECT DISTINCT tcb.toolkit_id, {name_exact} AS name_exact "
            "FROM toolkit_credential_bindings tcb "
            "JOIN credentials c ON c.id = tcb.credential_id "
            "JOIN toolkits tk ON tk.id = tcb.toolkit_id "
            f"WHERE {coverage} "
            f"{owner_clause}"
        )
        result = await session.execute(text(base_query), params)
        rows = result.all()
        # Prefer exact name matches: if any candidate matched the requested name
        # exactly, drop the NULL-wildcard (vendor-wide) matches so a named
        # reference never silently binds a broader catch-all toolkit.
        if any(row[1] for row in rows):
            return sorted({row[0] for row in rows if row[1]})
        return sorted({row[0] for row in rows})

    @staticmethod
    async def toolkit_visible_to_owners(
        session: AsyncSession,
        *,
        toolkit_id: str,
        owner_ids: list[str] | None,
    ) -> bool:
        """Return whether ``toolkit_id`` exists and is visible to the owners.

        ``owner_ids is None`` (an ``org:admin`` decider) sees every toolkit; an
        empty list sees none. Used to reject an explicit ``to_id``/``resource_id``
        ``toolkit:bind`` targeting a toolkit the decider does not own.
        """
        params: dict[str, object] = {"toolkit_id": toolkit_id}
        owner_clause = EffectsRepository._owner_in_clause(owner_ids, params, column="created_by")
        if owner_clause is None:
            return False
        result = await session.execute(
            text(f"SELECT 1 FROM toolkits WHERE id = :toolkit_id {owner_clause}LIMIT 1"),
            params,
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def grant_scope_to_actor(
        session: AsyncSession,
        *,
        actor_id: str,
        actor_type: str,
        scope: str,
        granted_by: str,
        created_by: str,
    ) -> bool:
        """Grant a scope to an actor idempotently via raw SQL.

        Returns True if created, False if already existed.
        """
        grant_id = generate_ksuid("asg")
        result = await session.execute(
            text(
                "INSERT INTO actor_scope_grants "
                "(id, actor_id, actor_type, scope, granted_by, created_by) "
                "VALUES (:id, :actor_id, :actor_type, :scope, :granted_by, :created_by) "
                "ON CONFLICT (actor_id, scope) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": grant_id,
                "actor_id": actor_id,
                "actor_type": actor_type,
                "scope": scope,
                "granted_by": granted_by,
                "created_by": created_by,
            },
        )
        inserted_id = result.scalar_one_or_none()
        if inserted_id is not None:
            await session.flush()
            return True
        return False
