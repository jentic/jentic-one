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
import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest

from .conftest import SRC_ROOT, python_files_in

SCOPED_SERVICE_DIRS = [
    SRC_ROOT / "admin" / "services",
    SRC_ROOT / "auth" / "services",
    SRC_ROOT / "control" / "services",
    SRC_ROOT / "registry" / "services",
]

_Func = ast.FunctionDef | ast.AsyncFunctionDef

#: Repository methods that load a single row by identifier.
_BY_ID_REPO_READ = re.compile(
    r"^(get|get_by_id|get_by_identity|get_for_update|require|require_by_id|load|load_by_id)$"
)

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
    "OAuthClientService.approve": _ORG_OAUTH_CLIENT,
    "OAuthClientService.deny": _ORG_OAUTH_CLIENT,
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
    "OAuthAppRegistrationService.create_authorization_code": _ORG_OAUTH_APP,
    "OAuthAppRegistrationService.create_device_authorization": _ORG_OAUTH_APP,
    "OAuthAppRegistrationService.update": _ORG_OAUTH_APP,
    "OAuthAppRegistrationService.rotate_client_secret": _ORG_OAUTH_APP,
    "OAuthAppRegistrationService.delete": _ORG_OAUTH_APP,
    "ApiService.update": _ORG_REVISION,
    "ApiService.delete": _ORG_REVISION,
    "NoteService.create": (
        "create-only operation: the note is caller-owned through created_by == identity.sub"
    ),
    "OverlayService.submit": _ORG_OVERLAY,
    "OverlayService.update": _ORG_OVERLAY,
    "OverlayService.confirm": _ORG_OVERLAY,
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


def _is_identity_filter(node: ast.AST) -> bool:
    return any(
        isinstance(call, ast.Call)
        and _name_of(call.func) == "build_access_filters"
        and _receives_whole_identity(call)
        for call in ast.walk(node)
    )


def _is_scope_builder(node: ast.AST) -> bool:
    """Any ``build_access_filters(...)`` call, including a re-scoped refreshed identity.

    Reassigning a filter variable to a fresh scope builder (e.g.
    ``filters = build_access_filters(refreshed, Event)``) keeps it scoped; only
    reassignment to a non-filter value kills taint.
    """
    return any(
        isinstance(call, ast.Call) and _name_of(call.func) == "build_access_filters"
        for call in ast.walk(node)
    )


