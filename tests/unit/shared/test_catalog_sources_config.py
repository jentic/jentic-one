"""Unit tests for ``catalog.sources`` config: deprecated aliases and startup validation."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from jentic_one.registry.services.catalog.sources import auth as auth_module
from jentic_one.registry.services.catalog.sources.validate import check_catalog_config
from jentic_one.shared.catalog.source_types import DEFAULT_PUBLIC_MANIFEST_URL
from jentic_one.shared.config import CatalogConfig, ConfigError


def _cfg(**data: Any) -> CatalogConfig:
    return CatalogConfig.model_validate(data)


def test_default_is_one_curated_public_git_manifest_source() -> None:
    cfg = _cfg()
    [public] = cfg.sources
    assert (public.slug, public.kind, public.trust) == ("public", "git-manifest", "curated")
    assert public.settings == {"url": DEFAULT_PUBLIC_MANIFEST_URL}
    assert cfg.legacy_source() is public
    check_catalog_config(cfg)


@pytest.mark.parametrize(
    ("key", "value", "replacement"),
    [
        (
            "manifest_url",
            "https://mirror.example/apis.json",
            "catalog.sources[slug=<legacy_source_slug>].settings.url",
        ),
        (
            "manifest_max_age_seconds",
            0,
            "catalog.sources[slug=<legacy_source_slug>].max_age_seconds",
        ),
    ],
)
def test_removed_manifest_key_fails_at_load_naming_its_replacement(
    key: str, value: object, replacement: str
) -> None:
    with pytest.raises(ValidationError) as exc:
        _cfg(**{key: value})
    assert f"catalog.{key} was removed: set {replacement} instead" in str(exc.value)


def test_unknown_catalog_key_fails_at_load() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _cfg(manifest_urls="https://typo.example/apis.json")


def _errors(cfg: CatalogConfig) -> str:
    with pytest.raises(ConfigError) as exc:
        check_catalog_config(cfg)
    return str(exc.value)


def test_unknown_kind_and_bad_settings_fail_validation() -> None:
    msg = _errors(
        _cfg(
            sources=[
                {"slug": "public", "kind": "git-manifest", "settings": {}},
                {"slug": "other", "kind": "nope"},
            ]
        )
    )
    assert "catalog.sources[slug=public].settings.url" in msg
    assert "catalog.sources[slug=other].kind: unknown source kind 'nope'" in msg


def test_duplicate_slug_fails_validation() -> None:
    src = {"slug": "public", "kind": "git-manifest", "settings": {"url": "https://x/a.json"}}
    assert "configured more than once" in _errors(_cfg(sources=[src, src]))


def test_mapping_row_with_unknown_source_fails_validation() -> None:
    msg = _errors(
        _cfg(
            catalog_api_id_mappings=[
                {"source": "ghost", "external_id": "x", "catalog_api_id": "stripe.com"}
            ]
        )
    )
    assert "catalog_api_id_mappings[0].source: unknown source 'ghost'" in msg


@pytest.mark.parametrize("bad", ["", "not valid", "a/b/c", "corp~stripe.com", "/x", "x" * 256])
def test_mapping_row_with_an_invalid_catalog_api_id_fails_validation(bad: str) -> None:
    msg = _errors(
        _cfg(
            catalog_api_id_mappings=[
                {"source": "public", "external_id": "x", "catalog_api_id": bad}
            ]
        )
    )
    assert "catalog_api_id_mappings[0].catalog_api_id" in msg


def _git(slug: str, trust: str, precedence: int, **kw: object) -> dict[str, object]:
    return {
        "slug": slug,
        "kind": "git-manifest",
        "trust": trust,
        "precedence": precedence,
        "settings": {"url": f"https://{slug}.example/apis.json"},
        **kw,
    }


def test_untrusted_source_ranked_above_a_curated_one_fails_validation() -> None:
    sources = [_git("public", "curated", 10), _git("low", "curated", 5), _git("x", "untrusted", 6)]
    msg = _errors(_cfg(sources=sources))
    assert "catalog.sources[slug=x].precedence: an untrusted source (6)" in msg


@pytest.mark.parametrize("visibility", ["org", "restricted"])
def test_curated_source_that_is_not_public_fails_validation(visibility: str) -> None:
    sources = [_git("public", "curated", 10), _git("corp", "curated", 5, visibility=visibility)]
    msg = _errors(_cfg(sources=sources))
    assert "catalog.sources[slug=corp].trust: a curated source must be public" in msg


def test_untrusted_and_internal_precedence_within_bounds_are_accepted() -> None:
    sources = [
        _git("public", "curated", 10),
        _git("x", "untrusted", 10),
        _git("corp", "internal", 50),
        # A disabled curated source does not set the bound.
        _git("old", "curated", 0, enabled=False),
    ]
    check_catalog_config(_cfg(sources=sources))


def test_restricted_source_needs_readers_and_others_reject_them() -> None:
    restricted = {
        "slug": "secret",
        "kind": "git-manifest",
        "visibility": "restricted",
        "settings": {"url": "https://x/a.json"},
    }
    assert "needs permissions or actor_ids" in _errors(_cfg(sources=[restricted]))
    bad_readers = {**restricted, "readers": {"permissions": ["nope:read"], "actor_ids": ["bob"]}}
    msg = _errors(_cfg(sources=[bad_readers]))
    assert "unknown permission 'nope:read'" in msg
    assert "'bob' is not a user or agent id" in msg
    public_with_readers = {
        "slug": "public",
        "kind": "git-manifest",
        "settings": {"url": "https://x/a.json"},
        "readers": {"actor_ids": ["usr_1"]},
    }
    assert "only a restricted source" in _errors(_cfg(sources=[public_with_readers]))


def _with_auth(auth: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": "public",
        "kind": "git-manifest",
        "settings": {"url": "https://x/a.json"},
        "auth": auth,
    }


def test_unknown_auth_type_fails_validation() -> None:
    errors = _errors(_cfg(sources=[_with_auth({"type": "vault", "env": "TOKEN"})]))
    assert "unknown auth type 'vault'" in errors


def test_malformed_env_auth_fails_validation() -> None:
    errors = _errors(_cfg(sources=[_with_auth({"type": "header_from_env", "env": "TOKEN"})]))
    assert "sources[slug=public].auth: auth type 'header_from_env' needs header" in errors


def test_env_auth_is_accepted_without_reading_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CATALOG_TOKEN", raising=False)
    check_catalog_config(
        _cfg(sources=[_with_auth({"type": "bearer_from_env", "env": "CATALOG_TOKEN"})])
    )


def test_source_egress_rejects_cloud_metadata_at_load() -> None:
    src = {
        **_with_auth({"type": "none"}),
        "egress": {"allowed_private_subnets": ["169.254.0.0/16"]},
    }
    with pytest.raises(ValidationError, match="cloud-metadata"):
        _cfg(sources=[src])


@pytest.mark.parametrize(
    "subnet",
    [
        "::ffff:169.254.169.254/128",
        "::ffff:0:0/96",
        "::ffff:169.254.0.0/112",
        "64:ff9b::a9fe:a9fe/128",  # NAT64 form of 169.254.169.254
        "2002:a9fe:a9fe::/48",  # 6to4 form of 169.254.169.254
        "::/0",
    ],
)
def test_source_egress_rejects_embedded_ipv4_metadata(subnet: str) -> None:
    src = {**_with_auth({"type": "none"}), "egress": {"allowed_private_subnets": [subnet]}}
    with pytest.raises(ValidationError, match="cloud-metadata"):
        _cfg(sources=[src])


def test_source_egress_unwraps_ipv4_mapped_subnets() -> None:
    src = {
        **_with_auth({"type": "none"}),
        "egress": {"allowed_private_subnets": ["::ffff:10.20.0.0/112", "fd00:1::/64"]},
    }
    egress = _cfg(sources=[src]).sources[0].egress
    assert [str(n) for n in egress.allowed_private_subnets] == ["10.20.0.0/16", "fd00:1::/64"]


def test_registered_credentialed_auth_type_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source requests carry the resolver's headers, so a registered type passes."""
    monkeypatch.setitem(auth_module._RESOLVERS, "token", lambda _cfg: auth_module.NoAuthResolver())
    src = {
        "slug": "public",
        "kind": "git-manifest",
        "settings": {"url": "https://x/a.json"},
        "auth": {"type": "token"},
    }
    check_catalog_config(_cfg(sources=[src]))


def test_invalid_slug_is_rejected_at_load() -> None:
    with pytest.raises(ValueError, match="must match"):
        _cfg(sources=[{"slug": "Bad_Slug", "kind": "git-manifest"}])
