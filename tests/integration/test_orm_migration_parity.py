"""Integration tests verifying ORM metadata matches the migrated database schema."""

from __future__ import annotations

import base64
import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from pydantic import SecretStr
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, delete, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateTable

import jentic_one.wiring  # noqa: F401  # ensures all ORM models are registered
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.admin.core.schema.provider_configs import ProviderConfigRecord
from jentic_one.control.core.schema.authorization_code_app_registration_details import (
    AuthorizationCodeAppRegistrationDetails,
)
from jentic_one.control.core.schema.basic_credentials import BasicCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential
from jentic_one.control.core.schema.token_value_credentials import TokenValueCredential
from jentic_one.migrations.targets import DB_TARGETS
from jentic_one.shared.config import AppConfig, EncryptionConfig, EncryptionKey
from jentic_one.shared.crypto import (
    EncryptedStorageRotator,
    EncryptionService,
    KeyRetirementError,
    KeyRotationReport,
    assert_only_active_key,
    rotate_encrypted_storage,
    verify_key_retired,
)
from jentic_one.shared.db.session import DatabaseSession, get_database_url

pytestmark = pytest.mark.integration

_ALLOWLIST_PATH = Path(__file__).with_name("orm_migration_parity_allowlist.yaml")
_SURFACES: tuple[str, ...] = ("registry", "control", "admin")

NormalizedDiff = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    """Single allowlisted ORM-vs-migration difference."""

    backend: str
    surface: str
    op: str
    table: str
    name: str
    reason: str

    @property
    def key(self) -> NormalizedDiff:
        return (self.op, self.table, self.name)


def _constraint_object_name(constraint: Any) -> str:
    """Return a stable constraint identifier (explicit name or comma-joined column names)."""
    if getattr(constraint, "name", None):
        return str(constraint.name)
    columns = [str(col.name) for col in getattr(constraint, "columns", ())]
    if columns:
        return ",".join(columns)
    return type(constraint).__name__


def _normalize_single_diff(item: tuple[Any, ...]) -> NormalizedDiff:
    """Convert one raw ``compare_metadata`` tuple into ``(op, table, column_or_index_name)``."""
    op = str(item[0])
    if op in ("add_table", "remove_table", "add_table_comment", "remove_table_comment"):
        table_obj = item[1]
        table_name = str(table_obj.name)
        return (op, table_name, table_name)
    if op in ("add_column", "remove_column"):
        table_name = str(item[2])
        col_obj = item[3]
        return (op, table_name, str(col_obj.name))
    if op in ("add_index", "remove_index"):
        idx_obj = item[1]
        table_name = str(idx_obj.table.name) if idx_obj.table is not None else ""
        return (op, table_name, str(idx_obj.name))
    if op in ("add_constraint", "remove_constraint", "add_fk", "remove_fk"):
        constraint_obj = item[1]
        table_name = str(constraint_obj.table.name) if constraint_obj.table is not None else ""
        return (op, table_name, _constraint_object_name(constraint_obj))
    if op in ("modify_type", "modify_nullable", "modify_default", "modify_comment"):
        table_name = str(item[2])
        col_name = str(item[3])
        return (op, table_name, col_name)
    return (op, str(item[1]) if len(item) > 1 else "", repr(item[2:]))


def _normalize_diffs(
    raw_diffs: list[Any],
    *,
    version_table: str = "alembic_version",
) -> list[NormalizedDiff]:
    """Flatten and normalise ``compare_metadata`` output into ``(op, table, name)`` tuples."""
    normalized: list[NormalizedDiff] = []
    for entry in raw_diffs:
        items = entry if isinstance(entry, list) else [entry]
        for sub in items:
            if not isinstance(sub, tuple):
                continue
            norm = _normalize_single_diff(sub)
            if norm == ("remove_table", version_table, version_table):
                continue
            normalized.append(norm)
    return sorted(normalized)


