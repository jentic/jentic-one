"""Validate OpenAPI specs are well-formed, non-trivial, and (control) not drifted.

The control-plane spec is *generated* from the FastAPI app (see
``tools/openapi_export``); the checked-in YAML is a build artefact. The drift
test regenerates it in-process and asserts byte-equality with the committed
file, so a route or model change that isn't accompanied by ``make openapi``
fails CI. The broker spec is hand-curated and validity-checked; its docs-SPA
artifact (``ui/public/broker-openapi.json``, see ``tools/broker_reference``) is
drift-checked against it here too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from openapi_spec_validator import validate
from tools.broker_reference import (
    BROKER_SPEC_JSON,
    dump_spec_json,
    load_broker_spec,
)
from tools.openapi_export import (
    CONTROL_SPEC_PATH,
    UI_SPEC_PATH,
    dump_spec_yaml,
)

OPENAPI_DIR = Path(__file__).resolve().parent.parent.parent / "openapi"

SPECS = [
    ("broker", OPENAPI_DIR / "broker" / "broker.openapi.yaml"),
    ("control", OPENAPI_DIR / "control" / "control.openapi.yaml"),
]


@pytest.mark.arch
@pytest.mark.parametrize("name,spec_path", SPECS, ids=[s[0] for s in SPECS])
def test_spec_is_valid(name: str, spec_path: Path) -> None:
    assert spec_path.exists(), f"OpenAPI spec not found: {spec_path}"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    validate(spec)


@pytest.mark.arch
@pytest.mark.parametrize("name,spec_path", SPECS, ids=[s[0] for s in SPECS])
def test_spec_has_paths(name: str, spec_path: Path) -> None:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    paths = spec.get("paths", {})
    assert paths, f"{name} OpenAPI spec has no paths defined"


def _drift_failure(message: str) -> None:
    """Fail with a hint, without letting pytest difflib huge spec strings/dicts.

    A plain ``assert a == b`` on a ~360 KB string or a deep dict makes pytest's
    assertion rewriter build a diff that can take minutes — indistinguishable
    from a hang. Comparing first and failing via this helper keeps failures fast.
    """
    pytest.fail(message)


@pytest.mark.arch
def test_control_spec_matches_generated(generated_control_spec: dict[str, Any]) -> None:
    """The checked-in control spec must equal what the app generates today."""
    expected = dump_spec_yaml(generated_control_spec)
    actual = CONTROL_SPEC_PATH.read_text(encoding="utf-8")
    if actual != expected:
        _drift_failure(
            "openapi/control/control.openapi.yaml is out of date with the FastAPI app. "
            "Regenerate it with `make openapi` and commit the result."
        )


@pytest.mark.arch
def test_ui_client_schema_matches_generated(generated_control_spec: dict[str, Any]) -> None:
    """The UI client schema (ui/openapi.json) must carry the generated document.

    ``make openapi`` writes both the YAML control spec and this JSON schema from
    the same app, so a route/model change that skips regeneration is caught here
    too (and reminds the author to re-run ``npm run codegen``).

    The comparison is **semantic** (parsed JSON), not byte-for-byte: the
    exporter writes 2-space-indented JSON, and although ``ui/openapi.json`` is
    listed in ``ui/.prettierignore`` (so the ``ui-format`` hook leaves its bytes
    alone), comparing parsed content keeps this test robust to any incidental
    whitespace differences while still asserting the document matches.
    """
    actual = json.loads(UI_SPEC_PATH.read_text(encoding="utf-8"))
    if actual != generated_control_spec:
        _drift_failure(
            "ui/openapi.json is out of date with the FastAPI app. "
            "Regenerate it with `make openapi` (then `cd ui && npm run codegen`) and commit."
        )


@pytest.mark.arch
def test_broker_reference_artifact_matches_source() -> None:
    """The docs-SPA broker artifact must equal codegen from the broker YAML.

    ``ui/public/broker-openapi.json`` (what the docs SPA's "Broker API" section
    renders) is generated from the hand-curated
    ``openapi/broker/broker.openapi.yaml`` by ``make broker-reference``. Nothing
    pinned the two together before, which is exactly how the artifact went
    stale — a spec edit without a regen silently shipped outdated docs.
    """
    assert BROKER_SPEC_JSON.exists(), f"missing artifact: {BROKER_SPEC_JSON}"
    expected = dump_spec_json(load_broker_spec())
    actual = BROKER_SPEC_JSON.read_text(encoding="utf-8")
    if actual != expected:
        _drift_failure(
            "ui/public/broker-openapi.json is out of date with "
            "openapi/broker/broker.openapi.yaml. Regenerate it with "
            "`make broker-reference` and commit the result."
        )
