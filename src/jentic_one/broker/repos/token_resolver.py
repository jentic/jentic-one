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
        stmt = text(
            "SELECT t.actor_id, t.actor_type, t.scopes, t.is_ephemeral,"
            " t.expires_at, t.revoked_at,"
            " CASE t.actor_type"
            "  WHEN 'agent' THEN"
            "   (SELECT a.status FROM agents a WHERE a.id = t.actor_id)"
            "  WHEN 'service_account' THEN"
            "   (SELECT sa.status FROM service_accounts sa WHERE sa.id = t.actor_id)"
            "  WHEN 'user' THEN"
            "   (SELECT CASE WHEN u.active THEN 'active' ELSE 'disabled' END"
            "    FROM users u WHERE u.id = t.actor_id)"
            "  ELSE 'active'"
            " END AS actor_status"
            " FROM access_tokens t"
            " WHERE t.token_hash = :token_hash"
        ).columns(is_ephemeral=Boolean)
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

        active = revoked_at is None and expires_at > now and row.actor_status == "active"
        return Identity(
            sub=row.actor_id,
            actor_type=ActorType(row.actor_type),
            permissions=permissions,
            expires_at=expires_at,
            active=active,
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