def _load_allowlist() -> list[AllowlistEntry]:
    """Load and validate ``orm_migration_parity_allowlist.yaml``."""
    raw = yaml.safe_load(_ALLOWLIST_PATH.read_text(encoding="utf-8")) or {}
    raw_entries = raw.get("entries", [])
    assert isinstance(raw_entries, list), "allowlist 'entries' must be a list"
    entries: list[AllowlistEntry] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for idx, item in enumerate(raw_entries):
        assert isinstance(item, dict), f"allowlist entry #{idx} must be a mapping"
        backend = str(item.get("backend", "")).strip()
        surface = str(item.get("surface", "")).strip()
        op = str(item.get("op", "")).strip()
        table = str(item.get("table", "")).strip()
        name = str(item.get("name", "")).strip()
        reason = str(item.get("reason", "")).strip()
        assert backend in ("sqlite", "postgres"), (
            f"allowlist entry #{idx} has invalid backend {backend!r}"
        )
        assert surface in _SURFACES, f"allowlist entry #{idx} has invalid surface {surface!r}"
        assert op and "*" not in op, (
            f"allowlist entry #{idx} must specify an exact op without wildcards"
        )
        assert table and "*" not in table, (
            f"allowlist entry #{idx} must specify an exact table without wildcards"
        )
        assert name and "*" not in name, (
            f"allowlist entry #{idx} must specify an exact object name without wildcards"
        )
        assert reason, (
            f"allowlist entry #{idx} ({backend}/{surface}/{op}/{table}.{name}) lacks a reason"
        )
        full_key = (backend, surface, op, table, name)
        assert full_key not in seen, f"duplicate allowlist entry: {full_key}"
        seen.add(full_key)
        entries.append(
            AllowlistEntry(
                backend=backend,
                surface=surface,
                op=op,
                table=table,
                name=name,
                reason=reason,
            )
        )
    return entries


async def _collect_surface_diffs(
    integration_config: AppConfig,
    surface: str,
) -> tuple[str, list[NormalizedDiff]]:
    """Run ``compare_metadata`` against the migrated database for ``surface``."""
    target = DB_TARGETS[surface]
    db_cfg = getattr(integration_config.databases, surface)
    backend = db_cfg.backend
    pg = backend == "postgres"
    schema = db_cfg.schema_name
    url = get_database_url(db_cfg)

    if pg:
        engine = create_async_engine(
            url,
            poolclass=pool.NullPool,
            connect_args={"server_settings": {"search_path": f"{schema},public"}},
        )
    else:
        engine = create_async_engine(url, poolclass=pool.NullPool)

    def _include_name(
        name: str | None,
        type_: NameFilterType,
        parent_names: NameFilterParentNames,
    ) -> bool:
        if type_ == "schema":
            return name in (None, schema)
        return not (type_ == "table" and name == target.version_table)

    def _compare(sync_conn: Connection) -> list[Any]:
        mc = MigrationContext.configure(
            sync_conn,
            opts={
                "compare_type": True,
                "version_table": target.version_table,
                "version_table_schema": schema if pg else None,
                "include_schemas": pg,
                "include_name": _include_name,
                "render_as_batch": not pg,
            },
        )
        return list(compare_metadata(mc, target.metadata))

    try:
        async with engine.connect() as conn:
            raw_diffs = await conn.run_sync(_compare)
    finally:
        await engine.dispose()

    return backend, _normalize_diffs(raw_diffs, version_table=target.version_table)


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", _SURFACES)
async def test_orm_metadata_matches_migrated_schema(
    integration_config: AppConfig,
    surface: str,
) -> None:
    """Every ORM metadata target matches the migrated schema outside explicit allowlist entries."""
    backend, observed = await _collect_surface_diffs(integration_config, surface)
    allowlisted = {
        entry.key
        for entry in _load_allowlist()
        if entry.backend == backend and entry.surface == surface
    }
    unexpected = [diff for diff in observed if diff not in allowlisted]
    formatted = "\n".join(f"  - ({op}, {table}, {name})" for op, table, name in unexpected)
    assert unexpected == [], (
        f"Unexpected ORM vs migrated schema differences on {backend}/{surface}:\n{formatted}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", _SURFACES)
