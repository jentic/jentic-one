"""The ``openapi_meta`` package keeps its public import surface stable.

``jentic_one.shared.web.openapi_meta`` is a package of focused submodules
(content, tags, operation ids, responses, examples, install). Importers keep
using the package root, and routers keep importing the parameter-example
catalogue as ``from jentic_one.shared.web import openapi_examples as ex``; both
re-export the submodules' objects themselves, not copies. Byte-for-byte spec
parity is pinned by the drift test in ``tests/arch/test_openapi_conformance.py``.
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI

from jentic_one.shared.web import openapi_examples, openapi_meta
from jentic_one.shared.web.openapi_meta import (
    content,
    examples,
    install,
    operation_ids,
    tags,
)

#: Where each public name of the package root is defined.
_ROOT_EXPORTS = {
    "API_DESCRIPTION": content,
    "API_SUMMARY": content,
    "API_TITLE": content,
    "API_VERSION": content,
    "BEARER_SECURITY_SCHEME": content,
    "CONTACT": content,
    "LICENSE_INFO": content,
    "OAUTH2_SECURITY_SCHEME": content,
    "PUBLISHED_SERVERS": content,
    "SERVERS": content,
    "authenticated_security": content,
    "OPENAPI_TAGS": tags,
    "X_TAG_GROUPS": tags,
    "NON_BEARER_AUTH_OPERATION_IDS": operation_ids,
    "PUBLIC_OPERATION_IDS": operation_ids,
    "ROUTER_RESHAPED_422_OPERATION_IDS": operation_ids,
    "generate_operation_id": operation_ids,
    "resolve_tag": operation_ids,
    "deployment_servers": install,
    "fastapi_metadata_kwargs": install,
    "install_openapi_metadata": install,
}


def test_package_root_exports_exactly_the_public_names() -> None:
    assert sorted(openapi_meta.__all__) == sorted(_ROOT_EXPORTS)


@pytest.mark.parametrize(("name", "module"), sorted(_ROOT_EXPORTS.items()))
def test_package_root_re_exports_the_defining_object(name: str, module: object) -> None:
    assert getattr(openapi_meta, name) is getattr(module, name)


def _public_example_names() -> list[str]:
    source = examples.__file__
    assert source is not None
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    return re.findall(r"^([A-Z][A-Z0-9_]*) =", text, re.MULTILINE)


def test_openapi_examples_re_exports_every_canonical_value() -> None:
    names = _public_example_names()
    assert names, "no canonical example constants found"
    assert sorted(openapi_examples.__all__) == sorted([*names, "example"])
    for name in openapi_examples.__all__:
        assert getattr(openapi_examples, name) is getattr(examples, name)


def test_example_labels_canonical_values_through_the_re_export() -> None:
    assert openapi_examples.example(openapi_examples.AGENT_ID) == {
        "example": {"value": openapi_examples.AGENT_ID, "summary": "Agent ID"}
    }
    assert openapi_examples.example(25) == {"example": {"value": 25}}


def test_install_openapi_metadata_applies_every_pass() -> None:
    app = FastAPI(**openapi_meta.fastapi_metadata_kwargs())

    @app.get("/credentials/{credential_id}")
    def get_credential(credential_id: str) -> dict[str, str]:
        """Get credential."""
        return {"id": credential_id}

    @app.get("/health")
    def get_health() -> dict[str, str]:
        """Health."""
        return {"status": "ok"}

    openapi_meta.install_openapi_metadata(app)
    spec = app.openapi()

    assert spec["x-tagGroups"] is openapi_meta.X_TAG_GROUPS
    assert set(spec["components"]["securitySchemes"]) == {"BearerAuth", "OAuth2"}
    operation = spec["paths"]["/credentials/{credential_id}"]["get"]
    assert operation["operationId"] == "getCredential"
    assert operation["tags"] == ["Credentials"]
    assert "default" in operation["responses"]
    health = spec["paths"]["/health"]["get"]
    assert health["operationId"] == "getHealth"
    assert health["security"] == []
    assert spec["paths"]["/health"]["summary"] == "Get Health"
