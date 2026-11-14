"""Architecture enforcement: scoping boundary rules.

1. No repository may import Identity (repos are auth-agnostic).
2. No shared module may import ORM models from surface schemas.
3. Each surface scoping module only imports models from its own surface.
4. A scoped load in a mutating service method uses a write filter.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in
from .test_scoped_by_id_services import _service_sources, _ServiceIndex

_Func = ast.FunctionDef | ast.AsyncFunctionDef
_WRITE_MUTATOR = re.compile(
    r"^(update|delete|set_|replace|patch|revoke|grant|bind|unbind|attach|detach|"
    r"decide|withdraw|mark_|cancel|disable|enable|confirm|archive|purge|resume)"
)
_WRITE_SERVICE_DIRS = [
    SRC_ROOT / "admin" / "services",
    SRC_ROOT / "auth" / "services",
    SRC_ROOT / "control" / "services",
    SRC_ROOT / "registry" / "services",
]

#: ``Class.method`` -> why its read filter is sufficient for this mutation.
_WRITE_FILTER_ALLOWLIST: dict[str, str] = {
    "PermissionService.set_assigned": (
        "User filters have no read widenings; ensure_can_manage authorizes the mutation"
    ),
    "UserService.update": (
        "User filters have no read widenings; ensure_can_manage authorizes the mutation"
    ),
    "JobService.cancel": (
        "owner cancellation covers jobs created by their agents, so the read filter is required"
    ),
    "CredentialService.replace_agent_permissions": (
        "_require_visible_binding(for_write=True) adds the credential owner/org:admin write gate"
    ),
    "CredentialService.patch_agent_permissions": (
        "_require_visible_binding(for_write=True) adds the credential owner/org:admin write gate"
    ),
    "CredentialService.attach_binding_rule_set": (
        "_require_visible_binding(for_write=True) adds the credential owner/org:admin write gate"
    ),
    "CredentialService.detach_binding_rule_set": (
        "_require_visible_binding(for_write=True) adds the credential owner/org:admin write gate"
    ),
}


def _collect_import_modules(tree: ast.AST) -> list[tuple[str, int]]:
    results: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            results.append((node.module, node.lineno))
    return results


def _check_no_import(source_dir: Path, forbidden_prefix: str) -> list[str]:
    violations: list[str] = []
    for py_file in python_files_in(source_dir):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for module, lineno in _collect_import_modules(tree):
            if module == forbidden_prefix or module.startswith(f"{forbidden_prefix}."):
                violations.append(f"{py_file}:{lineno} — imports '{module}'")
    return violations


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _has_repository_mutator(method: _Func) -> bool:
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = _name_of(node.func.value)
        is_repository = receiver is not None and (
            receiver.endswith("Repository")
            or receiver in {"repo", "repository", "_repo", "_repository"}
            or receiver.endswith(("_repo", "_repository"))
        )
        if is_repository and _WRITE_MUTATOR.match(node.func.attr):
            return True
    return False


def _uses_write_filter(call: ast.Call, true_parameters: frozenset[str]) -> bool:
    return any(
        kw.arg == "for_write"
        and (
            (isinstance(kw.value, ast.Constant) and kw.value.value is True)
            or (isinstance(kw.value, ast.Name) and kw.value.id in true_parameters)
        )
        for kw in call.keywords
    )


def _true_parameters(
    call: ast.Call, target: _Func, true_parameters: frozenset[str] = frozenset()
) -> frozenset[str]:
    """Parameters of *target* known to be ``True`` at this delegation site.

    A literal ``True`` and a name already known-true (``for_write=for_write``) both
    propagate, so a known-true flag survives every delegation hop.
    """

    def is_true(value: ast.expr) -> bool:
        return (isinstance(value, ast.Constant) and value.value is True) or (
            isinstance(value, ast.Name) and value.id in true_parameters
        )

    parameters = target.args.posonlyargs + target.args.args
    names = {
        parameter.arg
        for parameter, value in zip(parameters[1:], call.args, strict=False)
        if is_true(value)
    }
    names.update(kw.arg for kw in call.keywords if kw.arg is not None and is_true(kw.value))
    return frozenset(names)


class _WriteFilterIndex:
    def __init__(self, index: _ServiceIndex) -> None:
        self.index = index

    def _has_mutation(
        self, owner: str, method: _Func, seen: frozenset[tuple[str, str]] = frozenset()
    ) -> bool:
        key = (owner, method.name)
        if key in seen:
            return False
        if _has_repository_mutator(method):
            return True
        for call in ast.walk(method):
            if not isinstance(call, ast.Call):
                continue
            target = self.index._resolve_call(owner, method, call)
            if target is not None and self._has_mutation(target.owner, target.method, seen | {key}):
                return True
        return False

    def _filter_status(
        self,
        owner: str,
        method: _Func,
        true_parameters: frozenset[str] = frozenset(),
        seen: frozenset[tuple[str, str]] = frozenset(),
    ) -> tuple[bool, bool]:
        key = (owner, method.name)
        if key in seen:
            return False, True
        found = False
        write_scoped = True
        for call in ast.walk(method):
            if not isinstance(call, ast.Call):
                continue
            if _name_of(call.func) == "build_access_filters":
                found = True
                write_scoped = write_scoped and _uses_write_filter(call, true_parameters)
                continue
            values = list(call.args) + [kw.value for kw in call.keywords]
            if not any(isinstance(value, ast.Name) and value.id == "identity" for value in values):
                continue
            target = self.index._resolve_call(owner, method, call)
            if target is None:
                continue
            is_authorization_helper = target.method.name.startswith(("_load_", "_require_")) or (
                target.method.name == "get_agent"
            )
            if not is_authorization_helper and not self._has_mutation(target.owner, target.method):
                continue
            child_found, child_write_scoped = self._filter_status(
                target.owner,
                target.method,
                _true_parameters(call, target.method, true_parameters),
                seen | {key},
            )
            found = found or child_found
            write_scoped = write_scoped and child_write_scoped
        return found, write_scoped

    def findings(self) -> dict[str, tuple[Path, int, bool]]:
        found: dict[str, tuple[Path, int, bool]] = {}
        for name, resolved in self.index.operations():
            owner = name.split(".", 1)[0]
            if not self._has_mutation(owner, resolved.method):
                continue
            has_filters, write_scoped = self._filter_status(owner, resolved.method)
            if has_filters:
                found[name] = (resolved.path, resolved.method.lineno, write_scoped)
        return found


#: Credential mutations whose read filter is justified solely by the
#: ``_require_visible_binding(for_write=True)`` gate; removing that flag makes them stale.
_CREDENTIAL_WRITE_GATE_EXEMPTIONS = frozenset(
    {
        "CredentialService.replace_agent_permissions",
        "CredentialService.patch_agent_permissions",
        "CredentialService.attach_binding_rule_set",
        "CredentialService.detach_binding_rule_set",
    }
)


def _executable_nodes(root: _Func) -> Iterator[ast.AST]:
    """Nodes reachable in *root*, excluding dead branches and nested definitions."""

    def visit(node: ast.AST) -> Iterator[ast.AST]:
        if node is not root and isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ):
            return
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            if node.test.value is True:
                yield from visit_statements(node.body)
            elif node.test.value is False:
                yield from visit_statements(node.orelse)
            return
        yield node
        for child in ast.iter_child_nodes(node):
            yield from visit(child)

    def visit_statements(statements: list[ast.stmt]) -> Iterator[ast.AST]:
        for statement in statements:
            yield from visit(statement)

    yield from visit(root)


def _credential_write_gate_present(index: _ServiceIndex, name: str) -> bool:
    """Whether *name* still calls ``_require_visible_binding(..., for_write=True)``.

    The exemption is only valid while that exact write gate is present; downgrading or
    removing the flag (leaving only a read filter) makes the exemption stale.
    """
    owner, method_name = name.split(".", 1)
    resolved = index.resolve(owner, method_name)
    if resolved is None:
        return False
    for node in _executable_nodes(resolved.method):
        if not isinstance(node, ast.Call) or _name_of(node.func) != "_require_visible_binding":
            continue
        if any(
            kw.arg == "for_write" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in node.keywords
        ):
            return True
    return False


def _write_filter_findings(tree: ast.Module, py_file: Path) -> dict[str, tuple[Path, int, bool]]:
    """Return mutating methods with scoped loads and whether every load uses a write filter."""
    return _WriteFilterIndex(_ServiceIndex([(tree, py_file)])).findings()


def _write_filter_findings_in_services() -> dict[str, tuple[Path, int, bool]]:
    return _WriteFilterIndex(_ServiceIndex(_service_sources())).findings()


@pytest.mark.arch
def test_write_paths_use_for_write() -> None:
    """A scoped load in a method with a repository mutation opts out of read widenings."""
    violations = [
        f"{path}:{line} — {name} mutates a repository after build_access_filters; "
        "pass for_write=True or add _WRITE_FILTER_ALLOWLIST with a reason"
        for name, (path, line, scoped) in sorted(_write_filter_findings_in_services().items())
        if not scoped and name not in _WRITE_FILTER_ALLOWLIST
    ]
    assert not violations, "Read filters on write paths:\n" + "\n".join(violations)


@pytest.mark.arch
def test_write_filter_allowlist_is_not_stale() -> None:
    """Write-filter exemptions disappear when the method changes or opts into write scoping."""
    methods = _write_filter_findings_in_services()
    stale = [
        f"{name}: " + ("no matching mutating method" if name not in methods else "now scoped")
        for name in sorted(_WRITE_FILTER_ALLOWLIST)
        if name not in methods or methods[name][2]
    ]
    assert not stale, "Remove stale _WRITE_FILTER_ALLOWLIST entries:\n" + "\n".join(stale)


@pytest.mark.arch
def test_delegated_write_filter_coverage_is_exercised() -> None:
    """Concrete wrappers and repository aliases contribute to the write-filter boundary."""
    methods = _write_filter_findings_in_services()
    assert methods["UserService.delete"][2] is True
    assert methods["UserService.disable"][2] is True
    assert methods["AgentService.bind_credential"][2] is True
    assert methods["AgentService.unbind_credential"][2] is True


def _job_cancel_contract_source() -> str:
    job_contract = (
        SRC_ROOT.parent.parent
        / "tests"
        / "integration"
        / "admin"
        / "services"
        / "test_job_scoping.py"
    )
    return job_contract.read_text(encoding="utf-8")


def _owner_cancel_agent_job_test(source: str) -> _Func | None:
    """The named positive integration test, if present."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "test_owner_can_cancel_their_agents_job"
        ):
            return node
    return None


