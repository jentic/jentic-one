"""Registry web test fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.auth.web.app import install_on_app as install_auth_verifier
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import resolve_identity
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

CATALOG_MANIFEST_BASE = "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis"
CATALOG_MANIFEST_URL = f"{CATALOG_MANIFEST_BASE}/openapi/apis.json"


def catalog_spec_url(api_id: str) -> str:
    """The spec URL ``seed_catalog_snapshot`` records for ``api_id``."""
    return f"{CATALOG_MANIFEST_BASE}/openapi/{api_id}/openapi.json"


async def seed_catalog_snapshot(ctx: Context, api_ids: list[str]) -> None:
    """Persist a fresh catalog snapshot holding exactly ``api_ids`` (in that order)."""
    entries = [
        {
            "api_id": a,
            "vendor": a,
            "path": f"apis/openapi/{a}",
            "spec_url": catalog_spec_url(a),
            "github_url": f"https://github.com/jentic/jentic-public-apis/tree/main/{a}",
        }
        for a in api_ids
    ]
    async with ctx.registry_db.session() as session:
        session.add(
            CatalogSnapshot(
                source_url=CATALOG_MANIFEST_URL, entry_count=len(entries), entries=entries
            )
        )
        await session.commit()


async def register_catalog_entries(ctx: Context, api_ids: list[str]) -> None:
    """Mark ``api_ids`` locally registered (a draft revision sourced from their spec URL)."""
    async with ctx.registry_db.session() as session:
        for i, a in enumerate(sorted(api_ids)):
            api = Api(vendor=a, name=f"n{i}", version="v1", revision_count=1)
            session.add(api)
            await session.flush()
            session.add(
                ApiRevision(
                    api_id=api.id,
                    state="draft",
                    spec_digest=f"sha256:{i}",
                    source_type="url",
                    source_url=catalog_spec_url(a),
                )
            )
        await session.commit()


async def delete_registered_apis(ctx: Context) -> None:
    """Remove every local API + revision (cleanup for ``register_catalog_entries``)."""
    async with ctx.registry_db.session() as session:
        await session.execute(text("UPDATE registry.apis SET current_revision_id = NULL"))
        await session.execute(text("DELETE FROM registry.api_revisions"))
        await session.execute(text("DELETE FROM registry.apis"))
        await session.commit()


@pytest.fixture()
async def clean_catalog(web_context: Context) -> AsyncGenerator[None, None]:
    """Empty the catalog cache around a test for deterministic counts."""

    async def _truncate() -> None:
        async with web_context.registry_db.session() as session:
            await session.execute(delete(CatalogSnapshot))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


def _build_app(ctx: Context) -> FastAPI:
    """Build the registry FastAPI app using the real factory, with lifespan disabled."""
    app = create_app(ctx)
    install_auth_verifier(app, ctx)
    app.router.lifespan_context = noop_lifespan
    return app


def _build_app_as(ctx: Context, identity: Identity) -> FastAPI:
    """Build the registry app with ``resolve_identity`` overridden to a fixed identity.

    Post token/permission decoupling, ``verify_token`` resolves permissions live
    from the DB, so privileged-permission tests inject the identity directly via
    the canonical ``dependency_overrides[resolve_identity]`` pattern instead of
    minting a token with permission claims (which are ignored).
    """
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan

    async def _override(_: object = None) -> Identity:
        return identity

    app.dependency_overrides[resolve_identity] = _override
    return app


def _make_token(ctx: Context) -> str:
    """Issue a valid JWT for a registry caller with the registry permission set.

    Registry read routes require ``apis:read`` / ``capabilities:read`` and writes
    require ``apis:write``. Embedded JWT ``permissions`` are trusted verbatim
    (``verify_token`` does not expand implications), so list both the read and
    write permissions explicitly here so a single fixture client can exercise the
    full registry surface (reads, ingest, revision verbs, overlays).
    """
    config = ctx.config.admin.auth
    claims = {
        "sub": "usr_test_registry",
        "email": "registry-test@test.local",
        "actor_type": "user",
        "permissions": ["apis:read", "apis:write", "capabilities:read"],
        "must_change_password": False,
    }
    return issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)


@pytest.fixture()
def authed_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient with a valid Authorization header (live verifier, DB-resolved perms)."""
    app = _build_app(web_context)
    token = _make_token(web_context)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as tc:
        yield tc


@pytest.fixture()
def unauthed_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient with no Authorization header."""
    app = _build_app(web_context)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def wrong_scope_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient holding only an unrelated scope (events:read) — gates the apis:read reads."""
    identity = Identity(
        sub="usr_test_registry_wrong_scope",
        email="registry-wrongscope@test.local",
        permissions=["events:read"],
    )
    app = _build_app_as(web_context, identity)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as tc:
        yield tc


@pytest.fixture()
def write_only_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient holding only apis:write — must reach apis:read routes via implication."""
    identity = Identity(
        sub="usr_test_registry_write_only",
        email="registry-writeonly@test.local",
        permissions=["apis:write"],
    )
    app = _build_app_as(web_context, identity)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as tc:
        yield tc


@pytest.fixture()
def catalog_import_only_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient holding only catalog:import — gates the catalog import route."""
    identity = Identity(
        sub="usr_test_registry_catalog_import",
        email="registry-catalogimport@test.local",
        permissions=["catalog:import"],
    )
    app = _build_app_as(web_context, identity)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as tc:
        yield tc


@pytest.fixture()
def admin_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient acting as an org:admin (for catalog refresh), via identity override."""
    identity = Identity(
        sub="web-test-catalog-admin",
        email="catalog-admin@test.local",
        permissions=["org:admin"],
    )
    app = _build_app_as(web_context, identity)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as tc:
        yield tc


@pytest.fixture()
def overlays_confirm_only_client(web_context: Context) -> Iterator[TestClient]:
    """TestClient holding only overlays:confirm — gates the overlay confirm route."""
    identity = Identity(
        sub="usr_test_registry_overlays_confirm",
        email="registry-overlaysconfirm@test.local",
        permissions=["overlays:confirm"],
    )
    app = _build_app_as(web_context, identity)
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as tc:
        yield tc
