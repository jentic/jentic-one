"""Validate the configuration reference document is present and not drifted.

The configuration reference (``docs/reference/config.md``) is *generated* from
the ``AppConfig`` Pydantic model via ``tools/config_reference`` (which renders
the same schema exported to ``config/config-schema.json``); the checked-in
markdown is a build artefact. This drift test regenerates it in-process and
asserts byte-equality with the committed file, so a config-model change — a new
field, section, default, or description — that isn't accompanied by
``make config-reference`` fails CI. Every configuration option that exists in
code is therefore guaranteed to be listed in the document.
"""

from __future__ import annotations

import pytest
from tools.config_reference import CONFIG_REFERENCE_PATH, build_markdown


@pytest.mark.arch
def test_config_reference_exists() -> None:
    assert CONFIG_REFERENCE_PATH.exists(), (
        f"configuration reference not found: {CONFIG_REFERENCE_PATH} (run `make config-reference`)"
    )


@pytest.mark.arch
def test_config_reference_has_sections() -> None:
    text = CONFIG_REFERENCE_PATH.read_text(encoding="utf-8")
    # A representative spread of the real AppConfig sections must be present; if
    # the model is refactored these names may change, but the document must
    # never collapse to an empty/degenerate page.
    for section in ("## `databases`", "## `server`", "## `broker`", "## `security`"):
        assert section in text, f"configuration reference missing section {section!r}"
    # The open extensions map is deliberately excluded (matches the JSON schema).
    assert "## `extensions`" not in text


@pytest.mark.arch
def test_config_reference_not_drifted() -> None:
    regenerated = build_markdown()
    committed = CONFIG_REFERENCE_PATH.read_text(encoding="utf-8")
    assert regenerated == committed, (
        "docs/reference/config.md is out of date with AppConfig; "
        "run `make config-reference` and commit the result."
    )