def _owner_cancel_test_has_contract(func: _Func) -> bool:
    """The owner cancels the agent's queued job and observes its CANCELLED status."""
    cancelled_results: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
            continue
        value = node.value.value if isinstance(node.value, ast.Await) else node.value
        if not isinstance(value, ast.Call) or _name_of(value.func) != "cancel":
            continue
        owner_identity = any(
            kw.arg == "identity"
            and isinstance(kw.value, ast.Call)
            and _name_of(kw.value.func) == "_user"
            and kw.value.args
            and isinstance(kw.value.args[0], ast.Attribute)
            and kw.value.args[0].attr == "owner_id"
            and isinstance(kw.value.args[0].value, ast.Name)
            and kw.value.args[0].value.id == "seed"
            for kw in value.keywords
        )
        if not owner_identity:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        cancelled_results.update(target.id for target in targets if isinstance(target, ast.Name))
    for node in ast.walk(func):
        if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare):
            continue
        left = node.test.left
        observes_result = (
            isinstance(left, ast.Attribute)
            and left.attr == "status"
            and isinstance(left.value, ast.Name)
            and left.value.id in cancelled_results
        )
        if observes_result and any(
            isinstance(comp, ast.Attribute) and comp.attr == "CANCELLED"
            for comp in node.test.comparators
        ):
            return True
    return False


