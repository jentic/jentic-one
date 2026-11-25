"""Apply the same overlap and deadline checks to direct and connect-session binds."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, nullcontext

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.auth.expiry import binding_expired
from jentic_one.shared.auth.grant_policy import (
    GRANT_POLICY_COLUMNS,
    GrantPolicy,
    grant_deadline,
    uses_exhausted,
)
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.db.errors import DatabaseConsistencyError
from jentic_one.shared.db.locks import lock_agent_bindings, lock_credential_bindings
from jentic_one.shared.models.api_identity import CredentialScope


def scopes_overlap(a: CredentialScope, b: CredentialScope) -> bool:
    return (
        a.vendor == b.vendor
        and (a.name is None or b.name is None or a.name == b.name)
        and (a.version is None or b.version is None or a.version == b.version)
    )


_SCOPES = text(
    "SELECT id, api_vendor, api_name, api_version FROM credentials WHERE id IN :ids"
).bindparams(bindparam("ids", expanding=True))


@asynccontextmanager
async def guard_binding_write(
    admin: AsyncSession,
    control_db: DatabaseSession,
    *,
    agent_id: str,
    credential_id: str,
    suspended_error: Callable[[str], Exception],
    expired_error: Callable[[str], Exception],
    control_session: AsyncSession | None = None,
) -> AsyncIterator[None]:
    """Hold sibling bindings stable and check their deadlines on both sides of the write.

    All bind paths take the agent lock before locking a credential. Row locks
    serialize edits to existing bindings, including rows that currently have no
    policy. SQLite write transactions already hold the database write lock.
    """
    await lock_agent_bindings(admin, agent_id)
    await lock_credential_bindings(admin, credential_id)
    sql = (
        f"SELECT credential_id, suspended, {GRANT_POLICY_COLUMNS} FROM agent_credential_bindings "
        "WHERE agent_id = :agent_id ORDER BY credential_id"
    )
    if admin.get_bind().dialect.name == "postgresql":
        sql += " FOR UPDATE"
    rows = (await admin.execute(text(sql), {"agent_id": agent_id})).all()
    policies = [
        row
        for row in rows
        if row.suspended
        or row.expires_at is not None
        or row.max_idle_period is not None
        or row.max_quota is not None
    ]
    deadline = None
    if policies:
        async with (
            nullcontext(control_session)
            if control_session is not None
            else control_db.read_session()
        ) as control:
            ids = {credential_id, *(row.credential_id for row in policies)}
            scopes = {
                cid: CredentialScope(vendor, name, version)
                for cid, vendor, name, version in await control.execute(
                    _SCOPES, {"ids": sorted(ids)}
                )
            }
        if credential_id not in scopes:
            raise DatabaseConsistencyError("Binding target disappeared during approval")
        overlaps = [
            row
            for row in policies
            if row.credential_id in scopes
            and scopes_overlap(scopes[credential_id], scopes[row.credential_id])
        ]
        if any(row.suspended for row in overlaps):
            raise suspended_error(agent_id)
        # The earliest moment any overlapping binding's policy ends it (its end
        # date or idle limit), checked now and again after the write.
        deadline = min(
            (
                d
                for d in (grant_deadline(GrantPolicy.from_row(row)) for row in overlaps)
                if d is not None
            ),
            default=None,
        )
        # A used-up count has no deadline: it has simply ended.
        if binding_expired(deadline) or any(
            uses_exhausted(GrantPolicy.from_row(row)) for row in overlaps
        ):
            raise expired_error(agent_id)
    yield
    if binding_expired(deadline):
        raise expired_error(agent_id)
