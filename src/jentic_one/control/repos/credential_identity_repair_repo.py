"""Cross-database repository operations for credential/API identity repair (#1079, #1388).

Control ORM models (``Credential``, ``AgentPermissionRule``, ``ConnectSession``)
are queried directly on ``control_db``. Cross-tier reads and compare-and-set
updates against ``registry_db`` and ``admin_db`` use dialect-portable
parameterized ``sqlalchemy.text()`` statements with explicit ``GUID()`` and
``CAST(:param AS VARCHAR)`` bindings so control never imports registry or admin
ORM models.

All write methods flush without committing; callers own transaction boundaries.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.connect_sessions import TARGET_KIND_API, ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.shared.db.types import GUID, UTCDateTime
from jentic_one.shared.db.utils import utcnow
from jentic_one.shared.models.credential_identity_repair import (
    OPEN_CONNECT_SESSION_STATES,
    ApiReferenceCounts,
    ConnectSessionSnapshot,
    CredentialReferenceCounts,
    CredentialSnapshot,
    RegistryApiSnapshot,
)

_LIST_APIS_SQL = text(
    "SELECT id, vendor, name, version, catalog_api_id FROM apis ORDER BY vendor, name, version, id"
)
_LIST_REVISION_SOURCE_URLS_SQL = text(
    "SELECT api_id, source_url FROM api_revisions WHERE source_url IS NOT NULL"
)
_GET_CATALOG_SNAPSHOT_SQL = text(
    "SELECT entries FROM catalog_snapshots WHERE id = 'catalog' LIMIT 1"
)
_COUNT_REVISIONS_BY_API_SQL = text(
    "SELECT api_id, COUNT(*) AS cnt FROM api_revisions GROUP BY api_id"
)
_COUNT_OPERATIONS_BY_API_SQL = text(
    "SELECT r.api_id AS api_id, COUNT(o.id) AS cnt "
    "FROM api_revisions r JOIN operations o ON o.revision_id = r.id "
    "GROUP BY r.api_id"
)
_COUNT_OVERLAYS_BY_API_SQL = text("SELECT api_id, COUNT(*) AS cnt FROM overlays GROUP BY api_id")
_COUNT_NOTES_BY_API_SQL = text(
    "SELECT resource_api_id AS api_id, COUNT(*) AS cnt "
    "FROM notes WHERE resource_api_id IS NOT NULL GROUP BY resource_api_id"
)

_CAS_UPDATE_API_SQL = text(
    "UPDATE apis "
    "SET vendor = CAST(:after_vendor AS VARCHAR), "
    "    name = CAST(:after_name AS VARCHAR), "
    "    version = CAST(:after_version AS VARCHAR), "
    "    catalog_api_id = CAST(:after_catalog_api_id AS VARCHAR), "
    "    updated_at = :updated_at "
    "WHERE id = :api_id "
    "  AND vendor = CAST(:before_vendor AS VARCHAR) "
    "  AND name = CAST(:before_name AS VARCHAR) "
    "  AND version = CAST(:before_version AS VARCHAR) "
    "  AND ((CAST(:before_catalog_api_id AS VARCHAR) IS NULL AND catalog_api_id IS NULL) "
    "       OR catalog_api_id = CAST(:before_catalog_api_id AS VARCHAR))"
).bindparams(
    bindparam("api_id", type_=GUID()),
    bindparam("updated_at", type_=UTCDateTime()),
)

_GET_API_BY_ID_SQL = text(
    "SELECT id, vendor, name, version, catalog_api_id FROM apis WHERE id = :api_id"
).bindparams(bindparam("api_id", type_=GUID()))

_GET_API_BY_TRIPLE_SQL = text(
    "SELECT id FROM apis "
    "WHERE vendor = CAST(:vendor AS VARCHAR) "
    "  AND name = CAST(:name AS VARCHAR) "
    "  AND version = CAST(:version AS VARCHAR) "
    "LIMIT 1"
)

_LIST_BINDINGS_SQL = text(
    "SELECT credential_id, agent_id, suspended FROM agent_credential_bindings"
)
_LIST_AGENT_CREDENTIALS_SQL = text("SELECT DISTINCT agent_id FROM agent_credentials")
_LIST_HELD_EXECUTIONS_BY_CRED_SQL = text(
    "SELECT credential_id, state, COUNT(*) AS cnt "
    "FROM held_executions GROUP BY credential_id, state"
)
_LIST_PENDING_HELD_BY_API_SQL = text(
    "SELECT api_vendor, api_name, api_version, COUNT(*) AS cnt "
    "FROM held_executions WHERE state = 'pending' "
    "GROUP BY api_vendor, api_name, api_version"
)
_COUNT_EXECUTIONS_BY_CRED_SQL = text(
    "SELECT credential_id, COUNT(*) AS cnt "
    "FROM execution_records WHERE credential_id IS NOT NULL GROUP BY credential_id"
)
_COUNT_EXECUTIONS_BY_API_SQL = text(
    "SELECT api_vendor, api_name, api_version, COUNT(*) AS cnt "
    "FROM execution_records "
    "WHERE api_vendor IS NOT NULL AND api_name IS NOT NULL AND api_version IS NOT NULL "
    "GROUP BY api_vendor, api_name, api_version"
)


def _norm_uuid_str(raw: object) -> str:
    if isinstance(raw, uuid.UUID):
        return str(raw)
    return str(uuid.UUID(str(raw)))


class CredentialIdentityRepairRepository:
    """Reads and compare-and-set updates across registry, control, and admin DBs."""

    @staticmethod
    async def list_registry_apis(session: AsyncSession) -> list[RegistryApiSnapshot]:
        rows = (await session.execute(_LIST_APIS_SQL)).all()
        return [
            RegistryApiSnapshot(
                id=_norm_uuid_str(row.id),
                vendor=str(row.vendor),
                name=str(row.name),
                version=str(row.version),
                catalog_api_id=row.catalog_api_id,
            )
            for row in rows
        ]

    @staticmethod
    async def list_revision_urls_by_api(session: AsyncSession) -> dict[str, list[str]]:
        rows = (await session.execute(_LIST_REVISION_SOURCE_URLS_SQL)).all()
        out: dict[str, list[str]] = {}
        for row in rows:
            api_id = _norm_uuid_str(row.api_id)
            out.setdefault(api_id, []).append(str(row.source_url))
        return out

    @staticmethod
    async def list_catalog_manifest_entries(session: AsyncSession) -> list[dict[str, Any]]:
        row = (await session.execute(_GET_CATALOG_SNAPSHOT_SQL)).first()
        if row is None or row.entries is None:
            return []
        entries = json.loads(row.entries) if isinstance(row.entries, str) else row.entries
        if not isinstance(entries, list):
            return []
        return [e for e in entries if isinstance(e, dict)]

    @staticmethod
    async def registry_reference_counts(
        session: AsyncSession,
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
        """Return ``(revisions, operations, overlays, notes)`` counts keyed by ``api_id``."""
        revisions = {
            _norm_uuid_str(r.api_id): int(r.cnt)
            for r in (await session.execute(_COUNT_REVISIONS_BY_API_SQL)).all()
        }
        operations = {
            _norm_uuid_str(r.api_id): int(r.cnt)
            for r in (await session.execute(_COUNT_OPERATIONS_BY_API_SQL)).all()
        }
        overlays = {
            _norm_uuid_str(r.api_id): int(r.cnt)
            for r in (await session.execute(_COUNT_OVERLAYS_BY_API_SQL)).all()
        }
        notes = {
            _norm_uuid_str(r.api_id): int(r.cnt)
            for r in (await session.execute(_COUNT_NOTES_BY_API_SQL)).all()
        }
        return revisions, operations, overlays, notes

    @staticmethod
    async def list_credentials(session: AsyncSession) -> list[CredentialSnapshot]:
        rows = (
            await session.execute(
                select(
                    Credential.id,
                    Credential.name,
                    Credential.created_by,
                    Credential.api_vendor,
                    Credential.api_name,
                    Credential.api_version,
                    Credential.catalog_api_id,
                ).order_by(Credential.id)
            )
        ).all()
        return [
            CredentialSnapshot(
                id=row.id,
                name=row.name,
                created_by=row.created_by,
                api_vendor=row.api_vendor,
                api_name=row.api_name,
                api_version=row.api_version,
                catalog_api_id=row.catalog_api_id,
            )
            for row in rows
        ]

    @staticmethod
    async def list_api_connect_sessions(session: AsyncSession) -> list[ConnectSessionSnapshot]:
        rows = (
            await session.execute(
                select(
                    ConnectSession.id,
                    ConnectSession.credential_id,
                    ConnectSession.agent_id,
                    ConnectSession.initiator_actor_id,
                    ConnectSession.state,
                    ConnectSession.target_kind,
                    ConnectSession.vendor,
                    ConnectSession.api_name,
                    ConnectSession.api_version,
                )
                .where(ConnectSession.target_kind == TARGET_KIND_API)
                .order_by(ConnectSession.id)
            )
        ).all()
        return [
            ConnectSessionSnapshot(
                id=row.id,
                credential_id=row.credential_id,
                agent_id=row.agent_id,
                initiator_actor_id=row.initiator_actor_id,
                state=row.state,
                target_kind=row.target_kind,
                vendor=row.vendor,
                api_name=row.api_name,
                api_version=row.api_version,
            )
            for row in rows
        ]

    @staticmethod
    async def list_open_session_ids(
        session: AsyncSession, *, vendor: str, name: str | None, version: str | None
    ) -> list[str]:
        rows = (
            await session.execute(
                select(ConnectSession.id).where(
                    ConnectSession.target_kind == TARGET_KIND_API,
                    ConnectSession.state.in_(OPEN_CONNECT_SESSION_STATES),
                    ConnectSession.vendor == vendor,
                    ConnectSession.api_name.is_(None)
                    if name is None
                    else ConnectSession.api_name == name,
                    ConnectSession.api_version.is_(None)
                    if version is None
                    else ConnectSession.api_version == version,
                )
            )
        ).all()
        return [str(row.id) for row in rows]

    @staticmethod
    async def control_reference_counts(
        session: AsyncSession,
    ) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        """Return ``(permission_rules, open_sessions, closed_sessions)`` by ``credential_id``."""
        rule_rows = (
            await session.execute(
                select(
                    AgentPermissionRule.credential_id,
                    func.count(AgentPermissionRule.id),
                ).group_by(AgentPermissionRule.credential_id)
            )
        ).all()
        rule_counts = {cid: int(cnt) for cid, cnt in rule_rows}

        cs_rows = (
            await session.execute(
                select(
                    ConnectSession.credential_id,
                    ConnectSession.state,
                    func.count(ConnectSession.id),
                ).group_by(ConnectSession.credential_id, ConnectSession.state)
            )
        ).all()
        open_counts: dict[str, int] = {}
        closed_counts: dict[str, int] = {}
        for cid, state, cnt in cs_rows:
            if state in OPEN_CONNECT_SESSION_STATES:
                open_counts[cid] = open_counts.get(cid, 0) + int(cnt)
            else:
                closed_counts[cid] = closed_counts.get(cid, 0) + int(cnt)
        return rule_counts, open_counts, closed_counts

    @staticmethod
    async def load_full_reference_inventory(
        *,
        registry_session: AsyncSession,
        control_session: AsyncSession,
        admin_session: AsyncSession | None,
        apis: Sequence[RegistryApiSnapshot],
        credentials: Sequence[CredentialSnapshot],
    ) -> tuple[
        dict[str, ApiReferenceCounts],
        dict[str, CredentialReferenceCounts],
        dict[str, frozenset[str]],
    ]:
        """Assemble cross-DB reference counts and active bound-agent sets."""
        (
            rev_c,
            op_c,
            ovr_c,
            note_c,
        ) = await CredentialIdentityRepairRepository.registry_reference_counts(registry_session)
        (
            rule_c,
            open_cs_c,
            closed_cs_c,
        ) = await CredentialIdentityRepairRepository.control_reference_counts(control_session)

        active_bindings_c: dict[str, int] = {}
        suspended_bindings_c: dict[str, int] = {}
        bound_agent_creds_c: dict[str, int] = {}
        active_agents_by_cred: dict[str, set[str]] = {}
        all_agents_by_cred: dict[str, set[str]] = {}
        pending_held_by_cred: dict[str, int] = {}
        terminal_held_by_cred: dict[str, int] = {}
        pending_held_by_api: dict[tuple[str, str, str], int] = {}
        exec_by_cred: dict[str, int] = {}
        exec_by_api: dict[tuple[str, str, str], int] = {}

        if admin_session is not None:
            binding_rows = (await admin_session.execute(_LIST_BINDINGS_SQL)).all()
            agents_with_creds = {
                str(r.agent_id)
                for r in (await admin_session.execute(_LIST_AGENT_CREDENTIALS_SQL)).all()
            }
            for r in binding_rows:
                cid = str(r.credential_id)
                aid = str(r.agent_id)
                all_agents_by_cred.setdefault(cid, set()).add(aid)
                if bool(r.suspended):
                    suspended_bindings_c[cid] = suspended_bindings_c.get(cid, 0) + 1
                else:
                    active_bindings_c[cid] = active_bindings_c.get(cid, 0) + 1
                    active_agents_by_cred.setdefault(cid, set()).add(aid)
            for cid, aids in all_agents_by_cred.items():
                bound_agent_creds_c[cid] = len(aids & agents_with_creds)

            for r in (await admin_session.execute(_LIST_HELD_EXECUTIONS_BY_CRED_SQL)).all():
                cid = str(r.credential_id)
                if str(r.state) == "pending":
                    pending_held_by_cred[cid] = pending_held_by_cred.get(cid, 0) + int(r.cnt)
                else:
                    terminal_held_by_cred[cid] = terminal_held_by_cred.get(cid, 0) + int(r.cnt)

            for r in (await admin_session.execute(_LIST_PENDING_HELD_BY_API_SQL)).all():
                pending_held_by_api[(str(r.api_vendor), str(r.api_name), str(r.api_version))] = int(
                    r.cnt
                )

            for r in (await admin_session.execute(_COUNT_EXECUTIONS_BY_CRED_SQL)).all():
                exec_by_cred[str(r.credential_id)] = int(r.cnt)

            for r in (await admin_session.execute(_COUNT_EXECUTIONS_BY_API_SQL)).all():
                exec_by_api[(str(r.api_vendor), str(r.api_name), str(r.api_version))] = int(r.cnt)

        api_counts = {
            a.id: ApiReferenceCounts(
                revisions=rev_c.get(a.id, 0),
                operations=op_c.get(a.id, 0),
                overlays=ovr_c.get(a.id, 0),
                notes=note_c.get(a.id, 0),
                pending_held_executions=pending_held_by_api.get((a.vendor, a.name, a.version), 0),
                execution_records=exec_by_api.get((a.vendor, a.name, a.version), 0),
            )
            for a in apis
        }
        cred_counts = {
            c.id: CredentialReferenceCounts(
                active_bindings=active_bindings_c.get(c.id, 0),
                suspended_bindings=suspended_bindings_c.get(c.id, 0),
                bound_agent_credentials=bound_agent_creds_c.get(c.id, 0),
                permission_rules=rule_c.get(c.id, 0),
                open_connect_sessions=open_cs_c.get(c.id, 0),
                closed_connect_sessions=closed_cs_c.get(c.id, 0),
                pending_held_executions=pending_held_by_cred.get(c.id, 0),
                terminal_held_executions=terminal_held_by_cred.get(c.id, 0),
                execution_records=exec_by_cred.get(c.id, 0),
            )
            for c in credentials
        }
        frozen_active = {cid: frozenset(aids) for cid, aids in active_agents_by_cred.items()}
        return api_counts, cred_counts, frozen_active

    @staticmethod
    async def cas_update_api(
        session: AsyncSession,
        *,
        api_id: str,
        before: Mapping[str, str | None],
        after: Mapping[str, str | None],
    ) -> bool:
        result = await session.execute(
            _CAS_UPDATE_API_SQL,
            {
                "api_id": uuid.UUID(api_id),
                "before_vendor": before["vendor"],
                "before_name": before["name"],
                "before_version": before["version"],
                "before_catalog_api_id": before["catalog_api_id"],
                "after_vendor": after["vendor"],
                "after_name": after["name"],
                "after_version": after["version"],
                "after_catalog_api_id": after["catalog_api_id"],
                "updated_at": utcnow(),
            },
        )
        await session.flush()
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def get_api_snapshot(session: AsyncSession, api_id: str) -> RegistryApiSnapshot | None:
        row = (await session.execute(_GET_API_BY_ID_SQL, {"api_id": uuid.UUID(api_id)})).first()
        if row is None:
            return None
        return RegistryApiSnapshot(
            id=_norm_uuid_str(row.id),
            vendor=str(row.vendor),
            name=str(row.name),
            version=str(row.version),
            catalog_api_id=row.catalog_api_id,
        )

    @staticmethod
    async def find_api_id_by_triple(
        session: AsyncSession, *, vendor: str, name: str, version: str
    ) -> str | None:
        row = (
            await session.execute(
                _GET_API_BY_TRIPLE_SQL, {"vendor": vendor, "name": name, "version": version}
            )
        ).first()
        return _norm_uuid_str(row.id) if row is not None else None

    @staticmethod
    async def cas_update_credential(
        session: AsyncSession,
        *,
        credential_id: str,
        before: Mapping[str, str | None],
        after: Mapping[str, str | None],
    ) -> bool:
        stmt = (
            update(Credential)
            .where(
                Credential.id == credential_id,
                Credential.api_vendor == before["api_vendor"],
                Credential.api_name.is_(None)
                if before["api_name"] is None
                else Credential.api_name == before["api_name"],
                Credential.api_version.is_(None)
                if before["api_version"] is None
                else Credential.api_version == before["api_version"],
                Credential.catalog_api_id.is_(None)
                if before["catalog_api_id"] is None
                else Credential.catalog_api_id == before["catalog_api_id"],
            )
            .values(
                api_vendor=after["api_vendor"],
                api_name=after["api_name"],
                api_version=after["api_version"],
                catalog_api_id=after["catalog_api_id"],
                updated_at=utcnow(),
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def get_credential_snapshot(
        session: AsyncSession, credential_id: str
    ) -> CredentialSnapshot | None:
        row = (
            await session.execute(
                select(
                    Credential.id,
                    Credential.name,
                    Credential.created_by,
                    Credential.api_vendor,
                    Credential.api_name,
                    Credential.api_version,
                    Credential.catalog_api_id,
                ).where(Credential.id == credential_id)
            )
        ).first()
        if row is None:
            return None
        return CredentialSnapshot(
            id=row.id,
            name=row.name,
            created_by=row.created_by,
            api_vendor=row.api_vendor,
            api_name=row.api_name,
            api_version=row.api_version,
            catalog_api_id=row.catalog_api_id,
        )

    @staticmethod
    async def cas_update_connect_session(
        session: AsyncSession,
        *,
        session_id: str,
        before: Mapping[str, str | None],
        after: Mapping[str, str | None],
    ) -> bool:
        stmt = (
            update(ConnectSession)
            .where(
                ConnectSession.id == session_id,
                ConnectSession.vendor == before["vendor"],
                ConnectSession.api_name.is_(None)
                if before["api_name"] is None
                else ConnectSession.api_name == before["api_name"],
                ConnectSession.api_version.is_(None)
                if before["api_version"] is None
                else ConnectSession.api_version == before["api_version"],
            )
            .values(
                vendor=after["vendor"],
                api_name=after["api_name"],
                api_version=after["api_version"],
                updated_at=utcnow(),
            )
        )
        result = await session.execute(stmt)
        await session.flush()
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def get_connect_session_snapshot(
        session: AsyncSession, session_id: str
    ) -> ConnectSessionSnapshot | None:
        row = (
            await session.execute(
                select(
                    ConnectSession.id,
                    ConnectSession.credential_id,
                    ConnectSession.agent_id,
                    ConnectSession.initiator_actor_id,
                    ConnectSession.state,
                    ConnectSession.target_kind,
                    ConnectSession.vendor,
                    ConnectSession.api_name,
                    ConnectSession.api_version,
                ).where(ConnectSession.id == session_id)
            )
        ).first()
        if row is None:
            return None
        return ConnectSessionSnapshot(
            id=row.id,
            credential_id=row.credential_id,
            agent_id=row.agent_id,
            initiator_actor_id=row.initiator_actor_id,
            state=row.state,
            target_kind=row.target_kind,
            vendor=row.vendor,
            api_name=row.api_name,
            api_version=row.api_version,
        )