@pytest.mark.arch
def test_job_cancel_exception_matches_its_integration_contract() -> None:
    """The cancellation exemption requires the named positive owner-cancels-agent test."""
    assert _write_filter_findings_in_services()["JobService.cancel"][2] is False
    test = _owner_cancel_agent_job_test(_job_cancel_contract_source())
    assert test is not None, "test_owner_can_cancel_their_agents_job is missing"
    assert _owner_cancel_test_has_contract(test), (
        "test_owner_can_cancel_their_agents_job must cancel the agent's job as the owner "
        "and assert cancelled.status == JobStatus.CANCELLED"
    )


@pytest.mark.arch
def test_job_cancel_contract_self_check() -> None:
    """Self-check: a same-named test without the owner-cancel contract does not qualify."""
    bare = _owner_cancel_agent_job_test(
        "async def test_owner_can_cancel_their_agents_job():\n    assert True\n"
    )
    assert bare is not None
    assert not _owner_cancel_test_has_contract(bare)
    complete = _owner_cancel_agent_job_test(
        "async def test_owner_can_cancel_their_agents_job():\n"
        "    cancelled = await JobService(ctx).cancel(queued.id, identity=_user(seed.owner_id))\n"
        "    assert cancelled.status == JobStatus.CANCELLED\n"
    )
    assert complete is not None
    assert _owner_cancel_test_has_contract(complete)


