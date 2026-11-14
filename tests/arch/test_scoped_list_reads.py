"""Architecture enforcement: collection reads on scoped surfaces are caller-scoped.

Every public collection read on a ``*Service`` class in ``admin``, ``auth``,
``control`` or ``registry`` must take a keyword-only ``identity`` argument and
pass the whole object to a scoping builder, an authorization helper, or another
service method. Attribute-only uses such as ``identity.sub`` provide attribution,
not query scoping.

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
    SRC_ROOT / "registry" / "services",
]

_LIST_METHOD = re.compile(r"^(list_\w+|get_page|search\w*|count_\w+|stream\w*|export\w*|iter_\w+)$")

_API_REGISTRY = "org-wide API registry content, no caller-owned rows; gated by apis:read"

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
    "ToolkitExportService.export": (
        "operator-only local maintenance export across both databases, not a caller-facing read"
    ),
    "ApiService.list_all": _API_REGISTRY,
    "CatalogService.list_all": "public upstream API catalog, no per-caller rows",
    "OperationService.list_for_revision": _API_REGISTRY,
    "OperationService.list_for_live_revision": _API_REGISTRY,
    "OverlayService.list_page": (
        "overlays are shared catalogue content with no owner scoping; writes pass identity"
    ),
    "RevisionService.list_revisions": _API_REGISTRY,
    "SearchService.search": _API_REGISTRY,
}

_Func = ast.FunctionDef | ast.AsyncFunctionDef


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _receives_whole_identity(call: ast.Call) -> bool:
    values = list(call.args) + [kw.value for kw in call.keywords]
    return any(isinstance(value, ast.Name) and value.id == "identity" for value in values)


def _is_service_receiver(receiver: ast.expr) -> bool:
    if isinstance(receiver, ast.Name):
        return receiver.id == "self"
    if isinstance(receiver, ast.Attribute):
        return isinstance(receiver.value, ast.Name) and receiver.value.id == "self"
    if isinstance(receiver, ast.Call):
        name = _name_of(receiver.func)
        return name == "super" or (name is not None and name.endswith("Service"))
    return False


def _uses_identity(method: _Func) -> bool:
    """Whether the whole identity reaches a scoping call rather than an attribute read."""
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not _receives_whole_identity(node):
            continue
        name = _name_of(node.func)
        if name == "build_access_filters" or (
            name is not None and name.startswith(("_load_", "_require_"))
        ):
            return True
        if isinstance(node.func, ast.Attribute) and _is_service_receiver(node.func.value):
            return True
    return False


def _is_scoped(method: _Func) -> bool:
    takes_identity = any(arg.arg == "identity" for arg in method.args.kwonlyargs)
    return takes_identity and _uses_identity(method)


def _list_methods_in_tree(tree: ast.Module, py_file: Path) -> dict[str, tuple[Path, int, bool]]:
    found: dict[str, tuple[Path, int, bool]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Service"):
            continue
        for item in node.body:
            if isinstance(item, _Func) and _LIST_METHOD.match(item.name):
                found[f"{node.name}.{item.name}"] = (py_file, item.lineno, _is_scoped(item))
    return found


def _list_methods() -> dict[str, tuple[Path, int, bool]]:
    """``Class.method`` -> (file, line, is_scoped) for every service collection read."""
    found: dict[str, tuple[Path, int, bool]] = {}
    for services_dir in SCOPED_SERVICE_DIRS:
        for py_file in python_files_in(services_dir):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            found.update(_list_methods_in_tree(tree, py_file))
    return found


@pytest.mark.arch
def test_service_list_reads_are_identity_scoped() -> None:
    """Every service collection read takes and uses ``identity``, or is allowlisted."""
    violations = [
        f"{path}:{line} — {name} must take a keyword-only `identity` and pass the whole "
        "object to a scoping builder, an authorization helper, or another service method "
        "(or be added to _ALLOWLIST with a reason)"
        for name, (path, line, scoped) in sorted(_list_methods().items())
        if not scoped and name not in _ALLOWLIST
    ]
    assert not violations, "Unscoped collection reads on scoped surfaces:\n" + "\n".join(violations)


@pytest.mark.arch
def test_scoped_list_allowlist_is_not_stale() -> None:
    """Allowlist entries must name an existing, still-unscoped collection method."""
    methods = _list_methods()
    stale = [
        f"{name}: " + ("no such collection method" if name not in methods else "now scoped")
        for name in sorted(_ALLOWLIST)
        if name not in methods or methods[name][2]
    ]
    assert not stale, "Remove stale _ALLOWLIST entries:\n" + "\n".join(stale)


@pytest.mark.arch
def test_logged_identity_is_not_scoping() -> None:
    """Self-check: reading an identity attribute for a log line does not scope a query."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def list_items(self, *, identity: Identity):\n"
        "        logger.info('list', actor=identity.sub)\n"
        "        return await ItemRepository.list_all()\n"
    )
    methods = _list_methods_in_tree(tree, Path("<self-check>"))
    assert methods["ExampleService.list_items"][2] is False


@pytest.mark.arch
def test_search_and_count_methods_are_collected() -> None:
    """Self-check: non-list collection verbs cannot escape the walker."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def search(self): pass\n"
        "    async def count_visible(self): pass\n"
        "    async def stream_events(self): pass\n"
        "    async def export(self): pass\n"
        "    async def iter_items(self): pass\n"
        "    async def get(self): pass\n"
    )
    methods = _list_methods_in_tree(tree, Path("<self-check>"))
    assert set(methods) == {
        "ExampleService.search",
        "ExampleService.count_visible",
        "ExampleService.stream_events",
        "ExampleService.export",
        "ExampleService.iter_items",
    }
