"""CLI profiles: one shipped manifest per vendor CLI Jentic governs.

See :mod:`jentic_one.shared.cli_profiles.models` for the format and
``docs/reference/cli-profiles.md`` for the reference.
"""

from jentic_one.shared.cli_profiles.loader import (
    CliProfileError,
    derived_identity,
    load_profiles,
    parse_profile,
    profile_api_for_catalog_api_id,
    profile_api_for_connect,
    profile_api_for_identity,
    profile_for_vendor,
    shipped_spec_text,
)
from jentic_one.shared.cli_profiles.models import (
    ApiIdentity,
    ApiRole,
    CliProfile,
    ProfileApi,
    SourceKind,
)
from jentic_one.shared.cli_profiles.presets import (
    PresetError,
    expand_preset,
    expand_preset_by_role,
)

__all__ = [
    "ApiIdentity",
    "ApiRole",
    "CliProfile",
    "CliProfileError",
    "PresetError",
    "ProfileApi",
    "SourceKind",
    "derived_identity",
    "expand_preset",
    "expand_preset_by_role",
    "load_profiles",
    "parse_profile",
    "profile_api_for_catalog_api_id",
    "profile_api_for_connect",
    "profile_api_for_identity",
    "profile_for_vendor",
    "shipped_spec_text",
]