@pytest.mark.arch
def test_for_write_true_propagates_through_wrapper_hops() -> None:
    """Self-check: a known-true for_write name survives every delegation hop.

    A public method passing literal ``for_write=True`` to a wrapper that forwards
    ``for_write=for_write`` down to the filter builder is valid and must not be
    falsely rejected.
    """
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def replace(self, thing_id: str, *, identity: Identity):\n"
        "        await self._load_manageable(thing_id, identity=identity, for_write=True)\n"
        "        await ThingRepository.replace(thing_id)\n"
        "    async def _load_manageable(\n"
        "        self, thing_id: str, *, identity: Identity, for_write: bool = False\n"
        "    ):\n"
        "        return await self._load_thing(thing_id, identity=identity, for_write=for_write)\n"
        "    async def _load_thing(\n"
        "        self, thing_id: str, *, identity: Identity, for_write: bool = False\n"
        "    ):\n"
        "        filters = build_access_filters(identity, Thing, for_write=for_write)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
    )
    methods = _write_filter_findings(tree, Path("<self-check>"))
    assert methods["ExampleService.replace"][2] is True


@pytest.mark.arch
def test_credential_exemption_requires_for_write_gate() -> None:
    """Self-check: the credential exemption is only valid with for_write=True.

    Removing or downgrading the ``_require_visible_binding`` write flag must make
    the exemption stale even when a read filter remains.
    """
    gated = ast.parse(
        "class CredentialService:\n"
        "    async def attach_binding_rule_set(self, cid: str, aid: str, *, identity: Identity):\n"
        "        await self._require_visible_binding(cid, aid, identity=identity, for_write=True)\n"
        "        await ThingRepository.update(cid)\n"
        "    async def _require_visible_binding(\n"
        "        self, cid: str, aid: str, *, identity: Identity, for_write: bool = False\n"
        "    ):\n"
        "        filters = build_access_filters(identity, Credential)\n"
        "        return await CredentialRepository.get_by_id(cid, filters=filters)\n"
    )
    downgraded = ast.parse(
        "class CredentialService:\n"
        "    async def attach_binding_rule_set(self, cid: str, aid: str, *, identity: Identity):\n"
        "        await self._require_visible_binding(cid, aid, identity=identity)\n"
        "        await ThingRepository.update(cid)\n"
        "    async def _require_visible_binding(\n"
        "        self, cid: str, aid: str, *, identity: Identity, for_write: bool = False\n"
        "    ):\n"
        "        filters = build_access_filters(identity, Credential)\n"
        "        return await CredentialRepository.get_by_id(cid, filters=filters)\n"
    )
    name = "CredentialService.attach_binding_rule_set"
    assert _credential_write_gate_present(_ServiceIndex([(gated, Path("<self-check>"))]), name)
    assert not _credential_write_gate_present(
        _ServiceIndex([(downgraded, Path("<self-check>"))]), name
    )


@pytest.mark.arch
def test_credential_exemptions_have_write_gate_in_production() -> None:
    """Each credential exemption still delegates to _require_visible_binding(for_write=True)."""
    methods = _write_filter_findings_in_services()
    index = _ServiceIndex(_service_sources())
    for name in _CREDENTIAL_WRITE_GATE_EXEMPTIONS:
        assert name in methods, f"{name} no longer a scoped mutating method"
        assert _credential_write_gate_present(index, name), (
            f"{name} no longer reaches _require_visible_binding(for_write=True); "
            "its _WRITE_FILTER_ALLOWLIST entry is stale"
        )


