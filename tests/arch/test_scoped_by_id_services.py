"""Architecture enforcement: positional service operations use caller identity.

Public methods on ``*Service`` classes in ``admin``, ``auth``, ``control`` and
``registry`` that accept positional resource input and keyword-only ``identity``
must pass the whole identity to a scoping builder, an authorization helper, or
another service method. Attribute-only uses such as ``identity.sub`` provide
attribution, not authorization. Covering all positional resource input keeps
creators and self-keyed operations visible to the same guard as ``*_id`` lookups.

A deliberately unscoped operation goes in ``_ATTRIBUTION_ONLY_ALLOWLIST`` with
its safety reason. Missing methods and newly scoped methods make that entry stale.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

SCOPED_SERVICE_DIRS = [
    SRC_ROOT / "admin" / "services",
    SRC_ROOT / "auth" / "services",
    SRC_ROOT / "control" / "services",
    SRC_ROOT / "registry" / "services",
]

_Func = ast.FunctionDef | ast.AsyncFunctionDef

_ORG_OAUTH_CLIENT = (
    "org-wide OAuth client registry, no owner column; route gated by oauth-clients:write"
)
_ORG_OAUTH_APP = "org-wide OAuth app registrations, no owner column; route requires org:admin"
_ORG_REVISION = "org-wide API revision registry, no owner column; route gated by apis:write"
_ORG_OVERLAY = "shared org-wide overlay content with no owner scoping; permission-gated route"

#: ``Class.method`` -> why attribute-only identity use is safe for this operation.
_ATTRIBUTION_ONLY_ALLOWLIST: dict[str, str] = {
    "AuthService.change_own_password": (
        "self-keyed password change: the repository actor id and current-password check "
        "use identity.sub"
    ),
    "HeldExecutionService.withdraw": (
        "self-keyed withdrawal: both the visibility check and compare-and-set require "
        "created_by == identity.sub"
    ),
    "InviteService.reissue": (
        "guarded by UserService.reissue_invite, which calls _load_manageable before delegating"
    ),
    "OAuthClientService.update": _ORG_OAUTH_CLIENT,
    "OAuthClientService.deactivate": _ORG_OAUTH_CLIENT,
    "OAuthClientService.delete": _ORG_OAUTH_CLIENT,
    "OAuthClientService.rotate_secret": _ORG_OAUTH_CLIENT,
    "ProviderConfigService.set": (
        "org-wide runtime provider config, no owner column; route gated by config:write"
    ),
    "UserService.create": (
        "create-only users:write operation; no existing caller-owned row and creator "
        "attribution uses identity.sub"
    ),
    "AgentAuthService.get_api_key_info": (
        "inline owner-or-org:admin check on Agent.owner_id precedes the credential metadata read"
    ),
    "AgentAuthService.get_api_key_history": (
        "inline owner-or-org:admin check on Agent.owner_id precedes the audit history read"
    ),
    "AgentService.create": (
        "create-only operation with an explicit owner and grant-ceiling check; no existing "
        "agent row to scope"
    ),
    "AgentService.claim": (
        "the single-use claim token is the capability; ownership is assigned to identity.sub"
    ),
    "OAuthGrantService.list_grants_for_agent": (
        "inline owner-or-org:admin check precedes delegation to the identity-scoped grant listing"
    ),
    "TokenService.revoke": (
        "RFC 7009 self-keyed revocation: foreign actor tokens are a no-op and are never mutated"
    ),
    "CredentialService.create": (
        "create-only credentials:write operation; ownership is assigned from identity.sub"
    ),
    "CredentialService.list_agents": (
        "delegates identity to the credential-access mixin, which performs the scoped "
        "credential load"
    ),
    "CredentialService.list_agent_permissions": (
        "delegates identity to the credential-access mixin, which performs the scoped "
        "credential load"
    ),
    "OAuthAppRegistrationService.create_authorization_code": _ORG_OAUTH_APP,
    "OAuthAppRegistrationService.create_device_authorization": _ORG_OAUTH_APP,
    "OAuthAppRegistrationService.update": _ORG_OAUTH_APP,
    "OAuthAppRegistrationService.rotate_client_secret": _ORG_OAUTH_APP,
    "OAuthAppRegistrationService.delete": _ORG_OAUTH_APP,
    "ApiService.update": (
        "org-wide API registry content, no owner column; route gated by apis:write"
    ),
    "NoteService.create": (
        "create-only operation: the note is caller-owned through created_by == identity.sub"
    ),
    "OverlayService.submit": _ORG_OVERLAY,
    "OverlayService.deprecate": _ORG_OVERLAY,
    "OverlayService.rollback": _ORG_OVERLAY,
    "RevisionService.promote": _ORG_REVISION,
    "RevisionService.archive": _ORG_REVISION,
    "RevisionService.delete": _ORG_REVISION,
}


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
    return (
        isinstance(receiver, ast.Attribute)
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == "self"
    )


def _is_scoped(method: _Func) -> bool:
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


def _by_id_methods_in_tree(tree: ast.Module, py_file: Path) -> dict[str, tuple[Path, int, bool]]:
    found: dict[str, tuple[Path, int, bool]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Service"):
            continue
        for item in node.body:
            if not isinstance(item, _Func) or item.name.startswith("_"):
                continue
            positional = item.args.posonlyargs + item.args.args
            takes_identity = any(arg.arg == "identity" for arg in item.args.kwonlyargs)
            if len(positional) > 1 and takes_identity:
                found[f"{node.name}.{item.name}"] = (py_file, item.lineno, _is_scoped(item))
    return found


def _by_id_methods() -> dict[str, tuple[Path, int, bool]]:
    found: dict[str, tuple[Path, int, bool]] = {}
    for services_dir in SCOPED_SERVICE_DIRS:
        for py_file in python_files_in(services_dir):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            found.update(_by_id_methods_in_tree(tree, py_file))
    return found


@pytest.mark.arch
def test_by_id_service_methods_use_identity() -> None:
    """Public positional service operations pass whole identity to a scoping call."""
    violations = [
        f"{path}:{line} — {name} must pass the whole `identity` to build_access_filters, "
        "a _load_*/_require_* authorization helper, or another service method; attribute-only "
        "use is attribution (or add _ATTRIBUTION_ONLY_ALLOWLIST with a reason)"
        for name, (path, line, scoped) in sorted(_by_id_methods().items())
        if not scoped and name not in _ATTRIBUTION_ONLY_ALLOWLIST
    ]
    assert not violations, "Unscoped positional service operations:\n" + "\n".join(violations)


@pytest.mark.arch
def test_attribution_only_allowlist_is_not_stale() -> None:
    """Every exemption names an existing method that still lacks a scoping call."""
    methods = _by_id_methods()
    stale = [
        f"{name}: " + ("no such service method" if name not in methods else "now scoped")
        for name in sorted(_ATTRIBUTION_ONLY_ALLOWLIST)
        if name not in methods or methods[name][2]
    ]
    assert not stale, "Remove stale _ATTRIBUTION_ONLY_ALLOWLIST entries:\n" + "\n".join(stale)


@pytest.mark.arch
def test_by_id_service_check_finds_methods() -> None:
    """Guard against a signature or directory change silently emptying the walker."""
    total = len(_by_id_methods())
    assert total > 50, f"Only {total} positional identity-aware service methods found"


@pytest.mark.arch
def test_attribution_only_by_id_method_is_flagged() -> None:
    """Self-check: an unscoped repository read with identity.sub attribution fails."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def get(self, thing_id: str, *, identity: Identity):\n"
        "        logger.info('get', actor=identity.sub)\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    assert methods["ExampleService.get"][2] is False
