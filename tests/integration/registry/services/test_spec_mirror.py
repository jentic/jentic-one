"""Integration tests for the spec mirror — DB-to-disk sync of spec documents.

Covers the ``spec_mirror`` feature end to end against a real registry DB: the
import-handler hook, the revision lifecycle hooks (promote), the API delete
hook, the startup reconcile (backfill + prune), and the disabled no-op.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.services.api_service import ApiService
from jentic_one.registry.services.import_service import ImportHandler
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.registry.services.spec_mirror_service import SpecMirrorService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig, SpecMirrorConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, ApiRevisionState

pytestmark = pytest.mark.integration

_IDENTITY = Identity(
    sub="usr_test",
    email="test@example.com",
    actor_type=ActorType.USER,
    permissions=["org:admin"],
)

_SPEC_CONTENT = {
    "openapi": "3.1.0",
    "info": {"title": "Mirror Test API", "version": "1.0.0"},
    "paths": {
        "/items": {
            "get": {
                "operationId": "listItems",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}


@pytest.fixture()
def mirror_dir(tmp_path: Path) -> Path:
    return tmp_path / "mirror"


@pytest.fixture()
async def mirror_context(
    integration_config: AppConfig, mirror_dir: Path
) -> AsyncGenerator[Context, None]:
    """Connected ``Context`` with the spec mirror enabled at a temp directory."""
    config = integration_config.model_copy(
        update={"spec_mirror": SpecMirrorConfig(enabled=True, path=str(mirror_dir))}
    )
    async with Context(config) as ctx:
        yield ctx


@pytest.fixture()
async def _clean_registry(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Truncate the registry tables the import pipeline touches, before and after."""

    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(SecuritySchemeFlow))
            await session.execute(delete(SecurityScheme))
            await session.execute(delete(ServerVariable))
            await session.execute(delete(Server))
            await session.execute(delete(Operation))
            await session.execute(delete(SpecFile))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api_with_revision(
    registry_db: DatabaseSession,
    *,
    vendor: str = "mirror.test",
    name: str = "widget",
    version: str = "v1",
    state: str = ApiRevisionState.DRAFT,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed an API with one revision carrying a spec file; returns (api_id, revision_id)."""
    async with registry_db.session() as session:
        api = Api(vendor=vendor, name=name, version=version, created_by="usr_test")
        session.add(api)
        await session.flush()
        revision = ApiRevision(
            api_id=api.id,
            state=state,
            spec_digest=f"sha256:{uuid.uuid4().hex}",
            source_type="inline",
            created_by="usr_test",
        )
        session.add(revision)
        await session.flush()
        session.add(
            SpecFile(
                revision_id=revision.id,
                filename="openapi.json",
                content=_SPEC_CONTENT,
                sha="0" * 64,
                created_by="usr_test",
            )
        )
        if state in (ApiRevisionState.PUBLISHED, ApiRevisionState.IMPORTED):
            api.current_revision_id = revision.id
        await session.commit()
        return api.id, revision.id


def _version_dir(mirror_dir: Path, vendor: str = "mirror.test") -> Path:
    return mirror_dir / vendor / "widget" / "v1"


async def test_import_writes_spec_and_meta_to_mirror(
    mirror_context: Context,
    mirror_dir: Path,
    _clean_registry: None,
) -> None:
    """A successful import lands the spec in the state dir matching the revision."""
    handler = ImportHandler(mirror_context)
    payload: dict[str, Any] = {
        "sources": [
            {
                "type": "inline",
                "content": json.dumps(_SPEC_CONTENT),
                "filename": "openapi.json",
                "vendor": "mirror.test",
                "api_name": "widget",
                "version": "v1",
            }
        ]
    }

    result = await handler.execute(
        job_id=str(uuid.uuid4()), session=None, payload=payload, created_by="usr_test"
    )

    revision = result.body["revisions"][0]
    revision_id = revision["revision_id"]
    # Ingest may normalize the identifier (e.g. vendor dots -> dashes); the
    # mirror keys off the stored identity, so assert against the result's.
    api = revision["api"]
    version_dir = mirror_dir / api["vendor"] / api["name"] / api["version"]
    spec_path = version_dir / revision["state"] / f"{revision_id}.json"
    assert json.loads(spec_path.read_text())["info"]["title"] == "Mirror Test API"
    meta = json.loads((version_dir / f"{revision_id}.meta.json").read_text())
    assert meta["state"] == revision["state"]
    assert meta["api"] == api
    assert meta["filename"] == "openapi.json"


async def test_promote_moves_spec_between_state_dirs(
    mirror_context: Context,
    registry_db: DatabaseSession,
    mirror_dir: Path,
    _clean_registry: None,
) -> None:
    """Promote relocates the revision's file draft/ -> published/ in the mirror."""
    _api_id, revision_id = await _seed_api_with_revision(registry_db)
    svc = SpecMirrorService(mirror_context)
    await svc.sync_api("mirror.test", "widget", "v1")
    assert (_version_dir(mirror_dir) / "draft" / f"{revision_id}.json").is_file()

    await RevisionService(mirror_context).promote(
        "mirror.test", "widget", "v1", str(revision_id), identity=_IDENTITY
    )

    assert (_version_dir(mirror_dir) / "published" / f"{revision_id}.json").is_file()
    assert not (_version_dir(mirror_dir) / "draft").exists()
    meta = json.loads((_version_dir(mirror_dir) / f"{revision_id}.meta.json").read_text())
    assert meta["state"] == ApiRevisionState.PUBLISHED


async def test_delete_api_removes_mirror_dir(
    mirror_context: Context,
    registry_db: DatabaseSession,
    mirror_dir: Path,
    _clean_registry: None,
) -> None:
    """Deleting an API drops its whole directory from the mirror."""
    await _seed_api_with_revision(registry_db, state=ApiRevisionState.PUBLISHED)
    await SpecMirrorService(mirror_context).sync_api("mirror.test", "widget", "v1")
    assert _version_dir(mirror_dir).is_dir()

    await ApiService(mirror_context).delete("mirror.test", "widget", "v1", identity=_IDENTITY)

    assert not (mirror_dir / "mirror.test").exists()


async def test_reconcile_backfills_and_prunes(
    mirror_context: Context,
    registry_db: DatabaseSession,
    mirror_dir: Path,
    _clean_registry: None,
) -> None:
    """Reconcile writes specs missing from disk and removes unregistered dirs."""
    _api_id, revision_id = await _seed_api_with_revision(
        registry_db, state=ApiRevisionState.PUBLISHED
    )
    stray = mirror_dir / "gone.example" / "old-api" / "v9" / "archived"
    stray.mkdir(parents=True)
    (stray / "stale.json").write_text("{}")

    await SpecMirrorService(mirror_context).reconcile_all()

    assert (_version_dir(mirror_dir) / "published" / f"{revision_id}.json").is_file()
    assert not (mirror_dir / "gone.example").exists()


async def test_disabled_mirror_never_touches_disk(
    integration_context: Context,
    registry_db: DatabaseSession,
    tmp_path: Path,
    _clean_registry: None,
) -> None:
    """With spec_mirror disabled (the default), sync and reconcile are no-ops."""
    await _seed_api_with_revision(registry_db)
    svc = SpecMirrorService(integration_context)

    await svc.sync_api("mirror.test", "widget", "v1")
    await svc.reconcile_all()

    assert integration_context.config.spec_mirror.enabled is False
    # The default config points nowhere; nothing may have been created locally.
    assert list(tmp_path.iterdir()) == []
