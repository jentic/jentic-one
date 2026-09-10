"""Repository for cross-database effect operations.

Uses raw SQL for admin-DB reads/writes to avoid importing admin ORM models —
the control module must not import from the admin module. Uses ON CONFLICT DO
NOTHING for idempotent inserts without requiring rollback.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models.api_identity import credential_coverage_where, slugify_api_field


class EffectsRepository:
    """Read/write operations for approval effects, using raw SQL for cross-DB tables."""

    @staticmethod
    def _in_clause(
        values: list[str] | None,
        params: dict[str, object],
        *,
        prefix: str,
    ) -> str | None:
        """Build a parameterized ``<p>_0, <p>_1, …`` placeholder list.

        Mutates ``params`` in place and returns the comma-joined placeholder
        fragment. Returns:

        - ``""`` when ``values is None`` (no restriction — caller omits the
          clause entirely),
        - ``None`` when ``values`` is an empty list (sentinel: caller must
          short-circuit to "no rows" without running the query).
        """
        if values is None:
            return ""
        if not values:
            return None
        placeholders = ", ".join(f":{prefix}_{i}" for i in range(len(values)))
        for i, val in enumerate(values):
            params[f"{prefix}_{i}"] = val
        return placeholders

    @staticmethod
    async def resolve_credentials_for_api(
        session: AsyncSession,
        *,
        vendor: str,
        name: str | None,
        version: str | None,
        owner_ids: list[str] | None = None,
        bound_credential_ids: list[str] | None = None,
    ) -> list[str]:
        """Return credential IDs covering the given API identity, decider-visible.

        Theme-5 Phase 3 successor of the toolkit-era ``resolve_toolkits_for_api``
        — the join through ``toolkit_credential_bindings`` is gone; the
        candidate axis is the credential itself. An empty ``name``/``version``
        reference axis means "any"; a NULL ``api_name``/``api_version`` on the
        credential means it covers all names/versions for the vendor.

        **Owner axis (hard problem 8):** the toolkit era scoped candidates by
        the *toolkit's* owner (``tk.created_by``). Dropping the join must not
        silently narrow resolution to "credentials I own" — a decider may
        legitimately govern another owner's credential through an agent they
        own that is already bound to it (the binding-widened visibility of
        ``scoping/filters._binding_visibility_clause``). A credential is a
        candidate when the decider **owns it** (``created_by IN owner_ids``)
        **or** it appears in ``bound_credential_ids`` — the ids of credentials
        bound to agents the decider owns, resolved by the caller from the
        admin DB (`list_bound_credential_ids_for_owned_agents`) and pushed
        down here as a plain id list (the cross-DB seam of hard problem 9; no
        admin table is referenced in this query).

        ``owner_ids`` is ``None`` only for an ``org:admin`` decider, who may
        resolve across all owners (``bound_credential_ids`` is then irrelevant).
        Passing an empty list for both returns no candidates.

        To avoid widening a name-specific reference into a vendor-wide
        (``api_name IS NULL``) credential, an **exact** name/version match is
        preferred: NULL-wildcard credentials only contribute when no exact
        match exists for the requested name (#775).

        The ``vendor``/``name`` reference axes are canonicalized (slugified)
        here before binding, because stored rows are canonical (the credential
        service slugifies on write and the backfill migration re-slugs legacy
        rows) — a raw reference like ``GitHub.com`` must be slugified to
        ``github-com`` or it would match nothing (#656). ``version`` is trimmed
        but never slugified, matching ``canonical_credential_scope``.
        """
        params: dict[str, object] = {"vendor": slugify_api_field(vendor)}
        if name:
            params["name"] = slugify_api_field(name)
        if version:
            params["version"] = version.strip()
        name_scoped = bool(name)
        version_scoped = bool(version)

        visibility = ""
        if owner_ids is not None:
            owners = EffectsRepository._in_clause(owner_ids, params, prefix="owner")
            bound = EffectsRepository._in_clause(
                bound_credential_ids if bound_credential_ids is not None else [],
                params,
                prefix="bound",
            )
            clauses: list[str] = []
            if owners is not None:
                clauses.append(f"c.created_by IN ({owners})")
            if bound is not None:
                clauses.append(f"c.id IN ({bound})")
            if not clauses:
                return []
            visibility = f" AND ({' OR '.join(clauses)}) "

        # Shared coverage rule (see shared/models/api_identity.credential_coverage_where):
        # a wildcard *reference* axis (empty name/version at bind time) omits that
        # axis so it matches anything; a scoped axis matches NULL-wildcard or exact.
        coverage = credential_coverage_where(name_scoped=name_scoped, version_scoped=version_scoped)
        # Prefer an exact name match only when the reference names one — otherwise
        # there is no exactness to rank on.
        name_exact = "(CASE WHEN c.api_name = :name THEN 1 ELSE 0 END)" if name_scoped else "0"
        base_query = (
            f"SELECT DISTINCT c.id, {name_exact} AS name_exact "
            "FROM credentials c "
            f"WHERE {coverage} "
            f"{visibility}"
        )
        result = await session.execute(text(base_query), params)
        rows = result.all()
        # Prefer exact name matches: if any candidate matched the requested name
        # exactly, drop the NULL-wildcard (vendor-wide) matches so a named
        # reference never silently binds a broader catch-all credential.
        if any(row[1] for row in rows):
            return sorted({row[0] for row in rows if row[1]})
        return sorted({row[0] for row in rows})

    @staticmethod
    async def list_bound_credential_ids_for_owned_agents(
        session: AsyncSession,
        *,
        owner_ids: list[str],
    ) -> list[str]:
        """Credential ids bound to any agent owned by one of ``owner_ids`` (admin DB).

        The push-down half of the hard-problem-8 owner axis: run on an **admin**
        session, its result feeds ``resolve_credentials_for_api``'s
        ``bound_credential_ids`` so the control-DB query never references an
        admin table (cross-DB seam, hard problem 9; precedent
        ``PrerequisiteRepository.list_credential_ids_for_agent``). Suspended
        bindings still count — suspension is a broker-derivation cut-off, not a
        governance-ownership change; the decider who owns the agent can still
        see (and re-govern) the credential the binding names.
        """
        if not owner_ids:
            return []
        params: dict[str, object] = {}
        owners = EffectsRepository._in_clause(owner_ids, params, prefix="owner")
        result = await session.execute(
            text(
                "SELECT DISTINCT acb.credential_id "
                "FROM agent_credential_bindings acb "
                "JOIN agents a ON a.id = acb.agent_id "
                f"WHERE a.owner_id IN ({owners})"
            ),
            params,
        )
        return [str(row[0]) for row in result.fetchall()]

    @staticmethod
    async def bind_agent_to_credential(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        rule_set_id: str | None,
        created_by: str,
    ) -> tuple[str, bool]:
        """Create a direct agent↔credential binding idempotently via raw SQL (admin DB).

        Returns ``(binding_id, already_bound)``. An existing binding is left
        untouched (its ``rule_set_id``/``suspended`` state is authoritative) —
        the retry/reconcile path must converge without clobbering operator
        edits made between attempts.
        """
        binding_id = generate_ksuid("acb")
        result = await session.execute(
            text(
                "INSERT INTO agent_credential_bindings "
                "(id, agent_id, credential_id, rule_set_id, created_by) "
                "VALUES (:id, :agent_id, :credential_id, :rule_set_id, :created_by) "
                "ON CONFLICT (agent_id, credential_id) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": binding_id,
                "agent_id": agent_id,
                "credential_id": credential_id,
                "rule_set_id": rule_set_id,
                "created_by": created_by,
            },
        )
        inserted_id = result.scalar_one_or_none()
        if inserted_id is not None:
            await session.flush()
            return inserted_id, False

        existing = await session.execute(
            text(
                "SELECT id FROM agent_credential_bindings "
                "WHERE agent_id = :agent_id AND credential_id = :credential_id LIMIT 1"
            ),
            {"agent_id": agent_id, "credential_id": credential_id},
        )
        return existing.scalar_one(), True

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
