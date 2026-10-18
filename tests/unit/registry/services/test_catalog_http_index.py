"""Unit tests for the ``http-index`` source kind, source auth resolvers and per-source egress."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from jentic_one.registry.services.catalog.sources.auth import (
    BearerFromEnv,
    HeaderFromEnv,
    NoAuthResolver,
    build_source_auth,
    register_source_auth_type,
    registered_source_auth_types,
)
from jentic_one.registry.services.catalog.sources.deps import SourceDeps, source_ingest_config
from jentic_one.registry.services.catalog.sources.http_index import (
    DEFAULT_HTTP_INDEX_ENTRIES,
    MAX_HTTP_INDEX_ENTRIES,
    HttpIndexSettings,
    HttpIndexSource,
    index_changes,
)
from jentic_one.registry.services.catalog.sources.protocol import (
    CatalogSource,
    JsonScalar,
    ListingTooLargeError,
    ListStream,
    SourceCapabilities,
    SourceHealth,
    SourceUnavailableError,
    SpecFetch,
    SpecGoneError,
    Upserted,
)
from jentic_one.registry.services.catalog.sources.registry import resolve_catalog_source_kind
from jentic_one.registry.services.catalog.sources.streams import FullListStream
from jentic_one.shared.config import (
    CatalogSourceConfig,
    EgressConfig,
    IngestConfig,
    SourceAuthConfig,
    SourceEgressConfig,
)
from jentic_one.testing import FakeSourceHttp, SourceContract
from jentic_one.testing.catalog_source import contract_checks, fake_source_deps

_INDEX = "https://index.example/apis.json"
_STRIPE = "https://index.example/specs/stripe.json"
_OTHER_HOST = "https://cdn.other.example/acme.yaml"


def _index_doc() -> dict[str, Any]:
    return {
        "name": "Example index",
        "apis": [
            {
                "aid": "stripe.com",
                "name": "Stripe",
                "version": "2024-06-20",
                "humanURL": "https://stripe.com/docs",
                "tags": ["payments", 7],
                "x-catalog-api-id": "stripe.com",
                "properties": [
                    {"type": "Documentation", "url": "https://stripe.com/docs"},
                    {"type": "OpenAPI", "url": "specs/stripe.json"},
                ],
            },
            {
                "aid": "acme.io",
                "name": "Acme",
                "properties": [{"type": "Swagger", "url": _OTHER_HOST}],
            },
            {"aid": "card-only", "name": "No spec"},
            {"name": "no aid is skipped"},
            {"aid": "stripe.com", "name": "duplicate aid is skipped"},
            "not-a-mapping",
        ],
    }


def _index_source(deps: SourceDeps) -> HttpIndexSource:
    return HttpIndexSource.build(
        CatalogSourceConfig(slug="contract", kind="http-index", settings={"index_url": _INDEX}),
        deps=deps,
    )


def _static_cfg() -> CatalogSourceConfig:
    return CatalogSourceConfig(
        slug="contract",
        kind="http-index",
        settings={
            "specs": [
                {"external_id": "stripe", "spec_url": _STRIPE, "title": "Stripe", "tags": ["pay"]},
                {"external_id": "acme", "spec_url": _OTHER_HOST, "catalog_api_id": "acme.io"},
            ]
        },
    )


class _HttpIndexContract(SourceContract):
    def build_source(self, deps: SourceDeps) -> CatalogSource:
        return _index_source(deps)

    def seed_http(self, http: FakeSourceHttp) -> None:
        http.json_responses[_INDEX] = _index_doc()
        http.bytes_responses[_STRIPE] = b'{"openapi": "3.1.0"}'
        http.bytes_responses[_OTHER_HOST] = b"openapi: 3.0.3\n"


class _HttpIndexStaticContract(SourceContract):
    def build_source(self, deps: SourceDeps) -> CatalogSource:
        return HttpIndexSource.build(_static_cfg(), deps=deps)

    def seed_http(self, http: FakeSourceHttp) -> None:
        http.bytes_responses[_STRIPE] = b'{"openapi": "3.1.0"}'
        http.bytes_responses[_OTHER_HOST] = b"openapi: 3.0.3\n"


_CHECKS = [
    (kit, name)
    for kit in (_HttpIndexContract, _HttpIndexStaticContract)
    for name, _ in contract_checks(kit())
]


@pytest.mark.parametrize(("kit", "name"), _CHECKS, ids=[f"{k.__name__}-{n}" for k, n in _CHECKS])
async def test_source_contract(kit: type[SourceContract], name: str) -> None:
    result = getattr(kit(), name)()
    if hasattr(result, "__await__"):
        await result


# ── a connector that opens its own transport fails the contract ───────────────


class _SelfTransportSettings(BaseModel):
    pass


class _SelfTransportSource:
    """Bypasses ``SourceDeps.http`` by building its own transport."""

    kind: ClassVar[str] = "self-transport"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities.CONDITIONAL_FETCH
    config_model: ClassVar[type[BaseModel]] = _SelfTransportSettings

    def __init__(self, deps: SourceDeps) -> None:
        self._inner = _index_source(deps)

    def list_entries(self, *, since: str | None) -> ListStream:
        return self._inner.list_entries(since=since)

    async def fetch_spec(
        self, ref: Mapping[str, JsonScalar], *, validator: str | None = None
    ) -> SpecFetch:
        httpx.AsyncHTTPTransport()
        return await self._inner.fetch_spec(ref, validator=validator)

    async def health(self) -> SourceHealth:
        return await self._inner.health()


class _SelfTransportContract(_HttpIndexContract):
    def build_source(self, deps: SourceDeps) -> CatalogSource:
        return _SelfTransportSource(deps)


async def test_connector_opening_its_own_transport_fails_the_contract() -> None:
    with pytest.raises(AssertionError, match="opened its own HTTP client or transport"):
        await _SelfTransportContract().test_no_client_built_outside_deps()


# ── http-index behaviour ──────────────────────────────────────────────────────


def test_http_index_is_registered_at_import() -> None:
    assert resolve_catalog_source_kind("http-index") is HttpIndexSource
    assert HttpIndexSource.capabilities == SourceCapabilities.CONDITIONAL_FETCH


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"index_url": _INDEX, "specs": [{"external_id": "a", "spec_url": _STRIPE}]},
        {"specs": []},
        {
            "specs": [
                {"external_id": "a", "spec_url": _STRIPE},
                {"external_id": "a", "spec_url": _OTHER_HOST},
            ]
        },
        {"index_url": "ftp://index.example/apis.json"},
        {"index_url": _INDEX, "unknown": 1},
    ],
)
def test_settings_need_exactly_one_listing(settings: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        HttpIndexSettings.model_validate(settings)


def test_index_changes_map_apis_json() -> None:
    changes = index_changes(_index_doc(), index_url=_INDEX)
    entries = {c.entry.external_id: c.entry for c in changes if isinstance(c, Upserted)}
    assert list(entries) == ["stripe.com", "acme.io", "card-only"]
    stripe = entries["stripe.com"]
    assert stripe.spec_ref == {"url": _STRIPE}
    assert stripe.catalog_api_id_hint == "stripe.com"
    assert stripe.title == "Stripe"
    assert stripe.version == "2024-06-20"
    assert stripe.human_url == "https://stripe.com/docs"
    assert stripe.tags == ("payments",)
    assert entries["acme.io"].spec_ref == {"url": _OTHER_HOST}
    assert entries["card-only"].spec_ref is None


def test_index_changes_tolerate_a_malformed_document() -> None:
    assert index_changes({"apis": "nope"}, index_url=_INDEX) == []
    assert index_changes({}, index_url=_INDEX) == []
    doc = {"apis": [{"aid": "x", "properties": [{"type": "OpenAPI", "url": "javascript:x"}]}]}
    [change] = index_changes(doc, index_url=_INDEX)
    assert isinstance(change, Upserted) and change.entry.spec_ref is None


async def test_listing_is_always_full() -> None:
    http = FakeSourceHttp()
    http.json_responses[_INDEX] = _index_doc()
    source = _index_source(fake_source_deps("s", http))
    stream = source.list_entries(since="previous-watermark")
    assert isinstance(stream, FullListStream) and stream.is_full
    first = [c async for c in stream]
    watermark = (await stream.summary()).watermark
    again = source.list_entries(since=watermark)
    assert [c async for c in again] == first
    assert (await again.summary()).watermark == watermark


async def test_connector_leaves_the_credential_decision_to_the_source_client() -> None:
    """The connector asks for credentials on every fetch; ``SourceDeps.http`` filters by origin.

    Which origins actually get them (``credential_urls`` plus ``auth.origins``) is
    pinned end to end in ``test_catalog_source_deps.py``.
    """
    http = FakeSourceHttp()
    http.json_responses[_INDEX] = _index_doc()
    http.bytes_responses[_STRIPE] = b"{}"
    http.bytes_responses[_OTHER_HOST] = b"{}"
    source = _index_source(fake_source_deps("s", http))
    await source.fetch_spec({"url": _STRIPE})
    await source.fetch_spec({"url": _OTHER_HOST})
    [change async for change in source.list_entries(since=None)]
    assert http.authenticated == [_STRIPE, _OTHER_HOST, _INDEX]


def test_credential_urls_name_the_index_or_the_static_specs() -> None:
    index = HttpIndexSettings.model_validate({"index_url": _INDEX})
    assert HttpIndexSource.credential_urls(index) == [_INDEX]
    static = HttpIndexSettings.model_validate(_static_cfg().settings)
    assert HttpIndexSource.credential_urls(static) == [_STRIPE, _OTHER_HOST]


async def test_static_specs_fetch_and_report_healthy() -> None:
    http = FakeSourceHttp()
    http.bytes_responses[_STRIPE] = b"{}"
    http.bytes_responses[_OTHER_HOST] = b"{}"
    source = HttpIndexSource.build(_static_cfg(), deps=fake_source_deps("s", http))
    for url in (_STRIPE, _OTHER_HOST):
        await source.fetch_spec({"url": url})
    assert (await source.health()).ok
    assert http.requests == [_STRIPE, _OTHER_HOST]


def test_index_past_the_entry_cap_fails_instead_of_truncating() -> None:
    """A truncated full listing would remove the tail; the run must apply nothing."""
    items = [{"aid": f"api-{i}"} for i in range(DEFAULT_HTTP_INDEX_ENTRIES + 1)]
    with pytest.raises(ListingTooLargeError):
        index_changes({"apis": items}, index_url=_INDEX)
    assert len(index_changes({"apis": items[:-1]}, index_url=_INDEX)) == DEFAULT_HTTP_INDEX_ENTRIES


async def test_max_entries_sets_the_cap_up_to_its_hard_bound() -> None:
    items = [{"aid": f"api-{i}"} for i in range(4)]
    with pytest.raises(ListingTooLargeError):
        index_changes({"apis": items}, index_url=_INDEX, max_entries=3)
    http = FakeSourceHttp()
    http.json_responses[_INDEX] = {"apis": items}
    small = CatalogSourceConfig.model_validate(
        {"slug": "s", "kind": "http-index", "settings": {"index_url": _INDEX, "max_entries": 3}}
    )
    with pytest.raises(ListingTooLargeError):
        [
            c
            async for c in HttpIndexSource.build(
                small, deps=fake_source_deps("s", http)
            ).list_entries(since=None)
        ]
    assert (
        HttpIndexSettings.model_validate(
            {"index_url": _INDEX, "max_entries": MAX_HTTP_INDEX_ENTRIES}
        ).max_entries
        == MAX_HTTP_INDEX_ENTRIES
    )
    for bad in (0, MAX_HTTP_INDEX_ENTRIES + 1):
        with pytest.raises(ValidationError):
            HttpIndexSettings.model_validate({"index_url": _INDEX, "max_entries": bad})


def test_index_listed_spec_urls_with_userinfo_are_dropped() -> None:
    """The HTTP client would turn ``user:pass@`` into its own Authorization header,
    letting the index pick it: such an entry is listed card-only."""
    doc = {
        "apis": [
            {
                "aid": "a",
                "properties": [{"type": "OpenAPI", "url": "https://u:p@cdn.example/a.json"}],
            },
            {
                "aid": "b",
                "properties": [{"type": "OpenAPI", "url": "https://u@cdn.example/b.json"}],
            },
            {"aid": "c", "properties": [{"type": "OpenAPI", "url": "//x:y@cdn.example/c.json"}]},
        ]
    }
    changes = index_changes(doc, index_url=_INDEX)
    assert [c.entry.spec_ref for c in changes if isinstance(c, Upserted)] == [None, None, None]


async def test_fetch_spec_refuses_a_stored_url_with_userinfo() -> None:
    http = FakeSourceHttp()
    source = _index_source(fake_source_deps("s", http))
    with pytest.raises(SpecGoneError):
        await source.fetch_spec({"url": "https://u:p@cdn.example/a.json"})
    assert http.requests == []


@pytest.mark.parametrize(
    "settings",
    [
        {"index_url": "https://u:secret@index.example/apis.json"},
        {"specs": [{"external_id": "a", "spec_url": "https://u:secret@cdn.example/a.json"}]},
    ],
)
def test_configured_urls_with_userinfo_are_rejected_without_echoing_them(
    settings: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError) as exc:
        HttpIndexSettings.model_validate(settings)
    assert "secret" not in str(exc.value)
    assert "must not carry credentials" in str(exc.value)


async def test_fetch_spec_without_url_is_gone() -> None:
    source = _index_source(fake_source_deps("s"))
    with pytest.raises(SpecGoneError):
        await source.fetch_spec({"path": "x"})
    with pytest.raises(SpecGoneError):
        await source.fetch_spec({"url": "file:///etc/passwd"})


@pytest.mark.parametrize(
    ("error", "reason"),
    [(SourceUnavailableError("down"), "unreachable"), (SpecGoneError("gone"), "misconfigured")],
)
async def test_health_reports_the_index(error: Exception, reason: str) -> None:
    http = FakeSourceHttp()
    http.fail_with = error
    health = await _index_source(fake_source_deps("s", http)).health()
    assert not health.ok and health.reason == reason


# ── auth resolvers ────────────────────────────────────────────────────────────

_SECRET = "s3cr3t-value"  # pragma: allowlist secret


async def test_builtin_auth_types(monkeypatch: pytest.MonkeyPatch) -> None:
    assert {"none", "bearer_from_env", "header_from_env"} <= registered_source_auth_types()
    assert isinstance(build_source_auth(SourceAuthConfig()), NoAuthResolver)
    monkeypatch.setenv("CATALOG_TOKEN", _SECRET)
    bearer = build_source_auth(SourceAuthConfig(type="bearer_from_env", env="CATALOG_TOKEN"))
    assert isinstance(bearer, BearerFromEnv)
    assert await bearer.headers() == {"Authorization": f"Bearer {_SECRET}"}
    header = build_source_auth(
        SourceAuthConfig(type="header_from_env", env="CATALOG_TOKEN", header="X-Api-Key")
    )
    assert isinstance(header, HeaderFromEnv)
    assert await header.headers() == {"X-Api-Key": _SECRET}


async def test_auth_reads_the_environment_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = build_source_auth(SourceAuthConfig(type="bearer_from_env", env="CATALOG_TOKEN"))
    monkeypatch.delenv("CATALOG_TOKEN", raising=False)
    with pytest.raises(SourceUnavailableError, match="CATALOG_TOKEN") as excinfo:
        await resolver.headers()
    assert excinfo.value.retry_after_s is None
    monkeypatch.setenv("CATALOG_TOKEN", "first")
    assert await resolver.headers() == {"Authorization": "Bearer first"}
    monkeypatch.setenv("CATALOG_TOKEN", "second")
    assert await resolver.headers() == {"Authorization": "Bearer second"}
    monkeypatch.setenv("CATALOG_TOKEN", f"{_SECRET}\r\nX-Injected: 1")
    with pytest.raises(SourceUnavailableError) as excinfo:
        await resolver.headers()
    assert _SECRET not in str(excinfo.value)


@pytest.mark.parametrize(
    "cfg",
    [
        SourceAuthConfig(type="vault"),
        SourceAuthConfig(type="bearer_from_env"),
        SourceAuthConfig(type="bearer_from_env", env="1BAD"),
        SourceAuthConfig(type="bearer_from_env", env="TOKEN", header="X-Key"),
        SourceAuthConfig(type="header_from_env", env="TOKEN"),
        SourceAuthConfig(type="header_from_env", env="TOKEN", header="Bad Header"),
        SourceAuthConfig(type="header_from_env", env="TOKEN", header="Host"),
        SourceAuthConfig(type="none", env="TOKEN"),
    ],
)
def test_bad_auth_config_is_rejected(cfg: SourceAuthConfig) -> None:
    with pytest.raises(ValueError):
        build_source_auth(cfg)


def test_auth_env_that_is_not_a_name_is_never_echoed() -> None:
    """An ``env`` holding the token itself (a common slip) is refused without repeating it."""
    with pytest.raises(ValueError) as excinfo:
        build_source_auth(SourceAuthConfig(type="bearer_from_env", env=f"ghp-{_SECRET}"))
    assert "auth.env is not an environment variable name" in str(excinfo.value)
    assert _SECRET not in str(excinfo.value)


def test_auth_registry_refuses_shadowing() -> None:
    register_source_auth_type("bearer_from_env", BearerFromEnv)  # identical: idempotent
    with pytest.raises(ValueError, match="already registered"):
        register_source_auth_type("bearer_from_env", HeaderFromEnv)


# ── per-source egress ─────────────────────────────────────────────────────────


def test_source_egress_widens_only_its_own_policy() -> None:
    ingest = IngestConfig(egress=EgressConfig(allowed_internal_domains=["corp.example"]))
    plain = CatalogSourceConfig(slug="a", kind="http-index", settings={"index_url": _INDEX})
    assert source_ingest_config(ingest, plain) is ingest
    widened_cfg = plain.model_copy(
        update={
            "egress": SourceEgressConfig.model_validate(
                {
                    "allowed_private_subnets": ["10.20.0.0/16"],
                    "allowed_internal_domains": ["catalog.internal"],
                }
            )
        }
    )
    widened = source_ingest_config(ingest, widened_cfg)
    assert widened is not ingest
    assert [str(n) for n in widened.egress.allowed_private_subnets] == ["10.20.0.0/16"]
    assert set(widened.egress.allowed_internal_domains) == {"corp.example", "catalog.internal"}
    assert ingest.egress.allowed_private_subnets == []


@pytest.mark.parametrize(
    "egress",
    [
        {"allowed_private_subnets": ["169.254.169.254/32"]},
        {"allowed_private_subnets": ["169.254.0.0/16"]},
        {"allowed_private_subnets": ["0.0.0.0/0"]},
        {"allowed_private_subnets": ["fd00:ec2::254/128"]},
        {"allowed_private_subnets": ["not-a-subnet"]},
        {"allowed_internal_domains": ["metadata.google.internal"]},
        {"allowed_internal_domains": ["google.internal"]},
    ],
)
def test_source_egress_rejects_cloud_metadata(egress: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SourceEgressConfig.model_validate(egress)
