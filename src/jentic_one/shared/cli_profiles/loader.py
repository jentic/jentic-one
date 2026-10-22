"""Load and validate the CLI profiles shipped in this package.

Every ``*.yaml`` next to this module is one profile
(:class:`~jentic_one.shared.cli_profiles.models.CliProfile`). Loading is
strict: on top of the model's own checks it verifies, against the package's
data, that

- a ``shipped`` source names a spec file in ``specs/`` that parses as OpenAPI
  3, whose ``info.x-vendor`` is one of the vendor's registry domains and whose
  ``info.version`` is the declared identity's;
- a ``builtin`` source names a built-in API (:mod:`jentic_one.shared.builtin_apis`)
  with its identity;
- every declared identity is the one a catalog import (or the shipped import)
  registers the API under;
- the vendor has a ``cli_facades.vendors`` entry whose addressing mode is the
  profile's facade mode;
- every ``include`` names a known rule set and every preset expands into
  valid rules.

A shipped profile that fails any check raises at import time, so a broken
manifest never reaches a running deployment (the CI test loads them too).

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Mapping
from functools import cache
from types import MappingProxyType
from typing import Any

import yaml
from pydantic import ValidationError

from jentic_one.shared.access_guidance import catalog_import_name
from jentic_one.shared.builtin_apis import (
    BUILTIN_CATALOG_API_IDS,
    GITHUB_GIT_API_NAME,
    GITHUB_GIT_API_VENDOR,
    GITHUB_GIT_API_VERSION,
    GITHUB_GIT_CATALOG_API_ID,
)
from jentic_one.shared.cli_profiles.models import (
    ApiIdentity,
    ApiRole,
    CliProfile,
    ProfileApi,
    SourceKind,
)
from jentic_one.shared.cli_profiles.presets import PresetError, expand_preset
from jentic_one.shared.cli_profiles.rule_sets import RULE_SETS
from jentic_one.shared.config import CLI_FACADE_VENDORS, CliFacadeVendorsConfig
from jentic_one.shared.models.api_identity import slugify_api_field
from jentic_one.shared.vendor_domain import vendor_from_api_id

_PACKAGE = "jentic_one.shared.cli_profiles"

#: The identity each built-in API is served under.
_BUILTIN_IDENTITIES: Mapping[str, ApiIdentity] = MappingProxyType(
    {
        GITHUB_GIT_CATALOG_API_ID: ApiIdentity(
            vendor=GITHUB_GIT_API_VENDOR, name=GITHUB_GIT_API_NAME, version=GITHUB_GIT_API_VERSION
        )
    }
)

#: Sample values a preset is test-expanded with at load, per parameter kind.
_SAMPLE_PARAMS = {"repo_list": "octo-org/app,octo-org/*", "ref_glob": "agent/*", "flag": "true"}


class CliProfileError(ValueError):
    """A shipped CLI profile that fails validation."""


def _read(relative: str) -> str:
    return (importlib.resources.files(_PACKAGE) / relative).read_text("utf-8")


def _profile_files() -> list[str]:
    root = importlib.resources.files(_PACKAGE)
    return sorted(
        entry.name for entry in root.iterdir() if entry.is_file() and entry.name.endswith(".yaml")
    )


def derived_identity(api: ProfileApi) -> ApiIdentity:
    """The identity the registry gives ``api`` when it is imported (or served built in)."""
    if api.source_kind == SourceKind.BUILTIN:
        builtin = _BUILTIN_IDENTITIES.get(api.catalog_api_id)
        if builtin is None:
            raise CliProfileError(f"{api.source}: not a built-in API")
        return builtin
    api_id = api.catalog_api_id
    return ApiIdentity(
        vendor=slugify_api_field(vendor_from_api_id(api_id) or api_id),
        name=slugify_api_field(catalog_import_name(api_id)),
        version=api.identity.version,
    )


def shipped_spec_text(api: ProfileApi) -> str:
    """The raw text of a ``shipped`` API's spec file."""
    if api.source_kind != SourceKind.SHIPPED:
        raise CliProfileError(f"{api.source}: not a shipped API")
    return _read(api.source_ref)


