"""In-process token resolver for the broker — queries the admin DB directly."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import Boolean, text

from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models import ActorType

ACCESS_TOKEN_PREFIX = "at_"


class InProcessTokenResolver:
    """Resolves opaque access tokens by querying the admin schema directly.

    Avoids importing from the `auth` module — uses a raw SQL query against the
    `access_tokens` table in the admin schema.
    """

    def __init__(self, admin_db: DatabaseSession) -> None:
        self._admin_db = admin_db

    async def resolve_access_token(self, token: str) -> Identity | None:
        if not token.startswith(ACCESS_TOKEN_PREFIX):
            return None

        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC)

        # actor_status re-checks the actor row on every resolve (#1136): a
        # disabled/archived agent's outstanding token must stop resolving as
        # active, bounded only by the broker's short verdict-cache TTL. NULL
        # (actor row missing) fails closed; the users table models "disabled"
        # as active=false, normalised here to the same status string.
        # oauth_client_active mirrors that guarantee for third-party clients
        # (kill-switch on deactivate must reach broker traffic, not just auth
        # surface). NULL indicates no issuing client, treated as active.
        # oauth_client_approved is the D7 approval gate at the same layer: a
        # pending/denied row — even one force-set active — must fail closed
        # here too, matching the auth-surface resolver.
        # oauth_grant_active is the phase-3a grant gate (§4.5-4.6): a
        # grant-channel token (t.oauth_grant_id set) whose consent grant is
        # missing or revoked fails closed — NULL (row gone) is as dead as
        # status != 'active'. oauth_grant_scopes carries the consent-time
        # scope set for the quadruple intersection below.
        stmt = text(
            "SELECT t.actor_id, t.actor_type, t.scopes, t.is_ephemeral,"
            " t.expires_at, t.revoked_at, t.oauth_client_id, t.oauth_grant_id,"
            " CASE t.actor_type"
            "  WHEN 'agent' THEN"
            "   (SELECT a.status FROM agents a WHERE a.id = t.actor_id)"
            "  WHEN 'service_account' THEN"
            "   (SELECT sa.status FROM service_accounts sa WHERE sa.id = t.actor_id)"
            "  WHEN 'user' THEN"
            "   (SELECT CASE WHEN u.active THEN 'active' ELSE 'disabled' END"
            "    FROM users u WHERE u.id = t.actor_id)"
            "  ELSE 'active'"
            " END AS actor_status,"
            " (SELECT c.active FROM oauth_clients c"
            "  WHERE c.client_id = t.oauth_client_id) AS oauth_client_active,"
            " (SELECT c.approval_status = 'approved' FROM oauth_clients c"
            "  WHERE c.client_id = t.oauth_client_id) AS oauth_client_approved,"
            " (SELECT c.allowed_scopes FROM oauth_clients c"
            "  WHERE c.client_id = t.oauth_client_id) AS oauth_client_allowed_scopes,"
            " (SELECT g.status = 'active' FROM oauth_client_grants g"
            "  WHERE g.id = t.oauth_grant_id) AS oauth_grant_active,"
            " (SELECT g.scopes FROM oauth_client_grants g"
            "  WHERE g.id = t.oauth_grant_id) AS oauth_grant_scopes"
            " FROM access_tokens t"
            " WHERE t.token_hash = :token_hash"
        ).columns(
            is_ephemeral=Boolean,
            oauth_client_active=Boolean,
            oauth_client_approved=Boolean,
            oauth_grant_active=Boolean,
        )
        async with self._admin_db.session() as session:
            result = await session.execute(stmt, {"token_hash": token_hash})
            row = result.one_or_none()

            if row is None:
                return None

            permissions = _as_scope_list(row.scopes)

            # Long-lived agent/SA tokens (is_ephemeral=False) resolve scopes live
            # from actor_scope_grants so scope edits take effect immediately.
            # Ephemeral minted tokens keep their downscoped snapshot; user tokens
            # do not draw scopes from actor_scope_grants.
            if not row.is_ephemeral and row.actor_type in (
                ActorType.AGENT.value,
                ActorType.SERVICE_ACCOUNT.value,
            ):
                grants = await session.execute(
                    text(
                        "SELECT scope FROM actor_scope_grants"
                        " WHERE actor_id = :actor_id AND actor_type = :actor_type"
                        " ORDER BY scope"
                    ),
                    {"actor_id": row.actor_id, "actor_type": row.actor_type},
                )
                permissions = [str(g.scope) for g in grants.all()]

        # SQLite returns DATETIME columns from a ``text()`` query as ISO strings
        # (Postgres returns aware ``datetime``); normalise so comparisons and the
        # downstream contract are dialect-independent.
        expires_at = _as_aware_datetime(row.expires_at)
        revoked_at = _as_aware_datetime(row.revoked_at) if row.revoked_at is not None else None

        oauth_client_id = row.oauth_client_id
        client_active = oauth_client_id is None or (
            bool(row.oauth_client_active) and bool(row.oauth_client_approved)
        )
        if oauth_client_id is not None:
            ceiling = _as_scope_list(row.oauth_client_allowed_scopes)
            if row.oauth_client_allowed_scopes is not None:
                permissions = [s for s in permissions if s in ceiling]

        oauth_grant_id = row.oauth_grant_id
        grant_active = oauth_grant_id is None or bool(row.oauth_grant_active)
        if oauth_grant_id is not None and row.oauth_grant_scopes is not None:
            grant_scopes = set(_as_scope_list(row.oauth_grant_scopes))
            permissions = [s for s in permissions if s in grant_scopes]

        active = (
            revoked_at is None
            and expires_at > now
            and row.actor_status == "active"
            and client_active
            and grant_active
        )
        return Identity(
            sub=row.actor_id,
            actor_type=ActorType(row.actor_type),
            permissions=permissions,
            expires_at=expires_at,
            active=active,
            oauth_client_id=oauth_client_id,
            oauth_grant_id=oauth_grant_id,
        )


def _as_aware_datetime(value: datetime | str) -> datetime:
    """Coerce a DB datetime value to a timezone-aware UTC ``datetime``."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _as_scope_list(value: object) -> list[str]:
    """Coerce a JSON scopes column to ``list[str]`` (SQLite may return a JSON string)."""
    if isinstance(value, list):
        return [str(s) for s in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(s) for s in parsed] if isinstance(parsed, list) else []
    return []
