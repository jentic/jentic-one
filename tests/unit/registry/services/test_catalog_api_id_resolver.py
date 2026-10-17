"""Unit tests for ``CatalogApiIdResolver``, ``exclude_unowned`` and the resolver digest."""

from __future__ import annotations

import pytest

from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog.entries import (
    CatalogApiIdMapping,
    CatalogApiIdResolver,
    build_catalog_entry,
    curated_vendors_of,
    exclude_unowned,
    resolver_inputs_digest,
)
from jentic_one.registry.services.catalog.sources.git_manifest import manifest_changes
from jentic_one.registry.services.catalog.sources.protocol import Upserted
from jentic_one.shared.config import CatalogSourceConfig


def _src(slug: str, trust: str = "untrusted", **kw: object) -> CatalogSourceConfig:
    return CatalogSourceConfig.model_validate(
        {"slug": slug, "kind": "git-manifest", "trust": trust, **kw}
    )


_SOURCES = {
    "public": _src("public", "curated"),
    "corp": _src("corp"),
    "partner": _src("partner", "internal"),
}


def _resolver(
    curated: set[str], mappings: list[CatalogApiIdMapping] | None = None
) -> CatalogApiIdResolver:
    return CatalogApiIdResolver(
        sources=_SOURCES, mappings=mappings or [], curated_owned=frozenset(curated)
    )


def _manifest_url(domain: str, sub: str, version: str = "v1") -> str:
    return (
        "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/"
        f"apis/openapi/{domain}/{sub}/{version}/apis.json"
    )


def test_public_catalog_api_ids_equal_todays_api_ids_byte_for_byte() -> None:
    doc = {
        "include": [
            {"url": _manifest_url(d, s)}
            for d, s in [
                ("stripe.com", "main"),
                ("googleapis.com", "admin"),
                ("finage.co.uk", "main"),
                ("Twilio.com", "api"),
                ("posthog.com", "posthog-api"),
            ]
        ]
    }
    today = [e.api_id for e in mb.parse_apis_json(doc)]
    resolver = _resolver(set())
    resolved = [
        resolver.resolve(
            source_slug="public",
            external_id=c.entry.external_id,
            hint=c.entry.catalog_api_id_hint,
        )
        for c in manifest_changes(doc)
        if isinstance(c, Upserted)
    ]
    assert resolved == today


@pytest.mark.parametrize(
    "hint", ["stripe.com", "Stripe.com", "stripe.com/payments", "api.stripe.com"]
)
def test_non_curated_hint_on_a_curated_vendor_is_namespaced(hint: str) -> None:
    resolver = _resolver({"stripe.com"})
    assert resolver.resolve(source_slug="corp", external_id="x", hint=hint) == f"corp~{hint}"


def test_curated_source_keeps_its_hint_verbatim() -> None:
    resolver = _resolver({"stripe.com"})
    assert resolver.resolve(source_slug="public", external_id="x", hint=" Stripe.com ") == (
        "Stripe.com"
    )


def test_unrelated_vendor_is_kept_verbatim() -> None:
    resolver = _resolver({"stripe.com"})
    assert resolver.resolve(source_slug="corp", external_id="x", hint="Acme.io/Billing") == (
        "Acme.io/Billing"
    )


def test_namespaced_form_colliding_with_a_curated_vendor_falls_back() -> None:
    # "corp~stripe.com" slugifies to "corp-stripe-com", a curated vendor.
    resolver = _resolver({"stripe.com", "corp-stripe.com"})
    assert resolver.resolve(source_slug="corp", external_id="Billing API", hint="stripe.com") == (
        "corp~billing-api"
    )


def test_mapping_overrides_and_applies_the_curated_casing() -> None:
    resolver = _resolver(
        {"Stripe.com"},
        [CatalogApiIdMapping(source="corp", external_id="pay", catalog_api_id="stripe.COM")],
    )
    assert resolver.resolve(source_slug="corp", external_id="pay", hint="stripe.com") == (
        "Stripe.com"
    )
    assert resolver.unmatched_mappings == ()


def test_unmatched_mapping_row_is_ignored_and_reported() -> None:
    row = CatalogApiIdMapping(source="corp", external_id="pay", catalog_api_id="stripe.com")
    resolver = _resolver(set(), [row])  # fresh install: no curated ids yet
    assert resolver.unmatched_mappings == (row,)
    assert resolver.resolve(source_slug="corp", external_id="pay", hint="stripe.com") == (
        "stripe.com"
    )
    resolver.report_unmatched()


@pytest.mark.parametrize("hint", [None, "", "not a valid id", "  "])
def test_hintless_or_invalid_entry_gets_a_namespaced_fallback(hint: str | None) -> None:
    resolver = _resolver(set())
    assert resolver.resolve(source_slug="corp", external_id="My API/v2", hint=hint) == (
        "corp~my-api-v2"
    )


def test_exclude_unowned_drops_unmapped_non_curated_claims() -> None:
    public = build_catalog_entry(
        source_slug="public",
        external_id="stripe.com",
        catalog_api_id="stripe.com",
        hint="stripe.com",
        title=None,
        version=None,
    )
    stale = build_catalog_entry(
        source_slug="corp",
        external_id="pay",
        catalog_api_id="stripe.com",
        hint="stripe.com",
        title=None,
        version=None,
    )
    namespaced = build_catalog_entry(
        source_slug="corp",
        external_id="pay2",
        catalog_api_id="corp~stripe.com",
        hint="stripe.com",
        title=None,
        version=None,
    )
    other = build_catalog_entry(
        source_slug="corp",
        external_id="acme",
        catalog_api_id="acme.io",
        hint="acme.io",
        title=None,
        version=None,
    )
    vendors = curated_vendors_of({"stripe.com"})
    kept = exclude_unowned(
        [public, stale, namespaced, other],
        curated_sources=frozenset({"public"}),
        curated_vendors=vendors,
        mappings=[],
    )
    assert kept == [public, namespaced, other]

    mapped = exclude_unowned(
        [stale],
        curated_sources=frozenset({"public"}),
        curated_vendors=vendors,
        mappings=[
            CatalogApiIdMapping(source="corp", external_id="pay", catalog_api_id="Stripe.com")
        ],
    )
    assert mapped == [stale]


def test_namespaced_entry_vendor_is_the_whole_id() -> None:
    entry = build_catalog_entry(
        source_slug="corp",
        external_id="pay",
        catalog_api_id="corp~stripe.com",
        hint="stripe.com",
        title=None,
        version=None,
    )
    assert entry.entry_id == "corp/pay"
    assert entry.vendor == "corp~stripe.com"
    assert entry.display_vendor == "stripe.com"


def test_resolver_inputs_digest_tracks_mappings_and_trust() -> None:
    sources = list(_SOURCES.values())
    row = CatalogApiIdMapping(source="corp", external_id="pay", catalog_api_id="stripe.com")
    base = resolver_inputs_digest(sources, [])
    assert resolver_inputs_digest(sources, []) == base
    assert resolver_inputs_digest(sources, [row]) != base
    flipped = [
        s.model_copy(update={"trust": "curated"}) if s.slug == "corp" else s for s in sources
    ]
    assert resolver_inputs_digest(flipped, []) != base
    # A field the resolver does not read leaves the digest alone.
    moved = [s.model_copy(update={"precedence": 99}) for s in sources]
    assert resolver_inputs_digest(moved, []) == base
