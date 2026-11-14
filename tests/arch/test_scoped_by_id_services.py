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
_States = set[frozenset[str]]

#: Repository methods that load a single row by identifier.
_BY_ID_REPO_READ = re.compile(
    r"^(get|get_by_id|get_by_identity|get_for_update|require|require_by_id|load|load_by_id)$"
)
_COLLECTION_REPO_READ = re.compile(
    r"^(list_\w+|get_page|search\w*|count_\w+|stream\w*|export\w+|iter_\w+)$"
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
    "CredentialService.get_rule_set": (
        "shared permission rule-set/rule policy contains no credential secrets and is visible "
        "to every credentials reader; the binding count is restricted to caller-visible bindings"
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


def _refreshed_identity_aliases(method: _Func, call: ast.Call) -> set[str]:
    """Names live at *call* that came from the caller's identity-refresh callback."""
    refresh_parameters = {
        arg.arg
        for arg in method.args.posonlyargs + method.args.args + method.args.kwonlyargs
        if arg.arg == "refresh_identity"
    }
    aliases: set[str] = set()
    if not refresh_parameters:
        return aliases
    stop = (call.lineno, call.col_offset)
    assignments = sorted(
        (n for n in ast.walk(method) if isinstance(n, ast.Assign | ast.AnnAssign)),
        key=lambda n: (n.lineno, n.col_offset),
    )
    for node in assignments:
        if (node.lineno, node.col_offset) >= stop:
            break
        value = node.value
        if value is None:
            continue
        names = set(_assigned_names(node))
        source = value.value if isinstance(value, ast.Await) else value
        refreshed = (
            isinstance(source, ast.Call)
            and isinstance(source.func, ast.Name)
            and source.func.id in refresh_parameters
        )
        if refreshed or (isinstance(value, ast.Name) and value.id in aliases):
            aliases.update(names)
        else:
            aliases.difference_update(names)
    return aliases


def _is_scope_builder(method: _Func, node: ast.AST) -> bool:
    """A filter builder consuming the caller or its validated refreshed identity."""
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or _name_of(call.func) != "build_access_filters":
            continue
        if _receives_whole_identity(call):
            return True
        refreshed = _refreshed_identity_aliases(method, call)
        if any(
            isinstance(value, ast.Name) and value.id in refreshed
            for value in _argument_values(call)
        ):
            return True
    return False


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
    """Variable roots in call arguments; attribute suffixes are not resource aliases."""
    return {
        node.id
        for value in list(call.args) + [kw.value for kw in call.keywords]
        for node in ast.walk(value)
        if isinstance(node, ast.Name)
    }


def _argument_values(call: ast.Call) -> list[ast.expr]:
    return list(call.args) + [kw.value for kw in call.keywords]


def _call_argument_roots(call: ast.Call) -> set[str]:
    """Variable roots appearing anywhere in the call's argument expressions."""
    return {
        node.id
        for value in _argument_values(call)
        for node in ast.walk(value)
        if isinstance(node, ast.Name)
    }


def _call_attribute_roots(call: ast.Call) -> set[str]:
    """Variable roots whose attributes are passed to the call."""
    roots: set[str] = set()
    for value in _argument_values(call):
        for node in ast.walk(value):
            if not isinstance(node, ast.Attribute):
                continue
            root: ast.expr = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name):
                roots.add(root.id)
    return roots


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


def _assigned_call_names(method: _Func, call: ast.Call) -> set[str]:
    """Names assigned by the statement containing *call*."""
    for statement in ast.walk(method):
        if not isinstance(statement, ast.Assign | ast.AnnAssign) or statement.value is None:
            continue
        if any(node is call for node in ast.walk(statement.value)):
            return set(_assigned_names(statement))
    return set()


def _call_runs_on_every_normal_path(method: _Func, call: ast.Call) -> bool:
    """Whether normal execution reaches *call* before subsequent method statements."""
    parents = {
        child: parent for parent in ast.walk(method) for child in ast.iter_child_nodes(parent)
    }
    node: ast.AST = call
    while node is not method:
        parent = parents[node]
        if isinstance(parent, ast.If) and node is not parent.test:
            return False
        if isinstance(parent, ast.While) and node is not parent.test:
            return False
        if isinstance(parent, ast.For | ast.AsyncFor) and node is not parent.iter:
            return False
        if isinstance(parent, ast.BoolOp) and node is not parent.values[0]:
            return False
        if isinstance(parent, ast.IfExp) and node is not parent.test:
            return False
        if isinstance(parent, ast.Compare) and len(parent.ops) > 1 and node is not parent.left:
            return False
        if isinstance(parent, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
            return False
        if isinstance(parent, ast.Try | ast.TryStar) and node not in parent.finalbody:
            return False
        if isinstance(parent, ast.Match) and node is not parent.subject:
            return False
        if parent is not method and isinstance(
            parent, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
        ):
            return False
        node = parent
    return True


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
            mro = self.mro(owner)
            defining_class = next(
                (
                    class_name
                    for class_name, methods in self.methods.items()
                    if methods.get(method.name) is method
                ),
                owner,
            )
            try:
                start = mro.index(defining_class) + 1
            except ValueError:
                start = 1
            for class_name in mro[start:]:
                candidate = self.methods.get(class_name, {}).get(call.func.attr)
                if candidate is not None:
                    return _ResolvedMethod(class_name, candidate, self.paths[class_name])
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
        """Filter names tainted on every control-flow path reaching *call*."""

        def contains(root: ast.AST) -> bool:
            return any(node is call for node in ast.walk(root))

        def assign(states: _States, statement: ast.stmt) -> _States:
            if not isinstance(statement, ast.Assign | ast.AnnAssign) or statement.value is None:
                return states
            names = set(_assigned_names(statement))
            if not names:
                return states
            value = statement.value
            updated: _States = set()
            for state in states:
                if _is_scope_builder(method, value) or (
                    isinstance(value, ast.Name) and value.id in state
                ):
                    updated.add(state | frozenset(names))
                else:
                    updated.add(state - frozenset(names))
            return updated

        def after(statements: list[ast.stmt], states: _States) -> _States:
            for statement in statements:
                states = assign(states, statement)
                if isinstance(statement, ast.If):
                    body = after(statement.body, set(states))
                    branches = after(statement.orelse, set(states)) if statement.orelse else states
                    states = body | branches
                elif isinstance(statement, ast.While | ast.For | ast.AsyncFor):
                    iterated = after(statement.body, set(states))
                    states = after(statement.orelse, states | iterated)
                elif isinstance(statement, ast.Try | ast.TryStar):
                    body = after(statement.body, set(states))
                    body = after(statement.orelse, body)
                    handled = set().union(
                        *(after(handler.body, set(states)) for handler in statement.handlers)
                    )
                    states = after(statement.finalbody, body | handled)
                elif isinstance(statement, ast.With | ast.AsyncWith):
                    states = after(statement.body, states)
                elif isinstance(statement, ast.Return | ast.Raise | ast.Break | ast.Continue):
                    return set()
            return states

        def before(statements: list[ast.stmt], states: _States) -> _States:
            for statement in statements:
                if contains(statement):
                    if isinstance(statement, ast.If):
                        if contains(statement.test):
                            return states
                        branch = (
                            statement.body
                            if any(map(contains, statement.body))
                            else statement.orelse
                        )
                        return before(branch, states)
                    if isinstance(statement, ast.While | ast.For | ast.AsyncFor):
                        entered = (
                            statement.test if isinstance(statement, ast.While) else statement.iter
                        )
                        if contains(entered):
                            return states
                        if any(map(contains, statement.body)):
                            return before(statement.body, states)
                        return before(statement.orelse, states | after(statement.body, set(states)))
                    if isinstance(statement, ast.With | ast.AsyncWith):
                        if any(contains(item.context_expr) for item in statement.items):
                            return states
                        return before(statement.body, states)
                    if isinstance(statement, ast.Try | ast.TryStar):
                        if any(map(contains, statement.body)):
                            return before(statement.body, states)
                        if any(map(contains, statement.orelse)):
                            return before(statement.orelse, after(statement.body, states))
                        for handler in statement.handlers:
                            if any(map(contains, handler.body)):
                                return before(handler.body, states)
                        body = after(statement.body, set(states))
                        body = after(statement.orelse, body)
                        handled = set().union(
                            *(after(handler.body, set(states)) for handler in statement.handlers)
                        )
                        return before(statement.finalbody, body | handled)
                    return states
                states = after([statement], states)
            return states

        states = before(method.body, {frozenset(filter_names)})
        if not states:
            return set()
        return set.intersection(*(set(state) for state in states))

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
        if not _receives_whole_identity(call):
            return False
        explicit_keys = {
            f"{target.owner}.{target.method.name}",
            f"{owner}.{target.method.name}",
        }
        if explicit_keys & _EXPLICIT_AUTHORIZATION_HELPERS.keys():
            return True
        return self.is_authorized(target.owner, target.method, seen | {key})

    @staticmethod
    def _resource_aliases(method: _Func, call: ast.Call, parameters: set[str]) -> dict[str, str]:
        """Resource-parameter aliases live at *call*, mapped to their canonical keys."""
        aliases = {name: name for name in parameters}
        stop = (call.lineno, call.col_offset)
        assignments = sorted(
            (n for n in ast.walk(method) if isinstance(n, ast.Assign | ast.AnnAssign)),
            key=lambda n: (n.lineno, n.col_offset),
        )
        for node in assignments:
            if (node.lineno, node.col_offset) >= stop:
                break
            value = node.value
            if value is None:
                continue
            source = aliases.get(value.id) if isinstance(value, ast.Name) else None
            for name in _assigned_names(node):
                if source is None:
                    aliases.pop(name, None)
                else:
                    aliases[name] = source
        return aliases

    @staticmethod
    def _delegated_resource_keys(
        call: ast.Call, target: _ResolvedMethod, aliases: dict[str, str]
    ) -> set[str]:
        """Caller resource keys passed into the delegated method's resource parameters."""

        def roots(value: ast.expr) -> set[str]:
            return {
                aliases[node.id]
                for node in ast.walk(value)
                if isinstance(node, ast.Name) and node.id in aliases
            }

        parameters = target.method.args.posonlyargs + target.method.args.args
        resource_parameters = {arg.arg for arg in parameters[1:]}
        resource_parameters |= {
            arg.arg for arg in target.method.args.kwonlyargs if arg.arg.endswith("_id")
        }
        keys: set[str] = set()
        for parameter, value in zip(parameters[1:], call.args, strict=False):
            if parameter.arg in resource_parameters:
                keys |= roots(value)
        for keyword in call.keywords:
            if keyword.arg in resource_parameters:
                keys |= roots(keyword.value)
        return keys

    @staticmethod
    def _validates_loaded_resource(target: _ResolvedMethod) -> bool:
        """Whether a delegated method returns or enforces a loaded resource."""
        name = target.method.name
        return name.startswith(("_load", "_require", "_ensure", "_get")) or (
            name.startswith("get") and _COLLECTION_REPO_READ.match(name) is None
        )

    def _preserves_loaded_object(
        self, owner: str, method: _Func, statement: ast.Assign | ast.AnnAssign, names: set[str]
    ) -> set[str]:
        """Names reassigned to the same object or a by-id reload of that object."""
        value = statement.value
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(value, ast.Call):
            return set()
        target = self._resolve_call(owner, method, value)
        if target is None:
            return set()
        parameters = target.method.args.posonlyargs + target.method.args.args
        bound: set[str] = set()
        for parameter, argument in zip(parameters[1:], value.args, strict=False):
            if isinstance(argument, ast.Name) and argument.id in names:
                bound.add(parameter.arg)
        for keyword in value.keywords:
            if (
                keyword.arg is not None
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id in names
            ):
                bound.add(keyword.arg)
        if not bound:
            return set()

        def is_reload(node: ast.AST) -> bool:
            source = node.value if isinstance(node, ast.Await) else node
            return (
                isinstance(source, ast.Call)
                and _is_repository_call(source)
                and _name_of(source.func) is not None
                and _BY_ID_REPO_READ.match(_name_of(source.func) or "") is not None
                and bool(bound.intersection(_call_argument_roots(source)))
            )

        def returns_loaded_object(expression: ast.expr, position: tuple[int, int]) -> bool:
            if isinstance(expression, ast.IfExp):
                return returns_loaded_object(expression.body, position) and returns_loaded_object(
                    expression.orelse, position
                )
            if not isinstance(expression, ast.Name):
                return False
            found_reload = False
            for assignment in ast.walk(target.method):
                if not isinstance(assignment, ast.Assign | ast.AnnAssign):
                    continue
                if (assignment.lineno, assignment.col_offset) >= position:
                    continue
                if expression.id not in set(_assigned_names(assignment)):
                    continue
                if assignment.value is None or not is_reload(assignment.value):
                    return False
                found_reload = True
            return expression.id in bound or found_reload

        returns = [node for node in ast.walk(target.method) if isinstance(node, ast.Return)]
        if not returns or any(
            node.value is None
            or not returns_loaded_object(node.value, (node.lineno, node.col_offset))
            for node in returns
        ):
            return set()
        return {
            argument.id for argument in ast.walk(value) if isinstance(argument, ast.Name)
        } & names

    def _validated_loads(
        self,
        owner: str,
        method: _Func,
        calls: list[ast.Call],
        filter_parameters: frozenset[str],
        seen: frozenset[tuple[str, str]],
    ) -> dict[ast.Call, ast.Call]:
        """Map an enforcing validator to the exact assigned load it validates."""
        enforcing = self._enforcing_identity_calls(method, None)
        validators: dict[ast.Call, ast.Call] = {}
        for load in calls:
            name = _name_of(load.func)
            if not _is_repository_call(load) or name is None or not _BY_ID_REPO_READ.match(name):
                continue
            loaded_names = _assigned_call_names(method, load)
            if not loaded_names:
                continue
            for candidate in calls:
                if (candidate.lineno, candidate.col_offset) <= (load.lineno, load.col_offset):
                    continue
                if not _receives_whole_identity(candidate):
                    continue
                if not loaded_names.intersection(_call_argument_roots(candidate)):
                    continue
                reassigned = False
                for statement in ast.walk(method):
                    if (
                        not isinstance(statement, ast.Assign | ast.AnnAssign)
                        or statement.value is None
                    ):
                        continue
                    position = (statement.lineno, statement.col_offset)
                    if (
                        not (load.lineno, load.col_offset)
                        < position
                        < (
                            candidate.lineno,
                            candidate.col_offset,
                        )
                    ):
                        continue
                    if any(node is candidate for node in ast.walk(statement.value)):
                        continue
                    if loaded_names.intersection(_assigned_names(statement)):
                        reassigned = True
                        break
                if reassigned:
                    continue
                if candidate in enforcing:
                    validators[candidate] = load
                    break
                target = self._resolve_call(owner, method, candidate)
                if target is None or not self._validates_loaded_resource(target):
                    continue
                if not _call_runs_on_every_normal_path(method, candidate):
                    continue
                live = self._filter_aliases(method, candidate, set(filter_parameters))
                if self._delegation_scopes_call(owner, method, candidate, live, seen):
                    validators[candidate] = load
                    break
        return validators

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
        positional = method.args.posonlyargs + method.args.args
        resource_params = {arg.arg for arg in positional[1:]} - {"self"}
        resource_params |= {arg.arg for arg in method.args.kwonlyargs if arg.arg.endswith("_id")}
        calls = sorted(
            (n for n in ast.walk(method) if isinstance(n, ast.Call)),
            key=lambda n: (n.lineno, n.col_offset),
        )
        returned_names = {
            node.id
            for statement in ast.walk(method)
            if isinstance(statement, ast.Return) and statement.value is not None
            for node in ast.walk(statement.value)
            if isinstance(node, ast.Name)
        }
        returned_calls = {
            call
            for statement in ast.walk(method)
            if isinstance(statement, ast.Return) and statement.value is not None
            for call in ast.walk(statement.value)
            if isinstance(call, ast.Call)
        }
        for statement in ast.walk(method):
            if not isinstance(statement, ast.Assign | ast.AnnAssign):
                continue
            if (
                not returned_names.intersection(_assigned_names(statement))
                or statement.value is None
            ):
                continue
            returned_calls.update(
                call for call in ast.walk(statement.value) if isinstance(call, ast.Call)
            )
        validators = self._validated_loads(owner, method, calls, filter_parameters, seen)
        predicate_names = {key.rsplit(".", 1)[1] for key in _BOOLEAN_AUTHORIZATION_PREDICATES}
        enforced_predicates = self._enforcing_identity_calls(method, predicate_names)
        validated_loads = set(validators.values())
        assignments = sorted(
            (n for n in ast.walk(method) if isinstance(n, ast.Assign | ast.AnnAssign)),
            key=lambda n: (n.lineno, n.col_offset),
        )
        found_sensitive = False
        scoped_keys: set[str] = set()
        scoped_values: set[str] = set()
        pending_values: set[str] = set()
        last_position = (0, 0)
        for call in calls:
            call_position = (call.lineno, call.col_offset)
            for statement in assignments:
                position = (statement.lineno, statement.col_offset)
                if position < last_position:
                    continue
                if position >= call_position:
                    break
                names = set(_assigned_names(statement))
                candidates = names & scoped_values
                preserved = (
                    self._preserves_loaded_object(owner, method, statement, candidates)
                    if candidates
                    else set()
                )
                scoped_values.difference_update(names - preserved)
                pending_values.difference_update(names)
            last_position = call_position
            live = self._filter_aliases(method, call, set(filter_parameters))
            aliases = self._resource_aliases(method, call, resource_params)
            used = {aliases[name] for name in _call_argument_names(call) if name in aliases}
            authorized_keys = scoped_keys | self._predicate_resource_keys(
                owner, method, call, aliases, enforced_predicates
            )
            validated_load = validators.get(call)
            if validated_load is not None:
                load_aliases = self._resource_aliases(method, validated_load, resource_params)
                scoped_keys |= {
                    load_aliases[name]
                    for name in _call_argument_names(validated_load)
                    if name in load_aliases
                }
                scoped_values |= _assigned_call_names(method, validated_load)
                found_sensitive = True
            if _is_repository_call(call):
                name = _name_of(call.func)
                if call in validated_loads:
                    pending_values |= _assigned_call_names(method, call)
                related_roots = _call_attribute_roots(call) - {"self", "identity"}
                unscoped_related = related_roots - scoped_values - pending_values
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
                        return False
                    found_sensitive = True
                    scoped_keys |= used
                    if name is not None and _BY_ID_REPO_READ.match(name):
                        scoped_values |= _assigned_call_names(method, call)
                    continue
                if (
                    name is not None
                    and _BY_ID_REPO_READ.match(name)
                    and call not in validated_loads
                ):
                    resource_scoped = bool(used) and not (used - authorized_keys)
                    object_scoped = not used and bool(
                        related_roots & (scoped_values | pending_values)
                    )
                    if not (resource_scoped or object_scoped):
                        return False
                if (
                    name is not None
                    and _COLLECTION_REPO_READ.match(name)
                    and call in returned_calls
                    and (
                        used - authorized_keys
                        or (not used and (unscoped_related or not related_roots))
                    )
                ):
                    return False
                returns_resource = name is not None and (
                    _BY_ID_REPO_READ.match(name) is not None
                    or not name.startswith(
                        ("get", "list", "count", "search", "stream", "export", "iter")
                    )
                )
                if used & authorized_keys and returns_resource:
                    scoped_values |= _assigned_call_names(method, call)
                    found_sensitive = True
                continue
            carries_filter = any(
                _is_identity_filter(value) or (isinstance(value, ast.Name) and value.id in live)
                for value in _argument_values(call)
            )
            if not (carries_filter or _receives_whole_identity(call)):
                continue
            if self._delegation_scopes_call(owner, method, call, live, seen):
                found_sensitive = True
                target = self._resolve_call(owner, method, call)
                if target is not None and self._validates_loaded_resource(target):
                    scoped_keys |= self._delegated_resource_keys(call, target, aliases)
                    scoped_values |= _assigned_call_names(method, call)
        return found_sensitive

    def _enforcing_identity_calls(
        self, method: _Func, predicate_names: set[str] | None
    ) -> set[ast.Call]:
        """Identity predicate calls whose false result reaches denying control flow."""
        enforced: set[ast.Call] = set()

        def denies(statements: list[ast.stmt]) -> bool:
            for statement in statements:
                for node in ast.walk(statement):
                    if isinstance(node, ast.Raise):
                        return True
                    if isinstance(node, ast.Return) and (
                        node.value is None
                        or (
                            isinstance(node.value, ast.Constant)
                            and node.value.value in {None, False}
                        )
                    ):
                        return True
            return False

        def returns(statements: list[ast.stmt]) -> bool:
            return any(
                isinstance(node, ast.Return)
                for statement in statements
                for node in ast.walk(statement)
            )

        def outcome_when_false(node: ast.expr, predicate: ast.Call) -> bool | None:
            """Three-valued test result when *predicate* is false and peers are unknown."""
            if node is predicate:
                return False
            if isinstance(node, ast.Await):
                return outcome_when_false(node.value, predicate)
            if (
                isinstance(node, ast.Compare)
                and len(node.ops) == 1
                and isinstance(node.ops[0], ast.NotEq)
                and any(
                    isinstance(side, ast.Attribute)
                    and side.attr == "sub"
                    and isinstance(side.value, ast.Name)
                    and side.value.id == "identity"
                    for side in (node.left, *node.comparators)
                )
            ):
                # A non-self comparison documents the alternate caller-is-owner path.
                return True
            if isinstance(node, ast.Constant) and isinstance(node.value, bool):
                return node.value
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                value = outcome_when_false(node.operand, predicate)
                return None if value is None else not value
            if isinstance(node, ast.BoolOp):
                values = [outcome_when_false(value, predicate) for value in node.values]
                if isinstance(node.op, ast.And):
                    if any(value is False for value in values):
                        return False
                    if all(value is True for value in values):
                        return True
                else:
                    if any(value is True for value in values):
                        return True
                    if all(value is False for value in values):
                        return False
            return None

        def block(statements: list[ast.stmt]) -> None:
            for index, statement in enumerate(statements):
                if isinstance(statement, ast.If):
                    for call in ast.walk(statement.test):
                        if not isinstance(call, ast.Call):
                            continue
                        name = _name_of(call.func)
                        is_predicate = (
                            name is not None
                            and name.startswith(("_can_", "_may_", "_is_", "_has_"))
                            if predicate_names is None
                            else name in predicate_names
                        )
                        if not is_predicate or not _receives_whole_identity(call):
                            continue
                        outcome = outcome_when_false(statement.test, call)
                        if outcome is True and denies(statement.body):
                            enforced.add(call)
                        if outcome is False and denies(statement.orelse):
                            enforced.add(call)
                        if (
                            outcome is False
                            and returns(statement.body)
                            and denies(statements[index + 1 :])
                        ):
                            enforced.add(call)
                for field in ("body", "orelse", "finalbody"):
                    nested = getattr(statement, field, [])
                    if nested:
                        block(nested)
                for handler in getattr(statement, "handlers", []):
                    block(handler.body)

        block(method.body)
        return enforced

    def _predicate_resource_keys(
        self,
        owner: str,
        method: _Func,
        call: ast.Call,
        aliases: dict[str, str],
        enforced: set[ast.Call],
    ) -> set[str]:
        """Canonical resource keys guarded by enforced predicates before *call*."""
        keys: set[str] = set()
        for predicate in enforced:
            if (predicate.lineno, predicate.col_offset) >= (call.lineno, call.col_offset):
                continue
            if not _call_runs_on_every_normal_path(method, predicate):
                continue
            target = self._resolve_call(owner, method, predicate)
            if target is not None:
                keys |= self._delegated_resource_keys(predicate, target, aliases)
            else:
                keys |= {
                    aliases[name] for name in _call_argument_names(predicate) if name in aliases
                }
        return keys

    def is_authorized(
        self, owner: str, method: _Func, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> bool:
        """Accept repository-bound filters or delegation to a proven authorizer."""
        return self._filter_reaches_repository(owner, method, seen=seen)


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
        "    async def get_branch_mixed(self, thing_id: str, *, identity: Identity):\n"
        "        if condition:\n"
        "            filters = []\n"
        "        else:\n"
        "            filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "    async def get_branch_scoped(self, thing_id: str, *, identity: Identity):\n"
        "        if condition:\n"
        "            filters = build_access_filters(identity, Thing)\n"
        "        else:\n"
        "            filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "    async def get_internal_literal(self, thing_id: str, *, identity: Identity):\n"
        "        leaked = await ThingRepository.get_by_id('other')\n"
        "        await audit(leaked)\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "    async def get_scoped(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    assert methods["ExampleService.get_side"][2] is False
    assert methods["ExampleService.get_reassigned"][2] is False
    assert methods["ExampleService.get_branch_mixed"][2] is False
    assert methods["ExampleService.get_branch_scoped"][2] is True
    assert methods["ExampleService.get_internal_literal"][2] is False
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


@pytest.mark.arch
def test_predicate_test_must_enforce_denial() -> None:
    """Self-check: logging or passing on a false predicate does not deny access."""
    tree = ast.parse(
        "class CredentialService:\n"
        "    async def mutate_log(self, rule_set_id: str, *, identity: Identity):\n"
        "        if not self._may_mutate_rule_set(rule_set_id, identity):\n"
        "            logger.info('denied')\n"
        "        return await ThingRepository.get_by_id(rule_set_id)\n"
        "    async def mutate_pass(self, rule_set_id: str, *, identity: Identity):\n"
        "        if not self._may_mutate_rule_set(rule_set_id, identity):\n"
        "            pass\n"
        "        return await ThingRepository.get_by_id(rule_set_id)\n"
        "    async def mutate_raise(self, rule_set_id: str, *, identity: Identity):\n"
        "        if not self._may_mutate_rule_set(rule_set_id, identity):\n"
        "            raise Denied(rule_set_id)\n"
        "        return await ThingRepository.get_by_id(rule_set_id)\n"
        "    async def mutate_short_circuit(self, rule_set_id: str, *, identity: Identity):\n"
        "        if not self._may_mutate_rule_set(rule_set_id, identity) and feature_enabled:\n"
        "            raise Denied(rule_set_id)\n"
        "        return await ThingRepository.get_by_id(rule_set_id)\n"
        "    async def mutate_or(self, rule_set_id: str, *, identity: Identity):\n"
        "        if not self._may_mutate_rule_set(rule_set_id, identity) or feature_enabled:\n"
        "            raise Denied(rule_set_id)\n"
        "        return await ThingRepository.get_by_id(rule_set_id)\n"
        "    async def mutate_other(self, rule_set_id: str, *, identity: Identity):\n"
        "        if not self._may_mutate_rule_set(rule_set_id, identity):\n"
        "            raise Denied(rule_set_id)\n"
        "        return await ThingRepository.get_by_id(other_id)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    actual = {name: result[2] for name, result in methods.items()}
    assert actual == {
        "CredentialService.mutate_log": False,
        "CredentialService.mutate_pass": False,
        "CredentialService.mutate_raise": True,
        "CredentialService.mutate_short_circuit": False,
        "CredentialService.mutate_or": True,
        "CredentialService.mutate_other": False,
    }


@pytest.mark.arch
def test_filter_builder_requires_caller_identity_provenance() -> None:
    """Self-check: only the caller or its validated refreshed identity scopes a filter."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def get_other(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(other_identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "    async def get_without_identity(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "    async def get_refreshed(\n"
        "        self, thing_id: str, *, identity: Identity, refresh_identity: Refresh\n"
        "    ):\n"
        "        refreshed = await refresh_identity()\n"
        "        filters = build_access_filters(refreshed, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    actual = {name: result[2] for name, result in methods.items()}
    assert actual == {
        "ExampleService.get_other": False,
        "ExampleService.get_without_identity": False,
        "ExampleService.get_refreshed": True,
    }


@pytest.mark.arch
def test_unscoped_occurrences_and_resource_aliases_cannot_inherit_scoping() -> None:
    """Self-check: side queries and other-resource delegation do not authorize reads."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def get_list_side(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        await ThingRepository.get_by_id('other', filters=filters)\n"
        "        return await ThingRepository.list_all()\n"
        "    async def get_alias(self, thing_id: str, *, identity: Identity):\n"
        "        target = thing_id\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        await ThingRepository.get_by_id('other', filters=filters)\n"
        "        return await ThingRepository.get_by_id(target)\n"
        "    async def get_attribute_alias(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "        return await ThingRepository.get_by_id(other.thing_id)\n"
        "    async def get_delegated_other(self, thing_id: str, *, identity: Identity):\n"
        "        await self._load_thing('other', identity=identity)\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "    async def get_delegated(self, thing_id: str, *, identity: Identity):\n"
        "        return await self._load_thing(thing_id, identity=identity)\n"
        "    async def _load_thing(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    actual = {name: result[2] for name, result in methods.items()}
    assert actual == {
        "ExampleService.get_list_side": False,
        "ExampleService.get_alias": False,
        "ExampleService.get_attribute_alias": False,
        "ExampleService.get_delegated_other": False,
        "ExampleService.get_delegated": True,
    }


@pytest.mark.arch
def test_super_resolution_starts_after_concrete_owner_in_c3_mro() -> None:
    """Self-check: super() dispatches to Right.get, not Root.get through Left."""
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
        "    async def get(self, thing_id: str, *, identity: Identity):\n"
        "        return await super().get(thing_id, identity=identity)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    assert methods["DiamondService.get"][2] is False


@pytest.mark.arch
def test_inherited_super_resolution_starts_after_defining_class() -> None:
    """Self-check: an inherited super() call continues from its defining class."""
    tree = ast.parse(
        "class Root:\n"
        "    async def get(self, thing_id: str, *, identity: Identity):\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "class Right(Root):\n"
        "    async def get(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "class Left(Root):\n"
        "    async def get(self, thing_id: str, *, identity: Identity):\n"
        "        return await super().get(thing_id, identity=identity)\n"
        "class DiamondService(Left, Right):\n"
        "    pass\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    assert methods["DiamondService.get"][2] is True


@pytest.mark.arch
def test_shared_rule_set_exception_is_exactly_named() -> None:
    """The shared policy exception does not authorize lookalikes or side-query reads."""
    tree = ast.parse(
        "class CredentialService:\n"
        "    async def get_rule_set(self, rule_set_id: str, *, identity: Identity):\n"
        "        rule_set = await PermissionRuleSetRepository.get_by_id(rule_set_id)\n"
        "        rules = await PermissionRuleSetRepository.list_rules(rule_set_id)\n"
        "        count = await self._visible_binding_count_for_rule_set(\n"
        "            rule_set_id, identity=identity\n"
        "        )\n"
        "        return rule_set, rules, count\n"
        "    async def get_other_rule_set(self, rule_set_id: str, *, identity: Identity):\n"
        "        rule_set = await PermissionRuleSetRepository.get_by_id(rule_set_id)\n"
        "        rules = await PermissionRuleSetRepository.list_rules(rule_set_id)\n"
        "        count = await self._visible_binding_count_for_rule_set(\n"
        "            rule_set_id, identity=identity\n"
        "        )\n"
        "        return rule_set, rules, count\n"
        "    async def get_side(self, thing_id: str, *, identity: Identity):\n"
        "        await self._visible_binding_count_for_rule_set(thing_id, identity=identity)\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "    async def _visible_binding_count_for_rule_set(\n"
        "        self, rule_set_id: str, *, identity: Identity\n"
        "    ):\n"
        "        filters = build_access_filters(identity, Binding)\n"
        "        return await BindingRepository.count_bindings(rule_set_id, filters=filters)\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    admitted = {
        name: scoped or name in _ATTRIBUTION_ONLY_ALLOWLIST
        for name, (_, _, scoped) in methods.items()
    }
    assert admitted == {
        "CredentialService.get_rule_set": True,
        "CredentialService.get_other_rule_set": False,
        "CredentialService.get_side": False,
    }
    assert not any(scoped for _, _, scoped in methods.values())


@pytest.mark.arch
def test_deferred_validation_scopes_only_the_loaded_object() -> None:
    """Validated loads authorize their resource; counts and reassignment do not."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def get_validated(self, thing_id: str, *, identity: Identity):\n"
        "        thing = await ThingRepository.get_by_id(thing_id)\n"
        "        thing = await self._require_thing(thing, identity=identity)\n"
        "        return await EntryRepository.list_for_thing(thing.id)\n"
        "    async def get_conditionally_validated(\n"
        "        self, thing_id: str, *, identity: Identity\n"
        "    ):\n"
        "        thing = await ThingRepository.get_by_id(thing_id)\n"
        "        if should_validate:\n"
        "            thing = await self._require_thing(thing, identity=identity)\n"
        "        return await EntryRepository.list_for_thing(thing.id)\n"
        "    async def _require_thing(self, thing: Thing, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing.id, filters=filters)\n"
        "    async def get_predicate_validated(self, thing_id: str, *, identity: Identity):\n"
        "        thing = await ThingRepository.get_by_id(thing_id)\n"
        "        if thing is None or not self._can_bind(identity, thing.created_by):\n"
        "            raise Denied(thing_id)\n"
        "        return await BindingRepository.get(thing_id)\n"
        "    async def get_count_side(self, thing_id: str, *, identity: Identity):\n"
        "        await self._count_visible(thing_id, identity=identity)\n"
        "        return await ThingRepository.get_by_id(thing_id)\n"
        "    async def _count_visible(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.count_visible(thing_id, filters=filters)\n"
        "    async def get_reassigned(self, thing_id: str, *, identity: Identity):\n"
        "        thing = await ThingRepository.get_by_id(thing_id)\n"
        "        thing = other()\n"
        "        thing = await self._require_thing(thing, identity=identity)\n"
        "        return await EntryRepository.list_for_thing(thing.id)\n"
        "    async def get_reassigned_after_validation(\n"
        "        self, thing_id: str, *, identity: Identity\n"
        "    ):\n"
        "        thing = await ThingRepository.get_by_id(thing_id)\n"
        "        thing = await self._require_thing(thing, identity=identity)\n"
        "        thing = other()\n"
        "        return await EntryRepository.list_for_thing(thing.id)\n"
        "    async def get_transformed(self, thing_id: str, *, identity: Identity):\n"
        "        thing = await ThingRepository.get_by_id(thing_id)\n"
        "        thing = await self._require_thing(thing, identity=identity)\n"
        "        thing = await self._reload(thing)\n"
        "        return await EntryRepository.list_for_thing(thing.id)\n"
        "    async def _reload(self, thing: Thing):\n"
        "        current = await ThingRepository.get_by_id(thing.id)\n"
        "        return current if current is not None else thing\n"
        "    async def get_rebound_reload(self, thing_id: str, *, identity: Identity):\n"
        "        thing = await ThingRepository.get_by_id(thing_id)\n"
        "        thing = await self._require_thing(thing, identity=identity)\n"
        "        thing = await self._reload_other(thing)\n"
        "        return await EntryRepository.list_for_thing(thing.id)\n"
        "    async def _reload_other(self, thing: Thing):\n"
        "        thing = await OtherRepository.get_by_id('other')\n"
        "        return thing\n"
        "    async def get_replaced(self, thing_id: str, *, identity: Identity):\n"
        "        thing = await ThingRepository.get_by_id(thing_id)\n"
        "        thing = await self._require_thing(thing, identity=identity)\n"
        "        thing = await self._replace(thing)\n"
        "        return await EntryRepository.list_for_thing(thing.id)\n"
        "    async def _replace(self, thing: Thing):\n"
        "        return other()\n"
    )
    methods = _by_id_methods_in_tree(tree, Path("<self-check>"))
    actual = {name: result[2] for name, result in methods.items()}
    assert actual == {
        "ExampleService.get_validated": True,
        "ExampleService.get_conditionally_validated": False,
        "ExampleService.get_predicate_validated": True,
        "ExampleService.get_count_side": False,
        "ExampleService.get_reassigned": False,
        "ExampleService.get_reassigned_after_validation": False,
        "ExampleService.get_transformed": True,
        "ExampleService.get_rebound_reload": False,
        "ExampleService.get_replaced": False,
    }
