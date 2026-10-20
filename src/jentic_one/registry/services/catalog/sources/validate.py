"""Startup validation of ``catalog.sources`` (configuration-intrinsic checks only).

Runs once at registry startup, before the source scanner starts. It checks what
the configuration alone can decide (slugs, kinds, kind settings, auth types,
reader ACLs, mapping rows, an untrusted source ranked above a curated one); it
never contacts a source.
"""

from __future__ import annotations

from pydantic import ValidationError

from jentic_one.registry.services.catalog.sources.auth import registered_source_auth_types
from jentic_one.registry.services.catalog.sources.registry import (
    registered_catalog_source_kinds,
    resolve_catalog_source_kind,
)
from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS
from jentic_one.shared.catalog.api_id import normalise_catalog_api_id
from jentic_one.shared.config import CatalogConfig, ConfigError, SourceReadersConfig
from jentic_one.shared.context import Context

_ACTOR_ID_PREFIXES = ("usr_", "agnt_")


def validate_catalog_sources(ctx: Context) -> None:
    """Raise ``ConfigError`` naming every invalid ``catalog.sources`` setting."""
    check_catalog_config(ctx.config.catalog)


def check_catalog_config(catalog: CatalogConfig) -> None:
    """The checks behind ``validate_catalog_sources``, on a bare ``CatalogConfig``."""
    errors: list[str] = []
    sources = catalog.sources
    slugs = [s.slug for s in sources]
    for slug in sorted({s for s in slugs if slugs.count(s) > 1}):
        errors.append(f"catalog.sources: slug {slug!r} is configured more than once")

    kinds = registered_catalog_source_kinds()
    auth_types = registered_source_auth_types()
    for source in sources:
        where = f"catalog.sources[slug={source.slug}]"
        if source.kind not in kinds:
            errors.append(
                f"{where}.kind: unknown source kind {source.kind!r} "
                f"(registered: {', '.join(sorted(kinds)) or 'none'})"
            )
        else:
            model = resolve_catalog_source_kind(source.kind).config_model
            try:
                model.model_validate(source.settings)
            except ValidationError as exc:
                for err in exc.errors():
                    loc = ".".join(str(p) for p in err["loc"])
                    errors.append(f"{where}.settings.{loc}: {err['msg']}")
        if source.auth.type not in auth_types:
            errors.append(
                f"{where}.auth.type: unknown auth type {source.auth.type!r} "
                f"(registered: {', '.join(sorted(auth_types))})"
            )
        elif source.auth.type != "none":
            # Source requests carry no auth headers, so a credentialed source
            # would quietly fetch unauthenticated.
            errors.append(
                f"{where}.auth.type: {source.auth.type!r} is not supported: catalog "
                "source requests carry no credentials, so only 'none' is accepted"
            )
        errors.extend(_reader_errors(where, source.visibility, source.readers))

    known = set(slugs)
    for index, row in enumerate(catalog.catalog_api_id_mappings):
        where = f"catalog.catalog_api_id_mappings[{index}]"
        if row.source not in known:
            errors.append(f"{where}.source: unknown source {row.source!r}")
        if normalise_catalog_api_id(row.catalog_api_id) is None:
            errors.append(
                f"{where}.catalog_api_id: {row.catalog_api_id!r} is not a valid catalog_api_id "
                "(domain[/sub], at most 255 characters, no whitespace or '~')"
            )

    for source in sources:
        if source.trust == "curated" and source.visibility != "public":
            # Curated ownership applies to every caller's view (an untrusted
            # entry it owns is served as ``{slug}~{id}``), so a hidden curated
            # source would reveal what it lists to callers who can't read it.
            errors.append(
                f"catalog.sources[slug={source.slug}].trust: a curated source must be "
                f"public (visibility is {source.visibility!r}); use 'internal' instead"
            )

    curated = [s for s in sources if s.trust == "curated" and s.enabled]
    if curated:
        floor = min(s.precedence for s in curated)
        for source in sources:
            if source.trust == "untrusted" and source.precedence > floor:
                errors.append(
                    f"catalog.sources[slug={source.slug}].precedence: an untrusted source "
                    f"({source.precedence}) cannot rank above a curated one ({floor})"
                )

    legacy = catalog.legacy_source_slug
    if legacy in known:
        legacy_kind = next(s.kind for s in sources if s.slug == legacy)
        if legacy_kind != "git-manifest":
            errors.append(
                f"catalog.legacy_source_slug: {legacy!r} must be a git-manifest source "
                f"(is {legacy_kind!r})"
            )

    if errors:
        raise ConfigError("invalid catalog configuration:\n  " + "\n  ".join(errors))


def _reader_errors(where: str, visibility: str, readers: SourceReadersConfig | None) -> list[str]:
    if visibility != "restricted":
        if readers is not None:
            return [f"{where}.readers: only a restricted source takes a reader ACL"]
        return []
    if readers is None or not (readers.permissions or readers.actor_ids):
        return [f"{where}.readers: a restricted source needs permissions or actor_ids"]
    errors = [
        f"{where}.readers.permissions: unknown permission {p!r}"
        for p in readers.permissions
        if p not in ALL_PERMISSIONS
    ]
    errors.extend(
        f"{where}.readers.actor_ids: {a!r} is not a user or agent id"
        for a in readers.actor_ids
        if not a.startswith(_ACTOR_ID_PREFIXES)
    )
    return errors
