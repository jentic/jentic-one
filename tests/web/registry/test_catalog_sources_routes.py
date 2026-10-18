"""Web tests for ``GET /catalog/sources`` and ``POST /catalog/sources/{slug}:sync|:notify``.

Sources use the test-only ``static`` kind (plus a push-capable variant), so no
HTTP is involved. The request handlers only record a request; each test runs
the ``CatalogSourceSyncScanner`` tick itself, as the background task would.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from jentic_one.registry.core.schema.catalog_entries import CatalogEntryRow
from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.core.schema.catalog_sync_runs import CatalogSyncRun
from jentic_one.registry.services.catalog.snapshot import clear_snapshots
from jentic_one.registry.services.catalog.sources.protocol import (
    ListingTooLargeError,
    SourceCapabilities,
)
from jentic_one.registry.services.catalog.sources.registry import (
    register_catalog_source_kind,
    registered_catalog_source_kinds,
)
from jentic_one.registry.services.catalog.sources.scanner import CatalogSourceSyncScanner
from jentic_one.registry.services.catalog.sources.streams import FullListStream
from jentic_one.registry.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig, CatalogConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import resolve_identity
from jentic_one.testing.catalog_source import (
    StaticSource,
    register_static_source_kind,
    static_source_config,
)
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

_PUSH_KIND = "static-push"


class _PushStaticSource(StaticSource):
    """A ``static`` source that also takes push notifications."""

    kind: ClassVar[str] = _PUSH_KIND
    capabilities: ClassVar[SourceCapabilities] = (
        SourceCapabilities.CONDITIONAL_FETCH | SourceCapabilities.PUSH_NOTIFY
    )


def _register_kinds() -> None:
    register_static_source_kind()
    if _PUSH_KIND not in registered_catalog_source_kinds():
        register_catalog_source_kind(_PushStaticSource)


def _entries(*ids: str) -> list[dict[str, Any]]:
    return [{"external_id": i, "catalog_api_id_hint": i} for i in ids]


def _sources() -> list[dict[str, Any]]:
    _register_kinds()
    public = static_source_config("public", _entries("stripe.com"), trust="curated", precedence=10)
    corp = static_source_config(
        "corp", _entries("corp-billing.example"), visibility="org", precedence=20
    )
    vault = static_source_config(
        "vault",
        _entries("vault-a.example"),
        visibility="restricted",
        readers={"permissions": ["vault:read"], "actor_ids": []},
        precedence=5,
    )
    hooks = static_source_config(
        "hooks", _entries("hooked.example"), visibility="public", precedence=30
    ).model_copy(update={"kind": _PUSH_KIND})
    hidden_hooks = static_source_config(
        "vaulthooks",
        _entries("vh.example"),
        visibility="restricted",
        readers={"permissions": ["vault:read"], "actor_ids": []},
    ).model_copy(update={"kind": _PUSH_KIND})
    # ``max_age_seconds: 0``: the scanner runs only requested syncs, never stale ones.
    return [
        {**s.model_dump(), "max_age_seconds": 0} for s in (public, corp, vault, hooks, hidden_hooks)
    ]


_UNMATCHED = {"source": "corp", "external_id": "nope", "catalog_api_id": "ghost.example"}


@pytest.fixture()
async def ctx(web_config: AppConfig) -> AsyncIterator[Context]:
    cfg = web_config.model_copy(
        update={
            "catalog": CatalogConfig.model_validate(
                {"sources": _sources(), "catalog_api_id_mappings": [_UNMATCHED]}
            )
        }
    )
    async with Context(cfg) as c:
        await _truncate(c)
        yield c
        await _truncate(c)


async def _truncate(ctx: Context) -> None:
    async with ctx.registry_db.transaction() as session:
        await session.execute(delete(CatalogEntryRow))
        await session.execute(delete(CatalogSyncRun))
        await session.execute(delete(CatalogSourceRow))
    clear_snapshots()


@contextmanager
def _client(
    ctx: Context, permissions: Sequence[str], sub: str = "usr_caller"
) -> Iterator[TestClient]:
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan
    identity = Identity(sub=sub, email=f"{sub}@test.local", permissions=list(permissions))

    async def _override(_: object = None) -> Identity:
        return identity

    app.dependency_overrides[resolve_identity] = _override
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as tc:
        yield tc


def _tick(tc: TestClient, ctx: Context) -> None:
    tc.portal.call(CatalogSourceSyncScanner(ctx).tick)  # type: ignore[union-attr]


async def _runs(ctx: Context, slug: str) -> int:
    async with ctx.registry_db.session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(CatalogSyncRun)
            .where(CatalogSyncRun.source_slug == slug)
        )
        return int(result.scalar_one())


# ── GET /catalog/sources ──────────────────────────────────────────────────────


def test_list_sources_needs_capabilities_read(ctx: Context) -> None:
    with _client(ctx, ["events:read"]) as tc:
        assert tc.get("/catalog/sources").status_code == 403


def test_list_sources_shows_only_visible_ones(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read"]) as tc:
        body = tc.get("/catalog/sources").json()
    slugs = [s["slug"] for s in body["data"]]
    assert slugs == ["hooks", "public"]
    public = body["data"][1]
    assert public["kind"] == "static"
    assert public["status"] == "never_synced"
    assert public["live_entries"] == 0
    for operator_field in (
        "enabled",
        "trust",
        "precedence",
        "readers",
        "warnings",
        "last_error_code",
    ):
        assert public[operator_field] is None


def test_list_sources_org_and_restricted_readers(ctx: Context) -> None:
    with _client(
        ctx, ["capabilities:read", "catalog:org:read", "catalog:restricted:read", "vault:read"]
    ) as tc:
        slugs = [s["slug"] for s in tc.get("/catalog/sources").json()["data"]]
    assert slugs == ["corp", "hooks", "public", "vault", "vaulthooks"]


def test_admin_sees_every_source_with_operator_fields_and_warnings(ctx: Context) -> None:
    with _client(ctx, ["org:admin"]) as tc:
        assert tc.post("/catalog/sources/public:sync").status_code == 202
        _tick(tc, ctx)
        body = tc.get("/catalog/sources").json()
    by_slug = {s["slug"]: s for s in body["data"]}
    assert sorted(by_slug) == ["corp", "hooks", "public", "vault", "vaulthooks"]
    vault = by_slug["vault"]
    assert vault["readers"] == {"permissions": ["vault:read"], "actor_ids": []}
    assert vault["precedence"] == 5
    assert vault["enabled"] is True
    assert by_slug["public"]["trust"] == "curated"
    assert by_slug["public"]["status"] == "ok"
    assert by_slug["public"]["live_entries"] == 1
    assert by_slug["corp"]["warnings"] == [
        {
            "code": "mapping_unmatched",
            "external_id": "nope",
            "catalog_api_id": "ghost.example",
            "local_api": None,
        }
    ]
    assert by_slug["public"]["warnings"] == []


async def test_admin_sees_why_a_failed_source_failed(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed source carries its last run's error code, for an operator only."""

    def _too_large(self: StaticSource, *, since: str | None) -> Any:
        async def _load() -> list[Any]:
            raise ListingTooLargeError("index lists more than 3 entries")

        return FullListStream(_load, watermark=lambda _changes: "w")

    with _client(ctx, ["org:admin"]) as tc:
        monkeypatch.setattr(StaticSource, "list_entries", _too_large)
        assert tc.post("/catalog/sources/public:sync").status_code == 202
        _tick(tc, ctx)
        public = {s["slug"]: s for s in tc.get("/catalog/sources").json()["data"]}["public"]
        assert (public["status"], public["last_error_code"]) == ("failed", "listing_too_large")

        with _client(ctx, ["capabilities:read"]) as reader:
            shown = {s["slug"]: s for s in reader.get("/catalog/sources").json()["data"]}
        assert shown["public"]["status"] == "failed"
        assert shown["public"]["last_error_code"] is None

        # A source whose last run did not fail carries no code.
        hooks = {s["slug"]: s for s in tc.get("/catalog/sources").json()["data"]}["hooks"]
        assert hooks["last_error_code"] is None