def _is_repository_call(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    receiver = _name_of(call.func.value)
    return receiver is not None and (
        receiver.endswith("Repository")
        or receiver in {"repo", "repository", "_repo", "_repository"}
        or receiver.endswith(("_repo", "_repository"))
    )


def _call_argument_names(call: ast.Call) -> set[str]:
    names: set[str] = set()
    for value in list(call.args) + [kw.value for kw in call.keywords]:
        for node in ast.walk(value):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
    return names


def _argument_values(call: ast.Call) -> list[ast.expr]:
    return list(call.args) + [kw.value for kw in call.keywords]


def _assigned_names(node: ast.stmt) -> Iterator[str]:
    targets: list[ast.expr]
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return
    for target in targets:
        if isinstance(target, ast.Name):
            yield target.id


#: Helpers whose bodies implement owner/permission authorization without a query filter.
#: These complete authorization as a statement (they raise on denial).
_EXPLICIT_AUTHORIZATION_HELPERS: dict[str, str] = {
    "AgentAuthService._ensure_active_and_owned": (
        "locks the agent and rejects every non-admin whose identity.sub is not agent.owner_id"
    ),
    "AgentService._load_owned_agent": (
        "loads the agent and rejects every non-admin whose identity.sub is not agent.owner_id"
    ),
    "ConnectSessionService._require_session_access": (
        "requires the session poll token or validates the target agent's owner/org:admin"
    ),
}

#: Boolean predicates that only authorize when their result controls an enforcing
#: branch (``if not self._may_...(...): raise ...``). An ignored result is not
#: authorization; the bare call or a discarded return value does not count.
_BOOLEAN_AUTHORIZATION_PREDICATES: dict[str, str] = {
    "CredentialService._may_mutate_rule_set": (
        "requires org:admin or identity.sub == the non-curated rule set's created_by"
    ),
    "CredentialService._may_attach_rule_set": (
        "requires org:admin, the rule-set creator, or a curated set after the scoped binding load"
    ),
    "ConnectSessionService._is_session_approver": (
        "requires org:admin or a human target-agent owner with credentials/agents write grants"
    ),
    "_CredentialAccessMixin._may_mutate_rule_set": (
        "requires org:admin or identity.sub == the non-curated rule set's created_by"
    ),
    "_CredentialAccessMixin._may_attach_rule_set": (
        "requires org:admin, the rule-set creator, or a curated set after the scoped binding load"
    ),
}


class _ResolvedMethod(NamedTuple):
    owner: str
    method: _Func
    path: Path


class _ServiceIndex:
    """Resolve concrete service methods, including inherited implementations."""

    def __init__(self, sources: list[tuple[ast.Module, Path]]) -> None:
        self.classes: dict[str, ast.ClassDef] = {}
        self.paths: dict[str, Path] = {}
        self.methods: dict[str, dict[str, _Func]] = {}
        for tree, path in sources:
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                self.classes[node.name] = node
                self.paths[node.name] = path
                self.methods[node.name] = {
                    item.name: item for item in node.body if isinstance(item, _Func)
                }

    def owners(self) -> Iterator[str]:
        for name in self.classes:
            if name.endswith("Service"):
                yield name

    def _base_owners(self, owner: str) -> Iterator[str]:
        for base in self.classes[owner].bases:
            name = _name_of(base)
            if name in self.classes:
                yield name

    def mro(self, owner: str) -> list[str]:
        """Python's C3 linearization of *owner*, including the class itself.

        Resolution must inspect the implementation Python actually calls, so a
        diamond override (``Right.get`` shadowing ``Root.get``) is honoured.
        """
        return self._c3(owner, {})

    def _c3(self, owner: str, memo: dict[str, list[str]]) -> list[str]:
        if owner in memo:
            return memo[owner]
        if owner not in self.classes:
            return []
        bases = list(self._base_owners(owner))
        if not bases:
            memo[owner] = [owner]
            return [owner]
        seqs = [self._c3(base, memo)[:] for base in bases] + [bases[:]]
        result = [owner]
        while True:
            seqs = [seq for seq in seqs if seq]
            if not seqs:
                break
            candidate = None
            for seq in seqs:
                head = seq[0]
                if not any(head in other[1:] for other in seqs):
                    candidate = head
                    break
            if candidate is None:
                # Inconsistent hierarchy; fall back to declaration order.
                candidate = seqs[0][0]
            result.append(candidate)
            for seq in seqs:
                if seq and seq[0] == candidate:
                    seq.pop(0)
        memo[owner] = result
        return result

    def resolve(self, owner: str, method_name: str) -> _ResolvedMethod | None:
        for class_name in self.mro(owner):
            method = self.methods.get(class_name, {}).get(method_name)
            if method is not None:
                return _ResolvedMethod(class_name, method, self.paths[class_name])
        return None

    def operations(self) -> Iterator[tuple[str, _ResolvedMethod]]:
        """Yield each public operation once per concrete owner, honouring overrides."""
        for owner in self.owners():
            for resolved in self._visit_operations(owner):
                yield f"{owner}.{resolved.method.name}", resolved

    def _visit_operations(self, owner: str) -> Iterator[_ResolvedMethod]:
        seen: set[str] = set()
        for class_name in self.mro(owner):
            for name, method in self.methods.get(class_name, {}).items():
                if name in seen:
                    continue
                seen.add(name)
                positional = method.args.posonlyargs + method.args.args
                takes_identity = any(arg.arg == "identity" for arg in method.args.kwonlyargs)
                if not name.startswith("_") and len(positional) > 1 and takes_identity:
                    yield _ResolvedMethod(owner, method, self.paths[class_name])

    def _local_service_types(self, method: _Func) -> dict[str, str]:
        types: dict[str, str] = {}
        for node in ast.walk(method):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            class_name = _name_of(node.value.func)
            if class_name not in self.classes:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    types[target.id] = class_name
        return types

    def _resolve_call(self, owner: str, method: _Func, call: ast.Call) -> _ResolvedMethod | None:
        if not isinstance(call.func, ast.Attribute):
            return None
        receiver = call.func.value
        if isinstance(receiver, ast.Name) and receiver.id == "self":
            return self.resolve(owner, call.func.attr)
        if (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id == "super"
        ):
            for base in self._base_owners(owner):
                resolved = self.resolve(base, call.func.attr)
                if resolved is not None:
                    return resolved
            return None
        if isinstance(receiver, ast.Name):
            service_type = self._local_service_types(method).get(receiver.id)
            if service_type is not None:
                return self.resolve(service_type, call.func.attr)
        return None

    @staticmethod
    def _filter_parameters(call: ast.Call, target: _ResolvedMethod) -> set[str]:
        parameters = target.method.args.posonlyargs + target.method.args.args
        passed: set[str] = set()
        for parameter, value in zip(parameters[1:], call.args, strict=False):
            if isinstance(value, ast.Name) or _is_identity_filter(value):
                passed.add(parameter.arg)
        for keyword in call.keywords:
            if keyword.arg is not None and (
                isinstance(keyword.value, ast.Name) or _is_identity_filter(keyword.value)
            ):
                passed.add(keyword.arg)
        return passed

    @staticmethod
    def _filter_aliases(method: _Func, call: ast.Call, filter_names: set[str]) -> set[str]:
        """Filter-tainted names live at the point of *call*, honouring reassignment.

        Walking assignments in source order up to *call*, a tainted value adds taint
        and a clean value kills it, so a reassigned filter no longer authorizes a
        later repository occurrence.
        """
        live = set(filter_names)
        stop = (call.lineno, call.col_offset)
        for node in sorted(
            (n for n in ast.walk(method) if isinstance(n, ast.Assign | ast.AnnAssign)),
            key=lambda n: (n.lineno, n.col_offset),
        ):
            if (node.lineno, node.col_offset) >= stop:
                break
            value = node.value
            if value is None:
                continue
            names = set(_assigned_names(node))
            if not names:
                continue
            if _is_scope_builder(value) or (isinstance(value, ast.Name) and value.id in live):
                live.update(names)
            else:
                live.difference_update(names)
        return live

    def _delegation_scopes_call(
        self,
        owner: str,
        method: _Func,
        call: ast.Call,
        live: set[str],
        seen: frozenset[tuple[str, str]],
    ) -> bool:
        key = (owner, method.name)
        target = self._resolve_call(owner, method, call)
        if target is None:
            return False
        filter_parameters = self._filter_parameters(call, target)
        forwarded = filter_parameters & live
        # An inline build_access_filters(...) argument has no Name to be tainted, so
        # forward every parameter that carries an identity filter (inline or named).
        if any(_is_identity_filter(value) for value in _argument_values(call)):
            forwarded |= filter_parameters
        if forwarded and self._filter_reaches_repository(
            target.owner, target.method, frozenset(forwarded), seen | {key}
        ):
            return True
        # A resolved helper only proves scoping when it receives the whole identity.
        return _receives_whole_identity(call) and self.is_authorized(
            target.owner, target.method, seen | {key}
        )

    def _filter_reaches_repository(
        self,
        owner: str,
        method: _Func,
        filter_parameters: frozenset[str] = frozenset(),
        seen: frozenset[tuple[str, str]] = frozenset(),
    ) -> bool:
        """Whether every sensitive repository occurrence carries a scoped filter.

        Each repository read/write that takes a ``filters`` argument must receive a
        live identity filter or validated delegated authorization; a scoped side query
        does not authorize a different unscoped occurrence.
        """
        key = (owner, method.name)
        if key in seen:
            return False
        # The resource identifiers this method was handed (``thing_id`` etc.); a
        # filters-less repository call is only a by-id occurrence when it consumes one.
        positional = method.args.posonlyargs + method.args.args
        resource_params = {arg.arg for arg in positional[1:]} - {"self"}
        resource_params |= {arg.arg for arg in method.args.kwonlyargs} - {"identity"}
        calls = sorted(
            (n for n in ast.walk(method) if isinstance(n, ast.Call)),
            key=lambda n: (n.lineno, n.col_offset),
        )
        found_sensitive = False
        # Resource keys (method resource params) authorized by a scoped load so far.
        scoped_keys: set[str] = set()
        for call in calls:
            live = self._filter_aliases(method, call, set(filter_parameters))
            if _is_repository_call(call):
                filter_kws = [
                    kw for kw in call.keywords if kw.arg is not None and kw.arg.endswith("filters")
                ]
                if filter_kws:
                    scoped = any(
                        _is_identity_filter(kw.value)
                        or (isinstance(kw.value, ast.Name) and kw.value.id in live)
                        for kw in filter_kws
                    )
                    if not scoped:
                        # A filters argument that is not a live identity filter (e.g.
                        # after reassignment) is a hard violation; nothing rescues it.
                        return False
                    found_sensitive = True
                    scoped_keys |= resource_params & _call_argument_names(call)
                    continue
                # A by-id read of a resource this method has NOT yet scoped is an
                # unscoped occurrence: a scoped load on a different key does not
                # authorize it. Reads of an already-scoped key (a re-read inside the
                # authorized transaction) and auxiliary calls are fine.
                name = _name_of(call.func)
                if name is not None and _BY_ID_REPO_READ.match(name):
                    used = resource_params & _call_argument_names(call)
                    if used - scoped_keys:
                        return False
                continue
            carries = any(
                _is_identity_filter(value) or (isinstance(value, ast.Name) and value.id in live)
                for value in _argument_values(call)
            )
            if carries and self._delegation_scopes_call(owner, method, call, live, seen):
                found_sensitive = True
                scoped_keys |= resource_params & _call_argument_names(call)
        return found_sensitive

    def _predicate_enforces(self, owner: str, method: _Func) -> bool:
        """Whether a documented boolean predicate's result controls an enforcing branch.

        Matched by called method name so it holds whether or not the predicate body is
        resolvable in the indexed sources (mixins are indexed separately). The predicate
        names are unique to these authorizers.
        """
        predicate_names = {key.rsplit(".", 1)[1] for key in _BOOLEAN_AUTHORIZATION_PREDICATES}
        for node in ast.walk(method):
            if not isinstance(node, ast.If):
                continue
            for sub in ast.walk(node.test):
                if not isinstance(sub, ast.Call) or not _receives_whole_identity(sub):
                    continue
                if _name_of(sub.func) in predicate_names:
                    return True
        return False

    def is_authorized(
        self, owner: str, method: _Func, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> bool:
        """Accept repository-bound filters or delegation to a proven authorizer."""
        key = (owner, method.name)
        if key in seen:
            return False
        if self._filter_reaches_repository(owner, method):
            return True
        if self._predicate_enforces(owner, method):
            return True
        for call in ast.walk(method):
            if not isinstance(call, ast.Call) or not _receives_whole_identity(call):
                continue
            target = self._resolve_call(owner, method, call)
            if target is None:
                continue
            explicit_keys = {
                f"{target.owner}.{target.method.name}",
                f"{owner}.{target.method.name}",
            }
            if explicit_keys & _EXPLICIT_AUTHORIZATION_HELPERS.keys():
                return True
            if self.is_authorized(target.owner, target.method, seen | {key}):
                return True
        return False


def _by_id_methods_in_tree(tree: ast.Module, py_file: Path) -> dict[str, tuple[Path, int, bool]]:
    index = _ServiceIndex([(tree, py_file)])
    return {
        name: (resolved.path, resolved.method.lineno, index.is_authorized(owner, resolved.method))
        for name, resolved in index.operations()
        for owner in [name.split(".", 1)[0]]
    }


def _service_sources() -> list[tuple[ast.Module, Path]]:
    return [
        (ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file)), py_file)
        for services_dir in SCOPED_SERVICE_DIRS
        for py_file in python_files_in(services_dir)
    ]


