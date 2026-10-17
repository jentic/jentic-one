"""Unit tests for ``catalog.sources`` config: deprecated aliases and startup validation."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from jentic_one.registry.services.catalog.sources.validate import check_catalog_config
from jentic_one.shared.catalog.source_types import DEFAULT_PUBLIC_MANIFEST_URL
from jentic_one.shared.config import CatalogConfig, ConfigError


def _cfg(**data: Any) -> CatalogConfig:
    return CatalogConfig.model_validate(data)


def test_default_is_one_curated_public_git_manifest_source() -> None:
    [public] = _cfg().effective_sources()
    assert (public.slug, public.kind, public.trust) == ("public", "git-manifest", "curated")
    assert public.settings == {"url": DEFAULT_PUBLIC_MANIFEST_URL}
    assert _cfg().deprecated_keys_set() == []
    check_catalog_config(_cfg())


def test_manifest_url_alias_maps_onto_the_legacy_source_with_a_warning() -> None:
    cfg = _cfg(manifest_url="https://mirror.example/apis.json")
    assert cfg.legacy_source() is not None
    assert cfg.legacy_source().settings["url"] == "https://mirror.example/apis.json"  # type: ignore[union-attr]
    assert cfg.legacy_manifest_url() == "https://mirror.example/apis.json"
    with capture_logs() as logs:
        check_catalog_config(cfg)
    [warning] = [e for e in logs if e["event"] == "catalog_config_deprecated_key"]
    assert warning["key"] == "catalog.manifest_url"
    assert warning["replacement"] == "catalog.sources[slug=public].settings.url"


def test_manifest_max_age_zero_turns_off_the_legacy_source_auto_sync() -> None:
    cfg = _cfg(manifest_max_age_seconds=0)
    assert cfg.legacy_source().max_age_seconds == 0  # type: ignore[union-attr]
    assert cfg.legacy_max_age_seconds() == 0


def test_disabled_legacy_source_disables_the_snapshot_auto_refresh() -> None:
    cfg = _cfg(
        sources=[
            {
                "slug": "public",
                "kind": "git-manifest",
                "enabled": False,
                "settings": {"url": DEFAULT_PUBLIC_MANIFEST_URL},
            }
        ]
    )
    assert cfg.legacy_max_age_seconds() == 0


def test_alias_assigned_after_load_is_honoured() -> None:
    cfg = _cfg()
    cfg.manifest_url = "https://late.example/apis.json"
    assert cfg.legacy_manifest_url() == "https://late.example/apis.json"


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


def test_unknown_auth_type_fails_validation() -> None:
    src = {
        "slug": "public",
        "kind": "git-manifest",
        "settings": {"url": "https://x/a.json"},
        "auth": {"type": "bearer_from_env", "env": "TOKEN"},
    }
    assert "unknown auth type 'bearer_from_env'" in _errors(_cfg(sources=[src]))


def test_deprecated_key_without_a_legacy_source_fails_validation() -> None:
    cfg = _cfg(
        manifest_url="https://x/a.json",
        sources=[{"slug": "corp", "kind": "git-manifest", "settings": {"url": "https://y"}}],
    )
    assert "names no configured source" in _errors(cfg)


def test_invalid_slug_is_rejected_at_load() -> None:
    with pytest.raises(ValueError, match="must match"):
        _cfg(sources=[{"slug": "Bad_Slug", "kind": "git-manifest"}])