# ── :sync ─────────────────────────────────────────────────────────────────────


def test_sync_needs_catalog_sync(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read"]) as tc:
        assert tc.post("/catalog/sources/public:sync").status_code == 403


async def test_sync_is_non_blocking_and_runs_on_the_next_tick(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read", "catalog:sync"]) as tc:
        r = tc.post("/catalog/sources/public:sync")
        assert r.status_code == 202
        assert r.json()["status"] == "queued"
        assert await _runs(ctx, "public") == 0
        assert tc.get("/catalog/sources").json()["data"][1]["sync_pending"] is True
        _tick(tc, ctx)
        assert await _runs(ctx, "public") == 1
        assert tc.get("/catalog/sources").json()["data"][1]["sync_pending"] is False


def test_force_needs_org_admin(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read", "catalog:sync"]) as tc:
        r = tc.post("/catalog/sources/public:sync", params={"force": "true"})
    assert r.status_code == 403


async def test_forced_sync_is_recorded(ctx: Context) -> None:
    with _client(ctx, ["org:admin"]) as tc:
        r = tc.post("/catalog/sources/public:sync", params={"force": "true"})
        assert r.status_code == 202
        assert r.json()["forced"] is True
        _tick(tc, ctx)
    async with ctx.registry_db.session() as session:
        forced = (await session.execute(select(CatalogSyncRun.forced))).scalars().all()
    assert forced == [True]


def test_hidden_source_is_byte_identical_to_unknown(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read", "catalog:sync"]) as tc:
        hidden = tc.post("/catalog/sources/vault:sync")
        unknown = tc.post("/catalog/sources/no-such-source:sync")
    assert hidden.status_code == unknown.status_code == 404
    # Same body bar the slug the caller sent (it is echoed in ``instance``).
    assert hidden.text.replace("vault", "{slug}") == unknown.text.replace(
        "no-such-source", "{slug}"
    )


# ── :notify ───────────────────────────────────────────────────────────────────


def test_notify_needs_catalog_sync(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read"]) as tc:
        assert tc.post("/catalog/sources/hooks:notify").status_code == 403


def test_notify_without_push_support_is_404(ctx: Context) -> None:
    with _client(ctx, ["org:admin"]) as tc:
        assert tc.post("/catalog/sources/public:notify").status_code == 404


def test_notify_on_a_hidden_source_is_404(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read", "catalog:sync"]) as tc:
        hidden = tc.post("/catalog/sources/vaulthooks:notify")
        unknown = tc.post("/catalog/sources/nope:notify")
    assert hidden.status_code == unknown.status_code == 404


def test_notify_rejects_more_than_100_external_ids(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read", "catalog:sync"]) as tc:
        ok = tc.post(
            "/catalog/sources/hooks:notify",
            json={"external_ids": [f"id-{i}" for i in range(100)]},
        )
        too_many = tc.post(
            "/catalog/sources/hooks:notify",
            json={"external_ids": [f"id-{i}" for i in range(101)]},
        )
    assert ok.status_code == 202
    assert too_many.status_code == 422


async def test_notifications_within_the_interval_start_one_run(ctx: Context) -> None:
    with _client(ctx, ["capabilities:read", "catalog:sync"]) as tc:
        first = tc.post("/catalog/sources/hooks:notify")
        second = tc.post("/catalog/sources/hooks:notify", json={"external_ids": ["hooked.example"]})
        assert first.status_code == second.status_code == 202
        assert first.json()["status"] == "queued"
        assert second.json()["status"] == "coalesced"
        _tick(tc, ctx)
        assert await _runs(ctx, "hooks") == 1

        # Right after that run started: queued for when the interval ends, not now.
        third = tc.post("/catalog/sources/hooks:notify")
        fourth = tc.post("/catalog/sources/hooks:notify")
        assert third.json()["status"] == "queued"
        assert third.json()["due_at"] is not None
        assert fourth.json()["status"] == "coalesced"
        assert fourth.json()["due_at"] == third.json()["due_at"]
        _tick(tc, ctx)
        assert await _runs(ctx, "hooks") == 1