async def test_parity_allowlist_entries_are_still_needed(
    integration_config: AppConfig,
    surface: str,
) -> None:
    """Every allowlist entry for the active backend/surface must match an observed difference."""
    backend, observed = await _collect_surface_diffs(integration_config, surface)
    observed_set = set(observed)
    entries = [
        entry
        for entry in _load_allowlist()
        if entry.backend == backend and entry.surface == surface
    ]
    stale = [entry for entry in entries if entry.key not in observed_set]
    formatted = "\n".join(f"  - ({e.op}, {e.table}, {e.name}): {e.reason}" for e in stale)
    assert stale == [], (
        f"Stale allowlist entries on {backend}/{surface} (remove from "
        f"{_ALLOWLIST_PATH.name}):\n{formatted}"
    )


def test_parity_check_detects_a_missing_column(tmp_path: Path) -> None:
    """Negative test: ``compare_metadata`` + normaliser flags a missing DB column."""
    db_file = tmp_path / "negative_parity.db"
    engine = create_engine(f"sqlite:///{db_file}")
    try:
        db_meta = MetaData()
        partial_table = Table(
            "widgets",
            db_meta,
            Column("id", String(30), primary_key=True),
        )
        full_meta = MetaData()
        Table(
            "widgets",
            full_meta,
            Column("id", String(30), primary_key=True),
            Column("count", Integer(), nullable=False),
        )
        with engine.begin() as conn:
            conn.execute(CreateTable(partial_table))
        with engine.connect() as conn:
            mc = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = _normalize_diffs(list(compare_metadata(mc, full_meta)))
        assert diffs == [("add_column", "widgets", "count")]
    finally:
        engine.dispose()


def _make_key(key_id: str) -> EncryptionKey:
    raw = f"{key_id}-material-padding-32-bytes!!".encode()[:32].ljust(32, b"0")
    return EncryptionKey(id=key_id, material=SecretStr(base64.b64encode(raw).decode()))


async def _clean_rotation_tables(
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
) -> None:
    """Delete all rows in the encrypted families' tables so rotation counts are isolated."""
    async with control_db.transaction() as session:
        await session.execute(delete(CustomerAPIKey))
        await session.execute(delete(TokenValueCredential))
        await session.execute(delete(BasicCredential))
        await session.execute(delete(Sigv4Credential))
        await session.execute(delete(DeviceAuthorizationCredential))
        await session.execute(delete(OAuthClientCredential))
        await session.execute(delete(OAuthToken))
        await session.execute(delete(AuthorizationCodeAppRegistrationDetails))
        await session.execute(delete(OAuthAppRegistration))
        await session.execute(delete(Credential))
    async with admin_db.transaction() as session:
        await session.execute(delete(HeldExecution))
        await session.execute(delete(Job))
        await session.execute(delete(ProviderConfigRecord))