def _check_shipped(profile: CliProfile, api: ProfileApi) -> None:
    where = f"{profile.profile}: {api.source}"
    try:
        document = yaml.safe_load(shipped_spec_text(api))
    except (OSError, yaml.YAMLError) as exc:
        raise CliProfileError(f"{where}: cannot read the spec ({exc})") from exc
    if not isinstance(document, dict) or not str(document.get("openapi", "")).startswith("3."):
        raise CliProfileError(f"{where}: not an OpenAPI 3 document")
    info = document.get("info") or {}
    if info.get("x-vendor") not in profile.vendor.registry_domains:
        raise CliProfileError(
            f"{where}: info.x-vendor must be one of {list(profile.vendor.registry_domains)}"
        )
    if str(info.get("version")) != api.identity.version:
        raise CliProfileError(
            f"{where}: info.version {info.get('version')!r} is not identity.version "
            f"{api.identity.version!r}"
        )
    if not document.get("paths"):
        raise CliProfileError(f"{where}: the spec declares no operation")


def _check_profile(profile: CliProfile) -> None:
    if profile.vendor.id not in CLI_FACADE_VENDORS:
        raise CliProfileError(
            f"{profile.profile}: vendor {profile.vendor.id!r} has no cli_facades.vendors entry"
        )
    facade_settings = CliFacadeVendorsConfig.model_fields[profile.vendor.id].default_factory
    assert facade_settings is not None
    expected_mode = facade_settings().mode  # type: ignore[call-arg]
    if profile.routing.facade.mode != expected_mode:
        raise CliProfileError(
            f"{profile.profile}: facade mode {profile.routing.facade.mode!r} is not "
            f"cli_facades.vendors.{profile.vendor.id}'s {expected_mode!r}"
        )
    for api in profile.apis:
        builtin = api.source_kind == SourceKind.BUILTIN
        if builtin and api.catalog_api_id not in BUILTIN_CATALOG_API_IDS:
            raise CliProfileError(f"{profile.profile}: {api.source} is not a built-in API")
        if api.source_kind == SourceKind.SHIPPED:
            _check_shipped(profile, api)
        expected = derived_identity(api)
        if expected != api.identity:
            raise CliProfileError(
                f"{profile.profile}: {api.source} registers as {expected.model_dump()}, "
                f"not the declared {api.identity.model_dump()}"
            )
    for name, preset in profile.presets.items():
        for rule in preset.rules:
            if rule.include is not None and rule.include not in RULE_SETS:
                raise CliProfileError(
                    f"{profile.profile}: preset {name!r} includes unknown rule set {rule.include!r}"
                )
        sample = {key: _SAMPLE_PARAMS[spec.kind] for key, spec in preset.params.items()}
        for role in dict.fromkeys(rule.api for rule in preset.rules):
            try:
                expand_preset(profile, name, sample, role=role)
            except PresetError as exc:
                raise CliProfileError(f"{profile.profile}: preset {name!r}: {exc}") from exc


def parse_profile(text: str, *, source: str = "<string>") -> CliProfile:
    """Parse and validate one profile document (raises :class:`CliProfileError`)."""
    try:
        raw: Any = yaml.safe_load(text)
        profile = CliProfile.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise CliProfileError(f"{source}: {exc}") from exc
    _check_profile(profile)
    return profile


