"""The ``openapi_meta`` package keeps its public import surface stable.

``jentic_one.shared.web.openapi_meta`` is a package of focused submodules
(content, tags, operation ids, responses, examples, install). Importers keep
using the package root, and routers keep importing the parameter-example
catalogue as ``from jentic_one.shared.web import openapi_examples as ex``; both
re-export the submodules' objects themselves, not copies. These tests pin only
that import surface; byte-for-byte spec parity is pinned by the drift test in
``tests/arch/test_openapi_conformance.py``.
"""

from __future__ import annotations

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
    for name, module in _ROOT_EXPORTS.items():
        assert getattr(openapi_meta, name) is getattr(module, name), name


def _canonical_example_names() -> list[str]:
    """Public upper-case scalar constants defined in ``openapi_meta.examples``."""
    return [
        name
        for name, value in vars(examples).items()
        if name.isupper() and not name.startswith("_") and isinstance(value, str | int)
    ]


def test_openapi_examples_re_exports_every_canonical_value() -> None:
    names = _canonical_example_names()
    assert names, "no canonical example constants found"
    assert sorted(openapi_examples.__all__) == sorted([*names, "example"])
    for name in openapi_examples.__all__:
        assert getattr(openapi_examples, name) is getattr(examples, name), name


def test_example_labels_canonical_values_through_the_re_export() -> None:
    assert openapi_examples.example(openapi_examples.AGENT_ID) == {
        "example": {"value": openapi_examples.AGENT_ID, "summary": "Agent ID"}
    }
    assert openapi_examples.example(25) == {"example": {"value": 25}}
