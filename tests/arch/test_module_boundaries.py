"""Ensure broker and control modules do not import each other.

Both modules may import from shared, but must not cross-import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import python_files_in


def _collect_import_modules(tree: ast.AST) -> list[tuple[str, int]]:
    """Return (module_path, lineno) for all imports."""
    results: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            results.append((node.module, node.lineno))
    return results


def _check_boundary(
    source_dir: Path, forbidden_prefix: str, *, exclude_paths: tuple[str, ...] = ()
) -> list[str]:
    """Return violation messages for imports matching *forbidden_prefix*."""
    violations: list[str] = []
    for py_file in python_files_in(source_dir):
        if any(part in str(py_file) for part in exclude_paths):
            continue
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for module, lineno in _collect_import_modules(tree):
            if module == forbidden_prefix or module.startswith(f"{forbidden_prefix}."):
                violations.append(
                    f"{py_file}:{lineno} — imports '{module}' (cross-module import forbidden)"
                )
    return violations


@pytest.mark.arch
def test_broker_does_not_import_control(broker_source_dir: Path) -> None:
    violations = _check_boundary(
        broker_source_dir,
        "jentic_one.control",
        exclude_paths=("broker/services/credentials/",),
    )
    assert not violations, "Broker imports from control:\n" + "\n".join(violations)


@pytest.mark.arch
def test_control_does_not_import_broker(control_source_dir: Path) -> None:
    violations = _check_boundary(control_source_dir, "jentic_one.broker")
    assert not violations, "Control imports from broker:\n" + "\n".join(violations)


@pytest.mark.arch
def test_admin_does_not_import_broker(admin_source_dir: Path) -> None:
    violations = _check_boundary(admin_source_dir, "jentic_one.broker")
    assert not violations, "Admin imports from broker:\n" + "\n".join(violations)


@pytest.mark.arch
def test_admin_does_not_import_control(admin_source_dir: Path) -> None:
    violations = _check_boundary(admin_source_dir, "jentic_one.control")
    assert not violations, "Admin imports from control:\n" + "\n".join(violations)


@pytest.mark.arch
def test_broker_does_not_import_admin(broker_source_dir: Path) -> None:
    violations = _check_boundary(broker_source_dir, "jentic_one.admin")
    assert not violations, "Broker imports from admin:\n" + "\n".join(violations)


@pytest.mark.arch
def test_control_does_not_import_admin(control_source_dir: Path) -> None:
    violations = _check_boundary(control_source_dir, "jentic_one.admin")
    assert not violations, "Control imports from admin:\n" + "\n".join(violations)


@pytest.mark.arch
def test_registry_does_not_import_broker(registry_source_dir: Path) -> None:
    violations = _check_boundary(registry_source_dir, "jentic_one.broker")
    assert not violations, "Registry imports from broker:\n" + "\n".join(violations)


@pytest.mark.arch
def test_registry_does_not_import_admin(registry_source_dir: Path) -> None:
    violations = _check_boundary(registry_source_dir, "jentic_one.admin")
    assert not violations, "Registry imports from admin:\n" + "\n".join(violations)


@pytest.mark.arch
def test_registry_does_not_import_control(registry_source_dir: Path) -> None:
    violations = _check_boundary(registry_source_dir, "jentic_one.control")
    assert not violations, "Registry imports from control:\n" + "\n".join(violations)


@pytest.mark.arch
def test_control_does_not_import_registry(control_source_dir: Path) -> None:
    violations = _check_boundary(control_source_dir, "jentic_one.registry")
    assert not violations, "Control imports from registry:\n" + "\n".join(violations)


@pytest.mark.arch
def test_admin_does_not_import_registry(admin_source_dir: Path) -> None:
    violations = _check_boundary(admin_source_dir, "jentic_one.registry")
    assert not violations, "Admin imports from registry:\n" + "\n".join(violations)


@pytest.mark.arch
def test_broker_does_not_import_registry(broker_source_dir: Path) -> None:
    violations = _check_boundary(broker_source_dir, "jentic_one.registry")
    assert not violations, "Broker imports from registry:\n" + "\n".join(violations)


@pytest.mark.arch
def test_shared_does_not_import_broker(shared_source_dir: Path) -> None:
    violations = _check_boundary(shared_source_dir, "jentic_one.broker")
    assert not violations, "Shared imports from broker:\n" + "\n".join(violations)


@pytest.mark.arch
def test_shared_does_not_import_admin_permissions(shared_source_dir: Path) -> None:
    # The permission catalogue moved to shared.auth.permission_catalog (#938) so shared
    # callers expand grants without a shared→admin layering inversion. Scoped to the
    # permissions module specifically: shared jobs/events/audit machinery still references
    # admin-tier ORM schemas (a separate, pre-existing structural coupling — admin owns
    # those canonical schemas), which is out of scope for #938.
    violations = _check_boundary(shared_source_dir, "jentic_one.admin.core.permissions")
    assert not violations, (
        "Shared imports the admin permissions module (layering inversion #938); "
        "import from jentic_one.shared.auth.permission_catalog instead:\n" + "\n".join(violations)
    )


@pytest.mark.arch
def test_auth_does_not_import_broker(auth_source_dir: Path) -> None:
    violations = _check_boundary(auth_source_dir, "jentic_one.broker")
    assert not violations, "Auth imports from broker:\n" + "\n".join(violations)


@pytest.mark.arch
def test_auth_does_not_import_control(auth_source_dir: Path) -> None:
    violations = _check_boundary(auth_source_dir, "jentic_one.control")
    assert not violations, "Auth imports from control:\n" + "\n".join(violations)


@pytest.mark.arch
def test_auth_does_not_import_registry(auth_source_dir: Path) -> None:
    violations = _check_boundary(auth_source_dir, "jentic_one.registry")
    assert not violations, "Auth imports from registry:\n" + "\n".join(violations)


@pytest.mark.arch
def test_broker_does_not_import_auth(broker_source_dir: Path) -> None:
    violations = _check_boundary(broker_source_dir, "jentic_one.auth")
    assert not violations, "Broker imports from auth:\n" + "\n".join(violations)


@pytest.mark.arch
def test_control_does_not_import_auth(control_source_dir: Path) -> None:
    violations = _check_boundary(control_source_dir, "jentic_one.auth")
    assert not violations, "Control imports from auth:\n" + "\n".join(violations)


@pytest.mark.arch
def test_admin_does_not_import_auth(admin_source_dir: Path) -> None:
    violations = _check_boundary(admin_source_dir, "jentic_one.auth")
    assert not violations, "Admin imports from auth:\n" + "\n".join(violations)


@pytest.mark.arch
def test_registry_does_not_import_auth(registry_source_dir: Path) -> None:
    violations = _check_boundary(registry_source_dir, "jentic_one.auth")
    assert not violations, "Registry imports from auth:\n" + "\n".join(violations)


@pytest.mark.arch
def test_shared_does_not_import_auth(shared_source_dir: Path) -> None:
    violations = _check_boundary(shared_source_dir, "jentic_one.auth")
    assert not violations, "Shared imports from auth:\n" + "\n".join(violations)