@cache
def load_profiles() -> Mapping[str, CliProfile]:
    """Every shipped profile, by profile name (validated; cached)."""
    profiles: dict[str, CliProfile] = {}
    vendors: set[str] = set()
    identities: dict[tuple[str, str], str] = {}
    connects: dict[str, str] = {}
    for filename in _profile_files():
        profile = parse_profile(_read(filename), source=filename)
        if filename != f"{profile.profile}.yaml":
            raise CliProfileError(
                f"{filename}: profile {profile.profile!r} must be in {profile.profile}.yaml"
            )
        if profile.vendor.id in vendors:
            raise CliProfileError(f"{filename}: a second profile for vendor {profile.vendor.id!r}")
        vendors.add(profile.vendor.id)
        for api in profile.apis:
            # A profile credential's coverage is "the profile declaring this
            # identity", so an identity belongs to one profile only.
            key = (api.identity.vendor, api.identity.name)
            if key in identities:
                raise CliProfileError(
                    f"{filename}: {key[0]}/{key[1]} is also declared by profile {identities[key]!r}"
                )
            identities[key] = profile.profile
        if profile.connect is not None and profile.profile_scoped:
            other = connects.get(profile.connect.vendor_entry)
            if other is not None:
                raise CliProfileError(
                    f"{filename}: connect entry {profile.connect.vendor_entry!r} is also "
                    f"profile {other!r}'s connect"
                )
            connects[profile.connect.vendor_entry] = profile.profile
        profiles[profile.profile] = profile
    return MappingProxyType(profiles)


def profile_for_vendor(vendor: str) -> CliProfile | None:
    """The profile of CLI-facade vendor ``vendor`` (``github``), or ``None``."""
    return next((p for p in load_profiles().values() if p.vendor.id == vendor), None)


def profile_for_connect(vendor_entry: str) -> CliProfile | None:
    """The ``profile_scoped`` profile whose own connect is ``vendor_entry``, or ``None``."""
    return next(
        (
            p
            for p in load_profiles().values()
            if p.profile_scoped and p.connect is not None and p.connect.vendor_entry == vendor_entry
        ),
        None,
    )


def declaring_profile(vendor: str, name: str, version: str) -> str | None:
    """The name of the profile declaring the concrete API identity, or ``None``.

    The one input a profile credential's coverage needs besides its own
    ``cli_profile``: such a credential covers an API exactly when this returns
    its profile. Read from the shipped manifests at call time, so a manifest
    that adds or drops an API widens or narrows every profile credential with
    the release that ships it.
    """
    vendor_slug, name_slug = slugify_api_field(vendor), slugify_api_field(name)
    for profile in load_profiles().values():
        if profile.declared_api(vendor_slug, name_slug, version) is not None:
            return profile.profile
    return None


def profile_api_role(cli_profile: str, vendor: str, name: str, version: str) -> ApiRole | None:
    """The role of the API ``cli_profile`` declares as this identity, or ``None``."""
    profile = load_profiles().get(cli_profile)
    if profile is None:
        return None
    api = profile.declared_api(slugify_api_field(vendor), slugify_api_field(name), version)
    return api.role if api is not None else None


def profile_api_for_catalog_api_id(api_id: str) -> tuple[CliProfile, ProfileApi] | None:
    """The profile and API a vendor entry's ``vendor`` (catalog api_id) names, or ``None``."""
    for profile in load_profiles().values():
        api = profile.api_for_catalog_api_id(api_id)
        if api is not None:
            return profile, api
    return None


def profile_api_for_connect(vendor_entry: str) -> tuple[CliProfile, ProfileApi] | None:
    """The profile and API whose credential the ``vendor_entry`` connect creates, or ``None``."""
    for profile in load_profiles().values():
        api = profile.api_for_connect(vendor_entry)
        if api is not None:
            return profile, api
    return None


def profile_api_for_identity(vendor: str, name: str) -> tuple[CliProfile, ProfileApi] | None:
    """The profile and API registered as ``vendor``/``name`` (any slug spelling), or ``None``."""
    vendor_slug, name_slug = slugify_api_field(vendor), slugify_api_field(name)
    for profile in load_profiles().values():
        api = profile.api_for_identity(vendor_slug, name_slug)
        if api is not None:
            return profile, api
    return None


__all__ = [
    "CliProfileError",
    "declaring_profile",
    "derived_identity",
    "load_profiles",
    "parse_profile",
    "profile_api_for_catalog_api_id",
    "profile_api_for_connect",
    "profile_api_for_identity",
    "profile_api_role",
    "profile_for_connect",
    "profile_for_vendor",
    "shipped_spec_text",
]
