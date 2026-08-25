"""Validate OpenAPI specs are well-formed, non-trivial, and (control) not drifted.

The control-plane spec is *generated* from the FastAPI app (see
``tools/openapi_export``); the checked-in YAML is a build artefact. The drift
test regenerates it in-process and asserts byte-equality with the committed
file, so a route or model change that isn't accompanied by ``make openapi``
fails CI. The broker spec is hand-curated and validity-checked; its docs-SPA
artifact (``ui/public/broker-openapi.json``, see ``tools/broker_reference``) is
drift-checked against it here too.

Validation is fully **offline**: the broker spec's external problem-details
``$ref``s resolve from vendored copies under
``tests/arch/vendored/problem-details/`` (never the network), with checksum and
ref-pin guards keeping the vendored set in sync with the spec's pinned commit.
"""

from __future__ import annotations

import hashlib
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
# problem-details.yaml declares an $id on the upstream main branch. Today the
# referencing library resolves its relative "./error-item.yaml" ref against
# the retrieval URI (the pinned URL), so this base is never requested — it is
# a forward-compatibility fallback in case a future library version resolves
# against the $id instead. Mapping the mutable main-branch URL onto the pinned
# vendored copy is the only deterministic offline choice.
_ID_BASE = "https://raw.githubusercontent.com/jentic/api-problem-details/refs/heads/main/schemas/"
_VENDORED_SCHEMA_DIR = Path(__file__).resolve().parent / "vendored" / "problem-details"
# filename -> sha256 of the vendored copy, asserted by
# test_vendored_schemas_match_pinned_checksums so a hand-edit — or a pin bump
# that skips the re-download step — cannot silently validate against content
# that differs from what the pinned URL serves. Update on re-vendoring:
#   shasum -a 256 tests/arch/vendored/problem-details/*.yaml
_VENDORED_SCHEMA_SHA256 = {
    "problem-details.yaml": "9ffa96dbbac83e73ba727d09ad8881a665840a3a4fe44477bc6c376a9c5de716",
    "error-item.yaml": "edc4041b7a226f83136cd9dbe82b17ee0e5160608c5313a25c84e854045c3a9e",
}
# Exact-URI resolution map: no prefix/basename matching, so a ref to a
# different upstream file with a colliding basename (or a non-normalized path
# under the pinned base) cannot silently resolve to the wrong vendored copy.
_VENDORED_SCHEMA_URIS = {
    f"{base}{filename}": _VENDORED_SCHEMA_DIR / filename
    for filename in _VENDORED_SCHEMA_SHA256
    for base in (_PIN_BASE, _ID_BASE)
}
_PINNED_SCHEMA_URIS = frozenset(f"{_PIN_BASE}{filename}" for filename in _VENDORED_SCHEMA_SHA256)


def _vendored_schema_resolver(uri: str) -> Any:
    """Serve pinned external ``$ref``s from the vendored copies, offline.

    Any URI outside the vendored set fails loudly: adding a new external ref
    to a spec requires vendoring it, otherwise CI would silently depend on a
    third-party host being up.
    """
    vendored = _VENDORED_SCHEMA_URIS.get(uri)
    if vendored is None:
        raise LookupError(
            f"external $ref {uri!r} has no vendored copy. Spec validation must "
            "not touch the network — vendor the schema under "
            f"{_VENDORED_SCHEMA_DIR} and register it in _VENDORED_SCHEMA_SHA256 "
            "(see tests/arch/vendored/README.md)."
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
    """Collect every absolute ``$ref`` target (any URI scheme) in a spec document."""
    refs: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and urlparse(value).scheme:
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
def test_vendored_schemas_match_pinned_checksums() -> None:
    """The vendored schema bytes must be exactly what was fetched at the pin.

    Without this, a hand-edited vendored file — or a pin bump in the spec and
    ``PROBLEM_DETAILS_PIN`` that skips the re-download step — would pass the
    ref guard while ``test_spec_is_valid`` silently validates against content
    that differs from what the pinned URL serves.
    """
    for filename, expected in sorted(_VENDORED_SCHEMA_SHA256.items()):
        vendored = _VENDORED_SCHEMA_DIR / filename
        assert vendored.is_file(), f"missing vendored schema: {vendored}"
        actual = hashlib.sha256(vendored.read_bytes()).hexdigest()
        assert actual == expected, (
            f"{vendored} — sha256 {actual} does not match the recorded checksum "
            f"{expected}. If you re-vendored at a new pin, update "
            "_VENDORED_SCHEMA_SHA256; if not, restore the file from the pinned "
            "URL (see tests/arch/vendored/README.md)."
        )


@pytest.mark.arch
@pytest.mark.parametrize("name,spec_path", SPECS, ids=[s[0] for s in SPECS])
def test_spec_external_refs_are_pinned_and_vendored(name: str, spec_path: Path) -> None:
    """Every external ``$ref`` must be exactly a pinned, vendored schema URI.

    This is the ref-level drift guard: bumping the ``api-problem-details`` pin
    in a spec without updating ``PROBLEM_DETAILS_PIN`` (or vice versa), or
    adding any new external ref without vendoring it, fails here with
    instructions instead of reaching for the network. Content-level drift is
    covered by ``test_vendored_schemas_match_pinned_checksums``.
    """
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    unexpected = _external_refs(spec) - _PINNED_SCHEMA_URIS
    assert not unexpected, (
        f"{name} spec has external $refs that are not exactly a pinned, vendored "
        "schema URI:\n"
        + "\n".join(sorted(unexpected))
        + "\nExternal refs must pin an immutable commit and have a vendored copy: "
        "update PROBLEM_DETAILS_PIN and re-vendor per tests/arch/vendored/README.md."
    )


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
