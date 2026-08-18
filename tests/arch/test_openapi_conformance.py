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
from urllib.parse import urlparse

import pytest
import yaml
from jsonschema_path import SchemaPath
from openapi_spec_validator.shortcuts import get_validator_cls
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

# The broker spec $refs its RFC 9457 problem-details schemas from
# jentic/api-problem-details, pinned to an immutable commit. Validation must
# resolve those refs from the vendored copies below — never the network — so a
# GitHub outage cannot fail CI (as happened on 2026-08-17, when
# raw.githubusercontent.com was returning errors on main pushes).
# See tests/arch/vendored/README.md for the re-vendoring procedure.
PROBLEM_DETAILS_PIN = "c741877182d2deea2bb38859e3df4caa76265d98"
_PIN_BASE = (
    f"https://raw.githubusercontent.com/jentic/api-problem-details/{PROBLEM_DETAILS_PIN}/schemas/"
)
# problem-details.yaml declares an $id on the upstream main branch; its
# relative "./error-item.yaml" ref resolves against that $id, so both bases
# must map onto the vendored copies.
_ID_BASE = "https://raw.githubusercontent.com/jentic/api-problem-details/refs/heads/main/schemas/"
_VENDORED_SCHEMA_DIR = Path(__file__).resolve().parent / "vendored" / "problem-details"
_VENDORED_SCHEMA_BASES = (_PIN_BASE, _ID_BASE)


def _vendored_schema_resolver(uri: str) -> Any:
    """Serve pinned external ``$ref``s from the vendored copies, offline.

    Any URI outside the vendored set fails loudly: adding a new external ref
    to a spec requires vendoring it, otherwise CI would silently depend on a
    third-party host being up.
    """
    base = next((b for b in _VENDORED_SCHEMA_BASES if uri.startswith(b)), None)
    filename = Path(urlparse(uri).path).name
    vendored = _VENDORED_SCHEMA_DIR / filename if base else None
    if vendored is None or not vendored.is_file():
        raise LookupError(
            f"external $ref {uri!r} has no vendored copy. Spec validation must "
            "not touch the network — vendor the schema under "
            f"{_VENDORED_SCHEMA_DIR} (see tests/arch/vendored/README.md)."
        )
    return yaml.safe_load(vendored.read_text(encoding="utf-8"))


_OFFLINE_HANDLERS = {
    "http": _vendored_schema_resolver,
    "https": _vendored_schema_resolver,
}


def _validate_offline(spec: dict[str, Any]) -> None:
    """``openapi_spec_validator.validate`` with network resolution disabled."""
    validator_cls = get_validator_cls(spec)
    schema_path = SchemaPath.from_dict(spec, handlers=_OFFLINE_HANDLERS)
    validator_cls(schema_path).validate()


def _external_refs(node: Any) -> set[str]:
    """Collect every absolute http(s) ``$ref`` target in a spec document."""
    refs: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("http"):
                refs.add(value)
            else:
                refs.update(_external_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.update(_external_refs(item))
    return refs


@pytest.mark.arch
@pytest.mark.parametrize("name,spec_path", SPECS, ids=[s[0] for s in SPECS])
def test_spec_is_valid(name: str, spec_path: Path) -> None:
    assert spec_path.exists(), f"OpenAPI spec not found: {spec_path}"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    _validate_offline(spec)


@pytest.mark.arch
@pytest.mark.parametrize("name,spec_path", SPECS, ids=[s[0] for s in SPECS])
def test_spec_external_refs_are_pinned_and_vendored(name: str, spec_path: Path) -> None:
    """Every external ``$ref`` must point at the pinned, vendored schema set.

    This is the drift guard for the vendored copies: bumping the
    ``api-problem-details`` pin in a spec without re-vendoring (or vice versa)
    fails here with instructions, instead of silently validating against stale
    content or reaching for the network.
    """
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    for ref in sorted(_external_refs(spec)):
        if not ref.startswith(_PIN_BASE):
            problems.append(
                f"{ref} — not under the pinned base {_PIN_BASE!r}. External refs "
                "must pin an immutable commit; update PROBLEM_DETAILS_PIN in "
                "this test and re-vendor (tests/arch/vendored/README.md)."
            )
            continue
        vendored = _VENDORED_SCHEMA_DIR / Path(urlparse(ref).path).name
        if not vendored.is_file():
            problems.append(
                f"{ref} — no vendored copy at {vendored}. Vendor it so spec "
                "validation stays offline (tests/arch/vendored/README.md)."
            )
    assert not problems, f"{name} spec external $refs out of sync:\n" + "\n".join(problems)


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
