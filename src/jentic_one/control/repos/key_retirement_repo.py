"""Repository for theme-5 Phase 4 key retirement — admin-DB writes, raw SQL.

The retirement job runs in the control module (toolkit keys, toolkits, and
permission rule sets live in the control DB) but creates the successor actor
in the **admin** DB (service account, its credential hash, its scope grant,
and its binding rows). The control module must not import admin ORM models,
so — like ``EffectsRepository`` — every admin-side statement here is raw SQL,
idempotent via natural keys (deterministic service-account name, ON CONFLICT
DO NOTHING on uniquely-constrained binding tables).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.db.ids import generate_ksuid

# The job's system actor — stamped as created_by/registered_by/granted_by so
# every migrated row is attributable to the retirement run, mirroring the
# Phase-6a flattening provenance convention.
SYSTEM_ACTOR = "system:theme5-key-retirement"

_FIND_USER_BY_ID = text("SELECT id FROM users WHERE id = :user_id")
_FIND_USER_BY_EMAIL = text("SELECT id FROM users WHERE lower(email) = lower(:email)")

_FIND_SERVICE_ACCOUNT_BY_NAME = text("SELECT id FROM service_accounts WHERE name = :name")

_INSERT_SERVICE_ACCOUNT = text(
    "INSERT INTO service_accounts"
    " (id, name, description, owner_id, registered_by, status, created_by)"
    " VALUES (:id, :name, :description, :owner_id, :registered_by, 'active', :created_by)"
)

_INSERT_SA_CREDENTIAL = text(
    "INSERT INTO service_account_credentials"
    " (id, service_account_id, api_key_hash, created_by)"
    " VALUES (:id, :service_account_id, :api_key_hash, :created_by)"
)

_INSERT_SCOPE_GRANT = text(
    "INSERT INTO actor_scope_grants"
    " (id, actor_id, actor_type, scope, granted_by, created_by)"
    " VALUES (:id, :actor_id, 'service_account', :scope, :granted_by, :created_by)"
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


_SET_SERVICE_ACCOUNT_STATUS = text(
    "UPDATE service_accounts SET status = :status WHERE id = :service_account_id"
)


class KeyRetirementRepository:
    """Admin-DB write operations for the toolkit-key retirement job."""

    @staticmethod
    async def set_service_account_status(
        session: AsyncSession, *, service_account_id: str, status: str
    ) -> None:
        """Cut (or restore) a migrated key's successor actor.

        Caller-less since the toolkit-key management surface is gone (theme-5
        Phase 5b). Kept with the rest of this repository until Phase 6b
        retires the toolkit tables, so operator tooling can still cut a
        successor account.
        """
        await session.execute(
            _SET_SERVICE_ACCOUNT_STATUS,
            {"service_account_id": service_account_id, "status": status},
        )

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
    async def find_service_account_by_name(session: AsyncSession, *, name: str) -> str | None:
        """The name is the job's deterministic idempotency key (one per key row)."""
        row = (await session.execute(_FIND_SERVICE_ACCOUNT_BY_NAME, {"name": name})).one_or_none()
        return str(row.id) if row is not None else None

    @staticmethod
    async def create_service_account(
        session: AsyncSession,
        *,
        name: str,
        description: str,
        owner_id: str,
        api_key_hash: str,
    ) -> str:
        """Create the successor actor: account row + credential hash + execute grant.

        The credential row carries the retiring key's SHA-256 lookup digest,
        so the unchanged ``jntc_live_`` plaintext keeps authenticating — as
        this service account — through ``ApiKeyResolver`` (which hashes the
        presented key and matches ``api_key_hash`` regardless of prefix).
        The grant is exactly ``capabilities:execute``: a toolkit key *was*
        the execute capability and nothing else — never the default agent
        scope set (theme-5 plan, toolkit-keys decision).
        """
        service_account_id = generate_ksuid("sva")
        await session.execute(
            _INSERT_SERVICE_ACCOUNT,
            {
                "id": service_account_id,
                "name": name,
                "description": description,
                "owner_id": owner_id,
                "registered_by": SYSTEM_ACTOR,
                "created_by": SYSTEM_ACTOR,
            },
        )
        await session.execute(
            _INSERT_SA_CREDENTIAL,
            {
                "id": generate_ksuid("sac"),
                "service_account_id": service_account_id,
                "api_key_hash": api_key_hash,
                "created_by": SYSTEM_ACTOR,
            },
        )
        await session.execute(
            _INSERT_SCOPE_GRANT,
            {
                "id": generate_ksuid("asg"),
                "actor_id": service_account_id,
                "scope": "capabilities:execute",
                "granted_by": SYSTEM_ACTOR,
                "created_by": SYSTEM_ACTOR,
            },
        )
        return service_account_id

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