@pytest.mark.arch
def test_write_filter_mixin_resolution_uses_c3_mro() -> None:
    """Self-check: write-filter analysis inspects the C3-resolved override."""
    tree = ast.parse(
        "class Root:\n"
        "    async def update(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing, for_write=True)\n"
        "        return await ThingRepository.update(thing_id, filters=filters)\n"
        "class Left(Root):\n"
        "    pass\n"
        "class Right(Root):\n"
        "    async def update(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.update(thing_id, filters=filters)\n"
        "class DiamondService(Left, Right):\n"
        "    pass\n"
    )
    methods = _write_filter_findings(tree, Path("<self-check>"))
    # C3 resolves DiamondService.update to Right.update, which uses a read filter.
    assert methods["DiamondService.update"][2] is False


@pytest.mark.arch
def test_write_filter_check_rejects_read_filter_in_write_method() -> None:
    """Self-check: a repository mutator makes a read-filtered load a violation."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def update(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        await ThingRepository.update(thing_id, filters=filters)\n"
    )
    methods = _write_filter_findings(tree, Path("<self-check>"))
    assert methods["ExampleService.update"][2] is False


@pytest.mark.arch
def test_write_filter_check_covers_wrappers_and_repository_aliases() -> None:
    """Self-check: mutation aliases cannot hide a read-filtered delegated load."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def update(self, thing_id: str, *, identity: Identity):\n"
        "        await self._load_manageable(thing_id, identity)\n"
        "        await self._repo.update(thing_id)\n"
        "    async def _load_manageable(self, thing_id: str, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        return await ThingRepository.get_by_id(thing_id, filters=filters)\n"
        "    async def replace(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        await self._repo.replace(thing_id, filters=filters)\n"
    )
    methods = _write_filter_findings(tree, Path("<self-check>"))
    assert methods["ExampleService.update"][2] is False
    assert methods["ExampleService.replace"][2] is False