async def _seed_all_encrypted_families_on_migrated_dbs(
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    v1_svc: EncryptionService,
) -> None:
    async with control_db.transaction() as session:
        c_apikey = Credential(id="cred_apikey", type="api_key", name="k1", api_vendor="acme")
        session.add(c_apikey)
        session.add(
            CustomerAPIKey(
                id="cak_1",
                credential_id=c_apikey.id,
                encrypted_key=v1_svc.encrypt("apikey-secret-1"),
            )
        )

        c_bearer = Credential(id="cred_bearer", type="bearer", name="k2", api_vendor="acme")
        session.add(c_bearer)
        session.add(
            TokenValueCredential(
                credential_id=c_bearer.id,
                encrypted_token_value=v1_svc.encrypt("bearer-secret-1"),
            )
        )

        c_basic = Credential(id="cred_basic", type="basic", name="k3", api_vendor="acme")
        session.add(c_basic)
        session.add(
            BasicCredential(
                credential_id=c_basic.id,
                username="alice",
                encrypted_password=v1_svc.encrypt("basic-password-1"),
            )
        )

        c_sigv4 = Credential(id="cred_sigv4", type="sigv4", name="k4", api_vendor="aws")
        session.add(c_sigv4)
        session.add(
            Sigv4Credential(
                id="sigv4_1",
                credential_id=c_sigv4.id,
                access_key_id="AKIAIO...MPLE",  # pragma: allowlist secret
                encrypted_secret_access_key=v1_svc.encrypt("sigv4-secret-key"),
                encrypted_session_token=v1_svc.encrypt("sigv4-session-token"),
                region="us-east-1",
                service="s3",
                created_by="usr_rotation_test",
            )
        )

        c_device = Credential(id="cred_device", type="device", name="k5", api_vendor="github")
        session.add(c_device)
        session.add(
            DeviceAuthorizationCredential(
                id=c_device.id,
                client_id="dev-client",
                token_url="https://example.com/token",
                authorization_endpoint="https://example.com/device",
                encrypted_device_code=v1_svc.encrypt("device-code-secret"),
            )
        )

        c_oauth_client = Credential(
            id="cred_oauth_client", type="oauth2", name="k6", api_vendor="slack"
        )
        session.add(c_oauth_client)
        session.add(
            OAuthClientCredential(
                id=c_oauth_client.id,
                token_url="https://example.com/oauth/token",
                client_id="oauth-client",
                encrypted_client_secret=v1_svc.encrypt("oauth-client-secret"),
            )
        )

        reg = OAuthAppRegistration(
            id="oar_1",
            name="Acme OAuth App",
            api_vendor="acme",
            flow_kind="authorization_code",
            client_id="reg-client-id",
            catalog_api_id="acme/core",
            display_name="Acme",
        )
        session.add(reg)
        session.add(
            AuthorizationCodeAppRegistrationDetails(
                id=reg.id,
                encrypted_client_secret=v1_svc.encrypt("app-reg-client-secret"),
                authorize_url="https://example.com/authorize",
                token_url="https://example.com/token",
            )
        )

        c_oauth_tok = Credential(id="cred_oauth_tok", type="oauth2", name="k8", api_vendor="acme")
        session.add(c_oauth_tok)
        session.add(
            OAuthToken(
                id="oat_1",
                credential_id=c_oauth_tok.id,
                encrypted_access_token=v1_svc.encrypt("oauth-access-token"),
                encrypted_refresh_token=v1_svc.encrypt("oauth-refresh-token"),
            )
        )

    async with admin_db.transaction() as session:
        session.add(
            ProviderConfigRecord(
                id="pcfg_1",
                name="auth0-prod",
                config_json={
                    "issuer": "https://auth.example.com",
                    "client_secret": v1_svc.encrypt("provider-client-secret"),
                },
            )
        )

        session.add(
            Job(
                id="job_async_1",
                kind="execution",
                status="queued",
                actor_type="agent",
                attempts=0,
                payload={
                    "operation_id": "op_1",
                    "inbound_cookie_enc": v1_svc.encrypt("async-job-cookie"),
                },
            )
        )

        held_inner = {
            "method": "POST",
            "path": "/v1/Charges",
            "inbound_cookie_enc": v1_svc.encrypt("held-nested-cookie"),
        }
        held_job = Job(
            id="job_held_1",
            kind="execution",
            status="held",
            actor_type="agent",
            attempts=0,
            payload={"_enc": v1_svc.encrypt(json.dumps(held_inner))},
        )
        session.add(held_job)
        await session.flush()
        session.add(
            HeldExecution(
                id="held_1",
                job_id=held_job.id,
                agent_id="agt_1",
                credential_id="cred_apikey",
                api_vendor="acme",
                api_name="payments",
                api_version="v1",
                method="POST",
                path="/v1/Charges",
                request_fingerprint="fp_1",
                state="pending",
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
            )
        )


