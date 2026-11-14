"""Outbound HTTP clients must make their redirect policy explicit."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

pytestmark = pytest.mark.arch

_SCAN_ROOTS = tuple(SRC_ROOT / surface for surface in ("broker", "registry", "shared", "control"))

# These registry fetchers follow redirects only through bounded_read.follow_redirects,
# which validates every hop before dispatch.
_REDIRECT_POLICY_ALLOWLIST = {
    "registry/ingest/fetch.py",
    "registry/services/catalog/fetch.py",
    # Control credential and integration calls validate their fixed endpoint, pin DNS,
    # and issue one request per client without enabling automatic redirect following.
    "control/services/credentials/providers/oauth2.py",
    "control/services/credentials/providers/pipedream.py",
    "control/services/integrations/device_authorization.py",
    "control/services/integrations/flow_handlers/auth_code.py",
    "control/services/integrations/identity_echo.py",
    # Telemetry posts only to its configured ingest endpoint and has no redirect-
    # following path; it stays independent of the broker egress stack.
    "shared/telemetry/client.py",
}


def _is_async_client_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "AsyncClient"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
    )


def _follow_redirects_keyword(node: ast.Call) -> ast.keyword | None:
    return next(
        (keyword for keyword in node.keywords if keyword.arg == "follow_redirects"),
        None,
    )


def _check_file(path: Path, *, source_root: Path = SRC_ROOT) -> list[str]:
    relative_path = path.relative_to(source_root).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_async_client_call(node):
            continue
        redirect_keyword = _follow_redirects_keyword(node)
        if (
            redirect_keyword is not None
            and isinstance(redirect_keyword.value, ast.Constant)
            and redirect_keyword.value.value is False
        ):
            continue
        if redirect_keyword is None and relative_path in _REDIRECT_POLICY_ALLOWLIST:
            continue
        violations.append(
            f"{path}:{node.lineno} — httpx.AsyncClient must pass "
            "follow_redirects=False explicitly or use an allowlisted, "
            "hop-validating redirect path"
        )
    return violations


def test_outbound_async_clients_do_not_follow_redirects_implicitly() -> None:
    violations: list[str] = []
    for root in _SCAN_ROOTS:
        for path in python_files_in(root):
            violations.extend(_check_file(path))
    assert not violations, "Outbound redirect policy violations:\n" + "\n".join(violations)


def test_redirect_policy_checker_names_the_file_and_line(tmp_path: Path) -> None:
    path = tmp_path / "broker" / "client.py"
    path.parent.mkdir()
    path.write_text("client = httpx.AsyncClient()\n", encoding="utf-8")

    violations = _check_file(path, source_root=tmp_path)

    assert len(violations) == 1
    assert violations[0].startswith(f"{path}:1 —")


@pytest.mark.parametrize("value", ["True", "redirect_setting"])
def test_redirect_policy_checker_rejects_opt_in_even_in_allowlisted_modules(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "registry" / "ingest" / "fetch.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"client = httpx.AsyncClient(follow_redirects={value})\n",
        encoding="utf-8",
    )

    assert len(_check_file(path, source_root=tmp_path)) == 1


def test_redirect_policy_checker_accepts_explicit_opt_out_and_validated_hops(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "broker" / "client.py"
    explicit.parent.mkdir()
    explicit.write_text(
        "client = httpx.AsyncClient(follow_redirects=False)\n",
        encoding="utf-8",
    )
    bounded = tmp_path / "registry" / "ingest" / "fetch.py"
    bounded.parent.mkdir(parents=True)
    bounded.write_text("client = httpx.AsyncClient(transport=transport)\n", encoding="utf-8")

    assert _check_file(explicit, source_root=tmp_path) == []
    assert _check_file(bounded, source_root=tmp_path) == []