@pytest.mark.arch
def test_write_filter_check_resolves_delegated_loads() -> None:
    """Self-check: helper delegation preserves the helper's filter requirements."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def replace(self, thing_id: str, *, identity: Identity):\n"
        "        await self._load_binding_agent(thing_id, identity=identity)\n"
        "        await ThingRepository.replace(thing_id)\n"
        "    async def _load_binding_agent(self, thing_id: str, *, identity: Identity):\n"
        "        return await self.get_agent(thing_id, identity=identity)\n"
        "    async def get_agent(self, thing_id: str, *, identity: Identity):\n"
        "        return await ThingRepository.get_by_id(\n"
        "            thing_id, filters=build_access_filters(identity, Thing)\n"
        "        )\n"
    )
    methods = _write_filter_findings(tree, Path("<self-check>"))
    assert methods["ExampleService.replace"][2] is False


@pytest.mark.arch
def test_repos_do_not_import_identity() -> None:
    """Repositories must never know about Identity."""
    identity_module = "jentic_one.shared.auth.identity"
    repo_dirs = [
        SRC_ROOT / "admin" / "repos",
        SRC_ROOT / "control" / "repos",
        SRC_ROOT / "registry" / "repos",
    ]
    violations: list[str] = []
    for repo_dir in repo_dirs:
        if repo_dir.exists():
            violations.extend(_check_no_import(repo_dir, identity_module))
    assert not violations, "Repositories import Identity:\n" + "\n".join(violations)


@pytest.mark.arch
def test_shared_web_identity_does_not_import_surface_orm() -> None:
    """The shared identity resolution module must not import surface ORM models."""
    identity_file = SRC_ROOT / "shared" / "web" / "identity.py"
    surface_schema_prefixes = [
        "jentic_one.admin.core.schema",
        "jentic_one.control.core.schema",
        "jentic_one.registry.core.schema",
    ]
    violations: list[str] = []
    if identity_file.exists():
        source = identity_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(identity_file))
        for module, lineno in _collect_import_modules(tree):
            for prefix in surface_schema_prefixes:
                if module == prefix or module.startswith(f"{prefix}."):
                    violations.append(
                        f"{identity_file}:{lineno} — imports '{module}' (shared must not "
                        f"couple to surface ORM models)"
                    )
    assert not violations, "Shared web/identity imports surface ORM models:\n" + "\n".join(
        violations
    )


@pytest.mark.arch
def test_scoping_modules_only_import_own_surface_models() -> None:
    """Each surface's scoping module must only reference its own surface's ORM models."""
    scoping_files = [
        ("admin", SRC_ROOT / "admin" / "scoping" / "filters.py"),
        ("admin", SRC_ROOT / "admin" / "scoping" / "actor_lookup.py"),
        ("control", SRC_ROOT / "control" / "scoping" / "filters.py"),
        ("registry", SRC_ROOT / "registry" / "scoping" / "filters.py"),
    ]
    all_surface_schema_prefixes = {
        "admin": "jentic_one.admin.core.schema",
        "control": "jentic_one.control.core.schema",
        "registry": "jentic_one.registry.core.schema",
    }

    violations: list[str] = []
    for surface, scoping_file in scoping_files:
        if not scoping_file.exists():
            continue
        source = scoping_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(scoping_file))
        for module, lineno in _collect_import_modules(tree):
            for other_surface, prefix in all_surface_schema_prefixes.items():
                if other_surface == surface:
                    continue
                if module == prefix or module.startswith(f"{prefix}."):
                    violations.append(
                        f"{scoping_file}:{lineno} — {surface} scoping imports "
                        f"'{module}' from {other_surface} surface"
                    )
    assert not violations, "Cross-surface model imports in scoping:\n" + "\n".join(violations)


@pytest.mark.arch
def test_credential_write_gate_ignores_dead_and_nested_calls() -> None:
    """Self-check: unreachable and unrelated nested true flags do not gate the load."""
    dead = ast.parse(
        "class CredentialService:\n"
        "    async def attach_binding_rule_set(self, cid: str, aid: str, *, identity: Identity):\n"
        "        if False:\n"
        "            await self._require_visible_binding(\n"
        "                cid, aid, identity=identity, for_write=True\n"
        "            )\n"
        "        await self._require_visible_binding(cid, aid, identity=identity)\n"
    )
    nested = ast.parse(
        "class CredentialService:\n"
        "    async def attach_binding_rule_set(self, cid: str, aid: str, *, identity: Identity):\n"
        "        async def unrelated():\n"
        "            await self._require_visible_binding(\n"
        "                cid, aid, identity=identity, for_write=True\n"
        "            )\n"
        "        await self._require_visible_binding(cid, aid, identity=identity)\n"
    )
    name = "CredentialService.attach_binding_rule_set"
    actual = {
        label: _credential_write_gate_present(_ServiceIndex([(tree, Path("<self-check>"))]), name)
        for label, tree in {"dead": dead, "nested": nested}.items()
    }
    assert actual == {"dead": False, "nested": False}


@pytest.mark.arch
def test_job_cancel_contract_binds_owner_and_same_result() -> None:
    """Self-check: another owner or an unrelated status assertion does not qualify."""

    def has_contract(body: str) -> bool:
        func = _owner_cancel_agent_job_test(
            "async def test_owner_can_cancel_their_agents_job():\n" + body
        )
        assert func is not None
        return _owner_cancel_test_has_contract(func)

    actual = {
        "wrong_owner": has_contract(
            "    cancelled = await JobService(ctx).cancel(queued.id, identity=_user(other_id))\n"
            "    assert cancelled.status == JobStatus.CANCELLED\n"
        ),
        "unrelated_status": has_contract(
            "    cancelled = await JobService(ctx).cancel(\n"
            "        queued.id, identity=_user(seed.owner_id)\n"
            "    )\n"
            "    assert unrelated.status == JobStatus.CANCELLED\n"
        ),
        "different_result": has_contract(
            "    other = await JobService(ctx).cancel(queued.id, identity=_user(seed.owner_id))\n"
            "    assert cancelled.status == JobStatus.CANCELLED\n"
        ),
        "complete": has_contract(
            "    cancelled = await JobService(ctx).cancel(\n"
            "        queued.id, identity=_user(seed.owner_id)\n"
            "    )\n"
            "    assert cancelled.status == JobStatus.CANCELLED\n"
        ),
    }
    assert actual == {
        "wrong_owner": False,
        "unrelated_status": False,
        "different_result": False,
        "complete": True,
    }
