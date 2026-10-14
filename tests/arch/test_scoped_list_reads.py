"""Architecture enforcement: collection reads on scoped surfaces are caller-scoped.

Every public ``list_*`` / ``list_all`` / ``list_page`` / ``get_page`` method on a
``*Service`` class in ``admin``, ``auth`` or ``control`` must take a keyword-only
``identity`` argument and actually use it, so the read can be narrowed to what
the caller may see. Accepting ``identity`` and ignoring it is the same gap with
a reassuring signature, so it fails too.

A deliberately unscoped read goes in ``_ALLOWLIST`` with the reason it is safe.
The allowlist must not go stale: an entry whose method no longer exists, or that
is now properly scoped, fails the test so the exemption gets removed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

SCOPED_SERVICE_DIRS = [
    SRC_ROOT / "admin" / "services",
    SRC_ROOT / "auth" / "services",
    SRC_ROOT / "control" / "services",
]

_LIST_METHOD = re.compile(r"^(list_\w+|get_page)$")

#: ``Class.method`` -> why it is safe for this read not to be identity-scoped.
_ALLOWLIST: dict[str, str] = {
    "OAuthClientService.list_all": (
        "org-wide OAuth client registry, no owner column; route gated by oauth-clients:read"
    ),
    "OAuthAppRegistrationService.list_page": (
        "org-wide OAuth app registrations, no owner column; route requires org:admin"
    ),
    "ProviderConfigService.list_all": (
        "org-wide runtime provider config, secrets redacted; route gated by config:read"
    ),
    "PermissionService.list_catalogue": (
        "static permission catalogue; takes the caller's id to compute grantable_by_caller"
    ),
    "AuthorizeService.list_consentable_agents": (
        "consent flow: keyed on the authenticated consenting user's id, lists only their agents"
    ),
    "CredentialService.list_providers": "static credential-provider metadata, no rows",
    "CredentialService.list_rule_sets": (
        "shared permission rule sets carry policy, not secrets, and are visible to every "
        "credentials reader by design (see CredentialService._may_mutate_rule_set)"
    ),
    "VendorRegistryService.list_all": "public vendor auth metadata, no per-caller rows",
    "VendorRegistryService.list_entries": "public vendor registry entries, no per-caller rows",
}


def _uses_identity(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == "identity" and isinstance(node.ctx, ast.Load)
        for node in ast.walk(method)
    )


def _is_scoped(method: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    takes_identity = any(arg.arg == "identity" for arg in method.args.kwonlyargs)
    return takes_identity and _uses_identity(method)


def _list_methods() -> dict[str, tuple[Path, int, bool]]:
    """``Class.method`` -> (file, line, is_scoped) for every service list read."""
    found: dict[str, tuple[Path, int, bool]] = {}
    for services_dir in SCOPED_SERVICE_DIRS:
        for py_file in python_files_in(services_dir):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or not node.name.endswith("Service"):
                    continue
                for item in node.body:
                    if isinstance(
                        item, ast.FunctionDef | ast.AsyncFunctionDef
                    ) and _LIST_METHOD.match(item.name):
                        found[f"{node.name}.{item.name}"] = (py_file, item.lineno, _is_scoped(item))
    return found


@pytest.mark.arch
def test_service_list_reads_are_identity_scoped() -> None:
    """Every service list read takes and uses ``identity``, or is allowlisted with a reason."""
    violations = [
        f"{path}:{line} — {name} must take a keyword-only `identity` and scope the read "
        "with it (or be added to _ALLOWLIST with a reason)"
        for name, (path, line, scoped) in sorted(_list_methods().items())
        if not scoped and name not in _ALLOWLIST
    ]
    assert not violations, "Unscoped list reads on scoped surfaces:\n" + "\n".join(violations)


@pytest.mark.arch
def test_scoped_list_allowlist_is_not_stale() -> None:
    """Allowlist entries must name an existing, still-unscoped list method."""
    methods = _list_methods()
    stale = [
        f"{name}: " + ("no such list method" if name not in methods else "now scoped")
        for name in sorted(_ALLOWLIST)
        if name not in methods or methods[name][2]
    ]
    assert not stale, "Remove stale _ALLOWLIST entries:\n" + "\n".join(stale)
