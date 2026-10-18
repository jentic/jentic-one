"""Integration tests for the catalog fetch-spec migrations and the shared rate bucket.

Runs against the integration databases (PostgreSQL by default, SQLite under
``JENTIC_TEST_BACKEND=sqlite``):

- re-keying ``catalog_update_checks`` on the catalog entry backfills
  ``catalog_entry_id`` from the tracked API and keeps the row's observation and
  snooze; the downgrade restores the URL-keyed shape;
- ``catalog_source_buckets`` comes and goes with its revision;
- ``DbRateBucket`` (PostgreSQL only: SQLite installs use the in-memory bucket)
  takes tokens from one row, keeps the interactive reserve, and holds a ``429``
  block across bucket instances, i.e. across processes.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, delete, inspect, text

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_source_buckets import CatalogSourceBucket
from jentic_one.registry.core.schema.catalog_update_checks import CatalogUpdateCheck
from jentic_one.registry.services.catalog.sources.bucket import DbRateBucket
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from tests.harness.postgres import alembic_config_for

pytestmark = pytest.mark.integration

_BEFORE_REKEY = "b1c2d3e4f5a7"  # pragma: allowlist secret
_REKEY_REV = "c2d3e4f5a6b8"  # pragma: allowlist secret
_BUCKETS_REV = "d3e4f5a6b7c9"  # pragma: allowlist secret


@pytest.fixture()
async def restore_head(integration_config: AppConfig) -> AsyncGenerator[None, None]:
    yield
    cfg = alembic_config_for("registry", integration_config.databases.registry)
    await asyncio.to_thread(command.upgrade, cfg, "head")


@pytest.fixture()
async def clean(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _wipe() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(CatalogUpdateCheck))
            await session.execute(delete(CatalogSourceBucket))
            await session.execute(delete(Api).where(Api.vendor == "rekey-test.com"))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def _shape(db: DatabaseSession) -> tuple[set[str], set[str]]:
    async with db.session() as session:
        conn = await session.connection()

        def _inspect(sync: Connection) -> tuple[set[str], set[str]]:
            insp = inspect(sync)
            cols = {c["name"] for c in insp.get_columns("catalog_update_checks")}
            return set(insp.get_table_names()), cols

        return await conn.run_sync(_inspect)


def test_revisions_chain_onto_the_tree(integration_config: AppConfig) -> None:
    cfg = alembic_config_for("registry", integration_config.databases.registry)
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision(_REKEY_REV).down_revision == _BEFORE_REKEY
    assert script.get_revision(_BUCKETS_REV).down_revision == _REKEY_REV


async def test_rekey_backfills_entry_id_and_round_trips(
    integration_config: AppConfig,
    registry_db: DatabaseSession,
    clean: None,
    restore_head: None,
) -> None:
    cfg = alembic_config_for("registry", integration_config.databases.registry)
    await asyncio.to_thread(command.downgrade, cfg, _BEFORE_REKEY)
    tables, cols = await _shape(registry_db)
    assert "catalog_source_buckets" not in tables
    assert {"spec_url", "last_seen_etag"} <= cols
    assert not {"catalog_entry_id", "last_seen_validator", "last_seen_content_hint"} & cols

    api_id = uuid.uuid4()
    # Raw SQL below the ORM: SQLite stores the UUID as 32 hex chars.
    key: object = api_id.hex if registry_db.backend.dialect_name == "sqlite" else api_id
    async with registry_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO apis (id, vendor, name, version, catalog_entry_id, created_at) "
                "VALUES (:id, 'rekey-test.com', 'widgets', 'v1', 'public/rekey-test.com', "
                ":now)"
            ),
            {
                "id": key,
                "now": datetime.now(UTC),
            },
        )
        await session.execute(
            text(
                "INSERT INTO catalog_update_checks (id, local_api_id, spec_url, last_seen_etag, "
                "last_seen_digest, last_notified_digest, snoozed_digest) VALUES "
                "('cuc_rekey', :id, 'https://x.example/openapi.json', '\"v1\"', 'd1', 'd2', 'd2')"
            ),
            {
                "id": key,
            },
        )
        await session.commit()

    await asyncio.to_thread(command.upgrade, cfg, "head")
    tables, cols = await _shape(registry_db)
    assert "catalog_source_buckets" in tables
    assert {"catalog_entry_id", "last_seen_validator", "last_seen_content_hint"} <= cols
    assert not {"spec_url", "last_seen_etag"} & cols
    async with registry_db.session() as session:
        row = await session.get(CatalogUpdateCheck, "cuc_rekey")
        assert row is not None
        assert row.catalog_entry_id == "public/rekey-test.com"
        assert row.last_seen_validator == '"v1"'
        assert row.last_seen_content_hint is None
        assert (row.last_seen_digest, row.last_notified_digest) == ("d1", "d2")
        assert row.snoozed_digest == "d2"

    await asyncio.to_thread(command.downgrade, cfg, _BEFORE_REKEY)
    _tables, cols = await _shape(registry_db)
    assert {"spec_url", "last_seen_etag"} <= cols
    await asyncio.to_thread(command.upgrade, cfg, "head")


# ── DbRateBucket ─────────────────────────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _bucket(ctx: Context, clock: _Clock, **kw: float) -> DbRateBucket:
    return DbRateBucket(
        ctx,
        "corp",
        rate=kw.get("rate", 1.0),
        burst=int(kw.get("burst", 4)),
        interactive_share=kw.get("share", 0.5),
        clock=clock,
    )


def _require_postgres(db: DatabaseSession) -> None:
    if db.backend.dialect_name == "sqlite":
        pytest.skip("the shared bucket is Postgres-only; SQLite uses the in-memory bucket")


async def test_db_bucket_takes_tokens_and_keeps_the_interactive_reserve(
    registry_db: DatabaseSession, integration_context: Context, clean: None
) -> None:
    _require_postgres(registry_db)
    clock = _Clock()
    bucket = _bucket(integration_context, clock)
    await bucket.ensure()
    await bucket.ensure()  # idempotent
    assert (await bucket.take("background")).granted
    assert (await bucket.take("background")).granted
    refused = await bucket.take("background")
    assert not refused.granted
    assert refused.wait_s == pytest.approx(1.0)
    # Another process's bucket over the same row sees the same level.
    other = _bucket(integration_context, clock)
    assert (await other.take("interactive")).granted
    assert (await other.take("interactive")).granted
    assert not (await bucket.take("interactive")).granted
    clock.now += timedelta(seconds=1)
    assert (await bucket.take("interactive")).granted


async def test_db_bucket_block_is_shared_and_lapses(
    registry_db: DatabaseSession, integration_context: Context, clean: None
) -> None:
    _require_postgres(registry_db)
    clock = _Clock()
    bucket = _bucket(integration_context, clock, rate=10.0)
    await bucket.block(30.0)
    other = _bucket(integration_context, clock, rate=10.0)
    blocked = await other.take("interactive")
    assert blocked.blocked
    assert blocked.wait_s == pytest.approx(30.0)
    await other.block(5.0)  # a shorter block never shortens a longer one
    assert (await bucket.take("interactive")).wait_s == pytest.approx(30.0)
    clock.now += timedelta(seconds=31)
    assert (await bucket.take("interactive")).granted


async def test_db_bucket_creates_its_row_on_first_take(
    registry_db: DatabaseSession, integration_context: Context, clean: None
) -> None:
    _require_postgres(registry_db)
    bucket = _bucket(integration_context, _Clock())
    assert (await bucket.take("interactive")).granted
    async with registry_db.session() as session:
        rows = (await session.execute(text("SELECT tokens FROM catalog_source_buckets"))).all()
    assert [round(r[0]) for r in rows] == [3]


@pytest.mark.parametrize(("burst", "share"), [(1, 0.5), (2, 0.9)])
async def test_db_bucket_full_small_bucket_grants_background(
    registry_db: DatabaseSession,
    integration_context: Context,
    clean: None,
    burst: int,
    share: float,
) -> None:
    """The interactive reserve never takes a small bucket's last token from background."""
    _require_postgres(registry_db)
    bucket = _bucket(integration_context, _Clock(), burst=burst, share=share)
    assert (await bucket.take("background")).granted