def _by_id_methods() -> dict[str, tuple[Path, int, bool]]:
    index = _ServiceIndex(_service_sources())
    return {
        name: (resolved.path, resolved.method.lineno, index.is_authorized(owner, resolved.method))
        for name, resolved in index.operations()
        for owner in [name.split(".", 1)[0]]
    }


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


@pytest.mark.arch
def test_by_id_identity_call_shape_is_not_scoping() -> None:
    """Self-check: filter construction and identity-accepting calls are not proof."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def get_unused(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "    async def get_ignoring_helper(self, thing_id: str, *, identity: Identity):\n"
        "        return await self._load_thing(thing_id, identity)\n"
        "    async def _load_thing(self, thing_id: str, identity: Identity):\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "    async def get_audit(self, thing_id: str, *, identity: Identity):\n"
        "        await self.audit(identity=identity)\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "    async def get_recorded(self, thing_id: str, *, identity: Identity):\n"
        "        await self.service.record_view(identity)\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    assert {name for name, (_, _, scoped) in methods.items() if not scoped} == {
        "ExampleService.get_unused",
        "ExampleService.get_ignoring_helper",
        "ExampleService.get_audit",
        "ExampleService.get_recorded",
    }


@pytest.mark.arch
def test_by_id_inherited_methods_are_collected_once() -> None:
    """Self-check: concrete services expose authorizing methods inherited from mixins."""
    tree = ast.parse(
        "class AccessMixin:\n"
        "    async def get_shared(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "    async def update_unscoped(self, thing_id: str, *, identity: Identity):\n"
        "        return await ThingRepository.update(thing_id)\n"
        "class FirstService(AccessMixin): pass\n"
        "class SecondService(AccessMixin): pass\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    assert set(methods) == {
        "FirstService.get_shared",
        "FirstService.update_unscoped",
        "SecondService.get_shared",
        "SecondService.update_unscoped",
    }
    assert methods["FirstService.get_shared"][2] is True
    assert methods["SecondService.get_shared"][2] is True
    assert methods["FirstService.update_unscoped"][2] is False
    assert methods["SecondService.update_unscoped"][2] is False


@pytest.mark.arch
def test_ignored_bool_predicate_is_not_authorization() -> None:
    """Self-check: an ignored boolean predicate result does not authorize a mutation.

    ``_may_mutate_rule_set``/``_may_attach_rule_set``/``_is_session_approver`` only
    authorize when their result controls an enforcing branch. An ignored ``False``
    result followed by an unscoped repository mutation must fail the guard.
    """
    tree = ast.parse(
        "class ConnectSessionService:\n"
        "    async def decide(self, session_id: str, *, identity: Identity):\n"
        "        await self._is_session_approver(session_id, identity)\n"
        "        return await ThingRepository.update(session_id)\n"
        "class CredentialService:\n"
        "    async def attach(self, rule_set_id: str, *, identity: Identity):\n"
        "        self._may_attach_rule_set(rule_set_id, identity)\n"
        "        return await ThingRepository.update(rule_set_id)\n"
        "    async def mutate(self, rule_set_id: str, *, identity: Identity):\n"
        "        if not self._may_mutate_rule_set(rule_set_id, identity):\n"
        "            raise Denied(rule_set_id)\n"
        "        return await ThingRepository.get_by_id(rule_set_id)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    assert methods["ConnectSessionService.decide"][2] is False
    assert methods["CredentialService.attach"][2] is False
    assert methods["CredentialService.mutate"][2] is True


@pytest.mark.arch
def test_scoping_is_occurrence_sensitive_and_reassignment_kills_taint() -> None:
    """Self-check: a scoped side query does not authorize a different unscoped read.

    Authorization is per repository occurrence, not existential for the method,
    and reassigning the filter variable to a non-filter value kills its taint.
    """
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def get_side(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        await ThingRepository.get_by_id('other', filters=filters)\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "    async def get_reassigned(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        filters = other()\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "    async def get_scoped(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    assert methods["ExampleService.get_side"][2] is False
    assert methods["ExampleService.get_reassigned"][2] is False
    assert methods["ExampleService.get_scoped"][2] is True


@pytest.mark.arch
def test_mixin_resolution_uses_c3_mro() -> None:
    """Self-check: diamond MRO inspects the implementation Python actually calls.

    With ``class Service(Left, Right)`` where both inherit ``Root.get``, Python's
    C3 linearization resolves ``Service.get`` to ``Right.get`` (the rightmost
    sibling that overrides ``Root``), so the analyzer must inspect that body.
    """
    tree = ast.parse(
        "class Root:\n"
        "    async def get(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "class Left(Root):\n"
        "    pass\n"
        "class Right(Root):\n"
        "    async def get(self, thing_id: str, *, identity: Identity):\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "class DiamondService(Left, Right):\n"
        "    pass\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    # C3: DiamondService -> Left -> Right -> Root, so Right.get wins and is unscoped.
    assert methods["DiamondService.get"][2] is False
