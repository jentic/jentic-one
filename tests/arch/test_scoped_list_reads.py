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

from .test_scoped_by_id_services import _service_sources, _ServiceIndex

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
    "OAuthGrantService.list_grants_for_agent": (
        "inline owner-or-org:admin check precedes delegation to the identity-scoped grant listing"
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


def _list_methods_in_tree(tree: ast.Module, py_file: Path) -> dict[str, tuple[Path, int, bool]]:
    index = _ServiceIndex([(tree, py_file)])
    found: dict[str, tuple[Path, int, bool]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Service"):
            continue
        for item in node.body:
            if not isinstance(item, _Func) or not _LIST_METHOD.match(item.name):
                continue
            takes_identity = any(arg.arg == "identity" for arg in item.args.kwonlyargs)
            scoped = takes_identity and index.is_authorized(node.name, item)
            found[f"{node.name}.{item.name}"] = (py_file, item.lineno, scoped)
    return found


def _list_methods() -> dict[str, tuple[Path, int, bool]]:
    """``Class.method`` -> (file, line, is_scoped) for every service collection read."""
    index = _ServiceIndex(_service_sources())
    found: dict[str, tuple[Path, int, bool]] = {}
    for owner in index.owners():
        for name, method in index.methods.get(owner, {}).items():
            if not _LIST_METHOD.match(name):
                continue
            takes_identity = any(arg.arg == "identity" for arg in method.args.kwonlyargs)
            scoped = takes_identity and index.is_authorized(owner, method)
            found[f"{owner}.{name}"] = (index.paths[owner], method.lineno, scoped)
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


@pytest.mark.arch
def test_identity_call_shape_is_not_scoping() -> None:
    """Self-check: filters and helper names require evidence of authorization."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def list_unused(self, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.list_all()\n"
        "    async def list_ignoring_helper(self, *, identity: Identity):\n"
        "        return await self._load_things(identity)\n"
        "    async def _load_things(self, identity: Identity):\n"
        "        return await ThingRepository.list_all()\n"
        "    async def list_audit(self, *, identity: Identity):\n"
        "        await self.audit(identity=identity)\n"
        "        return await ThingRepository.list_all()\n"
        "    async def list_recorded(self, *, identity: Identity):\n"
        "        await self.service.record_view(identity)\n"
        "        return await ThingRepository.list_all()\n"
    )
    methods = _list_methods_in_tree(tree, Path("<self-check>"))
    assert {name for name, (_, _, scoped) in methods.items() if not scoped} == {
        "ExampleService.list_unused",
        "ExampleService.list_ignoring_helper",
        "ExampleService.list_audit",
        "ExampleService.list_recorded",
    }


@pytest.mark.arch
def test_filter_flow_and_scoped_delegation_are_scoping() -> None:
    """Self-check: repository filter flow and real delegated loads are accepted."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def list_direct(self, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.list_all(filters=filters)\n"
        "    async def list_delegated(self, *, identity: Identity):\n"
        "        return await self._load_things(identity=identity)\n"
        "    async def _load_things(self, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.list_all(filters=filters)\n"
        "    async def list_inline(self, thing_id: str, *, identity: Identity):\n"
        "        return await self._load_thing(\n"
        "            thing_id, filters=build_access_filters(identity, Thing)\n"
        "        )\n"
        "    async def _load_thing(self, thing_id: str, *, filters: list):\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
    )
    methods = _list_methods_in_tree(tree, Path("<self-check>"))
    assert methods["ExampleService.list_direct"][2] is True
    assert methods["ExampleService.list_delegated"][2] is True
    assert methods["ExampleService.list_inline"][2] is True


@pytest.mark.arch
def test_list_side_query_does_not_authorize_unfiltered_collection() -> None:
    """Self-check: an unrelated scoped read cannot authorize an unfiltered list."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def list_side(self, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        await ThingRepository.get_by_id('other', filters=filters)\n"
        "        return await ThingRepository.list_all()\n"
        "    async def list_scoped(self, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.list_all(filters=filters)\n"
    )
    methods = _list_methods_in_tree(tree, Path("<self-check>"))
    actual = {name: result[2] for name, result in methods.items()}
    assert actual == {
        "ExampleService.list_side": False,
        "ExampleService.list_scoped": True,
    }
