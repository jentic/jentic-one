"""Unit tests for the catalog source seam: kinds, contract kit, guard, sanitising, digests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any, ClassVar

import pytest

from jentic_one.registry.core.schema.catalog_sources import CatalogSourceRow
from jentic_one.registry.services.catalog.sources import digests, sanitize
from jentic_one.registry.services.catalog.sources.deps import SourceDeps
from jentic_one.registry.services.catalog.sources.git_manifest import GitManifestSource
from jentic_one.registry.services.catalog.sources.protocol import CatalogSource, Upserted
from jentic_one.registry.services.catalog.sources.registry import (
    register_catalog_source_kind,
    registered_catalog_source_kinds,
    resolve_catalog_source_kind,
    unregister_catalog_source_kind,
)
from jentic_one.registry.services.catalog.sources.sync import guard_trips, is_due
from jentic_one.shared.config import CatalogSourceConfig
from jentic_one.shared.db.utils import utcnow
from jentic_one.testing import (
    FakeSourceHttp,
    SourceContract,
    register_static_source_kind,
    static_source_config,
    synthetic_catalog,
)
from jentic_one.testing.catalog_source import (
    STATIC_SOURCE_KIND,
    StaticSource,
    contract_checks,
    fake_source_deps,
)

_INDEX = "https://index.example/apis.json"


def _manifest_url(domain: str) -> str:
    return (
        "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/"
        f"apis/openapi/{domain}/main/v1/apis.json"
    )


def _spec_url(domain: str) -> str:
    return (
        "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/"
        f"apis/openapi/{domain}/main/v1/openapi.json"
    )


class _GitManifestContract(SourceContract):
    def build_source(self, deps: SourceDeps) -> CatalogSource:
        return GitManifestSource.build(
            CatalogSourceConfig(slug="contract", kind="git-manifest", settings={"url": _INDEX}),
            deps=deps,
        )

    def seed_http(self, http: FakeSourceHttp) -> None:
        http.json_responses[_INDEX] = {
            "include": [{"url": _manifest_url("stripe.com")}, {"url": _manifest_url("acme.io")}]
        }
        http.bytes_responses[_spec_url("stripe.com")] = b'{"openapi": "3.1.0"}'
        http.bytes_responses[_spec_url("acme.io")] = b'{"openapi": "3.0.3"}'


class _StaticContract(SourceContract):
    slug: ClassVar[str] = "static-contract"

    def build_source(self, deps: SourceDeps) -> CatalogSource:
        [cfg] = synthetic_catalog(3, ["static-contract"])
        return StaticSource.build(cfg, deps=deps)


@pytest.fixture()
def static_kind() -> Iterator[None]:
    already = STATIC_SOURCE_KIND in registered_catalog_source_kinds()
    register_static_source_kind()
    yield
    if not already:
        unregister_catalog_source_kind(STATIC_SOURCE_KIND)


_CHECKS = [
    (kit, name)
    for kit in (_GitManifestContract, _StaticContract)
    for name, _ in contract_checks(kit())
]


@pytest.mark.parametrize(("kit", "name"), _CHECKS, ids=[f"{k.__name__}-{n}" for k, n in _CHECKS])
async def test_source_contract(kit: type[SourceContract], name: str) -> None:
    result = getattr(kit(), name)()
    if hasattr(result, "__await__"):
        await result


def test_static_kind_is_absent_until_registered() -> None:
    if STATIC_SOURCE_KIND in registered_catalog_source_kinds():
        unregister_catalog_source_kind(STATIC_SOURCE_KIND)
    assert STATIC_SOURCE_KIND not in registered_catalog_source_kinds()
    register_static_source_kind()
    register_static_source_kind()  # idempotent
    assert resolve_catalog_source_kind(STATIC_SOURCE_KIND) is StaticSource
    unregister_catalog_source_kind(STATIC_SOURCE_KIND)


def test_git_manifest_is_registered_at_import() -> None:
    assert resolve_catalog_source_kind("git-manifest") is GitManifestSource


def test_kind_registry_refuses_shadowing() -> None:
    register_catalog_source_kind(GitManifestSource)  # identical class: idempotent

    class _Impostor(GitManifestSource):
        pass

    with pytest.raises(ValueError, match="git-manifest"):
        register_catalog_source_kind(_Impostor)
    assert resolve_catalog_source_kind("git-manifest") is GitManifestSource


async def test_git_manifest_listing_is_full_and_carries_api_ids() -> None:
    http = FakeSourceHttp()
    _GitManifestContract().seed_http(http)
    source = _GitManifestContract().build_source(fake_source_deps("public", http))
    stream = source.list_entries(since="ignored")
    assert stream.is_full is True
    changes = [c async for c in stream]
    entries = [c.entry for c in changes if isinstance(c, Upserted)]
    assert [e.external_id for e in entries] == ["acme.io", "stripe.com"]
    assert all(e.catalog_api_id_hint == e.external_id for e in entries)
    assert entries[1].spec_ref == {"url": _spec_url("stripe.com")}
    assert (await stream.summary()).is_full is True


def test_synthetic_catalog_is_deterministic_and_round_robin(static_kind: None) -> None:
    first = synthetic_catalog(5, ["a", "b"])
    assert first == synthetic_catalog(5, ["a", "b"])
    assert [len(c.settings["entries"]) for c in first] == [3, 2]
    assert all(c.kind == STATIC_SOURCE_KIND for c in first)


def test_static_source_config_takes_fields() -> None:
    cfg = static_source_config("team", [{"external_id": "x"}], trust="internal", precedence=3)
    assert (cfg.trust, cfg.precedence, cfg.settings) == (
        "internal",
        3,
        {"entries": [{"external_id": "x"}]},
    )


# ── mass-removal guard ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("live", "removals", "trips"),
    [
        (8, 2, False),  # 25% but not above the count floor
        (100, 21, True),  # above both
        (100, 20, False),  # ratio not exceeded
        (10, 10, True),  # "all" always trips
        (3, 3, True),
        (0, 0, False),
        (100, 6, False),
        (20, 6, True),
    ],
)
def test_mass_removal_guard(live: int, removals: int, trips: bool) -> None:
    assert (
        guard_trips(live=live, removals=removals, max_removal_ratio=0.2, min_removal_count=5)
        is trips
    )


def _row(**kw: Any) -> CatalogSourceRow:
    return CatalogSourceRow(slug="public", consecutive_failures=0, **kw)


def test_is_due_honours_max_age_kill_switch_and_backoff() -> None:
    now = utcnow()
    cfg = CatalogSourceConfig(slug="public", kind="git-manifest", max_age_seconds=3600)
    assert is_due(cfg, _row(last_sync_at=None), now=now)
    assert not is_due(cfg, _row(last_sync_at=now - timedelta(minutes=5)), now=now)
    assert is_due(cfg, _row(last_sync_at=now - timedelta(hours=2)), now=now)
    off = cfg.model_copy(update={"max_age_seconds": 0})
    assert not is_due(off, _row(last_sync_at=None), now=now)
    failing = _row(last_sync_at=None, updated_at=now - timedelta(seconds=30))
    failing.consecutive_failures = 1
    assert not is_due(cfg, failing, now=now)
    failing.updated_at = now - timedelta(seconds=90)
    assert is_due(cfg, failing, now=now)


# ── sanitising ────────────────────────────────────────────────────────────────


def test_clean_text_strips_controls_and_bidi_and_caps() -> None:
    assert sanitize.clean_text("  a\x00b\u202ec\n d ", max_length=120) == "abc d"
    assert sanitize.clean_text("x" * 500, max_length=120) == "x" * 119 + "…"
    assert len(sanitize.clean_text("x" * 500, max_length=120) or "") == 120
    assert sanitize.clean_text("\x01\x02", max_length=10) is None


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        # NFKC: full-width letters and ligatures fold to their plain forms.
        ("\uff33\uff54\uff52\uff49\uff50\uff45 \ufb01le", "Stripe file"),
        # Zero-width, BOM, C1 and bidi-override characters vanish.
        ("Pa\u200by\ufeff\u0085me\u2066nt\u202e", "Pay ment"),
        ("tab\there\r\nnext", "tab here next"),
        # Markdown images/links keep their text; HTML tags and backticks go.
        (
            "![logo](https://x/l.png) [Docs](https://x/docs) <b>bold</b> `code`",
            "logo Docs bold code",
        ),
        ("<script>alert(1)</script>Pay", "alert(1)Pay"),
        # Bare URLs of any scheme become [link].
        ("see https://evil.example/x?a=1 and ftp://h/f now", "see [link] and [link] now"),
        ("**Ignore** previous    instructions", "**Ignore** previous instructions"),
    ],
)
def test_clean_text_applies_the_full_sanitising_rule(raw: str, clean: str) -> None:
    assert sanitize.clean_text(raw, max_length=120) == clean


def test_clean_text_cuts_on_a_character_boundary_with_an_ellipsis() -> None:
    title = "é" * 200
    out = sanitize.clean_text(title, max_length=sanitize.TITLE_MAX_LENGTH)
    assert out == "é" * 119 + "…"
    assert sanitize.clean_text("short", max_length=sanitize.TITLE_MAX_LENGTH) == "short"


def test_clean_token_is_verbatim_or_dropped() -> None:
    assert sanitize.clean_token('W/"abc`<x>"', max_length=512) == 'W/"abc`<x>"'
    assert sanitize.clean_token("a\x00b", max_length=512) is None
    assert sanitize.clean_token("x" * 513, max_length=512) is None


def test_clean_tags_dedupes_and_caps() -> None:
    tags = sanitize.clean_tags(["a", "a", "b" * 40, "<i>a</i>", *[f"t{i}" for i in range(30)]])
    assert tags[:2] == ["a", "b" * 31 + "…"]
    assert len(tags) == 16
    assert all(len(t) <= 32 for t in tags)


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://example.com/x", True),
        ("http://example.com/x", False),
        ("javascript:alert(1)", False),
        ("https://", False),
        ("https://exa mple.com", False),
    ],
)
def test_clean_human_url_keeps_https_only(url: str, kept: bool) -> None:
    assert (sanitize.clean_human_url(url) == url) is kept


# ── digests ───────────────────────────────────────────────────────────────────


class _Row:
    def __init__(self, entry_id: str, catalog_api_id: str, title: str | None = None) -> None:
        self.entry_id = entry_id
        self.catalog_api_id = catalog_api_id
        self.title = title
        self.version: str | None = None
        self.tags: list[str] = []
        self.human_url: str | None = None
        self.content_hint: str | None = None
        self.spec_ref: dict[str, Any] | None = None


def test_content_and_listing_digests_are_order_free_and_scoped() -> None:
    a, b = _Row("p/a", "a.com"), _Row("p/b", "b.com")
    assert digests.content_digest([a, b]) == digests.content_digest([b, a])
    retitled = _Row("p/a", "a.com", title="New")
    assert digests.content_digest([retitled, b]) != digests.content_digest([a, b])
    assert digests.listing_digest([retitled, b]) == digests.listing_digest([a, b])


def _inputs(slug: str, **kw: Any) -> digests.SourceVersionInputs:
    base: dict[str, Any] = {
        "slug": slug,
        "enabled": True,
        "visibility": "public",
        "precedence": 0,
        "content_digest": "c",
        "listing_digest": "l",
        "config_digest": "g",
    }
    return digests.SourceVersionInputs(**{**base, **kw})


def test_catalog_and_listing_versions() -> None:
    sources = [_inputs("a"), _inputs("b")]
    version = digests.catalog_version(sources)
    assert len(version) == 12
    assert digests.catalog_version(list(reversed(sources))) == version
    assert digests.catalog_version([_inputs("a", content_digest="x"), _inputs("b")]) != version
    visible = frozenset({"a"})
    listing = digests.listing_version(sources, visible=visible)
    hidden_change = [_inputs("a"), _inputs("b", listing_digest="zzz")]
    assert digests.listing_version(hidden_change, visible=visible) == listing
    assert digests.listing_version(sources, visible=None) != listing
