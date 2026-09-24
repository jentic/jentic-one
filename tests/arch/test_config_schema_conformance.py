"""Validate the backend config JSON Schema is well-formed and not drifted.

The config schema (``config/config-schema.json``) is *generated* from the
``AppConfig`` Pydantic model (see ``tools/config_schema_export``); the checked-in
JSON is a build artefact. This drift test regenerates it in-process and asserts
byte-equality with the committed file, so a config-model change that isn't
accompanied by ``make config-schema`` fails CI — the backend half of the gate
that keeps the Go installer (``cli/`` ``make generate-config``) in lockstep with
the Pydantic source of truth.
"""

from __future__ import annotations

import json

import pytest
from tools.config_schema_export import (
    CONFIG_SCHEMA_PATH,
    build_config_schema,
    dump_schema_json,
)


@pytest.mark.arch
def test_config_schema_exists() -> None:
    assert CONFIG_SCHEMA_PATH.exists(), (
        f"config schema not found: {CONFIG_SCHEMA_PATH} (run `make config-schema`)"
    )


@pytest.mark.arch
def test_config_schema_is_valid_json_with_sections() -> None:
    schema = json.loads(CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema.get("title") == "AppConfig"
    props = schema.get("properties", {})
    # A representative spread of the real AppConfig sections must be present; if
    # the model is refactored these names may change, but the schema must never
    # collapse to an empty/degenerate document.
    for section in ("databases", "server", "logging", "broker", "security"):
        assert section in props, f"config schema missing section {section!r}"
    # The open extensions map is deliberately excluded from the installer schema.
    assert "extensions" not in props


@pytest.mark.arch
def test_config_schema_not_drifted() -> None:
    regenerated = dump_schema_json(build_config_schema())
    committed = CONFIG_SCHEMA_PATH.read_text(encoding="utf-8")
    assert regenerated == committed, (
        "config/config-schema.json is out of date with AppConfig; "
        "run `make config-schema` and commit the result."
    )


@pytest.mark.arch
def test_cli_vendored_config_schema_not_drifted() -> None:
    """The CLI's vendored schema copy must be byte-identical to the canonical one.

    The vendored copy (and the Go struct generated from it) is otherwise gated
    only by ``make check-ctl-gen`` in the Go CI job — a pure-Python contributor
    running ``make test-arch`` would never see it go stale. This check needs no
    Go toolchain.
    """
    vendored = CONFIG_SCHEMA_PATH.parents[1] / "cli/internal/cli/ctl/assets/config-schema.json"
    assert vendored.exists(), f"vendored CLI config schema not found: {vendored}"
    assert vendored.read_text(encoding="utf-8") == CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"), (
        "cli/internal/cli/ctl/assets/config-schema.json is out of date with "
        "config/config-schema.json; run `cd cli && make generate-config` and "
        "commit the result."
    )
