"""Control-plane IdP and vendor calls go through the bounded ``idp_request`` client.

Token, device-authorization, identity-probe and Pipedream exchanges hit
operator- or user-supplied endpoints, so each must run under a total deadline
and a body cap. Those bounds live in ``shared/http/idp_client.py``; a direct
``httpx`` client in these packages would skip them, and reading ``.text`` /
``.json()`` straight off an ``httpx.Response`` buffers the whole (possibly
inflated) body.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

pytestmark = pytest.mark.arch

_GUARDED_PACKAGES = (
    SRC_ROOT / "control" / "services" / "credentials" / "providers",
    SRC_ROOT / "control" / "services" / "integrations",
)
_HTTPX_CLIENT_NAMES = frozenset({"AsyncClient", "Client"})
_RESPONSE_NAMES = frozenset({"response", "resp"})


def _violations(source: str, label: str) -> list[str]:
    """Return one message per direct httpx client use or unbounded body read."""
    found: list[str] = []
    tree = ast.parse(source, filename=label)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _HTTPX_CLIENT_NAMES
            and isinstance(node.value, ast.Name)
            and node.value.id == "httpx"
        ):
            found.append(f"{label}:{node.lineno} httpx.{node.attr} (use idp_request)")
        elif isinstance(node, ast.ImportFrom) and node.module == "httpx":
            found.extend(
                f"{label}:{node.lineno} from httpx import {alias.name} (use idp_request)"
                for alias in node.names
                if alias.name in _HTTPX_CLIENT_NAMES
            )
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "text"
            and id(node) not in called
            and isinstance(node.value, ast.Name)
            and node.value.id in _RESPONSE_NAMES
        ):
            found.append(
                f"{label}:{node.lineno} {node.value.id}.text read (use IdpResponse.text())"
            )
    return found


def test_control_idp_calls_use_idp_client() -> None:
    violations: list[str] = []
    for package in _GUARDED_PACKAGES:
        for path in python_files_in(package):
            label = str(Path(path).relative_to(SRC_ROOT))
            violations.extend(_violations(path.read_text(encoding="utf-8"), label))
    assert not violations, "control-plane IdP calls must use idp_request:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        "import httpx\nasync def f():\n    async with httpx.AsyncClient() as c: ...\n",
        "import httpx\nc = httpx.Client()\n",
        "from httpx import AsyncClient\n",
        "async def f(response):\n    return response.text\n",
    ],
)
def test_idp_scanner_flags_unbounded_patterns(source: str) -> None:
    assert _violations(source, "x.py")


def test_idp_scanner_accepts_the_bounded_client() -> None:
    source = (
        "from jentic_one.shared.http.idp_client import idp_request\n"
        "async def f():\n    r = await idp_request('GET', 'u', egress=None)\n    return r.text()\n"
    )
    assert _violations(source, "x.py") == []