@pytest.mark.asyncio
async def test_rotation_all_families_on_migrated_schema(
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
) -> None:
    """All 13 encrypted columns across 11 families rotate and retire on the migrated schema."""
    old_key = _make_key("v1")
    new_key = _make_key("v2")
    v1_svc = EncryptionService(EncryptionConfig(active_id="v1", entries=[old_key]))
    v2_svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[old_key, new_key]))
    v2_only_svc = EncryptionService(EncryptionConfig(active_id="v2", entries=[new_key]))

    await _clean_rotation_tables(control_db, admin_db)
    try:
        await _seed_all_encrypted_families_on_migrated_dbs(control_db, admin_db, v1_svc)

        dry_report = await rotate_encrypted_storage(
            v2_svc,
            control_db=control_db,
            admin_db=admin_db,
            dry_run=True,
            batch_size=2,
        )
        assert isinstance(dry_report, KeyRotationReport)
        assert dry_report.dry_run is True
        assert dry_report.batches_executed == 0
        assert dry_report.scanned_rows == 11
        assert dry_report.scanned_values == 13
        assert dry_report.rotated_rows == 0
        assert dry_report.rotated_values == 0
        assert dry_report.needs_rotation_values == 13
        assert dry_report.remaining_non_active_values == 13
        assert dry_report.counts_by_key_before == {"v1": 13}
        assert dry_report.counts_by_key_after == {"v1": 13}
        assert dry_report.completed is False

        for family_or_table, expected_values in (
            ("customer_api_keys", 1),
            ("token_value_credentials", 1),
            ("bearer_token_credentials", 1),
            ("basic_credentials", 1),
            ("basic_auth_credentials", 1),
            ("sigv4_credentials", 2),
            ("device_authorization_credentials", 1),
            ("oauth_client_credentials", 1),
            ("authorization_code_app_registration_details", 1),
            ("oauth_app_registrations", 1),
            ("oauth_tokens", 2),
            ("provider_configs", 1),
            ("jobs", 1),
            ("held_executions", 1),
        ):
            assert dry_report.by_family[family_or_table].scanned_values == expected_values
            assert dry_report.by_table[family_or_table].needs_rotation_values == expected_values

        with pytest.raises(KeyRetirementError) as exc_info:
            await verify_key_retired(
                v2_svc,
                "v1",
                control_db=control_db,
                admin_db=admin_db,
            )
        assert exc_info.value.remaining_by_key == {"v1": 13}
        assert len(exc_info.value.remaining_by_family) == 11

        with pytest.raises(KeyRetirementError):
            await assert_only_active_key(
                v2_svc,
                control_db=control_db,
                admin_db=admin_db,
            )

        with pytest.raises(KeyRetirementError, match="still the active encryption key"):
            dry_report.verify_key_retired("v2")

        partial_report = await rotate_encrypted_storage(
            v2_svc,
            control_db=control_db,
            admin_db=admin_db,
            dry_run=False,
            batch_size=1,
            max_batches=4,
        )
        assert partial_report.batches_executed == 4
        assert partial_report.rotated_rows == 4
        assert partial_report.rotated_values == 5
        assert partial_report.remaining_non_active_values == 8
        assert partial_report.completed is False

        with pytest.raises(KeyRetirementError) as partial_exc:
            await verify_key_retired(
                v2_svc,
                "v1",
                control_db=control_db,
                admin_db=admin_db,
            )
        assert partial_exc.value.remaining_by_key == {"v1": 8}

        rotator = EncryptedStorageRotator(
            v2_svc,
            control_db=control_db,
            admin_db=admin_db,
            batch_size=2,
        )
        resumed_report = await rotator.rotate(dry_run=False)
        assert resumed_report.completed is True
        assert resumed_report.rotated_rows == 7
        assert resumed_report.rotated_values == 8
        assert resumed_report.remaining_non_active_values == 0
        assert resumed_report.counts_by_key_before == {"v2": 5, "v1": 8}
        assert resumed_report.counts_by_key_after == {"v2": 13}

        retired_report = await rotator.verify_key_retired("v1")
        assert retired_report.completed is True
        active_only_report = await rotator.assert_only_active_key()
        assert active_only_report.counts_by_key == {"v2": 13}

        final_check = await assert_only_active_key(
            v2_only_svc,
            control_db=control_db,
            admin_db=admin_db,
        )
        assert final_check.completed is True
        assert final_check.failures == []

        async with admin_db.session() as session:
            held_payload_raw = (
                await session.execute(text("SELECT payload FROM jobs WHERE id = 'job_held_1'"))
            ).scalar_one()
            held_payload = (
                json.loads(held_payload_raw)
                if isinstance(held_payload_raw, str)
                else held_payload_raw
            )
            inner_decrypted = json.loads(v2_only_svc.decrypt(held_payload["_enc"]))
            assert v2_only_svc.decrypt(inner_decrypted["inbound_cookie_enc"]) == (
                "held-nested-cookie"
            )
    finally:
        await _clean_rotation_tables(control_db, admin_db)
