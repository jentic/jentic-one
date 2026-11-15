"""Real-database concurrency and compatibility checks for encryption rotation."""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy import MetaData, Table, delete, text, update
from sqlalchemy.schema import CreateTable
from tests.harness.postgres import superuser_config

from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import AppConfig, DatabaseConfig, EncryptionConfig, EncryptionKey
from jentic_one.shared.crypto import EncryptionService
from jentic_one.shared.crypto.rotation import (
    FamilyRotationStats,
    KeyRotationReport,
    _flush_write_batch,
    _init_report,
    _rotate_jobs_and_held_executions,
    _RowCandidate,
)
from jentic_one.shared.db import DatabaseSession

pytestmark = pytest.mark.integration


def _json_dict(raw: Any) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    assert isinstance(value, dict)
    return value


def _candidate(job_id: str, original: Any, updated: dict[str, Any]) -> _RowCandidate:
    return _RowCandidate(
        row_id=job_id,
        update_params={"id": job_id, "payload": json.dumps(updated)},
        values_scanned=1,
        values_needing_rotation=1,
        before_keys=["v1"],
        after_keys_if_written=["v2"],
        after_keys_if_skipped=["v1"],
        failures=[],
        original_values={
            "payload": json.dumps(original) if isinstance(original, dict) else original
        },
    )


def _report() -> tuple[KeyRotationReport, FamilyRotationStats]:
    report = KeyRotationReport(active_key_id="v2", dry_run=False, batch_size=1)
    stats = FamilyRotationStats(database="admin", family="jobs", table="jobs")
    return report, stats


async def _raw_job_payload(db: DatabaseSession, job_id: str) -> Any:
    async with db.session() as session:
        return (
            await session.execute(text("SELECT payload FROM jobs WHERE id = :id"), {"id": job_id})
        ).scalar_one()


@pytest.mark.asyncio
async def test_job_payload_compare_and_set_handles_json_and_concurrent_updates(
    admin_db: DatabaseSession,
) -> None:
    job_id = f"job_rotation_{uuid.uuid4().hex[:16]}"
    original = {
        "kind": "execution",
        "inbound_cookie_enc": "v1:old",
        "long_key_for_json_order": "value",
        "a": 1,
    }
    rotated = {**original, "inbound_cookie_enc": "v2:new"}
    async with admin_db.transaction() as session:
        session.add(
            Job(
                id=job_id,
                kind="execution",
                status="queued",
                actor_type="agent",
                attempts=0,
                payload=original,
            )
        )

    try:
        async with admin_db.session() as session:
            raw_original = (
                await session.execute(
                    text("SELECT payload FROM jobs WHERE id = :id"), {"id": job_id}
                )
            ).scalar_one()
        report, stats = _report()
        await _flush_write_batch(
            admin_db,
            "UPDATE jobs SET payload = :payload WHERE id = :id",
            [_candidate(job_id, raw_original, rotated)],
            report,
            stats,
            dry_run=False,
            max_batches=None,
        )
        assert report.rotated_values == 1
        assert report.failures == []
        assert _json_dict(await _raw_job_payload(admin_db, job_id)) == rotated

        raw_rotated = await _raw_job_payload(admin_db, job_id)
        concurrent = {"writer": "concurrent", "inbound_cookie_enc": "v2:concurrent"}
        async with admin_db.transaction() as session:
            await session.execute(update(Job).where(Job.id == job_id).values(payload=concurrent))

        await _flush_write_batch(
            admin_db,
            "UPDATE jobs SET payload = :payload WHERE id = :id",
            [_candidate(job_id, raw_rotated, {**rotated, "inbound_cookie_enc": "v2:stale"})],
            report,
            stats,
            dry_run=False,
            max_batches=None,
        )

        assert _json_dict(await _raw_job_payload(admin_db, job_id)) == concurrent
        assert report.remaining_non_active_values == 1
        assert len(report.failures) == 1
        assert report.failures[0].error == "row changed during rotation; rerun to retry"
        assert report.completed is False
    finally:
        async with admin_db.transaction() as session:
            await session.execute(delete(Job).where(Job.id == job_id))


def _encryption(active_id: str) -> EncryptionService:
    entries = [
        EncryptionKey(id="v1", material=SecretStr(base64.b64encode(b"1" * 32).decode())),
        EncryptionKey(id="v2", material=SecretStr(base64.b64encode(b"2" * 32).decode())),
    ]
    return EncryptionService(
        EncryptionConfig(active_id=active_id, entries=entries if active_id == "v2" else entries[:1])
    )


@pytest.mark.asyncio
async def test_jobs_rotation_recovers_when_held_table_is_absent(
    integration_config: AppConfig,
    tmp_path: Path,
) -> None:
    base = integration_config.databases.admin
    postgres = base.backend == "postgres"
    schema = f"rotation_missing_held_{uuid.uuid4().hex}"
    config = (
        superuser_config(base).model_copy(update={"schema_name": schema})
        if postgres
        else DatabaseConfig(backend="sqlite", path=str(tmp_path / "missing-held.db"))
    )
    db = DatabaseSession(config)
    await db.connect()
    source_table = cast(Table, Job.__table__)
    table = (
        source_table.to_metadata(MetaData(), schema=schema)
        if postgres
        else source_table.to_metadata(MetaData())
    )
    for column in table.columns:
        column.server_default = None

    try:
        async with db.engine.begin() as conn:
            if postgres:
                await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(lambda sync_conn: sync_conn.execute(CreateTable(table)))

        old = _encryption("v1")
        new = _encryption("v2")
        job_id = f"job_fallback_{uuid.uuid4().hex[:16]}"
        async with db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO jobs "
                    "(id, kind, status, actor_type, attempts, payload, created_at, updated_at) "
                    "VALUES (:id, 'execution', 'queued', 'agent', 0, :payload, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": job_id,
                    "payload": json.dumps({"inbound_cookie_enc": old.encrypt("cookie")}),
                },
            )

        report = _init_report(
            new,
            include_control=False,
            include_admin=True,
            dry_run=False,
            batch_size=1,
        )
        await _rotate_jobs_and_held_executions(
            db,
            new,
            report,
            target_held=False,
            dry_run=False,
            batch_size=1,
            max_batches=None,
        )

        assert report.by_family["jobs"].rotated_values == 1
        assert report.failures == []
        payload = _json_dict(await _raw_job_payload(db, job_id))
        assert new.decrypt(payload["inbound_cookie_enc"]) == "cookie"
    finally:
        try:
            if postgres:
                async with db.engine.begin() as conn:
                    await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            await db.close()
