"""Integration tests for ApiRepository."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.repos.api_repo import ApiRepository, CatalogApiIdConflictError
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


async def test_upsert_creates_new_api(registry_db: DatabaseSession, clean_registry: None) -> None:
    """upsert creates a new Api when no match exists."""
    async with registry_db.session() as session:
        api = await ApiRepository.upsert(
            session,
            vendor="github.com",
            name="rest",
            version="v3",
            display_name="GitHub REST",
            description="GitHub REST API",
            created_by="usr_test",
        )
        await session.commit()

    assert api.id is not None
    assert api.vendor == "github.com"
    assert api.name == "rest"
    assert api.version == "v3"
    assert api.display_name == "GitHub REST"
    assert api.description == "GitHub REST API"


async def test_upsert_idempotent_updates_mutable_fields(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """upsert updates display_name/description when Api already exists."""
    async with registry_db.session() as session:
        api = await ApiRepository.upsert(
            session, vendor="stripe.com", name="payments", version="v1", created_by="usr_test"
        )
        await session.commit()
        api_id = api.id

    async with registry_db.session() as session:
        updated = await ApiRepository.upsert(
            session,
            vendor="stripe.com",
            name="payments",
            version="v1",
            display_name="Stripe Payments",
            description="Process payments",
            created_by="usr_test",
        )
        await session.commit()

    assert updated.id == api_id
    assert updated.display_name == "Stripe Payments"
    assert updated.description == "Process payments"


async def test_upsert_does_not_clear_fields_when_none(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """upsert does not overwrite existing fields with None."""
    async with registry_db.session() as session:
        await ApiRepository.upsert(
            session,
            vendor="x.com",
            name="api",
            version="v2",
            display_name="X API",
            description="The X API",
            created_by="usr_test",
        )
        await session.commit()

    async with registry_db.session() as session:
        api = await ApiRepository.upsert(
            session, vendor="x.com", name="api", version="v2", created_by="usr_test"
        )
        await session.commit()

    assert api.display_name == "X API"
    assert api.description == "The X API"


async def test_set_current_revision(registry_db: DatabaseSession, clean_registry: None) -> None:
    """set_current_revision updates the pointer on the Api."""
    async with registry_db.session() as session:
        api = await ApiRepository.upsert(
            session, vendor="test.io", name="svc", version="v1", created_by="usr_test"
        )
        await session.commit()
        api_id = api.id

    async with registry_db.session() as session:
        rev = ApiRevision(api_id=api_id, spec_digest="sha256:rev1", source_type="url")
        session.add(rev)
        await session.commit()
        rev_id = rev.id

    async with registry_db.session() as session:
        await ApiRepository.set_current_revision(session, api_id, rev_id)
        await session.commit()

    async with registry_db.session() as session:
        loaded = await ApiRepository.get_by_id(session, api_id)
        assert loaded is not None
        assert loaded.current_revision_id == rev_id


async def test_apply_counts(registry_db: DatabaseSession, clean_registry: None) -> None:
    """apply_counts increments revision_count and sets operation_count."""
    async with registry_db.session() as session:
        api = await ApiRepository.upsert(
            session, vendor="apply.io", name="counter", version="v1", created_by="usr_test"
        )
        await session.commit()
        api_id = api.id

    async with registry_db.session() as session:
        await ApiRepository.apply_counts(
            session, api_id, revision_count_delta=1, operation_count=42
        )
        await session.commit()

    async with registry_db.session() as session:
        loaded = await ApiRepository.get_by_id(session, api_id)
        assert loaded is not None
        assert loaded.revision_count == 1
        assert loaded.operation_count == 42

    async with registry_db.session() as session:
        await ApiRepository.apply_counts(session, api_id, revision_count_delta=2)
        await session.commit()

    async with registry_db.session() as session:
        loaded = await ApiRepository.get_by_id(session, api_id)
        assert loaded is not None
        assert loaded.revision_count == 3
        assert loaded.operation_count == 42


async def test_list_page_returns_newest_first(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """list_page returns APIs ordered by created_at DESC, id DESC."""
    base_time = datetime(2024, 1, 1, tzinfo=UTC)
    async with registry_db.session() as session:
        for i in range(3):
            api = Api(vendor="list.io", name=f"api-{i}", version="v1")
            session.add(api)
            await session.flush()
            await session.execute(
                update(Api)
                .where(Api.id == api.id)
                .values(created_at=base_time + timedelta(hours=i))
            )
        await session.commit()

    async with registry_db.session() as session:
        results = await ApiRepository.list_page(session, limit=10)

    assert len(results) == 3
    assert results[0].name == "api-2"
    assert results[1].name == "api-1"
    assert results[2].name == "api-0"


async def test_list_page_vendor_filter(registry_db: DatabaseSession, clean_registry: None) -> None:
    """list_page filters by vendor when provided."""
    async with registry_db.session() as session:
        session.add(Api(vendor="alpha.io", name="svc", version="v1"))
        session.add(Api(vendor="beta.io", name="svc", version="v1"))
        session.add(Api(vendor="alpha.io", name="other", version="v1"))
        await session.commit()

    async with registry_db.session() as session:
        results = await ApiRepository.list_page(session, limit=10, vendor="alpha.io")

    assert len(results) == 2
    assert all(r.vendor == "alpha.io" for r in results)


async def test_list_page_cursor_pagination(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """list_page supports keyset cursor pagination."""
    base_time = datetime(2024, 6, 1, tzinfo=UTC)
    async with registry_db.session() as session:
        for i in range(3):
            api = Api(vendor="page.io", name=f"api-{i}", version="v1")
            session.add(api)
            await session.flush()
            await session.execute(
                update(Api)
                .where(Api.id == api.id)
                .values(created_at=base_time + timedelta(hours=i))
            )
        await session.commit()

    async with registry_db.session() as session:
        page1 = await ApiRepository.list_page(session, limit=2)

    assert len(page1) == 2
    last = page1[-1]

    async with registry_db.session() as session:
        page2 = await ApiRepository.list_page(
            session,
            limit=2,
            cursor_created_at=last.created_at,
            cursor_id=str(last.id),
        )

    assert len(page2) == 1
    assert page2[0].name == "api-0"


async def test_load_security_scheme_types(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """load_security_scheme_types returns distinct types per revision."""
    async with registry_db.session() as session:
        api = Api(vendor="sec.io", name="secure", version="v1")
        session.add(api)
        await session.flush()

        rev = ApiRevision(api_id=api.id, spec_digest="sha256:sec1", source_type="url")
        session.add(rev)
        await session.flush()

        session.add(SecurityScheme(revision_id=rev.id, name="bearer", type="http", raw_scheme={}))
        session.add(
            SecurityScheme(revision_id=rev.id, name="api_key", type="apiKey", raw_scheme={})
        )
        session.add(SecurityScheme(revision_id=rev.id, name="bearer2", type="http", raw_scheme={}))
        await session.commit()
        rev_id = rev.id

    async with registry_db.session() as session:
        result = await ApiRepository.load_security_scheme_types(session, [rev_id])

    assert rev_id in result
    assert sorted(result[rev_id]) == ["apiKey", "http"]


async def test_load_server_hosts(registry_db: DatabaseSession, clean_registry: None) -> None:
    """load_server_hosts parses host from the first non-operation server URL."""
    async with registry_db.session() as session:
        api = Api(vendor="host.io", name="hosted", version="v1")
        session.add(api)
        await session.flush()

        rev = ApiRevision(api_id=api.id, spec_digest="sha256:host1", source_type="url")
        session.add(rev)
        await session.flush()

        session.add(Server(revision_id=rev.id, url="https://api.example.com/v1"))
        session.add(Server(revision_id=rev.id, url="https://staging.example.com"))
        await session.commit()
        rev_id = rev.id

    async with registry_db.session() as session:
        result = await ApiRepository.load_server_hosts(session, [rev_id])

    assert rev_id in result
    assert result[rev_id] == "api.example.com"


async def test_load_server_hosts_ignores_operation_servers(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """load_server_hosts skips servers with operation_id set."""
    async with registry_db.session() as session:
        api = Api(vendor="ophost.io", name="optest", version="v1")
        session.add(api)
        await session.flush()

        rev = ApiRevision(api_id=api.id, spec_digest="sha256:ophost1", source_type="url")
        session.add(rev)
        await session.flush()

        op = Operation(id="op_test_host_ignore", revision_id=rev.id, path="/test", method="GET")
        session.add(op)
        await session.flush()

        session.add(
            Server(revision_id=rev.id, url="https://op-level.example.com", operation_id=op.id)
        )
        session.add(Server(revision_id=rev.id, url="https://global.example.com"))
        await session.commit()
        rev_id = rev.id

    async with registry_db.session() as session:
        result = await ApiRepository.load_server_hosts(session, [rev_id])

    assert result[rev_id] == "global.example.com"


_postgres_only = pytest.mark.skipif(
    os.environ.get("JENTIC_TEST_BACKEND", "postgres").lower() == "sqlite",
    reason="concurrent writers need Postgres (SQLite serializes them with BEGIN IMMEDIATE)",
)

_RACE_IDENTITY = {"vendor": "race.example.com", "name": "new-api", "version": "1.0.0"}


async def _race_upsert_behind_uncommitted_insert(
    registry_db: DatabaseSession,
    *,
    a_kwargs: dict[str, str],
    b_kwargs: dict[str, str],
) -> tuple[Api, asyncio.Task[Api]]:
    """Run upsert B while transaction A's insert of the same new identity is uncommitted.

    A upserts (inserting the identity) and keeps its transaction open. B starts
    its own transaction, records its backend pid, and upserts; its locking read
    cannot see A's row. A commits only once ``pg_blocking_pids`` shows B's own
    backend waiting on A. Returns A's row and B's (finished) task.
    """
    b_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def upsert_b() -> Api:
        async with registry_db.transaction() as session:
            b_pid.set_result((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
            return await ApiRepository.upsert(session, **_RACE_IDENTITY, **b_kwargs)

    async with registry_db.transaction() as session_a:
        api_a = await ApiRepository.upsert(session_a, **_RACE_IDENTITY, **a_kwargs)
        task_b = asyncio.create_task(upsert_b())
        pid = await b_pid
        async with registry_db.session() as probe:
            for _ in range(500):
                blocked = (
                    await probe.execute(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
                    )
                ).scalar_one()
                if blocked:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("upsert B never blocked on transaction A")
        assert not task_b.done()

    await asyncio.wait([task_b])
    return api_a, task_b


@_postgres_only
async def test_upsert_of_new_identity_waits_for_concurrent_insert(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """Pins #1980: an upsert racing another transaction's insert of the same new
    identity must resolve to that row once it commits, not fail on
    ``uq_apis_vendor_name_version``.
    """
    api_a, task_b = await _race_upsert_behind_uncommitted_insert(
        registry_db,
        a_kwargs={"catalog_api_id": "race.example.com", "created_by": "usr_a"},
        b_kwargs={"display_name": "From B", "created_by": "usr_b"},
    )

    api_b = task_b.result()
    assert api_b.id == api_a.id
    # The re-read keeps the joined-eager relationship loaded (no async lazy load).
    assert api_b.current_revision is None
    async with registry_db.session() as session:
        rows = list(
            (
                await session.execute(
                    select(Api).where(
                        Api.vendor == _RACE_IDENTITY["vendor"],
                        Api.name == _RACE_IDENTITY["name"],
                        Api.version == _RACE_IDENTITY["version"],
                    )
                )
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].display_name == "From B"
    assert rows[0].catalog_api_id == "race.example.com"
    assert rows[0].created_by == "usr_a"


@_postgres_only
async def test_upsert_losing_new_identity_race_enforces_catalog_guard(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """The catalog-identity guard applies to the row a concurrent import inserted
    first: the loser of the insert race raises CatalogApiIdConflictError, and
    the winner's catalog id is left untouched.
    """
    _api_a, task_b = await _race_upsert_behind_uncommitted_insert(
        registry_db,
        a_kwargs={"catalog_api_id": "race.example.com/a", "created_by": "usr_a"},
        b_kwargs={"catalog_api_id": "race.example.com/b", "created_by": "usr_b"},
    )

    exc = task_b.exception()
    assert isinstance(exc, CatalogApiIdConflictError)
    assert exc.stored_id == "race.example.com/a"
    async with registry_db.session() as session:
        stored = (
            await session.execute(select(Api).where(Api.vendor == _RACE_IDENTITY["vendor"]))
        ).scalar_one()
    assert stored.catalog_api_id == "race.example.com/a"
