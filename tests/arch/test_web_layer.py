"""Enforce web layer architectural rules.

Web modules (broker/web, control/web) must:
- Never import sqlalchemy, asyncpg, or DatabaseSession directly.
- Never import repository modules.
- Never construct Context directly (must use dependency injection).
- All non-health routers must reference identity/permission dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

AUTH_WEB = SRC_ROOT / "auth" / "web"
BROKER_WEB = SRC_ROOT / "broker" / "web"
CONTROL_WEB = SRC_ROOT / "control" / "web"
ADMIN_WEB = SRC_ROOT / "admin" / "web"
REGISTRY_WEB = SRC_ROOT / "registry" / "web"

FORBIDDEN_DB_MODULES = frozenset(
    {
        "sqlalchemy",
        "asyncpg",
        "jentic_one.shared.db.session",
    }
)

FORBIDDEN_DB_SYMBOLS = frozenset({"DatabaseSession", "AsyncSession", "create_async_engine"})

REPO_IMPORT_PATTERN = "repos"


def _collect_imports(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Return (kind, module_or_symbol, lineno) for all imports in the AST.

    kind is 'module' for the module path or 'symbol' for imported names.
    """
    results: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append(("module", alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            results.append(("module", module, node.lineno))
            for alias in node.names:
                results.append(("symbol", alias.name, node.lineno))
    return results


def _check_no_direct_db(filepath: Path) -> list[str]:
    """Check that a web module file does not import DB internals."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for kind, name, lineno in _collect_imports(tree):
        if kind == "module":
            for forbidden in FORBIDDEN_DB_MODULES:
                if name == forbidden or name.startswith(f"{forbidden}."):
                    violations.append(
                        f"{filepath}:{lineno} — imports '{name}' "
                        f"(web layer must not access DB directly; use a service instead)"
                    )
        if kind == "symbol" and name in FORBIDDEN_DB_SYMBOLS:
            violations.append(
                f"{filepath}:{lineno} — imports symbol '{name}' "
                f"(web layer must not access DB directly; use a service instead)"
            )

    return violations


def _check_no_repository_imports(filepath: Path) -> list[str]:
    """Check that web modules do not import from repository modules."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for kind, name, lineno in _collect_imports(tree):
        if kind == "module" and REPO_IMPORT_PATTERN in name.split("."):
            violations.append(
                f"{filepath}:{lineno} — imports '{name}' "
                f"(web layer must not import repositories; call services instead)"
            )

    return violations


def _check_no_context_construction(filepath: Path) -> list[str]:
    """Check that web handler files do not directly construct Context().

    Context should be injected via Depends(get_ctx), not created in handlers.
    The deps.py file is exempt (it provides the dependency itself).
    """
    if filepath.name == "deps.py" or filepath.name == "app.py":
        return []

    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            is_context_call = (isinstance(func, ast.Name) and func.id == "Context") or (
                isinstance(func, ast.Attribute) and func.attr == "Context"
            )
            if is_context_call:
                violations.append(
                    f"{filepath}:{node.lineno} — directly constructs Context() "
                    f"(use Depends(get_ctx) for dependency injection instead)"
                )

    return violations


def _web_dirs() -> list[Path]:
    """Return web directories that exist."""
    dirs = [AUTH_WEB, BROKER_WEB, CONTROL_WEB, ADMIN_WEB, REGISTRY_WEB]
    return [d for d in dirs if d.exists()]


def _web_files() -> list[Path]:
    """Return all Python files in web directories."""
    files: list[Path] = []
    for web_dir in _web_dirs():
        files.extend(python_files_in(web_dir))
    return files


@pytest.mark.arch
def test_web_no_direct_db_imports() -> None:
    """Web modules must not import sqlalchemy, asyncpg, or DatabaseSession."""
    violations: list[str] = []
    for py_file in _web_files():
        violations.extend(_check_no_direct_db(py_file))
    if violations:
        msg = (
            "Web layer has forbidden DB imports:\n"
            + "\n".join(violations)
            + "\n\nFix: Move data access logic to a service. "
            "Web handlers should only call service methods."
        )
        pytest.fail(msg)


@pytest.mark.arch
def test_web_no_repository_imports() -> None:
    """Web modules must not import from repository modules."""
    violations: list[str] = []
    for py_file in _web_files():
        violations.extend(_check_no_repository_imports(py_file))
    if violations:
        msg = (
            "Web layer imports repository modules:\n"
            + "\n".join(violations)
            + "\n\nFix: Web handlers should call service methods, "
            "not repository methods directly."
        )
        pytest.fail(msg)


@pytest.mark.arch
def test_web_no_direct_context_construction() -> None:
    """Web handlers must not construct Context directly.

    Context should be provided via FastAPI dependency injection (Depends(get_ctx)).
    The only files exempt are deps.py (which provides the dependency) and app.py
    (which sets up the app state).
    """
    violations: list[str] = []
    for py_file in _web_files():
        violations.extend(_check_no_context_construction(py_file))
    if violations:
        msg = (
            "Web handlers construct Context directly:\n"
            + "\n".join(violations)
            + "\n\nFix: Use 'ctx: Context = Depends(get_ctx)' in the route signature "
            "instead of constructing Context yourself."
        )
        pytest.fail(msg)


def _check_router_has_auth(filepath: Path) -> list[str]:
    """Check that non-health router files reference identity/permission dependencies.

    Router files (in routers/ directory) must use get_current_identity or
    equivalent auth dependency, unless they are health-check routers.
    """
    if "health" in filepath.name or "discovery" in filepath.name or "authorize" in filepath.name:
        return []
    # The anonymous OAuth-client DCR front door (RFC 7591):
    # deliberately unauthenticated — flagship MCP clients register anonymously;
    # the boundary is admin approval + consent. Gated by config + per-IP rate
    # limiting instead of an identity dependency.
    if filepath.name == "oauth_client_registration.py":
        return []
    # The local-account login form on the /authorize flow (#1276): the caller
    # is a browser mid-authorization with no token yet — the credential IS the
    # request body, verified by AuthService.authenticate. Gated by config
    # (auth.local_login.enabled → 404), the signed authorize state, a
    # single-use CSRF nonce, and the authorize rate limiter instead of an
    # identity dependency.
    if filepath.name == "local_login.py":
        return []

    source = filepath.read_text(encoding="utf-8")

    auth_indicators = (
        "get_current_identity",
        "Identity",
        "RequireOrgAdmin",
        "RequireActiveIdentity",
        "RequireIdentity",
        "RequireBrokerIdentity",
        "RequireToolkitAccess",
        "RequireUsersRead",
        "RequireUsersWrite",
        "RequireAgentsRead",
        "RequireAgentsWrite",
        "RequireServiceAccountsRead",
        "RequireServiceAccountsWrite",
        "required_permission",
        "required_permissions",
        "require_permission",
    )

    has_auth = any(indicator in source for indicator in auth_indicators)

    has_routes = "@router." in source or "@app." in source

    if has_routes and not has_auth:
        return [
            f"{filepath} — router defines routes but has no auth dependency "
            f"(all non-health routes must use get_current_identity for permission scoping)"
        ]
    return []


@pytest.mark.arch
def test_web_routers_require_auth() -> None:
    """All non-health router files must reference authentication dependencies.

    Every route (except health/liveness) must enforce permission checks via
    get_current_identity or equivalent. This ensures no unauthenticated access
    to business endpoints.
    """
    violations: list[str] = []
    for web_dir in _web_dirs():
        routers_dir = web_dir / "routers"
        if not routers_dir.exists():
            continue
        for py_file in python_files_in(routers_dir):
            if py_file.name == "__init__.py":
                continue
            violations.extend(_check_router_has_auth(py_file))
    if violations:
        msg = (
            "Routers missing authentication:\n"
            + "\n".join(violations)
            + "\n\nFix: Add 'identity: Identity = get_current_identity("
            "required_permissions=[Permission.XXX])' to all route handlers."
        )
        pytest.fail(msg)


def _check_uses_problem_details(filepath: Path) -> list[str]:
    """Check that web files raising HTTP errors use jentic-problem-details.

    Files that raise HTTPException directly should use ProblemDetailException instead.
    """
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for kind, name, lineno in _collect_imports(tree):
        if kind == "symbol" and name == "HTTPException":
            violations.append(
                f"{filepath}:{lineno} — imports HTTPException "
                f"(use jentic.problem_details exceptions instead for RFC 9457 compliance)"
            )

    return violations


@pytest.mark.arch
def test_web_uses_problem_details_not_http_exception() -> None:
    """Web modules must use jentic-problem-details, not raw HTTPException.

    All error responses must conform to RFC 9457 Problem Details format.
    Use exceptions from jentic.problem_details (NotFound, BadRequest, etc.)
    instead of FastAPI's HTTPException.
    """
    violations: list[str] = []
    for py_file in _web_files():
        violations.extend(_check_uses_problem_details(py_file))
    if violations:
        msg = (
            "Web layer uses HTTPException instead of problem details:\n"
            + "\n".join(violations)
            + "\n\nFix: Replace 'from fastapi import HTTPException' with "
            "'from jentic.problem_details import NotFound, BadRequest, ...' "
            "and raise those instead."
        )
        pytest.fail(msg)


def _check_handler_calls_service_not_repo(filepath: Path) -> list[str]:
    """Check that route handler function bodies do not call repository methods."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and "Repository" in func.attr:
                violations.append(
                    f"{filepath}:{node.lineno} — calls a Repository method directly "
                    f"(web handlers must only call service methods)"
                )
            elif isinstance(func, ast.Name) and "Repository" in func.id:
                violations.append(
                    f"{filepath}:{node.lineno} — calls a Repository class directly "
                    f"(web handlers must only call service methods)"
                )

    return violations


@pytest.mark.arch
def test_web_handlers_use_services_not_repos() -> None:
    """Web handlers must call services, never repositories directly."""
    violations: list[str] = []
    for py_file in _web_files():
        violations.extend(_check_handler_calls_service_not_repo(py_file))
    if violations:
        msg = (
            "Web handlers call repositories directly:\n"
            + "\n".join(violations)
            + "\n\nFix: Create or use a service method that wraps the repository call. "
            "Web handlers → Services → Repositories."
        )
        pytest.fail(msg)
